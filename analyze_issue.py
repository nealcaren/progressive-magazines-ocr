# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "requests"]
# ///
"""
LLM enrichment pass for a progressive-magazines issue: reconstruct reading order,
segment articles, classify advertisements, link cross-page continuations, and build
a table of contents / article index — from the OCR we already have. Writes a
separate `toc.json`; never mutates the original OCR.

Passes:
  0. CONTENTS RECOVERY (image, conditional) — if the issue prints a contents block
     but it OCR'd nearly empty, crop that region and re-read it with a vision model
     so the text pass has the printed TOC as a gold standard.
  1. TEXT + BBOX (one LLM call) — reading order, article segmentation, ad vs
     editorial classification, cross-page continuations, printed offset, TOC.
  2. TARGETED IMAGE — only for shredded headlines the text pass flags; crop just
     that zone and re-read it with the vision model. Not a whole-page rescan.

Backend (auto): ANTHROPIC_API_KEY -> Anthropic; else OPENROUTER_API_KEY ->
OpenRouter (default model anthropic/claude-sonnet-5); else OPENAI_API_KEY.
The same multimodal model handles the image crops, so no separate OCR server.

Usage:
    uv run analyze_issue.py site/the-crisis/crisis-1911-01
    uv run analyze_issue.py <issue_dir> --kind newspaper --no-image
    uv run analyze_issue.py <issue_dir> --single-author "Charlotte Perkins Gilman"
    uv run analyze_issue.py <issue_dir> --emit-prompt        # print prompt, no call
"""
import os, re, io, json, base64, argparse, sys
from pathlib import Path
import requests

TEXT_CAP = 500
SHRED_PAD = 40
CONTENTS_MIN = 60      # if a CONTENTS region has fewer chars, recover it from the image


def backend():
    if os.getenv("ANTHROPIC_API_KEY"):
        return ("anthropic", "claude-sonnet-5", None, os.environ["ANTHROPIC_API_KEY"])
    if os.getenv("OPENROUTER_API_KEY"):
        return ("openrouter", "anthropic/claude-sonnet-5",
                "https://openrouter.ai/api/v1", os.environ["OPENROUTER_API_KEY"])
    if os.getenv("OPENAI_API_KEY"):
        return ("openai", "gpt-4o", "https://api.openai.com/v1", os.environ["OPENAI_API_KEY"])
    sys.exit("No LLM key found (ANTHROPIC_API_KEY / OPENROUTER_API_KEY / OPENAI_API_KEY).")


def llm(messages, model, max_tokens=8000):
    """OpenAI-compatible chat call (OpenRouter/OpenAI); Anthropic via its own shape."""
    kind, default_model, base, key = backend()
    model = model or default_model
    if kind == "anthropic":
        # Anthropic messages API expects system separate; here we keep it simple.
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=180,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            json={"model": model, "max_tokens": max_tokens, "messages": messages})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []))
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0, "messages": messages}
    if kind == "openrouter":
        body["reasoning"] = {"enabled": False}   # structured extraction; no extended thinking
    r = requests.post(f"{base}/chat/completions", timeout=240,
        headers={"Authorization": f"Bearer {key}"}, json=body)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    content = msg.get("content")
    if not content:
        raise RuntimeError(f"empty content (finish={r.json()['choices'][0].get('finish_reason')}); "
                           f"reasoning tail: {(msg.get('reasoning') or '')[:200]}")
    return content


