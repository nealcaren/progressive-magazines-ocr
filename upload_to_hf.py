# /// script
# requires-python = ">=3.11"
# dependencies = ["datasets", "huggingface_hub"]
# ///
"""
Push the OCR corpus (one record per page) to a Hugging Face dataset, for the
protest-events / embeddings pipeline. Reads each issue's full_text.json from an
archive root (site/ or a deploy dir with <magazine>/<issue>/full_text.json).

    uv run upload_to_hf.py site --repo NealCaren/progressive-magazines-ocr

Auth: uses your cached `huggingface-cli login` token (NealCaren). One push, not
parallel fetches, so the Longleaf HF-block doesn't apply — run from Mac or the
Longleaf login node.
"""
import json, argparse
from pathlib import Path

R2_BASE = "https://pages.dangerouspress.org/progressive-magazines"


def collect(root):
    root = Path(root)
    records = []
    for mag in sorted(d for d in root.iterdir() if d.is_dir() and d.name != "iiif"):
        for issue in sorted(mag.iterdir()):
            ft = issue / "full_text.json"
            if not (issue.is_dir() and ft.exists()):
                continue
            try:
                data = json.loads(ft.read_text())
            except Exception:
                continue
            for pg in data.get("pages", []):
                regions = pg.get("regions", [])
                text = "\n\n".join(r.get("text", "") for r in regions if r.get("text", "").strip())
                if not text.strip():
                    continue
                p = pg.get("page")
                records.append({
                    "magazine": mag.name,
                    "issue": issue.name,
                    "page": p,
                    "text": text,
                    "n_regions": len(regions),
                    "regions": [{"label": r.get("label"), "text": r.get("text", ""),
                                 "bbox": r.get("bbox")} for r in regions],
                    "viewer_url": f"{R2_BASE}/{mag.name}/{issue.name}/index.html",
                    "image_url": f"{R2_BASE}/{issue.name}/page_{p:02d}.jpg",
                    "version": pg.get("version"),
                    "processed_at": pg.get("processed_at"),
                })
    return records


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="archive root (site/ or deploy dir)")
    ap.add_argument("--repo", default="NealCaren/progressive-magazines-ocr")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="collect + report, don't push")
    a = ap.parse_args()

    records = collect(a.root)
    mags = sorted({r["magazine"] for r in records})
    print(f"{len(records)} page records across {len(mags)} magazines: {mags}", flush=True)
    if a.dry_run:
        print("(dry run — not pushing)", flush=True)
        raise SystemExit(0)

    from datasets import Dataset
    ds = Dataset.from_list(records)
    ds.push_to_hub(a.repo, private=a.private)
    print(f"Pushed {len(records)} rows -> https://huggingface.co/datasets/{a.repo}", flush=True)
