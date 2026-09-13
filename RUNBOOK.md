# Runbook — adding a publication to the archive

End-to-end process for digitizing another periodical and getting it live with a
table of contents. Written so you can pick this up cold next year.

The live archive: **https://pages.dangerouspress.org/progressive-magazines/**
(Cloudflare R2, prefix `progressive-magazines/`). Also linked from the Save the
Masses "Newsstand." Text corpus mirrors to HF dataset `NealCaren/progressive-magazines-ocr`.

## The five stages

```
1. FETCH    source PDFs  ->  sources/fetch_*.py   (run on Longleaf login node)
2. STAGE    pdfs/<magazine>/<magazine>_<YYYY-MM[-DD]>.pdf   (naming matters, see below)
3. OCR      run_ocr_array.sl (new) / run_fix.sl (rotations)  -> site/<magazine>/<issue>/
4. PUBLISH  build_iiif.py -> deploy_all -> aws s3 sync -> R2   (auto_publish.sh watches a job)
5. TOC      analyze_issue.py / batch_analyze.py -> toc.json  -> re-publish (landing pages)
```

Everything runs on **UNC Longleaf** (`ssh longleaf`, works from the Mac while a
VPN+Duo session is open). Two conda envs, both on `/work` (built by
`longleaf/setup_env.sh`):
- `envs/progressive-magazines-ocr` — torch/paddle/GLM-OCR (the OCR job). Fragile; never pip into it.
- `envs/progmag-publish` — libvips + iiif-prezi3 + boto3 + awscli + datasets (tiling/publish/HF).

---

## 1. Fetch — where the PDFs come from

Each `sources/fetch_*.py` downloads **directly on the Longleaf login node** (IA and
marxists.org are NOT bot-blocked there, unlike HF). Idempotent (`%PDF` header skip),
nohup-able. They write into `pdfs/<magazine>/`.

