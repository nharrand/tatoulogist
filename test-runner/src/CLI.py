"""Command-line entry point.

    tatou-test run --group Group_01|all --scenario Name|all
                   [--data DIR] [--timeout SEC] [--output FILE] [-v | -vv | -q]
    tatou-test list scenarios|groups [--data DIR]
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import logging
import os
import pkgutil
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scenarii
from model.Group import DEFAULT_TIMEOUT, GROUPS_CSV, Group, GroupRow
from scenarii.Scenario import Scenario, ScenarioResult
from steps.Step import utc_now

log = logging.getLogger("tatou")

ALL = "all"
ENV_RMAP_IDENTITY = "TATOU_RMAP_IDENTITY"
ENV_KEY_PASSPHRASE = "TATOU_KEY_PASSPHRASE"
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


class UsageError(Exception):
    """Bad command-line input or configuration; reported without a traceback."""


# ------------------------------------------------------------------ discovery

def discover_scenarios() -> tuple[dict[str, type[Scenario]], list[str]]:
    """Import every module of the scenarii package and collect Scenario subclasses.

    Returns {name: class} sorted by name, plus a list of load errors. A
    broken scenario file is reported but does not prevent the others from
    running.
    """
    found: dict[str, type[Scenario]] = {}
    errors: list[str] = []
    for info in sorted(pkgutil.iter_modules(scenarii.__path__), key=lambda m: m.name):
        module_name = f"{scenarii.__name__}.{info.name}"
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            errors.append(f"{module_name}: {type(exc).__name__}: {exc}")
            continue
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if (not issubclass(cls, Scenario) or inspect.isabstract(cls)
                    or cls.__module__ != module.__name__):
                continue
            name = cls.scenario_name()
            if name in found:
                errors.append(f"{module_name}: duplicate scenario name {name!r} "
                              f"(already defined in {found[name].__module__})")
                continue
            found[name] = cls
    return dict(sorted(found.items())), errors


def select(requested: list[str], available: list[str], kind: str) -> list[str]:
    """Resolve a list of names (or 'all') against the available ones, keeping their order."""
    if ALL in requested:
        return list(available)
    unknown = [name for name in requested if name not in available]
    if unknown:
        raise UsageError(f"unknown {kind}: {', '.join(unknown)} "
                         f"(available: {', '.join(available) or 'none'})")
    return [name for name in available if name in requested]


def select_scenarios(requested: list[str], scenarios: dict[str, type[Scenario]]) -> list[str]:
    """Like select(), but 'all' leaves out maintenance scenarios.

    A maintenance scenario runs only when named, e.g. `-s RealignLocalData`
    or `-s all RealignLocalData`.
    """
    named = select([n for n in requested if n != ALL], list(scenarios), "scenario")
    if ALL not in requested:
        return named
    return [name for name, cls in scenarios.items()
            if not cls.maintenance or name in named]


def read_groups(data_root: Path) -> list[GroupRow]:
    csv_path = data_root / GROUPS_CSV
    if not csv_path.exists():
        raise UsageError(f"{csv_path} not found (use --data to point to the data directory)")
    try:
        return Group.read_csv(csv_path)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc


# ---------------------------------------------------------------------- run

def cmd_run(args: argparse.Namespace) -> int:
    data_root: Path = args.data
    scenarios, load_errors = discover_scenarios()
    for error in load_errors:
        log.error("could not load scenario %s", error)

    scenario_names = select_scenarios(args.scenario, scenarios)
    if not scenario_names:
        raise UsageError("no scenario selected (maintenance scenarios must be named)")
    rows = read_groups(data_root)
    group_names = select(args.group, [row.name for row in rows], "group")
    rows = [row for row in rows if row.name in group_names]

    report: dict[str, Any] = {
        "started_at": utc_now(),
        "finished_at": None,
        "command": redact_argv(sys.argv),
        "rmap_identity": args.rmap_identity,
        "data_root": str(data_root),
        "timeout": args.timeout,
        "groups": group_names,
        "scenarios": scenario_names,
        "interrupted": False,
        "load_errors": load_errors,
        "group_errors": [],
        "summary": {},
        "results": [],
    }
    results: list[ScenarioResult] = []

    try:
        for row in rows:
            name = row.name
            try:
                group = Group.from_row(row, data_root, timeout=args.timeout,
                                       rmap_identity=args.rmap_identity,
                                       key_passphrase=args.key_passphrase)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                log.error("[%s] could not load group data: %s", name, message)
                report["group_errors"].append({"group": name, "error": message})
                continue

            log.info("[%s] %s (%d scenario(s))", name, group.base_url, len(scenario_names))
            try:
                for scenario_name in scenario_names:
                    results.append(run_scenario(scenarios[scenario_name], group))
                    save_group(group, report)
            except KeyboardInterrupt:
                save_group(group, report)
                raise
            finally:
                group.close()
    except KeyboardInterrupt:
        report["interrupted"] = True
        log.warning("interrupted, writing a partial report")

    report["finished_at"] = utc_now()
    report["results"] = [r.to_dict() for r in results]
    report["summary"] = summarize(results)
    output = write_report(report, args.output)

    print_summary(results, report, output)
    clean = (not report["interrupted"] and not report["group_errors"]
             and not load_errors and all(r.passed for r in results))
    return EXIT_OK if clean else EXIT_FAILED


def redact_argv(argv: list[str]) -> list[str]:
    """Hide the value of --key-passphrase in the recorded command line."""
    redacted, hide_next = [], False
    for arg in argv:
        if hide_next:
            redacted.append("***")
            hide_next = False
        elif arg == "--key-passphrase":
            redacted.append(arg)
            hide_next = True
        elif arg.startswith("--key-passphrase="):
            redacted.append("--key-passphrase=***")
        else:
            redacted.append(arg)
    return redacted


def run_one(data_root: Path, group_name: str, scenario_name: str, *,
            timeout: float = DEFAULT_TIMEOUT, rmap_identity: str | None = None,
            key_passphrase: str | None = None,
            reports_dir: Path | None = None) -> tuple[Path, dict[str, Any]]:
    """Run a single scenario on a single group and write a report.

    Reuses the same machinery as the CLI (scenario discovery, running, per-group
    save, report writing). Returns the report path and the report dict. Raises
    UsageError for an unknown group or scenario, so callers can report it.
    """
    data_root = Path(data_root)
    scenarios, load_errors = discover_scenarios()
    if scenario_name not in scenarios:
        raise UsageError(f"unknown scenario: {scenario_name}")
    rows = {row.name: row for row in read_groups(data_root)}
    if group_name not in rows:
        raise UsageError(f"unknown group: {group_name}")

    report: dict[str, Any] = {
        "started_at": utc_now(), "finished_at": None,
        "command": ["webapp", "run", group_name, scenario_name],
        "rmap_identity": rmap_identity, "data_root": str(data_root), "timeout": timeout,
        "groups": [group_name], "scenarios": [scenario_name], "interrupted": False,
        "load_errors": load_errors, "group_errors": [], "summary": {}, "results": [],
    }
    results: list[ScenarioResult] = []
    try:
        group = Group.from_row(rows[group_name], data_root, timeout=timeout,
                               rmap_identity=rmap_identity, key_passphrase=key_passphrase)
    except Exception as exc:
        report["group_errors"].append({"group": group_name,
                                       "error": f"{type(exc).__name__}: {exc}"})
    else:
        try:
            results.append(run_scenario(scenarios[scenario_name], group))
            save_group(group, report)
        finally:
            group.close()

    report["finished_at"] = utc_now()
    report["results"] = [r.to_dict() for r in results]
    report["summary"] = summarize(results)
    if reports_dir is None:
        reports_dir = data_root.parent / "reports"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output = Path(reports_dir) / f"report_{group_name}_{scenario_name}_{stamp}.json"
    write_report(report, output)
    return output, report


def run_scenario(cls: type[Scenario], group: Group) -> ScenarioResult:
    try:
        scenario = cls()
    except Exception as exc:  # e.g. a constructor requiring arguments
        result = ScenarioResult(scenario=cls.scenario_name(), group=group.name)
        result.error = f"could not instantiate: {type(exc).__name__}: {exc}"
        result.finished_at = utc_now()
        log.error("[%s] %s: %s", group.name, result.scenario, result.error)
        return result
    return scenario.run(group)


def save_group(group: Group, report: dict[str, Any]) -> None:
    try:
        group.save()
    except OSError as exc:
        message = f"could not save {group.data_file}: {exc}"
        log.error("[%s] %s", group.name, message)
        report["group_errors"].append({"group": group.name, "error": message})


def summarize(results: list[ScenarioResult]) -> dict[str, int]:
    summary = {"total": len(results), "passed": 0, "failed": 0, "error": 0}
    for result in results:
        summary[result.status] += 1
    return summary


def write_report(report: dict[str, Any], output: Path | None) -> Path:
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = Path("reports") / f"report_{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False, default=str)
        fh.write("\n")
    return output


def print_summary(results: list[ScenarioResult], report: dict[str, Any],
                  output: Path) -> None:
    if results:
        width_g = max(len(r.group) for r in results)
        width_s = max(len(r.scenario) for r in results)
        print()
        for r in results:
            checks = f"{sum(c.passed for c in r.checks)}/{len(r.checks)} checks"
            reason = r.aborted or r.error or ""
            print(f"{r.group:<{width_g}}  {r.scenario:<{width_s}}  "
                  f"{r.status.upper():<6}  {checks}"
                  + (f"  ({reason})" if reason else ""))
    for error in report["group_errors"]:
        print(f"{error['group']}  GROUP ERROR  {error['error']}")
    s = report["summary"]
    print(f"\n{s['total']} run: {s['passed']} passed, {s['failed']} failed, "
          f"{s['error']} error(s)"
          + (" - INTERRUPTED" if report["interrupted"] else ""))
    print(f"Report: {output}")


# --------------------------------------------------------------------- list

def cmd_list(args: argparse.Namespace) -> int:
    if args.what == "scenarios":
        scenarios, errors = discover_scenarios()
        for name, cls in scenarios.items():
            tag = "[maintenance] " if cls.maintenance else ""
            print(f"{name:<30} {tag}{cls.description}")
        for error in errors:
            print(f"LOAD ERROR  {error}", file=sys.stderr)
        return EXIT_FAILED if errors else EXIT_OK
    for row in read_groups(args.data):
        members = "-" if row.members is None else row.members
        print(f"{row.name:<12} {row.ip:<16} members: {members}")
    return EXIT_OK


# ---------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data", type=Path, default=Path("data"),
                        help="data directory containing groups.csv (default: ./data)")
    common.add_argument("-v", "--verbose", action="count", default=0,
                        help="-v: show steps and passed checks; -vv: also HTTP internals")
    common.add_argument("-q", "--quiet", action="store_true",
                        help="only log warnings and errors")

    parser = argparse.ArgumentParser(prog="tatou-test",
                                     description="Test bench for Tatou API instances.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", parents=[common], help="run scenarios on groups")
    run.add_argument("--group", "-g", nargs="+", required=True, metavar="NAME",
                     help="group id(s) from groups.csv, or 'all'")
    run.add_argument("--scenario", "-s", nargs="+", required=True, metavar="NAME",
                     help="scenario name(s), or 'all' (maintenance scenarios "
                          "must be named explicitly)")
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                     help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT:g})")
    run.add_argument("--rmap-identity", default=os.environ.get(ENV_RMAP_IDENTITY),
                     help="identity sent in RMAP message 1, i.e. the name under which "
                          f"keys/server_public.asc is registered on the servers "
                          f"(default: ${ENV_RMAP_IDENTITY})")
    run.add_argument("--key-passphrase", default=os.environ.get(ENV_KEY_PASSPHRASE),
                     help="passphrase of keys/server_private.asc, if protected "
                          f"(default: ${ENV_KEY_PASSPHRASE}; prefer the variable, "
                          "it stays out of the shell history)")
    run.add_argument("--output", "-o", type=Path, default=None,
                     help="JSON report path (default: reports/report_<timestamp>.json)")
    run.set_defaults(handler=cmd_run)

    lst = sub.add_parser("list", parents=[common], help="list scenarios or groups")
    lst.add_argument("what", choices=["scenarios", "groups"])
    lst.set_defaults(handler=cmd_list)
    return parser


def configure_logging(verbose: int, quiet: bool) -> None:
    level = logging.WARNING if quiet else logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                                           datefmt="%H:%M:%S"))
    log.addHandler(handler)
    log.setLevel(level)
    log.propagate = False
    if verbose >= 2:
        logging.basicConfig(level=logging.DEBUG, handlers=[handler])
    else:
        logging.getLogger("urllib3").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    # PGPy (used by rmap) triggers many deprecation warnings from cryptography.
    warnings.filterwarnings("ignore", module=r"pgpy(\.|$)")
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose, args.quiet)
    if getattr(args, "timeout", 1) <= 0:
        parser.error("--timeout must be positive")
    try:
        return args.handler(args)
    except UsageError as exc:
        print(f"tatou-test: error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
