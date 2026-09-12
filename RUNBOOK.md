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
- **Google-scan multi-image pages:** `ocr_newspapers.py` picks the LARGEST embedded image (not image[0]).
- Smoke-test one shard interactively (`srun --pty`) before the array. GPU ≈ 28s/page.

## 4. Publish — to R2

MUST run on the **login node** (compute nodes have no outbound internet).
```bash
conda activate /work/users/n/c/ncaren/envs/progmag-publish
export PYTHONUTF8=1
python build_iiif.py site --out /work/users/n/c/ncaren/deploy_all \
    --prefix https://pages.dangerouspress.org/progressive-magazines
source ~/.r2env      # R2_ENDPOINT_URL / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
export AWS_ACCESS_KEY_ID=$R2_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY=$R2_SECRET_ACCESS_KEY
aws s3 sync /work/users/n/c/ncaren/deploy_all/ \
    s3://african-american-press-archive/progressive-magazines/ \
    --endpoint-url $R2_ENDPOINT_URL --only-show-errors
```
- `build_iiif` is **incremental** — reuses existing tiles (libvips `dzsave --layout iiif`,
  ~68 tiles/page); pass `--force` only to re-tile.
- It splits each issue into `index.html` (Contents landing) + `reader.html` (TIFY viewer,
  deep-linked `reader.html#?tify={"pages":[N]}`), copies a flat `page_01.jpg` cover, and
  rebuilds the per-magazine galleries + top archive + `search-index.json`.
- **`auto_publish.sh <jobid>`** automates this: polls squeue until the OCR job leaves the
  queue, then builds + syncs. Launch detached: `setsid bash auto_publish.sh <jobid> > auto_publish.log 2>&1 &`
  (do NOT guard with `pgrep -f auto_publish.sh` — it self-matches the launcher).
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

## Cost / provider keys
Keys live in the Mac shell env (OPENROUTER_API_KEY etc.) — keep them OFF the shared HPC.
Run `analyze_issue.py`/`batch_analyze.py` from the Mac (pull the small `full_text.json`s;
image passes pull only the needed pages), push `toc.json`s back to Longleaf.

## TODO / follow-ups
- **Serial threading:** link serialized works across issues (Forerunner "Won Over" Ch. I → Ch. II;
  "Humanness" series) so a reader follows a serial through the run. Cross-issue analog of the
  cross-page continuation logic already in `toc.json` (`serial` field is already populated).
- Add the 4 Google-Drive-only titles if wanted (International Socialist Review is the big gap).
