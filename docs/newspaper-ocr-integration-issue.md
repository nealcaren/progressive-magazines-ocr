# Make `newspaper-ocr` a drop-in engine for the production GLM-OCR newspaper pipeline

**Target repo:** https://github.com/nealcaren/newspaper-ocr (v0.5.1)

## Context

We run a GLM-OCR + PP-DocLayout_plus-L newspaper pipeline in production
(`dangerouspress-ocr`, and now a sibling `progressive-magazines-ocr` for the
SOCI 274 magazine collections). `newspaper-ocr` already ports our layout
heuristics (`LayoutProcessor`) and ships a `GlmOcrRecognizer`, so it's *close*
to being able to replace the hand-copied pipeline code in those repos. A few
gaps stop it from being a true drop-in. Reference implementation:
`dangerouspress-ocr/ocr_pipeline.py` and `longleaf/ocr_newspapers.py`
(pipeline version tag `2025-03-07-col-fix`).

## Gaps

### 1. Local-mode GLM-OCR has no per-region wall-clock timeout
`GlmOcrRecognizer._recognize_local` calls `self._model.generate(...)` with no
time guard (`src/newspaper_ocr/recognizers/glm_ocr.py:157`). The `timeout`
constructor arg only configures the API-mode `httpx.Client`; in local/GPU mode
(how we run on Longleaf) an oversized or pathological region can hang the whole
batch indefinitely. Production wraps `generate()` in `signal.alarm(25)` with
retry and returns `"[OCR timeout]"` on expiry. **Request:** enforce `timeout`
in local mode too (SIGALRM or a generation thread/watchdog).

### 2. No per-region OCR status is surfaced
On any exception the recognizer silently sets `region.text = ""`
(`glm_ocr.py:204`). Production records a `status` per region
(`"ok" | "timeout" | "repetition"`) and writes it to the page JSON — it drives
failure reporting and the recovery/re-OCR passes. `Region` currently has only
`text`/`confidence` (`src/newspaper_ocr/models.py`). **Request:** add
`Region.status` (default `"ok"`), set it in the recognizers, and include it in
the JSON formatter.

### 3. Repetition detection/truncation differs from production (different output)
This is an algorithm difference, not just tuning:
- Production `has_repetition` slides windows across the *whole* text at multiple
  offsets and counts `text.count(substr) >= 5` (`min_len=20, min_reps=5`).
- Package `_has_repetition` only checks **prefix-anchored** patterns
  (`text[:length]`), `threshold=3`, `min_len=10` (`glm_ocr.py:165`).
- `truncate_repetition` likewise differs: production finds the most-frequent
  phrase and cuts after its 2nd occurrence; the package only handles a repeated
  prefix.

So the same image can yield different text than production. **Request:** align
the algorithm with `ocr_pipeline.py`, and expose the thresholds as constructor
kwargs (`repetition_min_len`, `repetition_min_reps`) so callers can match the
production tag instead of subclassing.

### 4. No PDF ingestion (and: pick the *largest* embedded image, not the first)
`Pipeline.ocr` takes an image path. Our inputs are multi-page PDFs; we extract
the embedded page image when present and fall back to a 300-dpi render
(`extract_page_image` in `ocr_newspapers.py`). **Request:** a small PDF
front-end (e.g. `Pipeline.ocr_pdf(path)` or a documented helper) yielding
per-page PIL images.

Important refinement we had to add: choose the **largest** embedded image by
pixel area, not `images[0]`. Google-scanned PDFs (common for these titles) place
a tiny "Digitized by Google" strip as the first embedded image, so `images[0]`
yields a ~1000×200 sliver instead of the full page. Selecting max-area fixes it.

### 7. No page-rotation option (sideways broadsheet scans)
Many broadsheet scans (e.g. *Industrial Worker*, *Appeal to Reason*) come in
90°-rotated, and the two papers were even scanned in *opposite* directions.
Layout+OCR on a sideways image is garbage. Production added a `--rotate
{90,180,270}` (clockwise) option applied to each extracted page before layout.
**Request:** a `rotate` parameter on the PDF/image front-end (or auto-detect via
aspect ratio + a quick orientation check).

### 5. No interactive review-site / OpenSeadragon formatter
Formatters are `text` / `hocr` / `json`. Our deliverable is an OpenSeadragon
review website: per-page HTML with clickable overlay boxes and a synced text
pane, a per-issue index, and a top-level `manifest.json`. **Request:** an
`html`/`viewer` formatter (or a documented extension point) so the review site
can be produced from the package instead of bespoke code.

### 6. (confirmation) Layout params look ported 1:1 — please pin the version
`LayoutProcessor` matches production (`gap_thresh = median_w*0.3`, 40%
narrow-column merge, `max_height=600` merge cap, 0.5/0.15 confidence bands).
Worth recording the reference tag `2025-03-07-col-fix` somewhere so future
drift from `ocr_pipeline.py` is detectable.

### 8. (downstream, optional) Post-OCR LLM enrichment layer
Not core to a recognizer, but noting it since it's now part of our "current OCR
steps": `progressive-magazines-ocr/analyze_issue.py` runs an LLM (Gemini/Claude
via OpenAI-compatible API) over the finished page JSON to reconstruct reading
order, segment articles, classify advertisements, link cross-page
continuations, and emit a per-issue table of contents (`toc.json`) — with a
targeted image pass only for shredded headlines. It leans on the `status` field
from #2 and the per-region bbox+text the package already produces. **Not a
request to build this**, but a documented extension point / stable JSON schema
(region ids, bbox, label, text, status) would let this layer sit cleanly on top
of `newspaper-ocr` output.

## Why it matters
With 1–2 and 3 resolved, `progressive-magazines-ocr`/`dangerouspress-ocr` could
`pip install newspaper-ocr` and delete their vendored pipeline copies, ending
the "update all four scripts" maintenance burden noted in the dangerouspress
`CLAUDE.md`. 4 (incl. largest-image), 5, and 7 would let the package produce the
full deliverable end-to-end from raw PDFs, including the awkward Google-scan and
rotated-broadsheet cases we hit across this collection.
