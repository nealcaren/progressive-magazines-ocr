# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pymupdf",
#     "paddlepaddle",
#     "paddlex[ocr]",
#     "pillow",
#     "httpx",
#     "numpy",
# ]
# ///
"""
Progressive-magazines OCR pipeline (local PDFs -> review website).

Two-stage pipeline, identical to the dangerouspress-ocr project:
  PaddleX PP-DocLayout_plus-L  (layout detection)
  GLM-OCR                      (text recognition)

Unlike the dangerouspress pipeline this one reads PDFs from a local folder and
writes the review site to a local folder — no Hugging Face up/download. It is
meant for smaller collections (e.g. the "Role Readings" magazines).

Two OCR backends, picked by --backend (default: auto):
  mlx          — local Mac: calls a GLM-OCR MLX server over HTTP (localhost:8080)
  transformers — Longleaf/GPU: loads zai-org/GLM-OCR in-process via transformers
  auto         — mlx on macOS, transformers elsewhere

Usage (local Mac, via uv — no manual env needed):
    # In one terminal, start the OCR server (GLM-OCR is a vision model -> mlx_vlm):
    #   uv run --with mlx-vlm python -m mlx_vlm.server --model mlx-community/GLM-OCR-bf16 --port 8080
    uv run ocr_newspapers.py --input-dir "pdfs/woman-rebel" --output-dir site/woman-rebel

Usage (Longleaf GPU, in the conda env from longleaf/setup_env.sh):
    python ocr_newspapers.py --input-dir pdfs/woman-rebel --output-dir site/woman-rebel

Each PDF becomes one "issue" (a subdirectory of --output-dir named after the
PDF stem). Re-running skips issues that already have an index.html, so a job
that dies partway through resumes where it left off.
"""

import io, os, sys, json, base64, html, time, signal, argparse, shutil, platform
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
from PIL import Image
import fitz

os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
from paddlex import create_model

# ─── Config ───
GLM_OCR_MODEL = "zai-org/GLM-OCR"                   # transformers backend
GLM_MLX_MODEL = "mlx-community/GLM-OCR-bf16"         # mlx backend
GLM_MLX_URL = "http://localhost:8080/v1/chat/completions"
LAYOUT_MODEL = "PP-DocLayout_plus-L"
OCR_TIMEOUT = 25  # seconds per OCR request
OCR_LABELS = {"text", "paragraph_title", "doc_title", "figure_title"}
PIPELINE_VERSION = "2025-03-07-col-fix"  # matches dangerouspress-ocr layout logic

# Chosen in main(); controls which glm_ocr implementation runs.
BACKEND = "transformers"

# ─── Timeout helper ───
class OCRTimeoutError(Exception):
    pass

def _timeout_handler(signum, frame):
    raise OCRTimeoutError("OCR generation timed out")

# ─── GLM-OCR via transformers ───
_glm_model = None
_glm_processor = None

def _load_glm_ocr():
    global _glm_model, _glm_processor
    if _glm_model is not None:
        return
    import torch
    from transformers import AutoProcessor, AutoModelForImageTextToText
    print("Loading GLM-OCR model...", flush=True)
    t0 = time.time()
    _glm_processor = AutoProcessor.from_pretrained(GLM_OCR_MODEL)
    _glm_model = AutoModelForImageTextToText.from_pretrained(
        GLM_OCR_MODEL, torch_dtype="auto", device_map="auto")
    print(f"GLM-OCR loaded in {time.time()-t0:.1f}s", flush=True)

