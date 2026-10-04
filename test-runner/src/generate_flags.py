"""Generate the flag_3.pdf document of each group from groups.csv.

Separate from the test bench CLI: this only reads groups.csv and writes PDFs,
and never contacts any server. For every row that has a non-empty Flag_3
value, it writes a one-page PDF titled "Flag 3" containing that value to:

    <data>/Groups/<group>/flag_3.pdf

which is where the Mr_Important scenario reads it.

    python src/generate_flags.py [--data DIR] [--group NAME ...]
                                 [--force] [--list]

Without --force, an existing flag_3.pdf is left untouched. --list only reports
what would be done. Installed, it is also available as `tatou-flags`.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

GROUPS_CSV = "groups.csv"
FLAG_COLUMN = "Flag_3"
TITLE = "Flag 3"
FLAG_FILENAME = "flag_3.pdf"
SHA1_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
EXIT_OK, EXIT_PROBLEM, EXIT_USAGE = 0, 1, 2


def flag_path(data_root: Path, group: str) -> Path:
    return data_root / "Groups" / group / FLAG_FILENAME


def _column(fields: list[str], wanted: str) -> str | None:
    """The actual header matching `wanted`, compared case-insensitively."""
    for field in fields:
        if field.lower() == wanted.lower():
            return field
    return None


def read_flags(csv_path: Path) -> list[tuple[str, str]]:
    """Return (group, flag) for every row with a non-empty flag value.

    The Group and Flag_3 columns are matched case-insensitively, so flag_3,
    Flag_3 and FLAG_3 all work.
    """
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fields = [f.strip() for f in reader.fieldnames or []]
        reader.fieldnames = fields
        group_col = _column(fields, "Group")
        flag_col = _column(fields, FLAG_COLUMN)
        if group_col is None:
            raise ValueError(f"{csv_path} has no 'Group' column")
        if flag_col is None:
            raise ValueError(f"{csv_path} has no '{FLAG_COLUMN}' column "
                             f"(found: {', '.join(fields)})")
        rows = []
        for line, row in enumerate(reader, start=2):
            group = (row.get(group_col) or "").strip()
            flag = (row.get(flag_col) or "").strip()
            if not group:
                continue
            if not flag:
                print(f"{csv_path}:{line}: {group} has an empty {flag_col}, skipped",
                      file=sys.stderr)
                continue
            if not SHA1_PATTERN.match(flag):
                print(f"{csv_path}:{line}: {group} flag {flag!r} is not a 40-char sha1 "
                      f"(using it anyway)", file=sys.stderr)
            rows.append((group, flag))
        return rows


def write_flag_pdf(path: Path, flag: str) -> None:
    """Write a one-page PDF titled 'Flag 3' containing the flag value."""
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    value_style = ParagraphStyle("Flag", parent=styles["Normal"], fontName="Courier",
                                 fontSize=12, leading=18, spaceBefore=12, wordWrap="CJK")
    doc = SimpleDocTemplate(str(path), pagesize=A4, title=TITLE,
                            topMargin=40 * mm, leftMargin=25 * mm, rightMargin=25 * mm)
    # escape() keeps a flag with &, < or > from breaking reportlab's markup.
    doc.build([Paragraph(TITLE, styles["Title"]), Spacer(1, 12 * mm),
               Paragraph(escape(flag), value_style)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tatou-flags", description="Generate each group's flag_3.pdf from groups.csv.")
    parser.add_argument("--data", type=Path, default=Path("data"),
                        help="data directory containing groups.csv (default: ./data)")
    parser.add_argument("--group", "-g", nargs="+", metavar="NAME",
                        help="only these groups (default: all in groups.csv)")
    parser.add_argument("--force", "-f", action="store_true",
                        help="overwrite an existing flag_3.pdf")
    parser.add_argument("--list", "-l", action="store_true",
                        help="only show what would be generated")
    args = parser.parse_args(argv)

    csv_path = args.data / GROUPS_CSV
    if not csv_path.is_file():
        print(f"tatou-flags: error: {csv_path} not found", file=sys.stderr)
        return EXIT_USAGE
    try:
        rows = read_flags(csv_path)
    except ValueError as exc:
        print(f"tatou-flags: error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.group is not None:
        wanted = set(args.group)
        known = {group for group, _ in rows}
        missing = wanted - known
        if missing:
            print(f"tatou-flags: error: not in {GROUPS_CSV} (or no {FLAG_COLUMN}): "
                  f"{', '.join(sorted(missing))}", file=sys.stderr)
            return EXIT_USAGE
        rows = [(group, flag) for group, flag in rows if group in wanted]

    written = skipped = failed = 0
    for group, flag in rows:
        path = flag_path(args.data, group)
        exists = path.exists()
        if args.list:
            print(f"{'overwrite' if exists and args.force else 'skip (exists)' if exists else 'create':<14} "
                  f"{group:<12} {path}")
            continue
        if exists and not args.force:
            print(f"skip (exists)  {group:<12} {path}")
            skipped += 1
            continue
        try:
            write_flag_pdf(path, flag)
        except Exception as exc:
            print(f"FAILED         {group:<12} {path}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            failed += 1
            continue
        print(f"{'overwrote' if exists else 'created':<14} {group:<12} {path}")
        written += 1

    if not args.list:
        print(f"\n{written} written, {skipped} skipped, {failed} failed "
              f"({len(rows)} group(s) with a {FLAG_COLUMN})")
    return EXIT_PROBLEM if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
