# GP-58 Tape Bench

A dot-for-dot simulator of the receipt the photobooth prints on its GP-58 thermal head —
384 dots a line, 8 dots a millimetre, 48 mm of ink on 57 mm paper. Design a receipt here and
what you see is what the paper gets.

**Open it:** https://darknet-doll.github.io/receipt_photobooth/

## For whoever is designing

1. Open the page. The **Iterations** list already holds the saved sets from this repo —
   click one to load it.
2. Change whatever you like: shop name, the lines under each photo, rules, totals, type sizes.
   The preview redraws as you type.
3. Put a name in the box (keep the same name to update an existing set) and press
   **Save to GitHub**.
4. GitHub opens in a new tab with the file already filled in. Leave **Commit directly to the
   `main` branch** selected and press **Commit changes**. Done — the set is saved for everyone.

You need a (free) GitHub account and an accepted invite to this repo. If GitHub offers you a
pull request instead of a direct commit, the invite has not been accepted yet — check your
email, accept it, and try again.

That is the whole loop. Each save is a new timestamped file in [`sets/`](sets/); the newest
file for a given set wins, so nothing is ever overwritten and every version stays in history.

### Characters

The left-hand palette is the set of glyphs the Pi can actually print, each one rendered on the
Pi with the real receipt font and thresholded like a print. Anything outside that palette may
come out as a tofu box on paper. A dashed box marked **16** means the glyph goes faint at the
small-text size — use it on a bigger line instead.

## For the booth owner

The page is one file, `index.html`, and it behaves differently depending on where it is opened:

| Opened from | Saving goes to | Extra |
| --- | --- | --- |
| GitHub Pages | this repo, via a commit you confirm | no token in the page |
| the Claude artifact | the artifact's shared database | |
| a local `file://` copy | the browser | **Send to booth** POSTs to Receipt Studio on the Pi |

The source of truth is `software/tools/tape_bench.html` in the Photobooth project; `index.html`
here is a copy. Do not edit it by hand — run `software/tools/deploy_bench_site.sh`, which
re-copies it and pushes.

To put a set on the booth, pull the newest file out of `sets/` and write its `settings` to
`~/photobooth/receipt.json` on the Pi. The booth re-reads that file at the start of every
session, so no restart is needed.

## Note on what is public

This repo is public, so everything committed to `sets/` is world-readable — receipt copy
included. Keep names, addresses and anything personal out of the sets you save here.