# ─── Column detection ───
def _find_columns(boxes):
    widths = boxes[:, 2] - boxes[:, 0]
    median_w = float(np.median(widths))
    narrow_mask = widths <= median_w * 1.3
    if np.sum(narrow_mask) < 3:
        return [], median_w
    narrow_mids = ((boxes[narrow_mask, 0] + boxes[narrow_mask, 2]) / 2.0)
    sorted_mids = np.sort(narrow_mids)
    gap_thresh = median_w * 0.3
    diffs = sorted_mids[1:] - sorted_mids[:-1]
    split_points = sorted_mids[:-1][diffs > gap_thresh] + diffs[diffs > gap_thresh] / 2
    labels = np.zeros(len(narrow_mids), dtype=int)
    for sp in split_points:
        labels[narrow_mids > sp] += 1
    col_centers = []
    for c in range(int(labels.max()) + 1):
        members = narrow_mids[labels == c]
        if len(members) > 0:
            col_centers.append((float(np.mean(members)),
                                float(np.min(boxes[narrow_mask][labels == c, 0])),
                                float(np.max(boxes[narrow_mask][labels == c, 2]))))
    col_centers.sort(key=lambda x: x[0])
    # Merge columns narrower than 40% of median width into nearest neighbor
    min_col_w = median_w * 0.4
    filtered = []
    for center, cl, cr in col_centers:
        if cr - cl >= min_col_w:
            filtered.append((center, cl, cr))
        elif filtered:
            pc, pcl, pcr = filtered[-1]
            filtered[-1] = (pc, pcl, max(pcr, cr))
    col_centers = filtered
    return col_centers, median_w

# ─── Reading order ───
def newspaper_reading_order(bboxes):
    if not bboxes:
        return []
    boxes = np.asarray(bboxes, dtype=int)
    n = len(boxes)
    if n <= 1:
        return list(range(n))
    col_centers, median_w = _find_columns(boxes)
    if not col_centers:
        return boxes[:, 1].argsort().tolist()
    num_cols = len(col_centers)
    def overlapping_cols(x1, x2):
        cols = []
        for c, (center, cl, cr) in enumerate(col_centers):
            overlap = min(x2, cr) - max(x1, cl)
            col_w = cr - cl
            if col_w > 0 and overlap > col_w * 0.2:
                cols.append(c)
        return cols if cols else [min(range(num_cols), key=lambda c: abs(col_centers[c][0] - (x1+x2)/2))]
    col_buckets = [[] for _ in range(num_cols)]
    multi_col = []
    for i in range(n):
        x1, y1, x2, y2 = boxes[i]
        cols = overlapping_cols(x1, x2)
        if len(cols) == 1:
            col_buckets[cols[0]].append((y1, i))
        else:
            multi_col.append((min(cols), max(cols), y1, i))
    for bucket in col_buckets:
        bucket.sort()
    outputted = set()
    result = []
    for first_c, last_c, y1, idx in sorted(multi_col, key=lambda x: x[2]):
        if last_c - first_c + 1 >= num_cols - 1:
            result.append(idx); outputted.add(idx)
    for c in range(num_cols):
        for _, idx in sorted([(y1, idx) for fc, lc, y1, idx in multi_col if fc == c and idx not in outputted]):
            result.append(idx); outputted.add(idx)
        for y1, idx in col_buckets[c]:
            result.append(idx)
    for fc, lc, y1, idx in sorted(multi_col, key=lambda x: x[2]):
        if idx not in outputted:
            result.append(idx)
    return result

