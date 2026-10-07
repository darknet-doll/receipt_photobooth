#!/usr/bin/env python3
"""Render a sheet of decorative characters with the receipt font, as the printer would get them.

Runs on the Pi. Writes /tmp/glyphs.json ({chars:[{c, cp, cat, faint}], cols, cell16, cell24})
and /tmp/glyphs16.png, /tmp/glyphs24.png: 1-bit sprite grids, one cell per character.
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / "photobooth" / "software"))
from PIL import Image, ImageDraw
from booth import receipt

CATS = [
    ("Flowers & sparkles", "✿❀❁❂❃❄❅❆❇❈❉❊❋✽✾✼✻✺✹✸✷✶✵✴✳✲✱✰✯✮✭✬✫✪✩✧✦⁂❖✥✤✣✢✡⚘"),
    ("Stars & suns", "⋆★☆✶✷✸✹✺☀☼☾☽✩✪✫✬✭✮✯⭐⭑⭒✰☄"),
    ("Hearts & suits", "♥♡❤❣❥❦❧♠♣♦♤♧♢❢"),
    ("Faces & hands", "☺☻☹☜☝☞☟✌✍✎✏✐✑✒✓✔✕✖✗✘✂✁✀✃✄"),
    ("Music & sound", "♩♪♫♬♭♮♯☊☏☎☈"),
    ("Sky & weather", "☁☂☃☔☇☈❄❅❆☀☾☽☄⚡⚪⚫"),
    ("Objects", "☕☘☙⚐⚑⚒⚓⚔⚕⚖⚗⚙⚚⚛⚜⚠⚰⚱☠☢☣☤☥☦☧☨☩☪☫☬☭☮☯⚕⌚⌛⌨⏰⌘"),
    ("Dice & chess", "⚀⚁⚂⚃⚄⚅♔♕♖♗♘♙♚♛♜♝♞♟"),
    ("Zodiac", "♈♉♊♋♌♍♎♏♐♑♒♓"),
    ("Kaomoji parts", "◕◡◠◜◝◞◟◔◑◐◒◓ʚɞʘωᴗᵕ٩۶ღ╭╮╯╰‿⁀⌒~∼≈≋"),
    ("Dots & swirls", "˚˙˜·•‣‧‥…∘○◦◌⋯∞§¶※¤«»‹›°ºª"),
    ("Arrows", "←↑→↓↔↕↖↗↘↙↚↛↜↝↞↟↠↡↢↣↤↥↦↧↨↩↪↫↬↭↮↯↰↱↲↳↴↵↶↷↸↹↺↻➔➘➙➚➛➜➝➞➟➠➡➢➣➤➥➦➧➨➩➪➫➬➭➮➯➱➲➳➴➵➶➷➸➹➺➻➼➽➾"),
    ("Lines & borders", "─━┄┅┈┉╌╍═~∼≈≋⁓〰▁▂▃▄▅▆▇█░▒▓▔▕╱╲╳"),
    ("Shapes", "■□▪▫▬▭▮▯▰▱▲△▴▵▶▷▸▹►▻▼▽▾▿◀◁◂◃◄◅◆◇◈◉◊○◌◍◎●◐◑◒◓◔◕◖◗◘◙◚◛◢◣◤◥◦◧◨◩◪◫◬◭◮◯◰◱◲◳◴◵◶◷◸◹◺◻◼◽◾⬛⬜⬟⬠⬡⬢⬣⬤"),
    ("Circled", "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳ⓐⓑⓒⓓⓔⓕⓖⓗⓘⓙⓚⓛⓜⓝⓞⓟⓠⓡⓢⓣⓤⓥⓦⓧⓨⓩⒶⒷⒸⒹⒺⒻⒼⒽⒾⒿⓀⓁⓂⓃⓄⓅⓆⓇⓈⓉⓊⓋⓌⓍⓎⓏ⓪"),
    ("Signs", "©®™℗№℮℃℉℡☑☒✆✇✈✉✊✋⚉⚊⚋⚌⚍⚎⚏"),
]

font16, font24 = receipt._font(16), receipt._font(24)
CELL16, CELL24, COLS = (18, 24), (28, 34), 32


def render(ch, font, cell):
    img = Image.new("L", cell, 255)
    d = ImageDraw.Draw(img)
    l, t, r, b = font.getbbox(ch)
    w, h = r - l, b - t
    d.text(((cell[0] - w) // 2 - l, (cell[1] - h) // 2 - t), ch, font=font, fill=0)
    return img.convert("1", dither=Image.NONE)


chars, seen = [], set()
for cat, s in CATS:
    for ch in s:
        if ch in seen or not receipt.has_glyph(font16, ch):     # only the receipt face itself: crisp, mono width
            continue
        seen.add(ch)
        cell = render(ch, font16, CELL16)
        ink16 = sum(1 for p in cell.getdata() if p == 0)
        ink24 = sum(1 for p in render(ch, font24, CELL24).getdata() if p == 0)
        if ink16 == 0:
            continue
        chars.append({"c": ch, "cp": f"{ord(ch):04X}", "cat": cat, "faint": ink16 < 10 or ink16 * 3 < ink24})

rows = (len(chars) + COLS - 1) // COLS
for font, cell, name in ((font16, CELL16, "glyphs16"), (font24, CELL24, "glyphs24")):
    sheet = Image.new("1", (COLS * cell[0], rows * cell[1]), 1)
    for i, x in enumerate(chars):
        sheet.paste(render(x["c"], font, cell), ((i % COLS) * cell[0], (i // COLS) * cell[1]))
    sheet.save(f"/tmp/{name}.png", optimize=True)
json.dump({"chars": chars, "cols": COLS, "cell16": CELL16, "cell24": CELL24}, open("/tmp/glyphs.json", "w"), ensure_ascii=False)
print(len(chars), "characters,", sum(1 for x in chars if x["faint"]), "faint at 16 dots; categories:",
      {c: sum(1 for x in chars if x["cat"] == c) for c, _ in CATS})
