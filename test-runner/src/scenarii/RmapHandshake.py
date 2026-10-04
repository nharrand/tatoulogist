"""RMAP handshake, then retrieval of the PDF behind the link."""

from __future__ import annotations

import re
import secrets
from typing import TYPE_CHECKING, Any, ClassVar

from rmap import RMAPClient, RMAPError

from scenarii.Scenario import Scenario
from steps.GetVersion import GetVersion
from steps.RmapGetLink import RmapGetLink
from steps.RmapInitiate import RmapInitiate
from steps.Step import StepResult

if TYPE_CHECKING:
    from model.Group import Group

LINK_PATTERN = re.compile(r"^[0-9a-f]{32}$")
U64 = 2 ** 64


class RmapHandshake(Scenario):
    """Authenticate to the group's server with RMAP and retrieve a PDF.

    1. rmap-initiate: send msg1 {identity, nonceClient} encrypted with the
       group's public key (keys/public_keys/<group>.asc). The response must
       decrypt with the bench's private key (keys/server_private.asc) to
       {nonceClient, nonceServer}, echoing our nonceClient.
    2. rmap-get-link: send msg2 {nonceServer}. The response must decrypt to
       {result}, where result is hex(nonceClient) || hex(nonceServer).
    3. get-version/<result> must return a PDF, which is saved to pdfs/rmap/.

    With NEGATIVE = True, two more checks follow: an unknown identity is
    refused by rmap-initiate, and a wrong nonceServer is refused by
    rmap-get-link (in both cases without a 5xx).

    The identity comes from --rmap-identity (or $TATOU_RMAP_IDENTITY), and
    the private key passphrase, if any, from --key-passphrase (or
    $TATOU_KEY_PASSPHRASE).
    """

    description = "RMAP handshake (rmap-initiate, rmap-get-link) and get-version of the link"

    #: Also check that bad handshakes are refused.
    NEGATIVE: ClassVar[bool] = True

    def execute(self, group: Group) -> None:
        self.require(bool(group.rmap_identity),
                     "an RMAP identity is configured (--rmap-identity)")
        self.require(group.private_key_path.is_file(), "the bench private key exists",
                     {"path": str(group.private_key_path)})
        self.require(group.public_key_path.is_file(), f"the public key of {group.name} exists",
                     {"path": str(group.public_key_path)})

        client = self.new_client(group, group.rmap_identity)
        self.check_private_key(client, group)
        link = self.handshake(client)
        self.retrieve(group, link)

        if self.NEGATIVE:
            self.unknown_identity_is_refused(group)
            self.wrong_nonce_is_refused(group)

    # ------------------------------------------------------------ handshake

    def new_client(self, group: Group, identity: str) -> RMAPClient:
        try:
            client = RMAPClient(identity, group.private_key_path, group.public_key_path,
                                passphrase=group.key_passphrase)
        except RMAPError as exc:
            self.require(False, "the RMAP keys can be loaded", {"error": str(exc)})
            raise  # unreachable: require() raises
        return client

    def check_private_key(self, client: RMAPClient, group: Group) -> None:
        """Fail early, with a clear message, on a missing or wrong passphrase."""
        key = client.clientPrivateKey
        if not key.is_protected:
            return
        self.require(bool(group.key_passphrase), "a passphrase is given for the "
                     "protected private key (--key-passphrase)")
        try:
            with key.unlock(group.key_passphrase):
                pass
        except Exception as exc:
            self.require(False, "the private key passphrase is correct", _error(exc))

    def handshake(self, client: RMAPClient) -> str:
        """Run msg1/resp1/msg2/resp2 and return the link to request."""
        initiated = self.step(RmapInitiate(client.build_msg1()))
        self.check_status(initiated, (200, 201), "rmap-initiate succeeds", required=True)
        self.check_fields(initiated, {"payload": str},
                          "rmap-initiate returns a payload", required=True)
        try:
            client.process_resp1(initiated.response_json)
        except ValueError as exc:
            self.require(False, "rmap-initiate response echoes our nonceClient",
                         {"error": str(exc)})
        except (RMAPError, KeyError, TypeError) as exc:
            self.require(False, "rmap-initiate response decrypts to "
                                "{nonceClient, nonceServer}", _error(exc))
        nonce_server = client.nonceServer
        self.require(isinstance(nonce_server, int) and not isinstance(nonce_server, bool)
                     and 0 <= nonce_server < U64,
                     "rmap-initiate response decrypts to {nonceClient, nonceServer} "
                     "with a 64-bit nonceServer",
                     {"nonceClient": client.nonceClient, "nonceServer": nonce_server})

        got_link = self.step(RmapGetLink(client.build_msg2()))
        self.check_status(got_link, (200, 201), "rmap-get-link succeeds", required=True)
        self.check_fields(got_link, {"payload": str},
                          "rmap-get-link returns a payload", required=True)
        try:
            result = client.process_resp2(got_link.response_json)
        except (RMAPError, KeyError, TypeError) as exc:
            self.require(False, "rmap-get-link response decrypts to {result}", _error(exc))
            raise  # unreachable

        expected = client.expected_link
        self.require(isinstance(result, str) and bool(result),
                     "rmap-get-link response decrypts to {result}", {"result": result})
        self.check(result == expected,
                   "rmap-get-link result is hex(nonceClient) || hex(nonceServer)",
                   {"expected": expected, "received": result})
        self.check(bool(LINK_PATTERN.match(result)), "rmap-get-link result is 32 hex chars",
                   {"received": result})

        # Tolerate a server that sends a full URL (RMAP's linkPrefix): use its last segment.
        return result.rstrip("/").rsplit("/", 1)[-1]

    def retrieve(self, group: Group, link: str) -> None:
        result = self.step(GetVersion(link))
        self.check_status(result, 200, "get-version with the RMAP link succeeds",
                          required=True)
        content = result.content or b""
        if not self.check(content.startswith(b"%PDF"), "get-version returns a PDF",
                          {"content_type": result.content_type, "length": len(content)}):
            return
        self.check("application/pdf" in (result.content_type or ""),
                   "get-version is served as application/pdf",
                   {"content_type": result.content_type})
        path = group.write_rmap_pdf(content, f"{group.rmap_identity}_{link}.pdf")
        self.check(True, "the retrieved PDF is saved", {"path": path, "size": len(content)})

    # ------------------------------------------------------------- negative

    def unknown_identity_is_refused(self, group: Group) -> None:
        stranger = self.new_client(group, f"Unknown_{secrets.token_hex(4)}")
        result = self.step(RmapInitiate(stranger.build_msg1()))
        accepted = result.ok and _decrypts(stranger.process_resp1, result)
        self.check(not accepted, "rmap-initiate refuses an unknown identity",
                   _status(result))
        self.check(result.responded and result.status_code < 500,
                   "rmap-initiate answers an unknown identity without a server error",
                   _status(result))

    def wrong_nonce_is_refused(self, group: Group) -> None:
        client = self.new_client(group, group.rmap_identity)
        initiated = self.step(RmapInitiate(client.build_msg1()))
        if not (initiated.ok and _decrypts(client.process_resp1, initiated)):
            self.check(False, "a second rmap-initiate succeeds (needed for the wrong "
                              "nonce test)", _status(initiated))
            return
        client.nonceServer = (client.nonceServer + 1) % U64
        result = self.step(RmapGetLink(client.build_msg2()))
        accepted = result.ok and _decrypts(client.process_resp2, result)
        self.check(not accepted, "rmap-get-link refuses a wrong nonceServer", _status(result))
        self.check(result.responded and result.status_code < 500,
                   "rmap-get-link answers a wrong nonceServer without a server error",
                   _status(result))


def _decrypts(process: Any, result: StepResult) -> bool:
    """True if `process` (an RMAPClient.process_* method) accepts the response."""
    try:
        process(result.response_json)
        return True
    except (RMAPError, ValueError, KeyError, TypeError):
        return False


def _status(result: StepResult) -> dict[str, Any]:
    return {"status_code": result.status_code, "error": result.error,
            "response": result.response_json}


def _error(exc: Exception) -> dict[str, str]:
    return {"error": f"{type(exc).__name__}: {exc}"}
