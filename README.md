# GP-58 Tape Bench

A dot-for-dot simulator of the receipt the photobooth prints on its GP-58 thermal head —
384 dots a line, 8 dots a millimetre, 48 mm of ink on 57 mm paper. Design a receipt here and
what you see is what the paper gets.

**Open it:** https://darknet-doll.github.io/receipt_photobooth/

## Designing a receipt

No account, no sign-in, nothing to install. Just open the link.

1. The **Iterations** list on the left already holds the saved receipts — click one to load it.
2. Change whatever you like: the shop name, the line under each photo, the rules, the totals,
   the type sizes. The preview redraws as you type.
3. Press **Send my receipt**. That's it — it goes straight to darknetdoll, who puts it on the
   booth.

You can keep tweaking and send again as many times as you like. Your work also stays in your
own browser, so you can close the tab and pick it up later.

### Picking characters

The palette down the left is every glyph the booth can actually print — each one was rendered on
the printer itself and thresholded like a real print, so what you see is what the paper gets.
Click a text field, then click a character to drop it in. Characters from outside that palette
may come out as an empty box on paper. A dashed box marked **16** means the glyph goes faint at
the small-text size — use it on a bigger line instead.

## For the booth owner

The page is one file, `index.html`, and it behaves differently depending on where it is opened:

| Opened from | Saving goes to |
| --- | --- |
| GitHub Pages | a Google Form, collected in its linked Sheet |
| GitHub Pages with `?github=1` | a commit in this repo, under `sets/` |
| the Claude artifact | the artifact's shared database |
| a local `file://` copy | the browser, plus **Send to booth** → Receipt Studio on the Pi |

`config.json` names the Google Form the Send button posts to. Both values are public on purpose:
a form response endpoint only accepts submissions and returns nothing, so unlike a GitHub or
Airtable token there is no secret in the page to steal. Clear the values and the Send button
simply stays hidden. Responses collect in the form's linked Sheet.

The source of truth is `software/tools/tape_bench.html` in the Photobooth project; `index.html`
here is a copy. Don't edit it by hand — run `software/tools/deploy_bench_site.sh`, which
re-copies it and pushes.

To put a received receipt on the booth, write its `settings` object to `~/photobooth/receipt.json`
on the Pi. The booth re-reads that file at the start of every session, so no restart is needed.

## Note on what is public

This repo is public, so anything committed to `sets/` is world-readable. Keep names, addresses
and anything personal out of the receipts saved here.
