"""Flask app: group view (IP-scoped), admin view (password), background reruns.

Run it with:  python -m webapp  (see __main__.py), or create_app() for WSGI.

Configuration (environment variables):
  TATOU_DATA_DIR        data directory with groups.csv (default: ./data)
  TATOU_REPORTS_DIR     reports directory             (default: ./reports)
  TATOU_ADMIN_PASSWORD  password for /admin           (required for admin)
  TATOU_SECRET_KEY      Flask session secret          (random if unset)
  TATOU_TRUST_PROXY     "1" to trust X-Forwarded-For  (default: "1")
  TATOU_TIMEOUT         HTTP timeout for reruns, seconds (default: 5)
  TATOU_RMAP_IDENTITY   identity for the RMAP scenario
  TATOU_KEY_PASSPHRASE  passphrase for the RMAP private key
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, jsonify, redirect, render_template,
                   render_template_string, request, session, url_for)

import CLI
from model.Group import GROUPS_CSV, Group
from webapp.remote import RemoteQueue
from webapp.reports_store import ReportStore
from webapp.runner import Runner

log = logging.getLogger("tatou.webapp")


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    cfg = config or {}

    data_dir = Path(cfg.get("DATA_DIR") or os.environ.get("TATOU_DATA_DIR", "data"))
    reports_dir = Path(cfg.get("REPORTS_DIR")
                       or os.environ.get("TATOU_REPORTS_DIR", "reports"))
    control_dir = Path(cfg.get("CONTROL_DIR")
                       or os.environ.get("TATOU_CONTROL_DIR", "control"))
    remote = cfg.get("REMOTE_SCENARIOS",
                     os.environ.get("TATOU_REMOTE_SCENARIOS", "RmapHandshake"))
    remote_scenarios = {s.strip() for s in remote.split(",") if s.strip()} \
        if isinstance(remote, str) else set(remote)
    app.config.update(
        DATA_DIR=data_dir,
        REPORTS_DIR=reports_dir,
        CONTROL_DIR=control_dir,
        REMOTE_SCENARIOS=remote_scenarios,
        ADMIN_PASSWORD=cfg.get("ADMIN_PASSWORD") or os.environ.get("TATOU_ADMIN_PASSWORD"),
        TRUST_PROXY=str(cfg.get("TRUST_PROXY",
                                os.environ.get("TATOU_TRUST_PROXY", "1"))) == "1",
        TIMEOUT=float(cfg.get("TIMEOUT") or os.environ.get("TATOU_TIMEOUT", "5")),
        RMAP_IDENTITY=cfg.get("RMAP_IDENTITY") or os.environ.get("TATOU_RMAP_IDENTITY"),
        KEY_PASSPHRASE=cfg.get("KEY_PASSPHRASE") or os.environ.get("TATOU_KEY_PASSPHRASE"),
    )
    app.secret_key = (cfg.get("SECRET_KEY") or os.environ.get("TATOU_SECRET_KEY")
                      or secrets.token_hex(32))

    store = ReportStore(reports_dir)

    def run_fn(group: str, scenario: str):
        # Local lane: non-RMAP scenarios run in-process. RMAP has no key here.
        return CLI.run_one(data_dir, group, scenario, timeout=app.config["TIMEOUT"],
                           rmap_identity=app.config["RMAP_IDENTITY"],
                           key_passphrase=app.config["KEY_PASSPHRASE"],
                           reports_dir=reports_dir)

    runner = Runner(run_fn)
    remote_queue = RemoteQueue(control_dir)
    app.extensions["store"] = store
    app.extensions["runner"] = runner
    app.extensions["remote_queue"] = remote_queue

    _register_routes(app, store, runner, remote_queue)
    return app


# --------------------------------------------------------------------- helpers

def _client_ip(app: Flask) -> str:
    """The caller's IP, honoring X-Forwarded-For only when TRUST_PROXY is on."""
    if app.config["TRUST_PROXY"]:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            # The left-most entry is the original client.
            return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def _ip_to_group(app: Flask) -> dict[str, str]:
    """Map IP -> group name from groups.csv (best effort; empty on error)."""
    try:
        rows = Group.read_csv(app.config["DATA_DIR"] / GROUPS_CSV)
    except (OSError, ValueError):
        return {}
    return {row.ip: row.name for row in rows}


def _known_groups(app: Flask) -> list[str]:
    try:
        return [row.name for row in Group.read_csv(app.config["DATA_DIR"] / GROUPS_CSV)]
    except (OSError, ValueError):
        return []


def _caller_group(app: Flask) -> str | None:
    """The group whose server IP matches the caller, or None."""
    return _ip_to_group(app).get(_client_ip(app))


def _scenario_names() -> list[str]:
    scenarios, _ = CLI.discover_scenarios()
    return list(scenarios)


def _is_admin() -> bool:
    return bool(session.get("admin"))


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _is_admin():
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def _authorize_group(app: Flask, group: str) -> None:
    """Allow if admin, or if `group` is the caller's own group. Else 403."""
    if _is_admin():
        return
    if group != _caller_group(app):
        abort(403)


