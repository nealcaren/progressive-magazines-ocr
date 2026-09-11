# /// script
# requires-python = ">=3.11"
# dependencies = ["pymupdf", "paddlepaddle", "paddlex[ocr]", "pillow", "numpy"]
# ///
"""
Clean already-produced OCR output in place: strip stray ```markdown ... ```
fences GLM-OCR sometimes emits, and drop regions left empty afterwards. Then
regenerate each page's HTML/MD and the issue's full_text + index from the
cleaned data. No re-OCR — this only rewrites text that's already in the JSON.

Idempotent and safe to run repeatedly. Skips issues still being written (no
index.html yet).

Usage:
    python clean_ocr.py site                 # whole archive
    python clean_ocr.py site/woman-rebel      # one magazine
"""

import sys, json, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ocr_newspapers import (
    strip_md_fence, generate_page_viewer, generate_index,
)


def _clean_regions(regions):
    out = []
    for r in regions:
        text = strip_md_fence(r.get("text", ""))
        # drop regions that are empty after cleaning (illustrations/blanks)
        if not text.strip() and r.get("status", "ok") == "ok":
            continue
        r = dict(r)
        r["text"] = text
        out.append(r)
    return out


def clean_issue(issue_dir):
    page_jsons = sorted(issue_dir.glob("page_*.json"))
    if not page_jsons:
        return 0
    issue_name = issue_dir.name
    total_pages = len(page_jsons)
    changed = 0
    all_text, all_data, summaries = [], [], []
    for pj in page_jsons:
        data = json.loads(pj.read_text())
        page_num = data["page"]
        before = data["regions"]
        after = _clean_regions(before)
        if len(after) != len(before) or any(
                a["text"] != b["text"] for a, b in zip(after, before)):
            changed += 1
        data["regions"] = after
        pj.write_text(json.dumps(data, indent=2))

        # regenerate the page viewer + markdown from cleaned regions
        generate_page_viewer(
            data["image"], data["width"], data["height"], after,
            issue_dir / f"page_{page_num:02d}.html", page_num, total_pages, issue_name)
        parts = [("# " if r["label"] == "doc_title" else "## " if r["label"] == "paragraph_title" else "") + r["text"]
                 for r in after]
        page_md = "\n\n".join(parts)
        (issue_dir / f"page_{page_num:02d}.md").write_text(page_md)

        all_text.append(f"---\n## Page {page_num}\n\n{page_md}")
        all_data.append(data)
        summaries.append({"page": page_num, "regions": len(after)})

    (issue_dir / "full_text.md").write_text(f"# {issue_name}\n\n" + "\n\n".join(all_text))
    (issue_dir / "full_text.json").write_text(json.dumps({"issue": issue_name, "pages": all_data}, indent=2))
    generate_index(issue_dir, issue_name, summaries)
    return changed


def main(root):
    root = Path(root)
    # accept either an archive root, a magazine dir, or a single issue dir
    if (root / "full_text.json").exists() or list(root.glob("page_*.json")):
        issues = [root]
    else:
        issues = [d for d in sorted(root.rglob("*"))
                  if d.is_dir() and (d / "index.html").exists() and list(d.glob("page_*.json"))]
    total_changed = 0
    for iss in issues:
        c = clean_issue(iss)
        if c:
            print(f"  {iss.relative_to(root) if iss != root else iss.name}: cleaned {c} pages", flush=True)
        total_changed += c
    print(f"Done. Cleaned pages across {len(issues)} issues: {total_changed}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Strip stray markdown fences from OCR output")
    ap.add_argument("root", help="Archive root, a magazine dir, or a single issue dir")
    args = ap.parse_args()
    main(args.root)
