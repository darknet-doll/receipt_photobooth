"""What the booth prints: which layout, and the copy saved for it.

Four layouts print on the same 384-dot tape:

* ``receipt``   the register-tape designs in :mod:`booth.receipt`
* ``film``      the 35 mm film strip in :mod:`booth.film`
* ``stamp``     the postage stamps in :mod:`booth.stamp`
* ``grimoire``  the spellbook cover in :mod:`booth.grimoire`

They all save to the one file ``~/photobooth/receipt.json`` — the booth reads it
fresh every session — so the file has to say which it holds. It does, in a
``layout`` key written by :func:`save`. A file without one is a receipt, which is
every file written before the film strip existed.

The layouts share no fields, so a film strip parsed as a receipt would come back
as an unchanged default and the booth would print the wrong thing without a word.
:func:`booth.receipt.load_settings` refuses a file it does not own for that
reason, and everything that reads the saved copy should come through here.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PIL import Image

from . import film, grimoire, receipt, stamp
from .receipt import CONFIG_PATH

LAYOUTS = ("receipt", "film", "stamp", "grimoire")

Settings = (receipt.ReceiptSettings | film.FilmSettings | stamp.StampSettings
            | grimoire.GrimoireSettings)

MODULES = {"receipt": receipt, "film": film, "stamp": stamp, "grimoire": grimoire}


def layout_of(settings: Settings) -> str:
    if isinstance(settings, film.FilmSettings):
        return "film"
    if isinstance(settings, grimoire.GrimoireSettings):
        return "grimoire"
    return "stamp" if isinstance(settings, stamp.StampSettings) else "receipt"


def to_dict(settings: Settings) -> dict:
    """The settings as JSON, tagged with the layout that has to read them back."""
    layout = layout_of(settings)
    return {"layout": layout, **MODULES[layout].to_dict(settings)}


def from_dict(data: dict) -> tuple[str, Settings]:
    layout = data.get("layout") or "receipt"
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}; expected one of {', '.join(LAYOUTS)}")
    return layout, MODULES[layout].from_dict(data)


def load(path: Path = CONFIG_PATH) -> tuple[str, Settings] | None:
    """The copy saved by the studio as ``(layout, settings)``, or None if nothing is saved."""
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return from_dict(json.load(fh))


def save(settings: Settings, path: Path = CONFIG_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(to_dict(settings), fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def render(settings: Settings, photos: list, when: datetime | None = None,
           number: int | None = None) -> Image.Image:
    """Compose ``photos`` with whichever layout ``settings`` belongs to."""
    layout = layout_of(settings)
    if layout == "film":
        return film.make_film_strip(photos, settings, when=when)
    if layout == "stamp":
        return stamp.make_stamp_sheet(photos, settings, when=when)
    if layout == "grimoire":
        return grimoire.make_grimoire(photos, settings, when=when)
    return receipt.make_receipt(photos, settings, when=when, number=number)