# ---------------------------------------------------------------------- routes

def _register_routes(app: Flask, store: ReportStore, runner: Runner,
                     remote_queue: RemoteQueue) -> None:

    @app.route("/")
    def group_view():
        ip = _client_ip(app)
        group = _ip_to_group(app).get(ip)
        if group is None:
            # Personalize the not-found page with the caller's address so a
            # group can tell at a glance which location the server sees them at.
            message = (
                f"No test results are available for your location ({ip}). "
                "If you reach this from a group machine and still see this "
                "message, the server may not be reading the forwarded client "
                'address. An administrator can sign in at <a href="/admin">/admin</a>.'
            )
            page = (
                '{% extends "base.html" %}'
                "{% block title %}No group for this address{% endblock %}"
                "{% block body %}"
                '<header class="page"><div><h1>No group for this address</h1>'
                '<div class="sub">source IP ' + (ip or "unknown") + "</div>"
                "</div></header>"
                '<p class="empty">' + message + "</p>"
                "{% endblock %}"
            )
            return render_template_string(page), 404
        return render_template("group.html", group=group,
                               scenarios=_scenario_names(), is_admin=_is_admin())

    @app.route("/admin")
    @admin_required
    def admin_view():
        return render_template("admin.html", groups=_known_groups(app),
                               scenarios=_scenario_names())

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        configured = app.config["ADMIN_PASSWORD"]
        error = None
        if request.method == "POST":
            supplied = request.form.get("password", "")
            if configured and hmac.compare_digest(supplied, configured):
                session["admin"] = True
                target = request.args.get("next", "")
                return redirect(target if target.startswith("/") else url_for("admin_view"))
            error = "Incorrect password." if configured else \
                "Admin password is not configured on the server."
        return render_template("login.html", error=error), (401 if error else 200)

    @app.route("/admin/logout", methods=["POST"])
    def admin_logout():
        session.pop("admin", None)
        return redirect(url_for("admin_login"))

    # ---- data endpoints (JSON), scoped to the caller's group unless admin ----

    @app.get("/api/groups")
    @admin_required
    def api_groups():
        known = _known_groups(app)
        with_reports = set(store.groups())
        return jsonify([{"group": g, "has_reports": g in with_reports} for g in known])

    @app.get("/api/group/<group>/scenarios")
    def api_group_scenarios(group):
        _authorize_group(app, group)
        histories = {h.scenario: h for h in store.history_for(group)}
        out = []
        for scenario in _scenario_names():
            history = histories.pop(scenario, None)
            last = history.last if history else None
            out.append({
                "scenario": scenario,
                "runs": len(history.runs) if history else 0,
                "last": last.to_summary() if last else None,
            })
        # Scenarios no longer in the code but present in old reports.
        for scenario, history in sorted(histories.items()):
            out.append({"scenario": scenario, "runs": len(history.runs),
                        "last": history.last.to_summary() if history.last else None,
                        "unknown": True})
        return jsonify(out)

    @app.get("/api/group/<group>/scenario/<scenario>/history")
    def api_history(group, scenario):
        _authorize_group(app, group)
        for history in store.history_for(group):
            if history.scenario == scenario:
                return jsonify([r.to_summary() for r in history.runs])
        return jsonify([])

    @app.get("/api/group/<group>/run")
    def api_run_detail(group, source=None):
        _authorize_group(app, group)
        source = request.args.get("source", "")
        scenario = request.args.get("scenario", "")
        run = store.run_detail(group, source, scenario)
        if run is None:
            abort(404)
        return jsonify(run.result)

    # ---- rerun (start + poll) ----

    @app.post("/api/group/<group>/rerun")
    def api_rerun(group):
        _authorize_group(app, group)
        scenario = (request.get_json(silent=True) or {}).get("scenario") \
            or request.form.get("scenario", "")
        if scenario not in _scenario_names():
            return jsonify({"error": f"unknown scenario: {scenario}"}), 400
        if group not in _known_groups(app):
            return jsonify({"error": f"unknown group: {group}"}), 404
        # RMAP scenarios run on the separate rmap-runner (it holds the key).
        if scenario in app.config["REMOTE_SCENARIOS"]:
            job_id, reason = remote_queue.start(group, scenario)
            if job_id is None:
                return jsonify({"error": reason, "busy": True}), 409
            return jsonify({"job": job_id, "remote": True})
        job, reason = runner.start(group, scenario)
        if job is None:
            return jsonify({"error": reason, "busy": True}), 409
        return jsonify({"job": job.id})

    @app.get("/api/job/<job_id>")
    def api_job(job_id):
        remote = remote_queue.status(job_id)
        if remote is not None:
            _authorize_group(app, remote["group"])
            return jsonify(remote)
        job = runner.job(job_id)
        if job is None:
            abort(404)
        _authorize_group(app, job.group)
        return jsonify(job.to_dict())

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok", "busy": runner.busy})
