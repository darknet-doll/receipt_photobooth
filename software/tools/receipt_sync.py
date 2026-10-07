#!/usr/bin/env python3
"""Put receipts sent in from the Tape Bench on the booth.

    ssh pi@raspberrypi.local '~/photobooth/software/tools/receipt_sync.py -' < set.json
    python3 tools/receipt_sync.py sets.json --dry-run     # render and report, write nothing
    python3 tools/receipt_sync.py --list                  # what the booth is offering now

Writes ``~/photobooth/receipt_choices.json``, the file ``booth.receipt.load_choices`` re-reads at
the start of every session. The CONFIRM screen shows the booth's own copy beside the FIRST row in
that file, so incoming sets go on top unless ``--sort-by-saved`` says otherwise. The booth's default
copy (``receipt.json``) belongs to Receipt Studio; this script never touches it.

Input is whatever the bench already produces, so a set can be pasted out of the Google Form's
response sheet, exported from the artifact with "Export all", or copied from a receipt.json:

    {"tape_bench_sets": [{"name": ..., "settings": {...}}, ...]}   an export, or one form response
    [{"name": ..., "settings": {...}}, ...]                       a bare list
    {"name": ..., "settings": {...}}                              a single set
    {"shop": ..., "footer": [...], ...}                           a raw receipt.json settings object

Every set is composed with the booth's own renderer before anything is written, so copy that would
crash ``make_receipt`` can never reach a guest, and the file it replaces is backed up first.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from booth.receipt import (  # noqa: E402
    CHOICES_PATH, RECEIPT, ReceiptSettings, _font, font_for, from_dict, load_choices, make_receipt,
    to_dict,
)

KEEP = 8                      # rows kept in the file; the booth only ever shows the first


def parse_sets(data) -> list[dict]:
    """The sets in ``data``, in the order given, whichever shape the bench wrote it in."""
    if isinstance(data, dict):
        if isinstance(data.get("tape_bench_sets"), list):
            rows = data["tape_bench_sets"]
        elif isinstance(data.get("receipts"), list):          # our own file, fed back in
            rows = data["receipts"]
        elif isinstance(data.get("settings"), dict):
            rows = [data]
        elif "shop" in data:                                  # a bare receipt.json
            rows = [{"name": str(data.get("shop") or "Imported"), "settings": data}]
        else:
            rows = []
    elif isinstance(data, list):
        rows = data
    else:
        rows = []

    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("settings"), dict):
            continue
        name = str(row.get("name") or row["settings"].get("shop") or "Saved").strip() or "Saved"
        out.append({
            "id": row.get("id"),
            "name": name,
            "saved": row.get("saved") or datetime.now().astimezone().isoformat(),
            "photos": int(row.get("photos") or 3),
            "settings": row["settings"],
        })
    return out


def strings(s: ReceiptSettings) -> list[str]:
    """Every string that gets drawn on the tape, for the glyph check."""
    out = [s.shop, *s.address, *s.footer, *s.photo_items, s.rule_text]
    for pair in (*s.meta, *s.items, *s.totals):
        out.extend(pair)
    for maybe in (s.table_header, s.total):
        if maybe:
            out.extend(maybe)
    out.extend(x for x in (s.barcode, s.barcode_caption, s.fine_print) if x)
    return out


def unprintable(s: ReceiptSettings) -> list[str]:
    """Characters no font on THIS machine can draw; the renderer drops them silently."""
    body = _font(s.size_body)
    return sorted({ch for line in strings(s) for ch in line
                   if not ch.isspace() and font_for(ch, body) is None})


def check(row: dict) -> tuple[ReceiptSettings, int, list[str]]:
    """Validate one set: settings, the height it renders at, and the glyphs that will be dropped."""
    settings = from_dict(row["settings"])
    photos = [Image.new("L", (800, 600), 128) for _ in range(max(1, row["photos"]))]
    return settings, make_receipt(photos, settings).height, unprintable(settings)


def merge(incoming: list[dict], existing: list[dict], sort_by_saved: bool) -> list[dict]:
    """Incoming sets first (that is what the booth shows), then the rows already on the booth.

    A set replaces an earlier one with the same id OR the same name, so re-sending a receipt
    updates it instead of filling the file with near-duplicates. Both tests are needed: rows
    written before the bench carried ids match by name alone.
    """
    ids = {row["id"] for row in incoming if row.get("id")}
    names = {row["name"].casefold() for row in incoming}
    merged = incoming + [row for row in existing
                         if not ((row.get("id") and row["id"] in ids) or row["name"].casefold() in names)]
    if sort_by_saved:
        merged.sort(key=lambda row: str(row.get("saved") or ""), reverse=True)
    return merged[:KEEP]


def read_existing(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return parse_sets(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        print(f"! {path} is unreadable ({type(exc).__name__}), starting a fresh file", file=sys.stderr)
        return []


def write(path: Path, rows: list[dict]) -> Path | None:
    """Back the file up, then replace it in one step.

    The booth re-reads this file mid-session; os.replace is atomic, so it never sees half of it.
    """
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        backup.write_bytes(path.read_bytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    # the id rides along so a re-sent set updates its own row instead of adding a near-duplicate
    payload = {"receipts": [{k: row[k] for k in ("id", "name", "saved", "photos", "settings") if row.get(k)}
                            for row in rows]}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return backup


def show(path: Path) -> None:
    rows = load_choices(path, limit=KEEP)
    if not rows:
        print(f"{path} holds nothing the booth can use; it will show its own copy alone.")
        return
    ref = to_dict(RECEIPT)
    print(f"{path}:")
    for i, (label, settings) in enumerate(rows):
        changed = [k for k, v in to_dict(settings).items() if ref[k] != v]
        where = "on the CONFIRM screen" if i == 0 else "in the file"
        print(f"  {i + 1}. {label:<24} {where:<21} differs from the preset in {', '.join(changed) or 'nothing'}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Put receipts sent in from the Tape Bench on the booth.")
    ap.add_argument("source", nargs="?", help="JSON file with the set(s); - for stdin")
    ap.add_argument("--path", type=Path, default=CHOICES_PATH, help=f"file to write (default {CHOICES_PATH})")
    ap.add_argument("--list", action="store_true", help="show what the booth is offering and exit")
    ap.add_argument("--dry-run", action="store_true", help="render and report, write nothing")
    ap.add_argument("--replace", action="store_true", help="drop the rows already in the file")
    ap.add_argument("--sort-by-saved", action="store_true", help="order strictly newest first")
    args = ap.parse_args()

    if args.list or not args.source:
        show(args.path)
        return 0

    raw = sys.stdin.read() if args.source == "-" else Path(args.source).read_text(encoding="utf-8")
    try:
        incoming = parse_sets(json.loads(raw))
    except ValueError as exc:
        print(f"that is not JSON: {exc}", file=sys.stderr)
        return 1
    if not incoming:
        print("no receipts in that input (expected tape_bench_sets, a list, a set, or a receipt.json)",
              file=sys.stderr)
        return 1

    kept: list[dict] = []
    for row in incoming:
        try:
            settings, height, missing = check(row)
        except Exception as exc:                     # noqa: BLE001 - a bad set must not stop the rest
            print(f"  skipped {row['name']!r}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        row["settings"] = to_dict(settings)          # normalised: unknown keys dropped, gaps filled
        kept.append(row)
        mm = round(height / 8)
        note = f"drops {' '.join(missing)}" if missing else "every character prints"
        print(f"  {row['name']:<24} {height:>5} dots ({mm:>3} mm) · {note}")
    if not kept:
        print("nothing valid to write", file=sys.stderr)
        return 1

    rows = merge(kept, [] if args.replace else read_existing(args.path), args.sort_by_saved)
    if args.dry_run:
        print("\n--dry-run: nothing written. It would become:")
        for i, row in enumerate(rows):
            print(f"  {i + 1}. {row['name']}{'   <- on the CONFIRM screen' if i == 0 else ''}")
        return 0

    backup = write(args.path, rows)
    if backup:
        print(f"\nbacked up the old file as {backup.name}")
    show(args.path)
    print("\nThe booth re-reads this at the start of the next session; no restart needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