| Source | Script | Notes |
|---|---|---|
| **Internet Archive** "Sim" microfilm serials | `fetch_suffrage.py` (Woman's Journal), `fetch_poetry.py` | per-issue PDFs at `archive.org/download/<id>/<id>.pdf`; ids like `sim_<title>_YYYY-MM-DD_vol_n` |
| **marxists.org** | `fetch_solidarity.py`, `fetch_suffrage.py` (Progressive Woman) | scrape the journal index page for `*.pdf` links; parse date from filename |
| **Brown Digital Repository / Modernist Journals Project** | `fetch_crisis_mjp.py` | best scans. Issues are IIIF page sets; pull full-res page JPEGs from the IIIF manifest and assemble a PDF (needs Pillow). Journal page: `modjourn.org/journal/<slug>/`; item API: `repository.library.brown.edu/api/items/bdr:NNNNNN/`; date key `mods_dateIssued_ssim` |
| **IA bound annual volumes** | `fetch_motherearth.py` | one IA item (`mother-earth`) holds 138 per-issue PDFs named `... (YYYY-MM) ...`; filter by date |
| **HathiTrust annual volumes** | `longleaf/split_forerunner.py` | split a yearly PDF into monthly issues (cover-cadence detection) |
| Google Drive (`23-5th-Avenue-Publications`) | manual copy | the original Masses/Crisis/Mother Earth 1912 set; note Google-scan PDFs put a "Digitized by Google" strip as image[0] — handled by the largest-image fix in `ocr_newspapers.py` |

**zsh gotcha:** unquoted `$var` does NOT word-split in zsh — use `while read` loops or `${=var}`.

⚠️ **IA "Text PDF" is a bad derivative for some serials — use the JP2 zip instead.**
IA's downloadable `<id>.pdf` was washed-out for Poetry & Woman's Journal (two
same-size grayscale layers per page — extracting one layer gave a faded image AND
degraded OCR) and for Mother Earth (faded across all layers). IA's **"Single Page
Processed JP2 ZIP"** is the crisp derivative the online viewer shows. Fix:
- Detect the layered-PDF signature first: `python detect_layered_pdfs.py pdfs` (flags
  titles whose pages have ≥2 same-size embedded images). Also just *look* at a page.
- Rebuild those titles from JP2: `python sources/fetch_ia_jp2.py <title> [years...]`
  (downloads each issue's `_jp2.zip`, decodes JP2→JPEG via PIL, assembles a clean
  one-image-per-page PDF, same filenames). `RESUME=1` skips already-fetched issues.
  Handles sim_ items (Poetry, Woman's Journal) and the Mother Earth bound-volume item.
- Then re-OCR those titles (§3) and **`--force` re-tile** them at publish (§4) — the
  new images differ, so stale tiles must be regenerated.
`extract_page_image` now auto-renders layered pages (composites both layers), but the
JP2 zip is the pristine source and the right fix when a title looks faded.

## 2. Stage — naming convention (drives sorting + labels)

Name issue PDFs `<magazine>_<YYYY-MM-DD>.pdf` (weeklies) or `<magazine>_<YYYY-MM>.pdf`
(monthlies). `build_index.issue_date()` parses these into an ISO sort key + a natural
label ("May 1911" / "January 4, 1913"). It also handles legacy patterns (embedded
`sep-05-1912`, volume/number). Volume/number-only names (e.g. old Woman Rebel `v1n07`)
need a hand-map in `build_index._WOMAN_REBEL` — add a dict for any new such title.

Add the display title to `TITLES` in `build_iiif.py` (else it title-cases the slug).

## 3. OCR — on Longleaf GPU

```bash
cd /work/users/n/c/ncaren/progressive-magazines-ocr
sbatch longleaf/run_ocr_array.sl          # array over ALL pdfs/*/; skip-existing
```
- **`export PYTHONUTF8=1` is mandatory** (Longleaf's ASCII locale crashes on em-dashes). It's in the .sl files.
- **Sideways scans:** re-OCR that title with `--rotate {90|180|270}` (clockwise). See `run_fix.sl` for the per-magazine rotation pattern (Industrial Worker = 90 CW, Appeal to Reason = 270 CCW — they were scanned opposite ways). Check orientation first: page dims where width > height = landscape = rotated.
- **Google-scan multi-image pages:** `ocr_newspapers.py` picks the LARGEST embedded image (not image[0]) — Google PDFs embed a tiny "Digitized by Google" strip as image[0].
- ⚠️ **Any time you rotate or re-extract page images, you must `--force` re-tile that title at publish (§4)** — the incremental build otherwise keeps the old tiles and the site shows the wrong image.
- Re-OCR just some titles: `sbatch longleaf/run_ocr_ia.sl` (edit the title list). **Clear
  their `site/<title>` dirs first** (the array skips issues that already have output).
- **Blank OCR-refusal regions before publishing.** GLM sometimes returns a refusal
  ("The image is too blurry…", "cannot recognize…") for an individual hard region.
  Left in, it pollutes search results and the text pane. After OCR, empty any region
  whose short text matches those phrases (set `text=""`, `status="illegible"`) in both
  `page_*.json` and `full_text.json`. On the 2026 re-do this was ~53 regions across 47
  issues (0.02% of content) — negligible after the JP2 fix, but worth blanking.
- Smoke-test one shard interactively (`srun --pty`) before the array. GPU ≈ 28s/page.
  Sanity-check text quality afterward (grep full_text for refusal phrases; confirm
  real content, not a whole-issue refusal).

## 4. Publish — to R2

MUST run on the **login node** (compute nodes have no outbound internet).

⚠️ **`conda activate` SILENTLY FAILS in a non-interactive ssh** (`ssh longleaf '... && cmd &'`
→ nothing runs, no log, no error). Call the env's python DIRECTLY and run detached via a
launcher script. This is the reliable pattern (see `run_build_iiif.sh` / `run_sync.sh`):
```bash
# on the login node — build into deploy_all (no R2 touch yet)
cat > ~/run_build_iiif.sh <<'EOF'
#!/bin/bash
set -euo pipefail
ENV=/work/users/n/c/ncaren/envs/progmag-publish
export PATH="$ENV/bin:$PATH"   # so `vips` resolves
export PYTHONUTF8=1
cd /work/users/n/c/ncaren/progressive-magazines-ocr
python -u build_iiif.py site --out /work/users/n/c/ncaren/deploy_all \
    --prefix https://pages.dangerouspress.org/progressive-magazines
echo "END $(date)"
EOF
nohup bash ~/run_build_iiif.sh > ~/build_iiif.log 2>&1 &   # poll build_iiif.log for "END"

# then sync deploy_all -> R2 (also via a nohup'd launcher that sources ~/.r2env):
source ~/.r2env      # R2_ENDPOINT_URL / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
export AWS_ACCESS_KEY_ID=$R2_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY=$R2_SECRET_ACCESS_KEY
export PATH="/work/users/n/c/ncaren/envs/progmag-publish/bin:$PATH"
aws s3 sync /work/users/n/c/ncaren/deploy_all/ \
    s3://african-american-press-archive/progressive-magazines/ \
    --endpoint-url $R2_ENDPOINT_URL --only-show-errors
```
- ⚠️ **Incremental build reuses tiles by `info.json` existence — so if you CHANGED a page
  image (rotation, largest-image re-extract, new source), the old tiles are kept and the
  site shows the wrong/old image.** You MUST `--force` re-tile those titles. `build_iiif.py`
  accepts a single title or issue path, so scope the force:
  ```bash
  python -u build_iiif.py site/progressive-woman --out $OUT --prefix $PREFIX --force
  ```
  Detect stale tiles first (compares each page image's dims to its tiled `info.json` dims):
  `python detect_stale_tiles.py` (in repo). This session caught 4 titles this way
  (progressive-woman=Google-strip 1034×204, appeal-to-reason/industrial-worker=rotation,
  the-crisis=new MJP source). After force-retiling the affected titles, run a normal (non-force)
  `build_iiif.py site …` to regenerate all HTML + `search-index.json` reusing the now-correct tiles.
- **Full `aws s3 sync` is SLOW (~67 min)** — almost all of it is listing/diffing the ~550K tile
  objects, not uploading. Speed it up by scope:
  - HTML-only change (TOC/landing/viewer): `aws s3 sync … --exclude "iiif/*"` (seconds).
  - Re-tiled a few titles: `aws s3 cp deploy_all/iiif/ s3://…/iiif/ --recursive --exclude "*"
    --include "progressive-woman_*" --include "the-crisis_*" …` (cp overwrites unconditionally,
    skips the remote listing) — tile dir names are FLAT `iiif/<issue>_page_NN/`.
- Each issue → `index.html` (Contents landing) + `reader.html` (TIFY viewer) + flat `page_01.jpg`
  cover; plus per-magazine galleries, top archive, `search-index.json`.
- **TIFY deep links** go in the QUERY string, not the hash: `reader.html?tify={"pages":[N]}`
  (URL-encoded) and `reader.html` must init Tify with `urlQueryKey:'tify'`. TIFY reads
  `location.search` only when `urlQueryKey` is set (defaults to null); a `#?tify=` hash is
  silently ignored and every link opens page 1. `{"pages":[N]}` selects canvas N (1-based).
- **Bare directory URLs 404.** The `pages.dangerouspress.org` R2 custom domain serves objects by
  exact key with NO index-document rewrite, so `…/progressive-magazines/` 404s — the entry URL is
  `…/progressive-magazines/index.html`. To make bare paths work, add a Cloudflare Transform Rule
  (Rewrite URL: when URI path ends with `/`, rewrite to `{path}index.html`) — one-time, domain-wide.
- **`auto_publish.sh <jobid>`** automates build+sync after an OCR job: polls squeue until the job
  leaves the queue, then builds + syncs. Launch detached: `setsid bash auto_publish.sh <jobid> >
  auto_publish.log 2>&1 &` (do NOT guard with `pgrep -f auto_publish.sh` — it self-matches the launcher).
  NOTE: it uses `conda activate` internally — if it no-ops silently, switch it to the direct-env-python
  pattern above.
- Cloudflare caches `index.html`/`search-index.json` at the edge; a fresh publish serves
  `cf-cache-status: DYNAMIC`, but negative (404) probes you make before objects land can
  get cached — don't probe URLs before they exist.
- Push text corpus to HF: `python upload_to_hf.py site --repo NealCaren/progressive-magazines-ocr`.

## 5. TOC enrichment — reading order, contents, authors

`analyze_issue.py` runs an LLM over the OCR we already have (`full_text.json`), writes a
separate `toc.json` (original OCR untouched), and `build_iiif` renders it into the
Contents landing page. Backend auto-detects: `ANTHROPIC_API_KEY` → Anthropic, else
`OPENROUTER_API_KEY` → OpenRouter (**default model `google/gemini-3.8-flash`** — strong OCR,
~4x cheaper than Claude Sonnet, matches it on structure). `--model` to override.

```bash
# single issue (with image passes: contents-block recovery + shredded-headline crops)
uv run analyze_issue.py site/the-crisis/crisis-1911-01 --single-author "..."  # author hint for one-author mags
# newspapers: no printed TOC, dense -> compact output, article index
uv run analyze_issue.py site/solidarity/<issue> --kind newspaper --no-image
```

**Batch (half price, async, text-only)** for large sets — the newspapers:
```bash
uv run batch_analyze.py site/solidarity/*/ site/womans-journal/*/ --kind newspaper
uv run batch_analyze.py --fetch --state batch_state.json     # resume polling without resubmitting
```
OpenRouter Batch API = `POST /api/beta/batches` with inline `{endpoint,model,requests:[{custom_id,body}]}`,
model suffix `:batch`, poll `GET /api/beta/batches/:id`, results inline `{custom_id,response:{body},error}`.

**Gotchas learned:**
- Claude via OpenRouter runs extended reasoning by default and eats the whole token budget →
  send `reasoning:{enabled:false}` for Claude only (Gemini *requires* reasoning; rejects the flag).
- Force `response_format:{type:"json_object"}` for non-Claude models (Gemini emitted invalid JSON without it).
- Don't cap `max_tokens` — big TOCs (Woman's Journal ≈ 60 entries) truncate otherwise.
- Newspapers omit `reading_order`/`relabel` (huge, low-value) — keeps output parseable.
- The workflow **enriches beyond the printed TOC**: expands department sub-sections and pulls
  authors from end-of-piece signatures (Crisis Jan 1911: printed 12 entries → 40, added Du Bois etc.).

**Cost (Gemini 3.8 flash):** ~$0.03 small mag · ~$0.07 large/dense · **whole corpus ≈ $15–16** (newspapers batched at half). One-time per title unless OCR changes.

## Site features the builders produce (2026)
Reused as-is for any new publication — all live and student-tested:
- **TIFY reader** deep-links via the QUERY string: `reader.html?tify={"pages":[N]}` +
  `urlQueryKey:'tify'` in the init (TIFY reads `location.search` only; a `#?tify=` hash
  is ignored). reader.html also has a hash→query shim for legacy links, a breadcrumb
  (Archive / Journal / Issue) + a Contents button.
- **Full-text search** (`build_search.py`): result links deep-link into the reader (NOT
  the defunct `page_NN.html`); `"quoted phrases"` do exact-phrase matching; results
  show clean date labels + printed folios.
- **Printed folios for citation:** the TOC, reader page labels, and search results show
  the printed page (`scan page + toc.json printed_offset`); deep-links still jump by
  scan page. Per-issue magazines have offset 0; bound volumes (e.g. Freewoman) get the
  real folio (scan p.6 → p.306). `analyze_issue` derives `printed_offset` from folios.
- **LLM reading order in the reader:** `build_iiif` reorders each page's text
  annotations by `toc.json` reading_order (magazines; newspapers omit it and keep OCR
  order). Annotation ids keep the original region index so deep links stay stable.
- **Cover thumbnails:** galleries/homepage load a ~500px `cover.jpg` (build_iiif writes
  it next to `page_01.jpg`), not the 1-3 MB full page scan — heavy covers were failing
  to paint. Regenerate for an existing deploy by downscaling each `deploy_all/*/page_01.jpg`.
- **Homepage** has a search box + a student-facing subtitle (no OCR jargon); galleries
  dropped the dev-facing pipeline/version badges. Site name: **"Voices of Dissent"**.

## Cost / provider keys
Keys live in the Mac shell env (OPENROUTER_API_KEY etc.) — keep them OFF the shared HPC.
Run `analyze_issue.py`/`batch_analyze.py` from the Mac (pull the small `full_text.json`s;
image passes pull only the needed pages), push `toc.json`s back to Longleaf.

## Future: move to its own domain (voicesofdissent.org)

Goal: serve this at `voicesofdissent.org` on Cloudflare, working like dangerouspress —
which serves via a **Cloudflare Worker** (fetches objects from the R2 bucket, handles
routing), NOT a plain R2 custom-domain binding. Reuse/adapt the DP Worker. Because IIIF
manifests, `search-index.json`, and the HTML bake in **absolute** URLs, this is NOT just
a DNS/Worker change — it needs a rebuild with the new prefix:
1. Register `voicesofdissent.org`, add to Cloudflare; point a route/Worker at it.
2. **Worker → R2:** adapt DP's Worker to serve this content (an R2 binding to the bucket).
   The Worker maps request paths to bucket keys and appends `index.html` for directory
   requests (so bare URLs like `/` and `/masses/` work — the current setup lacks this and
   404s on bare paths). Decide the public layout: cleanest is the archive at the **root**
   of the new domain (`voicesofdissent.org/<magazine>/<issue>/...`).
3. **Rebuild everything** with `--prefix https://voicesofdissent.org` so IIIF `@id`s,
   cover/reader/deep-link URLs, and search records point at the new host. The baked-in
   prefix must match whatever the Worker serves — the Worker can remap the *key path* in
   the bucket, but the absolute host in the baked URLs must be the new domain.
4. `aws s3 sync` the rebuilt `deploy_all/` to the bucket/prefix the Worker reads.
5. Optionally 301 the old `pages.dangerouspress.org/progressive-magazines/*` to the new host.
Note: the build already uses "Voices of Dissent" as the site name, so only the prefix/host
and the Worker setup change. (Confirm the exact DP Worker config — this describes the model,
not the specific code.)

## TODO / follow-ups
- **Own domain:** voicesofdissent.org on Cloudflare (see "Future" section above).
- **Serial threading:** link serialized works across issues (Forerunner "Won Over" Ch. I → Ch. II;
  "Humanness" series) so a reader follows a serial through the run. Cross-issue analog of the
  cross-page continuation logic already in `toc.json` (`serial` field is already populated).
- Add the 4 Google-Drive-only titles if wanted (International Socialist Review is the big gap).