# ─── Gap filling ───
def fill_column_gaps(blocks, img_w, img_h, min_gap_height=80):
    if len(blocks) < 3:
        return blocks
    boxes = np.asarray([b["bbox"] for b in blocks], dtype=int)
    col_centers, median_w = _find_columns(boxes)
    if not col_centers or len(col_centers) < 2:
        return blocks
    new_blocks = list(blocks)
    for center, cl, cr in col_centers:
        col_w = cr - cl
        col_boxes = []
        for b in blocks:
            x1, y1, x2, y2 = b["bbox"]
            if (x2 - x1) <= median_w * 1.3:
                overlap = min(x2, cr) - max(x1, cl)
                if col_w > 0 and overlap > col_w * 0.3:
                    col_boxes.append((y1, y2))
        if not col_boxes:
            continue
        col_boxes.sort()
        col_top = min(y1 for y1, _ in col_boxes)
        col_bot = max(y2 for _, y2 in col_boxes)
        col_detected = sum(y2 - y1 for y1, y2 in col_boxes)
        col_span = col_bot - col_top
        if col_span > 0 and col_detected / col_span > 0.85:
            continue
        prev_bot = col_top
        for y1, y2 in col_boxes:
            if y1 - prev_bot >= min_gap_height:
                new_blocks.append({"label": "text", "bbox": [int(cl), int(prev_bot), int(cr), int(y1)]})
            prev_bot = max(prev_bot, y2)
        content_bot = min(img_h - 20, col_bot + (col_bot - col_top) * 0.15)
        if content_bot - prev_bot >= min_gap_height:
            new_blocks.append({"label": "text", "bbox": [int(cl), int(prev_bot), int(cr), int(content_bot)]})
    added = len(new_blocks) - len(blocks)
    if added > 0:
        print(f" +{added} gaps", end="", flush=True)
    return new_blocks

# ─── Rescue low-confidence detections that fill gaps ───
def rescue_low_confidence(accepted, boxes_raw, low_thresh=0.15, max_overlap=0.3):
    if not boxes_raw: return accepted
    def intersection_area(a, b):
        x1 = max(a[0], b[0]); y1 = max(a[1], b[1])
        x2 = min(a[2], b[2]); y2 = min(a[3], b[3])
        return max(0, x2 - x1) * max(0, y2 - y1)
    def box_area(b):
        return (b[2] - b[0]) * (b[3] - b[1])
    candidates = []
    for b in boxes_raw:
        score = b["score"]
        if score < low_thresh or score > 0.5: continue
        bbox = [int(c) for c in b["coordinate"]]
        if box_area(bbox) < 500: continue
        candidates.append({"label": b["label"] if b["label"] in OCR_LABELS else "text",
                          "bbox": bbox, "score": score})
    rescued = []
    for cand in candidates:
        cand_area = box_area(cand["bbox"])
        if cand_area == 0: continue
        total_overlap = sum(intersection_area(cand["bbox"], acc["bbox"]) for acc in accepted)
        if total_overlap / cand_area < max_overlap:
            rescued.append(cand)
    if rescued: print(f" +{len(rescued)} rescued", end="", flush=True)
    return accepted + rescued

# ─── Deduplicate overlapping boxes ───
def deduplicate_boxes(blocks, containment_thresh=0.7, duplicate_thresh=0.8):
    if len(blocks) < 2:
        return blocks
    def box_area(b):
        return (b[2] - b[0]) * (b[3] - b[1])
    def intersection_area(a, b):
        x1 = max(a[0], b[0]); y1 = max(a[1], b[1])
        x2 = min(a[2], b[2]); y2 = min(a[3], b[3])
        return max(0, x2 - x1) * max(0, y2 - y1)
    title_labels = {"doc_title", "paragraph_title"}
    remove = set()
    for i in range(len(blocks)):
        if i in remove: continue
        for j in range(i + 1, len(blocks)):
            if j in remove: continue
            bi, bj = blocks[i]["bbox"], blocks[j]["bbox"]
            ai, aj = box_area(bi), box_area(bj)
            inter = intersection_area(bi, bj)
            if inter == 0: continue
            smaller_area = min(ai, aj)
            containment = inter / smaller_area if smaller_area > 0 else 0
            li, lj = blocks[i]["label"], blocks[j]["label"]
            if containment > duplicate_thresh:
                if li in title_labels and lj not in title_labels:
                    remove.add(j); continue
                if lj in title_labels and li not in title_labels:
                    remove.add(i); break
            if li == lj and containment > duplicate_thresh:
                si, sj = blocks[i].get("score", 0), blocks[j].get("score", 0)
                if si >= sj: remove.add(j)
                else: remove.add(i); break
                continue
            if li == lj and containment > containment_thresh:
                if ai >= aj: remove.add(j)
                else: remove.add(i); break
    kept = [b for i, b in enumerate(blocks) if i not in remove]
    removed = len(blocks) - len(kept)
    if removed > 0: print(f" -{removed} dupes", end="", flush=True)
    return kept

