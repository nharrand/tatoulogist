"""Full watermarking round trip on one user."""

from __future__ import annotations

import hashlib
import logging
import random
import secrets
import uuid
from typing import TYPE_CHECKING, Any, ClassVar

from model.Document import Document
from model.User import User
from model.Version import Version
from scenarii.Scenario import Scenario
from steps.CreateUser import CreateUser
from steps.CreateWatermark import CreateWatermark
from steps.DeleteDocument import DeleteDocument
from steps.GetVersion import GetVersion
from steps.GetWatermarkingMethods import GetWatermarkingMethods
from steps.ListDocuments import ListDocuments
from steps.ListVersions import ListVersions
from steps.Login import Login
from steps.ReadWatermark import ReadWatermark
from steps.Step import StepResult
from steps.UploadDocument import UploadDocument

if TYPE_CHECKING:
    from model.Group import Group

log = logging.getLogger("tatou")


class WatermarkLifecycle(Scenario):
    """Take an existing user (or create one), log in, then:

    1. Upload a random PDF from data/pdfs and check that it is listed.
    2. Pick a watermarking method from get-watermarking-methods.
    3. Create a watermark and check that list-versions shows it.
    4. Download the version with get-version.
    5. Upload the downloaded PDF as a new document.
    6. Read the watermark from that copy and compare it with the secret.
    7. Delete the copy and check that it is no longer listed.

    The API has no delete-version endpoint, so step 7 deletes the
    re-uploaded copy. The original document and its version are kept and
    recorded in data.json.
    """

    description = ("Upload, watermark, download, re-upload, read the watermark, "
                   "and delete the re-uploaded copy")

    #: Force a watermarking method. None picks one at random from the server's list.
    METHOD: ClassVar[str | None] = None
    #: Position sent to create-watermark and read-watermark. None omits it.
    POSITION: ClassVar[str | None] = None

    def execute(self, group: Group) -> None:
        user = self.get_user(group)
        self.login(user)
        document = self.upload_source(group, user)
        self.check_document_listed(user, document)
        method = self.pick_method()
        self.round_trip(group, user, document, method)

    def round_trip(self, group: Group, user: User, document: Document, method: str) -> None:
        """Watermark `document` with `method`, then download, re-upload, read, delete."""
        version = self.create_watermark(user, document, method)
        self.check_version_listed(user, document, version)
        content = self.download_version(group, document, version)
        copy = self.upload_version(group, user, document, version, content)
        self.read_watermark(user, copy, version)
        self.delete_copy(user, document, copy)

    # ---------------------------------------------------------------- user

    def get_user(self, group: Group) -> User:
        if group.users:
            return group.users[0]
        suffix = uuid.uuid4().hex[:8]
        user = User(login=f"tester_{suffix}", email=f"tester_{suffix}@example.com",
                    password=secrets.token_urlsafe(12))
        created = self.step(CreateUser(user))
        self.check_status(created, (200, 201), "create-user succeeds", required=True)
        user.update_from_server(_obj(created))
        group.add_user(user)
        return user

    def login(self, user: User) -> None:
        result = self.step(Login(user))
        self.check_status(result, 200, f"login as {user.login} succeeds", required=True)
        self.require(user.token is not None, "login returns a token")

    # ------------------------------------------------------------- upload

    def upload_source(self, group: Group, user: User) -> Document:
        pool_dir = group.data_root / "pdfs"
        pool = sorted(pool_dir.glob("*.pdf"))
        self.require(bool(pool), f"source PDFs are available in {pool_dir}")
        source = random.choice(pool)

        document = Document(name=f"{source.stem}_{uuid.uuid4().hex[:6]}", path=str(source))
        result = self.step(UploadDocument(user, document))
        self.check_status(result, (200, 201), f"upload-document of {source.name} succeeds",
                          required=True)
        data = _obj(result)
        self.require(data.get("id") is not None, "upload-document returns the document id",
                     {"response": result.response_json})

        # The document is on the server: record it.
        document.update_from_server(data)
        document.path = group.import_pdf(source, f"{document.id}_{source.name}")
        user.add_document(document)

        self.check_fields(result, {"id": (int, str), "name": str, "creation": str,
                                   "sha256": str, "size": int},
                          "upload-document returns id, name, creation, sha256 and size")
        self.check_upload_metadata(data, document.name, source.read_bytes())
        return document

    def check_upload_metadata(self, data: dict[str, Any], name: str, content: bytes) -> None:
        expected_sha = hashlib.sha256(content).hexdigest()
        self.check(str(data.get("sha256", "")).lower() == expected_sha,
                   "upload-document sha256 matches the uploaded file",
                   {"expected": expected_sha, "server": data.get("sha256")})
        self.check(data.get("size") == len(content),
                   "upload-document size matches the uploaded file",
                   {"expected": len(content), "server": data.get("size")})
        self.check(data.get("name") == name, "upload-document keeps the given name",
                   {"expected": name, "server": data.get("name")})

    def check_document_listed(self, user: User, document: Document) -> None:
        result = self.step(ListDocuments(user))
        if not self.check_status(result, 200, "list-documents succeeds"):
            return
        entry = _find(_field(result.response_json, "documents"), document.id)
        if self.check(entry is not None, f"uploaded document {document.id} is listed"):
            self.check(entry.get("name") == document.name,
                       "listed document has the uploaded name",
                       {"expected": document.name, "server": entry.get("name")})

    # ---------------------------------------------------------- watermark

    def list_methods(self) -> list[str]:
        """Names of the methods returned by get-watermarking-methods (at least one)."""
        result = self.step(GetWatermarkingMethods())
        self.check_status(result, 200, "get-watermarking-methods succeeds", required=True)
        self.check_fields(result, {"count": int, "methods": list},
                          "get-watermarking-methods returns count and methods")
        methods = _field(result.response_json, "methods")
        methods = methods if isinstance(methods, list) else []
        count = _field(result.response_json, "count")
        if isinstance(count, int):
            self.check(count == len(methods), "method count matches the methods list",
                       {"count": count, "listed": len(methods)})
        names = [m["name"] for m in methods
                 if isinstance(m, dict) and isinstance(m.get("name"), str)]
        self.require(bool(names), "at least one watermarking method is available",
                     {"methods": methods})
        return names

    def pick_method(self) -> str:
        """The forced METHOD, or a random method from the server's list."""
        names = self.list_methods()
        if self.METHOD is not None:
            self.require(self.METHOD in names, f"method {self.METHOD!r} is available",
                         {"methods": names})
            return self.METHOD
        method = random.choice(names)
        log.info("[%s]   using watermarking method %r", self.result.group, method)
        return method

    def create_watermark(self, user: User, document: Document, method: str) -> Version:
        suffix = uuid.uuid4().hex[:8]
        version = Version(method=method, position=self.POSITION,
                          key=secrets.token_urlsafe(12), secret=f"secret_{suffix}",
                          intended_for=f"recipient_{suffix}")
        result = self.step(CreateWatermark(user, document, version))
        self.check_status(result, (200, 201), f"create-watermark with {method} succeeds",
                          required=True)
        data = _obj(result)
        self.require(isinstance(data.get("link"), str) and bool(data["link"]),
                     "create-watermark returns a link", {"response": result.response_json})

        self.check_fields(result, {"id": int, "documentid": (int, str), "link": str,
                                   "intended_for": str, "method": str,
                                   "filename": str, "size": int},
                          "create-watermark returns the version metadata")
        self.check(str(data.get("documentid")) == str(document.id),
                   "create-watermark refers to the watermarked document",
                   {"expected": document.id, "server": data.get("documentid")})
        self.check(data.get("method") == method
                   and data.get("intended_for") == version.intended_for,
                   "create-watermark echoes method and intended_for",
                   {"sent": {"method": method, "intended_for": version.intended_for},
                    "server": {"method": data.get("method"),
                               "intended_for": data.get("intended_for")}})

        # The version is on the server: record it.
        version.update_from_server(data)
        document.add_version(version)
        return version

    def check_version_listed(self, user: User, document: Document, version: Version) -> None:
        result = self.step(ListVersions(user, document))
        if not self.check_status(result, 200, "list-versions succeeds"):
            return
        entry = _find(_field(result.response_json, "versions"), version.id)
        if not self.check(entry is not None, f"version {version.id} is listed"):
            return
        diffs = {name: {"local": getattr(version, name), "server": entry.get(name)}
                 for name in ("link", "secret", "method", "intended_for")
                 if entry.get(name) != getattr(version, name)}
        self.check(not diffs, "listed version matches the created one", diffs or None)

    def download_version(self, group: Group, document: Document, version: Version) -> bytes:
        result = self.step(GetVersion(version.link))
        self.check_status(result, 200, "get-version succeeds", required=True)
        content = result.content or b""
        self.require(content.startswith(b"%PDF"), "get-version returns a PDF",
                     {"content_type": result.content_type, "length": len(content)})

        version.path = group.write_version_pdf(content, f"{version.link}.pdf")

        self.check("application/pdf" in (result.content_type or ""),
                   "get-version is served as application/pdf",
                   {"content_type": result.content_type})
        if version.size is not None:
            self.check(len(content) == version.size,
                       "downloaded size matches the create-watermark size",
                       {"expected": version.size, "downloaded": len(content)})
        self.check(content != document.read_bytes(),
                   "watermarked PDF differs from the original")
        return content

    # ----------------------------------------------------- re-upload, read

    def upload_version(self, group: Group, user: User, document: Document,
                       version: Version, content: bytes) -> Document:
        source = version.resolved_path()
        copy = Document(name=f"{document.name}_{version.intended_for}", path=str(source))
        result = self.step(UploadDocument(user, copy))
        self.check_status(result, (200, 201), "upload of the watermarked PDF succeeds",
                          required=True)
        data = _obj(result)
        self.require(data.get("id") is not None,
                     "upload of the watermarked PDF returns a document id",
                     {"response": result.response_json})

        copy.update_from_server(data)
        copy.path = group.import_pdf(source, f"{copy.id}_{source.name}")
        user.add_document(copy)

        self.check_upload_metadata(data, copy.name, content)
        return copy

    def read_watermark(self, user: User, copy: Document, version: Version) -> None:
        result = self.step(ReadWatermark(user, copy, version.method, version.key,
                                         version.position))
        if not self.check_status(result, (200, 201), "read-watermark succeeds"):
            return
        self.check_fields(result, {"secret": str, "method": str},
                          "read-watermark returns secret and method")
        secret = _field(result.response_json, "secret")
        self.check(secret == version.secret, "read-watermark returns the embedded secret",
                   {"expected": version.secret, "read": secret})

    # ------------------------------------------------------------- delete

    def delete_copy(self, user: User, document: Document, copy: Document) -> None:
        result = self.step(DeleteDocument(user, copy))
        if not self.check_status(result, (200, 204), "delete-document succeeds"):
            return

        still_listed: bool | None = None
        listing = self.step(ListDocuments(user))
        if self.check_status(listing, 200, "list-documents succeeds after delete"):
            entries = _field(listing.response_json, "documents")
            still_listed = _find(entries, copy.id) is not None
            self.check(not still_listed, f"deleted document {copy.id} is no longer listed")
            self.check(_find(entries, document.id) is not None,
                       f"original document {document.id} is still listed")
        if still_listed:
            return  # the server still has it, so it stays in the model

        path = copy.resolved_path()
        user.remove_document(copy)
        path.unlink(missing_ok=True)


def _obj(result: StepResult) -> dict[str, Any]:
    """The response JSON if it is an object, else an empty dict."""
    return result.response_json if isinstance(result.response_json, dict) else {}


def _field(data: Any, name: str) -> Any:
    return data.get(name) if isinstance(data, dict) else None


def _find(entries: Any, id: int | str | None) -> dict[str, Any] | None:
    """The entry of a listing whose id equals `id` (compared as strings)."""
    if not isinstance(entries, list) or id is None:
        return None
    for entry in entries:
        if isinstance(entry, dict) and str(entry.get("id")) == str(id):
            return entry
    return None
