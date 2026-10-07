#!/usr/bin/env python3
"""Receipt Studio: a browser editor for what the photobooth prints.

    python3 tools/receipt_studio.py             # http://<pi>.local:8765
    python3 tools/receipt_studio.py --port 9000

Two layouts are edited here, chosen at the top of the page:

* **Receipt tape** the register-tape designs (``booth.receipt``)
* **Film strip**  the 35 mm film strip, names and date up the rail (``booth.film``)

Every line of copy and every layout number is a form field. The preview on the
right is rendered by the booth's own renderer for that layout, with sample photos
from the latest capture session (or photos you drop onto the page), so what you
see is what the paper gets, dot for dot.

* Save to booth   writes ~/photobooth/receipt.json, which the Photobooth app
                  loads at start-up in place of the built-in copy. The file says
                  which layout it holds, so saving a film strip here means
                  ``photobooth --theme film`` prints this one.
* Print test      sends the current preview to the thermal printer.
* Presets         reload the built-in "receipt" or "birthday" copy.

Standard library HTTP server, no extra packages. Run it on the Pi so the fonts
and the printer are the real ones; it also runs on a Mac for a quick look.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import socket
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont, ImageOps  # noqa: E402

from booth import film, tape  # noqa: E402
from booth.printer import BoothPrinter  # noqa: E402
from booth.receipt import (  # noqa: E402
    CONFIG_PATH, PRESETS, RULE_SLOTS, RULE_STYLES, ReceiptSettings, from_dict, load_settings, make_receipt,
    save_settings, to_dict,
)

CAPTURE_DIR = Path.home() / "photobooth" / "captures"
UPLOAD_DIR = Path.home() / "photobooth" / "studio_photos"
PREVIEW_MAX_WIDTH = 900          # sample photos are cached at this size; plenty for a 360-dot tile

_photo_cache: dict[str, Image.Image] = {}
_lock = threading.Lock()


# -- sample photos ------------------------------------------------------------

def placeholder_photo(i: int) -> Image.Image:
    """A synthetic 4:3 'photo' with tones, edges and a big number, for machines without captures."""
    w, h = 800, 600
    img = Image.new("L", (w, h))
    px = img.load()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            v = int(130 + 90 * math.sin(x / 140 + i) * math.cos(y / 110 - i))
            px[x, y] = px[x + 1, y] = px[x, y + 1] = px[x + 1, y + 1] = v
    d = ImageDraw.Draw(img)
    d.ellipse([250, 110, 550, 410], fill=235, outline=15, width=5)
    d.ellipse([320, 210, 360, 250], fill=15)
    d.ellipse([440, 210, 480, 250], fill=15)
    d.arc([320, 260, 480, 370], 15 + 30 * i, 165 - 30 * i, fill=15, width=8)
    d.rectangle([40, 470, 760, 570], fill=25)
    try:
        font = ImageFont.load_default(size=64)
    except TypeError:            # Pillow < 10.1
        font = ImageFont.load_default()
    d.text((70, 490), f"SAMPLE PHOTO {i + 1}", fill=245, font=font)
    return img


def load_sample(path: Path) -> Image.Image:
    key = str(path)
    with _lock:
        if key in _photo_cache:
            return _photo_cache[key]
    img = Image.open(path)
    img.draft("L", (PREVIEW_MAX_WIDTH, PREVIEW_MAX_WIDTH))      # fast JPEG downscale on decode
    img = ImageOps.exif_transpose(img).convert("L")
    if img.width > PREVIEW_MAX_WIDTH:
        img = img.resize((PREVIEW_MAX_WIDTH, round(img.height * PREVIEW_MAX_WIDTH / img.width)), Image.LANCZOS)
    with _lock:
        _photo_cache[key] = img
    return img


def photo_sets() -> list[dict]:
    """Selectable sample-photo sets: uploads, recent capture sessions, synthetic placeholders."""
    sets: list[dict] = []
    if UPLOAD_DIR.exists():
        ups = sorted(p for p in UPLOAD_DIR.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        if ups:
            sets.append({"id": "uploads", "label": f"your uploads ({len(ups)})", "paths": [str(p) for p in ups]})
    if CAPTURE_DIR.exists():
        sessions = sorted((p for p in CAPTURE_DIR.iterdir() if p.is_dir()), reverse=True)
        for s in sessions:
            shots = sorted(s.glob("photo_*.jpg"))
            if shots:
                sets.append({"id": f"session:{s.name}", "label": f"session {s.name} ({len(shots)} photos)",
                             "paths": [str(p) for p in shots]})
            if len(sets) >= 12:
                break
    sets.append({"id": "placeholder", "label": "synthetic placeholders", "paths": []})
    return sets


def sample_photos(set_id: str, count: int) -> list[Image.Image]:
    count = max(1, min(int(count), 6))
    paths: list[str] = []
    for s in photo_sets():
        if s["id"] == set_id:
            paths = s["paths"]
            break
    photos: list[Image.Image] = []
    for i in range(count):
        if paths:
            photos.append(load_sample(Path(paths[i % len(paths)])))
        else:
            photos.append(placeholder_photo(i))
    return photos


# -- rendering ------------------------------------------------------------------

def render(body: dict) -> tuple[Image.Image, tape.Settings]:
    """Compose whichever layout the page is editing; the layout rides in the request."""
    layout = body.get("layout") or "receipt"
    if layout not in tape.LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}")
    photos = sample_photos(body.get("photo_set", "placeholder"), body.get("photos", 3))
    if layout == "film":
        settings = film.from_dict(body.get("settings") or {})
        return film.make_film_strip(photos, settings, when=datetime.now()), settings
    settings = from_dict(body.get("settings") or {})
    number = int(body.get("number") or 47)
    return make_receipt(photos, settings, when=datetime.now(), number=number), settings


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# -- HTTP -----------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "ReceiptStudio/1.0"

    def log_message(self, fmt, *args):      # quieter log: method, path, status only
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")          # Tape Bench (a local file) posts here
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"))

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_OPTIONS(self) -> None:  # noqa: N802 - CORS preflight for Tape Bench
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/state":
            try:
                found = tape.load()                      # either layout; None when nothing is saved
            except (OSError, ValueError):
                found = None
            saved_layout, saved = found if found else (None, None)
            self._json(200, {
                "saved": tape.to_dict(saved) if saved else None,
                "saved_layout": saved_layout,
                "film_preset": film.to_dict(film.FILM),
                "film_bases": list(film.BASE_STYLES),
                "film_rails": list(film.RAIL_STYLES),
                "film_fills": ["solid", "half", "quarter"],
                "config_path": str(CONFIG_PATH).replace(str(Path.home()), "~"),
                "presets": {k: to_dict(v) for k, v in PRESETS.items()},
                "photo_sets": [{"id": s["id"], "label": s["label"]} for s in photo_sets()],
                "rule_slots": list(RULE_SLOTS.items()),
                "rule_styles": list(RULE_STYLES),
                "printer": BoothPrinter().is_writable(),
                "host": socket.gethostname(),
            })
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path == "/render":
                img, settings = render(self._read_json())
                out = {"png": base64.b64encode(png_bytes(img)).decode("ascii"),
                       "width": img.width, "height": img.height}
                if tape.layout_of(settings) == "film":     # how close the rail type is to not fitting
                    run, room = film.type_extent(settings, img.height)
                    ink, band = film.ink_coverage(img)
                    out["note"] = (f"type runs {run} of {room} dots down the rail  ·  "
                                   f"ink {ink * 100:.0f}% (worst band {band * 100:.0f}%)")
                self._json(200, out)
            elif self.path == "/save":
                body = self._read_json()
                img, settings = render(body)                 # validates the copy before saving
                path = tape.save(settings)
                self._json(200, {"ok": True, "path": str(path).replace(str(Path.home()), "~"),
                                 "height": img.height})
            elif self.path == "/print":
                body = self._read_json()
                img, _ = render(body)
                printer = BoothPrinter()
                if not printer.is_writable():
                    self._json(503, {"error": "printer not available (check power, USB, and the lp group)"})
                    return
                CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
                out = CAPTURE_DIR / f"studio_{datetime.now():%Y%m%d_%H%M%S}.png"
                img.save(out)
                printer.print_image(img)
                self._json(200, {"ok": True, "height": img.height, "saved": out.name})
            elif self.path == "/upload":
                name = Path(self.headers.get("X-Filename", "photo.jpg")).name
                n = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(n)
                Image.open(io.BytesIO(data)).verify()        # reject non-images
                UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
                target = UPLOAD_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{name}"
                target.write_bytes(data)
                self._json(200, {"ok": True, "name": target.name})
            elif self.path == "/clear-uploads":
                if UPLOAD_DIR.exists():
                    for p in UPLOAD_DIR.iterdir():
                        p.unlink()
                        _photo_cache.pop(str(p), None)
                self._json(200, {"ok": True})
            else:
                self._send(404, b"not found", "text/plain")
        except (KeyError, ValueError, IndexError, TypeError, OSError) as exc:
            self._json(400, {"error": f"{type(exc).__name__}: {exc}"})


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Receipt Studio</title>
<style>
  :root { --ink:#241b28; --pink:#f34fae; --green:#288c5a; --red:#aa3232; --paper:#f4f2ec; --line:#d9d4c8; }
  * { box-sizing: border-box; }
  body { margin:0; font: 14px/1.4 -apple-system, system-ui, "Segoe UI", sans-serif; background:#e9e6de; color:var(--ink); }
  header { display:flex; align-items:center; gap:12px; padding:10px 16px; background:var(--ink); color:#fff; position:sticky; top:0; z-index:2; flex-wrap:wrap; }
  header h1 { font-size:16px; margin:0 12px 0 0; letter-spacing:2px; font-weight:700; }
  header #host { font-size:12px; color:#aaa; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:34%; }
  header .spacer { flex:1; }
  button { font: inherit; padding:8px 14px; border-radius:8px; border:1px solid transparent; cursor:pointer; background:#fff; color:var(--ink); }
  button.primary { background:var(--green); color:#fff; }
  button.danger { background:var(--red); color:#fff; }
  button.pink { background:var(--pink); color:#fff; }
  button.ghost { background:transparent; color:#fff; border-color:#666; }
  button:disabled { opacity:.5; cursor:default; }
  main { display:grid; grid-template-columns: minmax(340px, 520px) 1fr; gap:16px; padding:16px; align-items:start; }
  @media (max-width: 860px) { main { grid-template-columns: 1fr; } }
  form { background:#fff; border-radius:12px; padding:6px 18px 18px; box-shadow:0 1px 3px rgba(0,0,0,.08); }
  h2 { font-size:12px; letter-spacing:2px; text-transform:uppercase; color:#777; margin:22px 0 8px; border-bottom:1px solid var(--line); padding-bottom:4px; }
  label { display:block; margin:10px 0 0; font-size:12px; color:#555; }
  label small { color:#999; }
  input[type=text], input[type=number], textarea, select { width:100%; font: 14px/1.35 ui-monospace, "SF Mono", Menlo, Consolas, monospace; padding:6px 8px; border:1px solid var(--line); border-radius:6px; background:var(--paper); color:var(--ink); }
  textarea { resize: vertical; min-height: 58px; }
  input[type=checkbox] { transform: scale(1.2); margin-right:8px; vertical-align: middle; }
  .row { display:grid; grid-template-columns: 1fr 1fr; gap:10px; }
  .row3 { display:grid; grid-template-columns: 1fr 1fr 1fr; gap:10px; }
  .hint { font-size:12px; color:#777; background:var(--paper); border-radius:8px; padding:8px 10px; margin-top:12px; }
  code { font-family: ui-monospace, Menlo, monospace; background:#eee; padding:0 4px; border-radius:4px; }
  aside { position: sticky; top:64px; display:flex; flex-direction:column; align-items:center; gap:10px; }
  .controls { display:flex; gap:10px; align-items:center; flex-wrap:wrap; justify-content:center; font-size:13px; }
  .controls select, .controls input { width:auto; }
  .tape { background:#fff; padding:12px; box-shadow: 0 8px 30px rgba(0,0,0,.18); border-radius:4px; max-height: calc(100vh - 150px); overflow:auto; }
  .tape img { display:block; image-rendering: pixelated; }
  .meta { font-size:13px; color:#555; }
  #status { min-height:18px; font-size:13px; }
  #status.err { color:var(--red); font-weight:600; }
  #status.ok { color:var(--green); font-weight:600; }
  .drop { border:2px dashed var(--line); border-radius:8px; padding:8px; font-size:12px; color:#777; text-align:center; }
  .drop.over { border-color: var(--pink); color: var(--pink); }
</style>
</head>
<body>
<header>
  <h1>RECEIPT STUDIO</h1>
  <span id="host"></span>
  <span class="spacer"></span>
  <label style="margin-right:10px">layout
    <select id="layout"><option value="receipt">Receipt tape</option><option value="film">Film strip</option></select></label>
  <button class="ghost" id="preset-receipt" title="Load the built-in receipt copy">Preset: receipt</button>
  <button class="ghost" id="preset-birthday" title="Load the built-in birthday copy">Preset: birthday</button>
  <button class="ghost" id="preset-film" title="Load the built-in film strip">Preset: film</button>
  <button class="ghost" id="reload-saved" title="Reload what the booth is using now">Reload saved</button>
  <button class="pink" id="download">Download PNG</button>
  <button class="danger" id="print">Print test</button>
  <button class="primary" id="save">Save to booth</button>
</header>
<main>
  <form id="form" autocomplete="off" onsubmit="return false"></form>
  <aside>
    <div class="controls">
      <label>photos <input type="number" id="photos" min="1" max="6" value="3" style="width:56px"></label>
      <label>sample set <select id="photo_set"></select></label>
      <label>order # <input type="number" id="number" min="0" max="9999" value="47" style="width:72px"></label>
      <label><input type="checkbox" id="zoom"> 2x</label>
    </div>
    <div class="drop" id="drop">drop photos here to preview with your own pictures (or click) <input type="file" id="file" accept="image/*" multiple hidden>
      <div><button type="button" id="clear-uploads" style="margin-top:6px;padding:3px 8px;font-size:12px">clear uploads</button></div></div>
    <div id="status"></div>
    <div class="tape"><img id="preview" alt="tape preview" width="384"></div>
    <div class="meta" id="meta"></div>
  </aside>
</main>
<script>
const FIELDS = [
  {group: "Header"},
  {k: "shop", t: "text", label: "Shop name"},
  {k: "address", t: "lines", label: "Address lines", small: "one per line; leave empty for none"},
  {row: [{k: "size_shop", t: "num", label: "Header size (dots)"}, {k: "shop_tracking", t: "num", label: "Header letter spacing"}]},
  {group: "Transaction"},
  {k: "meta", t: "pairs", label: "Meta rows", small: "left | right, one per line"},
  {group: "Items"},
  {k: "table_header", t: "pair", label: "Table header", small: "left | right; blank for none"},
  {k: "photos_as_items", t: "bool", label: "Each photo is a line item (off: photos stacked above the items)"},
  {k: "photo_items", t: "lines", label: "Photo item names", small: "one per photo, cycles if there are more photos"},
  {row: [{k: "photo_item_qty", t: "text", label: "Photo item qty"}, {k: "photo_item_price", t: "text", label: "Photo item price"}]},
  {k: "items", t: "pairs", label: "Extra text items", small: "left | right, one per line"},
  {group: "Totals"},
  {k: "totals", t: "pairs", label: "Total rows", small: "left | right, one per line"},
  {k: "total", t: "pair", label: "Big total line", small: "left | right; blank for none"},
  {group: "Footer"},
  {k: "footer", t: "lines", label: "Footer lines", small: "first line is bold; an empty line makes a gap"},
  {row: [{k: "barcode", t: "text", label: "Barcode payload (Code 39)", small: "blank for none"}, {k: "barcode_caption", t: "text", label: "Barcode caption"}]},
  {k: "fine_print", t: "text", label: "Fine print"},
  {group: "Rules"},
  {k: "rules", t: "multi", label: "Where a line appears", options: "rule_slots"},
  {row: [{k: "rule_style", t: "select", label: "Line style", options: "rule_styles"}, {k: "rule_text", t: "text", label: "Text pattern", small: "for the text style, e.g. * or = or - "}]},
  {row: [{k: "rule_thickness", t: "num", label: "Thickness (dots)"}, {k: "rule_pad", t: "num", label: "Space above & below (dots)"}]},
  {row: [{k: "rule_dash", t: "num", label: "Dash length (dots)"}, {k: "rule_gap", t: "num", label: "Gap (dots)", small: "between dashes/dots, or the two lines of a double rule"}]},
  {group: "Layout"},
  {row: [{k: "torn_edges", t: "bool", label: "Torn zigzag edges"}, {k: "dither", t: "bool", label: "Dither photos"}]},
  {row3: [{k: "margin", t: "num", label: "Side margin (dots)"}, {k: "photo_border", t: "num", label: "Photo frame (dots)"}, {k: "photo_aspect", t: "aspect", label: "Photo aspect w:h"}]},
  {row: [{k: "size_small", t: "num", label: "Small text size"}, {k: "size_body", t: "num", label: "Body text size"}]},
  {row: [{k: "size_total", t: "num", label: "Total size"}, {k: "size_fine", t: "num", label: "Fine print size"}]},
];
const FILM_FIELDS = [
  {group: "The rail"},
  {k: "names", t: "text", label: "Names up the rail", small: "set on its side; the page reports if it runs past the end"},
  {k: "date_format", t: "text", label: "Date line", small: "blank for none"},
  {row: [{k: "ornament", t: "boolstr", on: "heart", label: "Heart between them"}, {k: "serif", t: "bool", label: "Serif type"}]},
  {row: [{k: "name_size", t: "num", label: "Name size (dots)"}, {k: "date_size", t: "num", label: "Date size (dots)"}]},
  {row3: [{k: "name_tracking", t: "num", label: "Letter spacing"}, {k: "text_gap", t: "num", label: "Gap between runs"}, {k: "text_clear", t: "num", label: "Clear of the holes"}]},
  {row: [{k: "text_rail_sprockets", t: "bool", label: "Perforate the text rail too"}, {k: "text_rail_marks", t: "bool", label: "Edge bars around the type"}]},
  {group: "Perforations"},
  {row: [{k: "left_style", t: "select", label: "Left rail", options: "film_rails"}, {k: "right_style", t: "select", label: "Right rail", options: "film_rails"}]},
  {row3: [{k: "hole_w", t: "num", label: "Hole width"}, {k: "hole_h", t: "num", label: "Hole height"}, {k: "hole_pitch", t: "num", label: "Pitch", small: "hole height + gap"}]},
  {row: [{k: "hole_radius", t: "num", label: "Hole corner radius"}, {k: "hole_stroke", t: "num", label: "Draw as a ring (dots)", small: "0 fills the hole"}]},
  {group: "The film base"},
  {row: [{k: "base", t: "select", label: "Base", options: "film_bases", small: "full: black tape · rails: black rails only · outline: drawn on paper"}, {k: "rail_fill", t: "select", label: "Ink of the base", options: "film_fills"}]},
  {group: "Frames"},
  {row3: [{k: "frame_aspect", t: "aspect", label: "Frame w:h"}, {k: "frame_inset", t: "num", label: "Inset from the rail"}, {k: "frame_gap", t: "num", label: "Gap between frames"}]},
  {row3: [{k: "frame_radius", t: "num", label: "Corner radius"}, {k: "frame_keyline", t: "num", label: "Keyline (dots)"}, {k: "end_pad", t: "num", label: "End pad"}]},
  {row3: [{k: "rail_left", t: "num", label: "Left rail width"}, {k: "rail_right", t: "num", label: "Right rail width"}, {k: "ornament_size", t: "num", label: "Heart size"}]},
  {row: [{k: "dither", t: "bool", label: "Dither photos"}, {k: "caption", t: "text", label: "Caption under the film", small: "blank for none"}]},
];
const FIELD_SETS = {receipt: FIELDS, film: FILM_FIELDS};
const NULLABLE_BY_LAYOUT = {
  receipt: new Set(["table_header", "total", "barcode", "barcode_caption", "fine_print"]),
  film: new Set(["caption"]),
};
let layout = "receipt";
const fieldSet = () => FIELD_SETS[layout];
const NULLABLE = {has: (k) => NULLABLE_BY_LAYOUT[layout].has(k)};

const form = document.getElementById("form");
const $ = (id) => document.getElementById(id);
let state = null, timer = null, lastPng = null, busy = false, dirty = false;

function fieldHtml(f) {
  const small = f.small ? ` <small>${f.small}</small>` : "";
  if (f.t === "bool" || f.t === "boolstr") return `<label><input type="checkbox" id="f_${f.k}"> ${f.label}</label>`;
  if (f.t === "select") return `<label>${f.label}${small}<select id="f_${f.k}">${state[f.options].map(v => `<option value="${v}">${v}</option>`).join("")}</select></label>`;
  if (f.t === "multi") return `<div id="f_${f.k}"><label>${f.label}${small}</label>${state[f.options].map(([v, l]) => `<label><input type="checkbox" value="${v}"> <b>${v.replace("_", " ")}</b> <small>${l}</small></label>`).join("")}</div>`;
  if (f.t === "lines" || f.t === "pairs") return `<label>${f.label}${small}<textarea id="f_${f.k}" rows="3"></textarea></label>`;
  if (f.t === "num") return `<label>${f.label}${small}<input type="number" id="f_${f.k}" min="0" max="400"></label>`;
  return `<label>${f.label}${small}<input type="text" id="f_${f.k}"></label>`;
}
function build() {
  let html = "";
  for (const f of fieldSet()) {
    if (f.group) html += `<h2>${f.group}</h2>`;
    else if (f.row) html += `<div class="row">${f.row.map(fieldHtml).join("")}</div>`;
    else if (f.row3) html += `<div class="row3">${f.row3.map(fieldHtml).join("")}</div>`;
    else html += fieldHtml(f);
  }
  html += layout === "film"
    ? `<div class="hint">The date line takes <code>{date:%m.%d.%Y}</code> and the rest of strftime.
       Sizes are printer dots: 8 dots = 1 mm, 384 dots across, so both rails plus the window must come to 384.
       Type that will not fit its rail is refused rather than clipped &mdash; the message says by how much.</div>`
    : `<div class="hint">Placeholders work in any text: <code>{date:%m/%d/%Y}</code>, <code>{date:%-I:%M %p}</code>,
       <code>{date:%B %-d, %Y}</code>, <code>{txn:04d}</code> (order number), <code>{n}</code> (photo count).
       Sizes are printer dots: 8 dots = 1 mm, 384 dots across.</div>`;
  form.innerHTML = html;
  form.addEventListener("input", () => { dirty = true; schedule(); });
}
function allFields() { return fieldSet().flatMap(f => f.row || f.row3 || (f.k ? [f] : [])); }
function pairText(p) { return p ? `${p[0]} | ${p[1]}` : ""; }
function parsePair(s) { const i = s.indexOf("|"); return i < 0 ? [s.trim(), ""] : [s.slice(0, i).trimEnd(), s.slice(i + 1).trimStart()]; }

function fill(s) {
  for (const f of allFields()) {
    const el = $("f_" + f.k), v = s[f.k];
    if (f.t === "bool" || f.t === "boolstr") el.checked = !!v;
    else if (f.t === "multi") { for (const cb of el.querySelectorAll("input")) cb.checked = (v || []).includes(cb.value); }
    else if (f.t === "select") el.value = v;
    else if (f.t === "lines") el.value = (v || []).join("\n");
    else if (f.t === "pairs") el.value = (v || []).map(pairText).join("\n");
    else if (f.t === "pair") el.value = pairText(v);
    else if (f.t === "aspect") el.value = v ? `${v[0]}:${v[1]}` : "4:3";
    else el.value = v == null ? "" : v;
  }
}
function collect() {
  const s = {};
  for (const f of allFields()) {
    const el = $("f_" + f.k), v = el.value;
    if (f.t === "boolstr") s[f.k] = el.checked ? f.on : null;
    else if (f.t === "bool") s[f.k] = el.checked;
    else if (f.t === "multi") s[f.k] = [...el.querySelectorAll("input:checked")].map(cb => cb.value);
    else if (f.t === "select") s[f.k] = el.value;
    else if (f.t === "num") s[f.k] = Number(v || 0);
    else if (f.t === "lines") s[f.k] = v === "" ? [] : v.split("\n");
    else if (f.t === "pairs") s[f.k] = v.split("\n").filter(l => l.trim() !== "").map(parsePair);
    else if (f.t === "pair") s[f.k] = v.trim() === "" ? null : parsePair(v);
    else if (f.t === "aspect") { const m = v.match(/(\d+)\D+(\d+)/); s[f.k] = m ? [Number(m[1]), Number(m[2])] : [4, 3]; }
    else s[f.k] = (v === "" && NULLABLE.has(f.k)) ? null : v;
  }
  return s;
}
function body() {
  return {layout, settings: collect(), photos: Number($("photos").value || 3),
          photo_set: $("photo_set").value, number: Number($("number").value || 0)};
}
function presetFor(name) { return name === "film" ? state.film_preset : state.presets[name]; }
function switchTo(next, settings) {
  layout = next;
  $("layout").value = next;
  for (const id of ["preset-receipt", "preset-birthday"]) $(id).hidden = next === "film";
  $("preset-film").hidden = next !== "film";
  $("number").closest("label").hidden = next === "film";     // a film strip has no order number
  build();
  fill(settings || presetFor(next === "film" ? "film" : "receipt"));
  if (next === "film") $("photos").value = 4;
  renderPreview();
}
function status(text, cls) { const el = $("status"); el.textContent = text; el.className = cls || ""; }

async function post(path, payload) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
  const j = await r.json().catch(() => ({error: r.statusText}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function schedule() { clearTimeout(timer); timer = setTimeout(renderPreview, 350); }
async function renderPreview() {
  if (busy) { schedule(); return; }
  busy = true;
  try {
    const j = await post("/render", body());
    lastPng = j.png;
    $("preview").src = "data:image/png;base64," + j.png;
    $("meta").textContent = `${j.width} x ${j.height} dots  =  ${(j.height / 8).toFixed(0)} mm of paper`
      + (j.note ? `  ·  ${j.note}` : "") + (dirty ? "  (unsaved changes)" : "");
    status("");
  } catch (e) { status(e.message, "err"); }
  busy = false;
}
function applyZoom() { $("preview").width = $("zoom").checked ? 768 : 384; }

async function loadState() {
  const r = await fetch("/state"); state = await r.json();
  $("host").textContent = `${state.host}  ·  saves to ${state.config_path}` + (state.printer ? "" : "  ·  printer not available");
  $("print").disabled = !state.printer;
  const sel = $("photo_set"), cur = sel.value;
  sel.innerHTML = state.photo_sets.map(s => `<option value="${s.id}">${s.label}</option>`).join("");
  if ([...sel.options].some(o => o.value === cur)) sel.value = cur;
  return state;
}

$("preset-receipt").onclick = () => { fill(state.presets.receipt); $("photos").value = 3; dirty = true; renderPreview(); };
$("preset-birthday").onclick = () => { fill(state.presets.birthday); $("photos").value = 1; dirty = true; renderPreview(); };
$("preset-film").onclick = () => { fill(state.film_preset); $("photos").value = 4; dirty = true; renderPreview(); };
$("layout").addEventListener("change", (e) => {
  if (dirty && !confirm("Switch layout and lose the unsaved changes on this one?")) { e.target.value = layout; return; }
  dirty = false;
  const next = e.target.value;
  switchTo(next, state.saved_layout === next ? state.saved : null);
});
$("reload-saved").onclick = async () => {
  await loadState();
  switchTo(state.saved_layout || "receipt", state.saved);
  dirty = false;
};
$("save").onclick = async () => {
  try { const j = await post("/save", body()); dirty = false; status(`saved to ${j.path}; the booth uses it on its next start`, "ok"); renderPreview(); }
  catch (e) { status("not saved: " + e.message, "err"); }
};
$("print").onclick = async () => {
  if (!confirm(`Print this ${layout === "film" ? "film strip" : "receipt"} on the thermal printer now?`)) return;
  status("printing...");
  try { const j = await post("/print", body()); status(`sent to the printer (${(j.height / 8).toFixed(0)} mm, saved as ${j.saved})`, "ok"); }
  catch (e) { status("print failed: " + e.message, "err"); }
};
$("download").onclick = () => {
  if (!lastPng) return;
  const a = document.createElement("a"); a.href = "data:image/png;base64," + lastPng;
  a.download = layout === "film" ? "film-strip.png" : "receipt.png"; a.click();
};
for (const id of ["photos", "photo_set", "number"]) $(id).addEventListener("input", schedule);
$("zoom").addEventListener("change", applyZoom);

const drop = $("drop"), file = $("file");
drop.addEventListener("click", (e) => { if (e.target === drop || e.target.tagName === "DIV" && !e.target.querySelector("button")) file.click(); });
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload(e.dataTransfer.files); });
file.addEventListener("change", () => upload(file.files));
async function upload(files) {
  for (const f of files) {
    try { await fetch("/upload", {method: "POST", headers: {"X-Filename": f.name}, body: f}); }
    catch (e) { status("upload failed: " + e.message, "err"); return; }
  }
  await loadState(); $("photo_set").value = "uploads"; renderPreview();
}
$("clear-uploads").onclick = async (e) => { e.stopPropagation(); await post("/clear-uploads", {}); await loadState(); renderPreview(); };

loadState().then(() => {
  const sess = state.photo_sets.find(s => s.id.startsWith("session:") || s.id === "uploads");
  if (sess) $("photo_set").value = sess.id;
  applyZoom();
  switchTo(state.saved_layout || "receipt", state.saved);     // opens on whatever the booth is set to
  if (!state.saved) status("no saved copy yet: showing the built-in receipt preset");
});
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--bind", default="0.0.0.0", help="interface to listen on (default: all)")
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    host = socket.gethostname()
    print(f"Receipt Studio: http://{host}.local:{args.port}  (or http://localhost:{args.port} on this machine)")
    print(f"saved copy goes to {str(CONFIG_PATH).replace(str(Path.home()), '~')}; Ctrl-C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
