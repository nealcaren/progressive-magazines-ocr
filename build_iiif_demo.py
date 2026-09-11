# /// script
# requires-python = ">=3.11"
# dependencies = ["iiif", "iiif-prezi3", "pillow"]
# ///
"""
Prototype: turn one OCR'd issue into IIIF (tiled images + Presentation-3 manifest
with our region OCR as text annotations) and a TIFY viewer page.

    uv run build_iiif_demo.py site/woman-rebel/woman-rebel_v1n01 \
        --out iiif_demo --prefix http://localhost:8791

Serves smooth deep-zoom (IIIF Image API level-0 static tiles) + a fulltext panel
fed by our per-region OCR. Generic enough to reuse for dangerouspress.
"""
import json, argparse, warnings
from pathlib import Path
warnings.filterwarnings("ignore")

from iiif.static import IIIFStatic
from iiif_prezi3 import Manifest, config


def build(issue_dir, out, prefix, max_pages=None):
    issue_dir = Path(issue_dir); out = Path(out); prefix = prefix.rstrip("/")
    issue = issue_dir.name
    page_jsons = sorted(issue_dir.glob("page_*.json"))
    if max_pages:
        page_jsons = page_jsons[:max_pages]
    tiles_root = out / "iiif"
    tiles_root.mkdir(parents=True, exist_ok=True)

    # base URL the manifest/services resolve against
    config.configs['helpers.auto_fields.AutoLang'].auto_lang = "en"
    man_id = f"{prefix}/{issue}/manifest.json"
    manifest = Manifest(id=man_id, label={"en": [issue.replace('-', ' ').title()]})

    sg = IIIFStatic(dst=str(tiles_root), tilesize=512, api_version="2.0",
                    prefix=f"{prefix}/iiif")
    for pj in page_jsons:
        d = json.loads(pj.read_text())
        n = d["page"]; w = d["width"]; h = d["height"]
        pid = f"{issue}_page_{n:02d}"   # flat id (no slash -> clean static paths)
        img_path = issue_dir / "images" / f"page_{n:02d}.jpg"
        sg.generate(str(img_path), identifier=pid)   # writes tiles/<pid>/info.json
        service_id = f"{prefix}/iiif/{pid}"

        canvas = manifest.make_canvas(
            id=f"{prefix}/{issue}/canvas/{n}", height=h, width=w,
            label={"en": [f"Page {n}"]})
        # painting: the tiled image (level-0 service -> smooth zoom in TIFY/OSD)
        canvas.add_image(
            image_url=f"{service_id}/full/full/0/default.jpg",
            anno_page_id=f"{prefix}/{issue}/page/{n}/1",
            anno_id=f"{prefix}/{issue}/anno/{n}/img",
            format="image/jpeg", height=h, width=w,
            service=[{"@id": service_id, "@type": "ImageService2",
                      "profile": "http://iiif.io/api/image/2/level0.json"}])
        # supplementing: our region OCR as targeted text annotations (fulltext)
        for i, r in enumerate(d["regions"]):
            txt = r.get("text", "").strip()
            if not txt:
                continue
            x1, y1, x2, y2 = r["bbox"]
            canvas.add_annotation({
                "id": f"{prefix}/{issue}/text/{n}/{i}",
                "type": "Annotation", "motivation": "supplementing",
                "body": {"type": "TextualBody", "language": "en",
                         "format": "text/plain", "value": txt},
                "target": f"{canvas.id}#xywh={x1},{y1},{x2-x1},{y2-y1}",
            }, anno_page_id=f"{prefix}/{issue}/textpage/{n}")

    (out / issue).mkdir(parents=True, exist_ok=True)
    (out / issue / "manifest.json").write_text(manifest.json(indent=2))

    # TIFY viewer page
    (out / issue / "index.html").write_text(f"""<!DOCTYPE html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{issue} — TIFY</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/tify@0.36.2/dist/tify.css">
<style>html,body,#tify{{margin:0;height:100%}}</style></head><body>
<div id="tify"></div>
<script type="module">
import Tify from 'https://cdn.jsdelivr.net/npm/tify@0.36.2/dist/tify.js'
new Tify({{container:'#tify', manifestUrl:'manifest.json'}})
</script>
</body></html>""")
    print(f"Built IIIF + TIFY for {issue}: {len(page_jsons)} pages -> {out/issue}/index.html", flush=True)
    print(f"manifest: {man_id}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("issue_dir")
    ap.add_argument("--out", default="iiif_demo")
    ap.add_argument("--prefix", default="http://localhost:8791")
    ap.add_argument("--max-pages", type=int, default=None)
    a = ap.parse_args()
    build(a.issue_dir, a.out, a.prefix, a.max_pages)
