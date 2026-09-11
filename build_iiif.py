# /// script
# requires-python = ">=3.11"
# dependencies = ["iiif-prezi3"]
# ///
"""
Build IIIF (libvips tiles + Presentation-3 manifest with our region OCR as text
annotations) and a house-styled TIFY viewer, for one issue, one magazine, or a
whole archive. Tiles via `vips dzsave --layout iiif` (~0.15s/page).

    # one issue
    uv run build_iiif.py site/woman-rebel/woman-rebel_v1n01 --out iiif_site \
        --prefix https://pages.dangerouspress.org/progressive-magazines

    # a whole magazine (all its issues)
    uv run build_iiif.py site/woman-rebel --out iiif_site --prefix <R2-base>

Output layout under --out (upload verbatim to R2 under the prefix's path):
    iiif/<issue>_page_NN/...            # tiles + info.json (shared, flat)
    <magazine>/<issue>/manifest.json
    <magazine>/<issue>/index.html       # TIFY viewer

Reusable for dangerouspress: same region-JSON shape in, IIIF out.
"""
import os, sys, json, shutil, subprocess, argparse
from pathlib import Path
from iiif_prezi3 import Manifest, config

VIPS = shutil.which("vips") or "/opt/homebrew/bin/vips"

TITLES = {"masses": "The Masses", "woman-rebel": "The Woman Rebel",
          "the-crisis": "The Crisis", "mother-earth": "Mother Earth"}


def title_of(slug):
    return TITLES.get(slug, slug.replace("-", " ").replace("_", " ").title())


def _tile(img_path, out_dir, id_base, skip):
    """vips dzsave IIIF; @id becomes {id_base}/{out_dir.name}. Returns full/ width."""
    if not skip:
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([VIPS, "dzsave", str(img_path), str(out_dir),
                        "--layout", "iiif", "--tile-size", "512", "--id", id_base],
                       check=True, capture_output=True)
    full = out_dir / "full"
    widths = [d.name.rstrip(",") for d in full.iterdir() if d.is_dir()] if full.exists() else []
    return int(widths[0]) if widths else 336


def build_issue(issue_dir, out, prefix):
    issue = issue_dir.name
    magazine = issue_dir.parent.name
    page_jsons = sorted(issue_dir.glob("page_*.json"))
    if not page_jsons:
        return 0
    tiles_root = out / "iiif"
    id_base = f"{prefix}/iiif"

    config.configs['helpers.auto_fields.AutoLang'].auto_lang = "en"
    man_id = f"{prefix}/{magazine}/{issue}/manifest.json"
    manifest = Manifest(id=man_id, label={"en": [f"{title_of(magazine)} — {issue}"]})

    thumbs = {}
    for idx, pj in enumerate(page_jsons):
        d = json.loads(pj.read_text())
        n = d["page"]; w = d["width"]; h = d["height"]
        pid = f"{issue}_page_{n:02d}"
        img = issue_dir / "images" / f"page_{n:02d}.jpg"
        tw = _tile(img, tiles_root / pid, id_base, skip=not img.exists())
        service_id = f"{id_base}/{pid}"

        canvas = manifest.make_canvas(id=f"{prefix}/{magazine}/{issue}/canvas/{n}",
                                      height=h, width=w, label={"en": [f"Page {n}"]})
        canvas.add_image(
            image_url=f"{service_id}/full/full/0/default.jpg",
            anno_page_id=f"{prefix}/{magazine}/{issue}/page/{n}/1",
            anno_id=f"{prefix}/{magazine}/{issue}/anno/{n}/img",
            format="image/jpeg", height=h, width=w,
            service=[{"@id": service_id, "@type": "ImageService2",
                      "profile": "http://iiif.io/api/image/2/level0.json"}])
        for i, r in enumerate(d["regions"]):
            txt = r.get("text", "").strip()
            if not txt:
                continue
            x1, y1, x2, y2 = r["bbox"]
            canvas.add_annotation({
                "id": f"{prefix}/{magazine}/{issue}/text/{n}/{i}",
                "type": "Annotation", "motivation": "supplementing",
                "body": {"type": "TextualBody", "language": "en",
                         "format": "text/plain", "value": txt},
                "target": f"{canvas.id}#xywh={x1},{y1},{x2-x1},{y2-y1}",
            }, anno_page_id=f"{prefix}/{magazine}/{issue}/textpage/{n}")
        thumbs[idx] = {"id": f"{service_id}/full/{tw},/0/default.jpg", "type": "Image",
                       "format": "image/jpeg", "width": tw, "height": round(tw * h / w),
                       "service": [{"@id": service_id, "@type": "ImageService2",
                                    "profile": "http://iiif.io/api/image/2/level0.json"}]}

    issue_out = out / magazine / issue
    issue_out.mkdir(parents=True, exist_ok=True)
    mdict = json.loads(manifest.json())
    for i, c in enumerate(mdict.get("items", [])):
        if i in thumbs:
            c["thumbnail"] = [thumbs[i]]
    (issue_out / "manifest.json").write_text(json.dumps(mdict, indent=2))
    (issue_out / "index.html").write_text(_TIFY_HTML.replace("__TITLE__", title_of(magazine)))
    return len(page_jsons)


_TIFY_HTML = """<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/tify@0.36.2/dist/tify.css">
<style>
  html,body,#tify{margin:0;height:100%}
  #tify{
    --tify-base-color:#8b7355; --tify-bg-color:#faf8f4; --tify-text-color:#2a2622;
    --tify-border-radius:4px; --tify-body-bg:#e8e0d4;
    font-family:Georgia,'Times New Roman',serif;
  }
</style></head><body>
<div id="tify"></div>
<script type="module">
import Tify from 'https://cdn.jsdelivr.net/npm/tify@0.36.2/dist/tify.js'
new Tify({container:'#tify', manifestUrl:'manifest.json', language:'en'})
</script>
</body></html>"""


def main(inp, out, prefix):
    inp = Path(inp); out = Path(out)
    if list(inp.glob("page_*.json")):          # single issue
        issues = [inp]
    else:                                        # magazine (or archive) dir
        issues = [d for d in sorted(inp.rglob("*"))
                  if d.is_dir() and list(d.glob("page_*.json"))]
    total_pages = 0
    for iss in issues:
        p = build_issue(iss, out, prefix)
        total_pages += p
        print(f"  {iss.parent.name}/{iss.name}: {p} pages", flush=True)
    print(f"Done. {len(issues)} issues, {total_pages} pages -> {out}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="issue dir, magazine dir, or archive root")
    ap.add_argument("--out", required=True)
    ap.add_argument("--prefix", required=True, help="public base URL (R2) the IIIF ids resolve against")
    a = ap.parse_args()
    main(a.input, a.out, a.prefix)