# ─── Block merging ───
def merge_adjacent_blocks(blocks, x_overlap_thresh=0.5, y_gap_max=30, max_height=600):
    if not blocks:
        return blocks
    merged, current = [], None
    for block in blocks:
        label, (x1, y1, x2, y2) = block["label"], block["bbox"]
        if label in ("doc_title", "paragraph_title"):
            if current: merged.append(current)
            merged.append({"label": label, "bbox": [x1, y1, x2, y2]}); current = None; continue
        if current is None:
            current = {"label": "text", "bbox": [x1, y1, x2, y2]}; continue
        cx1, cy1, cx2, cy2 = current["bbox"]
        overlap = max(0, min(cx2, x2) - max(cx1, x1))
        ratio = overlap / min(cx2-cx1, x2-x1) if min(cx2-cx1, x2-x1) > 0 else 0
        merged_height = max(cy2, y2) - cy1
        if ratio >= x_overlap_thresh and 0 <= y1 - cy2 <= y_gap_max and merged_height <= max_height:
            current["bbox"] = [min(cx1,x1), cy1, max(cx2,x2), max(cy2,y2)]
        else:
            merged.append(current); current = {"label": "text", "bbox": [x1, y1, x2, y2]}
    if current: merged.append(current)
    return merged

# ─── Repetition detection ───
def has_repetition(text, min_len=20, min_reps=5):
    if len(text) < min_len * min_reps: return False
    for length in range(min_len, min(80, len(text) // min_reps + 1)):
        for start in range(0, len(text) - length * min_reps + 1, length // 2):
            if text.count(text[start:start+length]) >= min_reps: return True
    return False

def truncate_repetition(text, min_len=20):
    best_phrase, best_count = "", 0
    for length in range(min_len, min(80, len(text)//3+1)):
        for start in range(0, len(text)-length*3+1, length//2):
            phrase = text[start:start+length]
            count = text.count(phrase)
            if count > best_count: best_count, best_phrase = count, phrase
    if best_count < 5 or not best_phrase: return text
    first = text.find(best_phrase)
    second = text.find(best_phrase, first+len(best_phrase))
    return text[:second+len(best_phrase)].rstrip() if second != -1 else text

# ─── GLM-OCR: transformers backend (Longleaf / GPU) ───
def _glm_ocr_transformers(image, max_retries=2, timeout=OCR_TIMEOUT):
    import torch
    _load_glm_ocr()
    buf = io.BytesIO(); image.save(buf, format="PNG"); buf.seek(0)
    b64 = base64.b64encode(buf.getvalue()).decode()
    for attempt in range(max_retries + 1):
        try:
            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(timeout)
            messages = [{"role": "user", "content": [
                {"type": "image", "image": f"data:image/png;base64,{b64}"},
                {"type": "text", "text": "Text Recognition:"},
            ]}]
            inputs = _glm_processor.apply_chat_template(
                messages, add_generation_prompt=True, return_dict=True,
                return_tensors="pt", tokenize=True)
            inputs.pop("token_type_ids", None)
            inputs = {k: v.to(_glm_model.device) for k, v in inputs.items()}
            with torch.no_grad():
                outputs = _glm_model.generate(**inputs, max_new_tokens=4096)
            signal.alarm(0)
            text = _glm_processor.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()
            if not has_repetition(text): return text, "ok"
            if attempt < max_retries: print(f"\n      [repetition, retry {attempt+1}]", flush=True)
        except OCRTimeoutError:
            signal.alarm(0)
            if attempt < max_retries:
                print(f"\n      [timeout after {timeout}s, retry {attempt+1}]", flush=True)
                continue
            print(f"\n      [timeout, giving up]", flush=True)
            return "[OCR timeout]", "timeout"
    print(f"\n      [truncating]", flush=True)
    return truncate_repetition(text), "repetition"

# ─── GLM-OCR: MLX backend (local Mac, HTTP to an MLX server) ───
_mlx_client = None

def _mlx_http_client():
    global _mlx_client
    if _mlx_client is None:
        import httpx
        _mlx_client = httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=OCR_TIMEOUT, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=8))
    return _mlx_client

def _call_mlx(b64, conn_retries=3):
    import httpx
    client = _mlx_http_client()
    for attempt in range(conn_retries + 1):
        try:
            resp = client.post(GLM_MLX_URL, json={
                "model": GLM_MLX_MODEL,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": "Text Recognition:"},
                ]}],
                "max_tokens": 4096,
            })
            resp.raise_for_status()
            # The mlx_vlm server can return 200 with content: null; coerce to "".
            content = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content")
            return (content or "").strip()
        except httpx.ReadTimeout:
            return None  # signal timeout to caller
        except (httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError) as e:
            if attempt < conn_retries:
                wait = 2 ** attempt
                print(f"\n      [connection error: {e}, retry in {wait}s]", flush=True)
                time.sleep(wait)
            else:
                raise

def _glm_ocr_mlx(image, max_retries=2):
    buf = io.BytesIO(); image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    text = None
    for attempt in range(max_retries + 1):
        text = _call_mlx(b64)
        if text is None:
            print(f"\n      [timeout after {OCR_TIMEOUT}s, retry {attempt+1}/{max_retries}]", flush=True)
            if attempt == max_retries:
                return "[OCR timeout]", "timeout"
            continue
        if not has_repetition(text): return text, "ok"
        if attempt < max_retries: print(f"      [repetition, retry {attempt+1}/{max_retries}]", flush=True)
    print(f"      [truncating]", flush=True)
    return truncate_repetition(text or ""), "repetition"

# ─── GLM-OCR dispatch ───
def glm_ocr(image):
    """Returns (text, status) where status is 'ok', 'timeout', or 'repetition'."""
    if BACKEND == "mlx":
        return _glm_ocr_mlx(image)
    return _glm_ocr_transformers(image)

# ─── PDF extraction ───
def extract_page_image(doc, page_idx, output_path):
    page = doc[page_idx]
    images = page.get_images()
    if images:
        xref = images[0][0]
        pix = fitz.Pixmap(doc, xref)
        if pix.n > 4: pix = fitz.Pixmap(fitz.csRGB, pix)
        pix.save(str(output_path))
        w, h = pix.width, pix.height; pix = None; return w, h
    pix = page.get_pixmap(dpi=300)
    pix.save(str(output_path))
    w, h = pix.width, pix.height; pix = None; return w, h

# ─── Page viewer HTML ───
def generate_page_viewer(img_rel, img_w, img_h, regions, output_path, page_num, total_pages, issue_name):
    regions_json = json.dumps([{"bbox": r["bbox"], "label": r["label"], "text": r["text"], "order": i} for i, r in enumerate(regions)])
    text_html = []
    for i, r in enumerate(regions):
        escaped = html.escape(r["text"]).replace("\n", "<br>")
        if r["label"] == "doc_title": text_html.append(f'<h2 class="block-title" data-idx="{i}">{escaped}</h2>')
        elif r["label"] == "paragraph_title": text_html.append(f'<h3 class="block-subtitle" data-idx="{i}">{escaped}</h3>')
        else: text_html.append(f'<p class="block-text" data-idx="{i}">{escaped}</p>')
    hocr = []
    for i, r in enumerate(regions):
        x1,y1,x2,y2 = r["bbox"]
        hocr.append(f'<div class="ocr_carea" title="bbox {x1} {y1} {x2} {y2}" data-label="{r["label"]}"><p class="ocr_par"><span class="ocr_line">{html.escape(r["text"])}</span></p></div>')
    prev = f'<a href="page_{page_num-1:02d}.html">&larr;</a>' if page_num > 1 else '<span class="dis">&larr;</span>'
    nxt = f'<a href="page_{page_num+1:02d}.html">&rarr;</a>' if page_num < total_pages else '<span class="dis">&rarr;</span>'
    page_html = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="ocr-system" content="GLM-OCR via transformers + PP-DocLayout_plus-L">
<title>{html.escape(issue_name)} — Page {page_num}</title>
<link rel="stylesheet" href="../viewer.css">
<script src="https://cdn.jsdelivr.net/npm/openseadragon@4.1/build/openseadragon/openseadragon.min.js"></script>
</head><body>
<div id="header"><h1>{html.escape(issue_name)}</h1>
  <div class="nav">{prev} {nxt}</div><span class="page-info">Page {page_num} of {total_pages}</span>
  <div class="spacer"></div><div class="controls"><label><input type="checkbox" id="toggleBoxes" checked> Boxes</label></div>
  <a href="index.html" class="idx-link">Issue</a> <a href="../index.html" class="idx-link">Home</a></div>
<div id="split"><div id="image-pane"><div id="viewer"></div></div><div id="resize-handle"></div>
  <div id="text-pane">{chr(10).join(text_html)}</div></div>
<div class="ocr_page" id="page_1" title="bbox 0 0 {img_w} {img_h}; image {html.escape(str(img_rel))}; ppageno {page_num-1}">{chr(10).join(hocr)}</div>
<script>
const REGIONS = {regions_json};
const imgW = {img_w}; const IMG_URL = {json.dumps(str(img_rel))};
const PAGE_NUM = {page_num}; const TOTAL_PAGES = {total_pages};
</script><script src="../viewer.js"></script></body></html>'''
    output_path.write_text(page_html)

def generate_index(output_dir, issue_name, page_summaries):
    rows = []
    for ps in page_summaries:
        rows.append(f'<div class="page-card"><div class="thumb"><a href="page_{ps["page"]:02d}.html"><img src="images/page_{ps["page"]:02d}.jpg" loading="lazy"></a></div><div class="info"><a href="page_{ps["page"]:02d}.html">Page {ps["page"]}</a><span class="meta">{ps["regions"]} regions</span></div></div>')
    display = issue_name.replace("-"," ").replace("_"," ").title()
    idx = f'''<!DOCTYPE html><html><head><meta charset="utf-8"><title>{html.escape(display)}</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:Georgia,serif;background:#f4f1eb;color:#2a2a2a}}.container{{max-width:1000px;margin:0 auto;padding:32px 24px}}h1{{font-size:28px;font-weight:800;border-bottom:3px solid #8b7355;padding-bottom:8px;margin-bottom:6px}}.issue-meta{{color:#6a5d4d;font-size:14px;margin-bottom:28px}}.issue-meta a{{color:#6a5d4d}}.pages-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:16px}}.page-card{{background:#fff;border:1px solid #d4cabb;border-radius:6px;overflow:hidden}}.page-card:hover{{box-shadow:0 4px 16px rgba(0,0,0,.12)}}.thumb img{{width:100%;height:auto;display:block}}.info{{padding:8px 10px}}.info a{{color:#2a2622;text-decoration:none;font-weight:600;font-size:14px}}.info .meta{{display:block;font-size:11px;color:#8a7d6d;margin-top:2px;font-family:sans-serif}}</style></head><body>
<div class="container"><h1>{html.escape(display)}</h1>
<p class="issue-meta"><a href="../index.html">&larr; All issues</a> &middot; {len(page_summaries)} pages &middot; <a href="full_text.md">Full text</a> &middot; <a href="full_text.json">JSON</a></p>
<div class="pages-grid">{chr(10).join(rows)}</div></div></body></html>'''
    (output_dir / "index.html").write_text(idx)

# ─── Process one PDF ───
def process_one_pdf(pdf_path, output_dir, layout_model):
    issue_name = pdf_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / "images"; images_dir.mkdir(exist_ok=True)
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    print(f"  Pages: {total_pages}", flush=True)
    page_summaries, all_text, all_data = [], [], []
    for page_idx in range(total_pages):
        page_num = page_idx + 1
        print(f"  Page {page_num}/{total_pages}", end="", flush=True)
        t0 = time.time()
        img_fn = f"page_{page_num:02d}.jpg"
        img_path = images_dir / img_fn
        img_w, img_h = extract_page_image(doc, page_idx, img_path)
        full_image = Image.open(img_path).convert("RGB")
        for result in layout_model.predict(str(img_path)):
            boxes_raw = result["boxes"]
        text_blocks = []
        for b in boxes_raw:
            if b["score"] > 0.5:
                text_blocks.append({
                    "label": b["label"] if b["label"] in OCR_LABELS else "text",
                    "bbox": [int(c) for c in b["coordinate"]],
                    "score": b["score"],
                })
        raw_count = len(text_blocks)
        text_blocks = rescue_low_confidence(text_blocks, boxes_raw)
        text_blocks = deduplicate_boxes(text_blocks)
        if text_blocks: text_blocks = fill_column_gaps(text_blocks, img_w, img_h)
        gap_filled_count = len(text_blocks)
        if text_blocks:
            order = newspaper_reading_order([b["bbox"] for b in text_blocks])
            text_blocks = [text_blocks[i] for i in order]
        merged = merge_adjacent_blocks(text_blocks)
        print(f" -- {gap_filled_count}->{len(merged)} regions", end="", flush=True)
        regions_with_text = []
        for block in merged:
            x1,y1,x2,y2 = block["bbox"]
            try:
                text, status = glm_ocr(full_image.crop((x1,y1,x2,y2)))
            except Exception as e:
                # One bad region must never abort a whole multi-page run.
                print(f"\n      [region error: {e}]", flush=True)
                text, status = "[OCR error]", "error"
            regions_with_text.append({"bbox": block["bbox"], "label": block["label"], "text": text, "status": status})
        img_rel = f"images/{img_fn}"
        generate_page_viewer(img_rel, img_w, img_h, regions_with_text,
                           output_dir / f"page_{page_num:02d}.html", page_num, total_pages, issue_name)
        parts = [("# " if r["label"]=="doc_title" else "## " if r["label"]=="paragraph_title" else "") + r["text"] for r in regions_with_text]
        page_md = "\n\n".join(parts)
        (output_dir / f"page_{page_num:02d}.md").write_text(page_md)
        elapsed = time.time() - t0
        n_failures = sum(1 for r in regions_with_text if r["status"] != "ok")
        page_data = {
            "page": page_num,
            "image": img_rel,
            "width": img_w,
            "height": img_h,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "processing_time": round(elapsed, 1),
            "version": PIPELINE_VERSION,
            "pipeline": {
                "layout_model": LAYOUT_MODEL,
                "ocr_backend": BACKEND,
                "ocr_model": GLM_MLX_MODEL if BACKEND == "mlx" else GLM_OCR_MODEL,
                "ocr_timeout": OCR_TIMEOUT,
            },
            "layout": {
                "raw_detections": raw_count,
                "after_gap_fill": gap_filled_count,
                "after_merge": len(merged),
            },
            "regions": regions_with_text,
        }
        (output_dir / f"page_{page_num:02d}.json").write_text(json.dumps(page_data, indent=2))
        if n_failures:
            print(f" [{n_failures} failed]", end="", flush=True)
        page_summaries.append({"page": page_num, "regions": len(regions_with_text), "time": elapsed, "preview": page_md[:120]})
        all_text.append(f"---\n## Page {page_num}\n\n{page_md}"); all_data.append(page_data)
        print(f" -- {elapsed:.0f}s", flush=True)
    doc.close()
    (output_dir / "full_text.md").write_text(f"# {issue_name}\n\n" + "\n\n".join(all_text))
    (output_dir / "full_text.json").write_text(json.dumps({"issue": issue_name, "pages": all_data}, indent=2))
    generate_index(output_dir, issue_name, page_summaries)
    return page_summaries

def ensure_assets(output_dir):
    """Copy viewer.css / viewer.js next to the issue folders so page_NN.html finds them."""
    assets = Path(__file__).resolve().parent / "assets"
    for name in ("viewer.css", "viewer.js"):
        src = assets / name
        if src.exists():
            shutil.copy(src, output_dir / name)

# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local newspaper/magazine OCR pipeline")
    parser.add_argument("--input-dir", required=True,
                        help="Folder containing the PDFs to OCR")
    parser.add_argument("--output-dir", required=True,
                        help="Folder to write the review website into")
    parser.add_argument("--recursive", action="store_true",
                        help="Search --input-dir recursively for PDFs")
    parser.add_argument("--force", action="store_true",
                        help="Re-process issues even if an index.html already exists")
    parser.add_argument("--backend", choices=["auto", "mlx", "transformers"], default="auto",
                        help="OCR backend: mlx (local Mac server), transformers (GPU), "
                             "or auto (mlx on macOS, transformers elsewhere)")
    args = parser.parse_args()

    BACKEND = args.backend
    if BACKEND == "auto":
        BACKEND = "mlx" if platform.system() == "Darwin" else "transformers"
    # Push the resolved choice into module scope so glm_ocr() can see it.
    globals()["BACKEND"] = BACKEND

    input_dir = Path(args.input_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.recursive:
        pdf_files = sorted(set(input_dir.rglob("*.pdf")) | set(input_dir.rglob("*.PDF")))
    else:
        pdf_files = sorted(set(input_dir.glob("*.pdf")) | set(input_dir.glob("*.PDF")))

    if not pdf_files:
        print(f"No PDFs found in {input_dir}", flush=True)
        raise SystemExit(1)

    print(f"Backend: {BACKEND}", flush=True)
    print(f"Input:  {input_dir}", flush=True)
    print(f"Output: {output_dir}", flush=True)
    print(f"Found {len(pdf_files)} PDFs", flush=True)

    # Fail fast if the local MLX server isn't up.
    if BACKEND == "mlx":
        import httpx
        try:
            httpx.get(GLM_MLX_URL.replace("/v1/chat/completions", "/health"), timeout=3.0)
        except Exception:
            try:
                httpx.get("http://localhost:8080/v1/models", timeout=3.0)
            except Exception:
                print("\nERROR: no GLM-OCR MLX server at localhost:8080.\n"
                      "Start one first (GLM-OCR is a vision model -> mlx_vlm):\n"
                      f"  uv run --with mlx-vlm python -m mlx_vlm.server --model {GLM_MLX_MODEL} --port 8080\n",
                      file=sys.stderr, flush=True)
                raise SystemExit(1)

    ensure_assets(output_dir)

    print(f"Loading {LAYOUT_MODEL}...", flush=True)
    layout_model = create_model(LAYOUT_MODEL)
    print("Layout model ready", flush=True)

    for i, pdf_path in enumerate(pdf_files):
        issue_name = pdf_path.stem
        issue_out = output_dir / issue_name
        if not args.force and (issue_out / "index.html").exists():
            print(f"[{i+1}/{len(pdf_files)}] Skipping {issue_name} (done)", flush=True)
            continue

        print(f"{'='*60}\n[{i+1}/{len(pdf_files)}] {issue_name}", flush=True)
        summaries = process_one_pdf(pdf_path, issue_out, layout_model)
        total = sum(s["time"] for s in summaries)
        print(f"  Done: {len(summaries)} pages in {total:.0f}s", flush=True)

    # Rebuild the top-level gallery + manifest after the run
    try:
        import build_index
        title = output_dir.name.replace("-", " ").replace("_", " ").title() + " — OCR Review"
        build_index.build(output_dir, title=title)
    except Exception as e:
        print(f"(index build skipped: {e})", flush=True)

    print("\nAll done!")