def vlm_read(img_bytes, instruction, model):
    b64 = base64.b64encode(img_bytes).decode()
    kind, *_ = backend()
    if kind == "anthropic":
        content = [{"type": "text", "text": instruction},
                   {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}]
    else:
        content = [{"type": "text", "text": instruction},
                   {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]
    return llm([{"role": "user", "content": content}], model, max_tokens=400).strip()


def parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


def load_pages(issue_dir):
    return json.loads((Path(issue_dir) / "full_text.json").read_text()).get("pages", [])


def crop(issue_dir, page_no, box, pad=0):
    from PIL import Image
    p = Path(issue_dir) / "images" / f"page_{page_no:02d}.jpg"
    if not p.exists():
        return None
    im = Image.open(p).convert("RGB")
    x1, y1, x2, y2 = box
    c = im.crop((max(0, x1 - pad), max(0, y1 - pad), min(im.width, x2 + pad), min(im.height, y2 + pad)))
    buf = io.BytesIO(); c.save(buf, "JPEG"); return buf.getvalue()


def recover_contents(pages, issue_dir, model):
    """If a CONTENTS/INDEX heading exists but its text is nearly empty, read it from
    the image so the text pass gets the printed TOC as gold standard."""
    for p in pages[:2]:
        regs = p.get("regions", [])
        for i, r in enumerate(regs):
            if not re.search(r"\bCONTENTS\b|\bINDEX\b", r.get("text", ""), re.I):
                continue
            # gather this heading + the block just below it (same column-ish)
            hx1, hy1, hx2, hy2 = r["bbox"]
            box = [hx1, hy1, hx2, min(p.get("height", hy2 + 2500), hy2 + 2400)]
            joined = " ".join(rr.get("text", "") for rr in regs
                              if rr["bbox"][1] >= hy1 and rr["bbox"][1] < box[3] and abs(rr["bbox"][0]-hx1) < 1200)
            if len(joined) >= CONTENTS_MIN:
                return joined, False               # already captured in OCR
            img = crop(issue_dir, p["page"], box, pad=30)
            if not img:
                return None, False
            txt = vlm_read(img, "This is a magazine table-of-contents block. Transcribe every line "
                                "(title, author, page number) exactly, one entry per line.", model)
            return txt, True                       # recovered from image
    return None, False


SCHEMA = """Return ONLY a JSON object (no prose, no code fence):
{
  "publication_kind": "magazine|newspaper",
  "printed_offset": <int|null>,
  "reading_order": [ {"page": <int>, "order": [<region_id>,...]} ],
  "relabel": [ {"page":<int>,"id":<int>,"label":"title|byline|body|caption|advertisement|running_head|folio|section_break|department"} ],
  "needs_image": [ {"page":<int>,"id":<int>,"why":"shredded_headline","fragment_ids":[<int>,...]} ],
  "articles": [
    {"title":"<clean>","author":<str|null>,"author_confidence":"high|low",
     "type":"article|essay|story|poem|novel_chapter|series|editorial|department|advertisement",
     "serial":<str|null>,"is_advertisement":<bool>,
     "start_page":<int>,"pages":[<int>,...],"region_ids":[{"page":<int>,"ids":[<int>,...]}]}
  ],
  "toc": [ {"title":"<t>","author":<str|null>,"type":"<type>","start_page":<int>,"pages":[<int>,...],"parent":<str|null>} ]
}
Rules:
- Use region ids/pages exactly as given; never invent coordinates.
- CLASSIFY ADVERTISEMENTS: many pages (esp. front/back) are ads for hotels, schools,
  books, patent medicines, etc. Mark those articles is_advertisement=true / type=advertisement
  and EXCLUDE them from "toc". "toc" is the editorial contents only.
- Link cross-page continuations via "continued on/from page N" AND linguistic continuity;
  list every page an article touches in "pages".
- Bylines ("By X" / lone name by a title) => author + relabel "byline". Bare numbers => "folio".
  Repeated masthead at page top => "running_head". "* * *" => "section_break".
- A department (e.g. a review column) is one toc entry; its sub-sections get parent=<department title>.
- If publication_kind is newspaper there is usually no printed contents — still produce
  "toc" as the front-to-back article index (headline + subhead-as-author-ish where useful).
- A title that is a broken fragment of a larger headline => list in needs_image with sibling ids."""


def build_prompt(pages, printed_toc, single_author, kind):
    lines = []
    for p in pages:
        lines.append(f"\n## PAGE {p.get('page')} (w={p.get('width')} h={p.get('height')})")
        for i, r in enumerate(p.get("regions", [])):
            b = r.get("bbox", [0, 0, 0, 0])
            t = " ".join(r.get("text", "").split())[:TEXT_CAP]
            lines.append(f"[{i}] label={r.get('label')} x={b[0]} y={b[1]} w={b[2]-b[0]} h={b[3]-b[1]} | {t}")
    ctx = f"\nThis is a {kind}." if kind else ""
    if single_author:
        ctx += (f"\nWritten almost entirely by {single_author}; do not hunt per-article bylines — "
                "author is that name only where explicitly credited otherwise, else null.")
    if printed_toc:
        ctx += (f"\nThe issue's PRINTED contents (GOLD STANDARD — match it; you may find items it "
                f"omits like ads/notes):\n\"\"\"\n{printed_toc}\n\"\"\"")
    return ("You are refining OCR layout output for a historical periodical. Below is every detected "
            "region per page in current (possibly wrong) reading order, with bounding boxes "
            f"(x,y top-left; w,h px) and OCR text.{ctx}\n\n{SCHEMA}\n\n=== REGIONS ===" + "\n".join(lines))


def resolve_shredded(result, issue_dir, pages, model):
    by_page = {p["page"]: p for p in pages}
    out = []
    for f in result.get("needs_image", []):
        pg = f.get("page"); page = by_page.get(pg)
        if not page:
            continue
        ids = f.get("fragment_ids") or [f.get("id")]
        boxes = [page["regions"][i]["bbox"] for i in ids if i is not None and i < len(page["regions"])]
        if not boxes:
            continue
        box = [min(b[0] for b in boxes), min(b[1] for b in boxes),
               max(b[2] for b in boxes), max(b[3] for b in boxes)]
        img = crop(issue_dir, pg, box, pad=SHRED_PAD)
        if not img:
            continue
        try:
            txt = vlm_read(img, "Transcribe the headline/title in this image. Output only the text.", model)
        except Exception as e:
            txt = f"(crop failed: {e})"
        out.append({"page": pg, "fragment_ids": ids, "headline": txt})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("issue_dir")
    ap.add_argument("--model", default=None)
    ap.add_argument("--kind", choices=["magazine", "newspaper"], default="magazine")
    ap.add_argument("--single-author", default=None)
    ap.add_argument("--no-image", action="store_true")
    ap.add_argument("--emit-prompt", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    pages = load_pages(a.issue_dir)
    printed_toc, recovered = (None, False)
    if not a.no_image:
        printed_toc, recovered = recover_contents(pages, a.issue_dir, a.model)
    if printed_toc is None:      # fall back to whatever text OCR captured
        for p in pages[:2]:
            for r in p.get("regions", []):
                if re.search(r"\bCONTENTS\b|\bINDEX\b", r.get("text", ""), re.I) and len(r.get("text", "")) >= CONTENTS_MIN:
                    printed_toc = r["text"]

    prompt = build_prompt(pages, printed_toc, a.single_author, a.kind)
    if a.emit_prompt:
        print(prompt); return

    result = parse_json(llm([{"role": "user", "content": prompt}], a.model, max_tokens=16000))
    result["_printed_toc_present"] = bool(printed_toc)
    result["_printed_toc_recovered_from_image"] = recovered
    if not a.no_image and result.get("needs_image"):
        result["needs_image_resolved"] = resolve_shredded(result, a.issue_dir, pages, a.model)

    out = Path(a.out) if a.out else Path(a.issue_dir) / "toc.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    toc = result.get("toc", []); ads = [x for x in result.get("articles", []) if x.get("is_advertisement")]
    print(f"{Path(a.issue_dir).name}: {len(toc)} TOC entries, {len(result.get('articles',[]))} articles "
          f"({len(ads)} ads excluded), {len(result.get('needs_image',[]))} headline crops. "
          f"TOC gold={'recovered-image' if recovered else ('text' if printed_toc else 'none-generated')} -> {out}",
          flush=True)


if __name__ == "__main__":
    main()
