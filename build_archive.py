"""
Build the combined-archive master landing page: one card per magazine, each
linking to that magazine's own gallery (built by build_index.py).

Scans <root> for subdirectories that contain a manifest.json (i.e. per-magazine
galleries) and writes <root>/index.html.

Usage:
    python build_archive.py site --title "Voices of Dissent"
    python build_archive.py site --image-base https://pages.dangerouspress.org/progressive-magazines
"""

import json, html, argparse
from pathlib import Path


def _magazine_meta(mag_dir):
    """(issue_count, total_pages, first_issue_name) from the gallery manifest."""
    mf = mag_dir / "manifest.json"
    try:
        issues = json.loads(mf.read_text())
    except Exception:
        return 0, 0, None
    total_pages = sum(i.get("pages", 0) for i in issues)
    first = issues[0]["name"] if issues else None
    return len(issues), total_pages, first


def build(root, title="Voices of Dissent", image_base=""):
    root = Path(root)
    image_base = image_base.rstrip("/")
    mags = sorted(d for d in root.iterdir()
                  if d.is_dir() and (d / "manifest.json").exists())

    cards = []
    for d in mags:
        n_issues, n_pages, first = _magazine_meta(d)
        display = d.name.replace("-", " ").replace("_", " ").title()
        if first:
            # small cover thumbnail (~500px); full page scans are 1-3 MB and paint slowly
            thumb = (f"{image_base}/{first}/cover.jpg" if image_base
                     else f"{d.name}/{first}/images/page_01.jpg")
        else:
            thumb = ""
        cards.append(
            f'<div class="mag-card"><a href="{html.escape(d.name)}/index.html">'
            f'<div class="thumb"><img src="{html.escape(thumb)}" loading="lazy" alt="{html.escape(display)}"></div>'
            f'<div class="info"><div class="title">{html.escape(display)}</div>'
            f'<div class="meta">{n_issues} issues &middot; {n_pages} pages</div></div></a></div>')

    total_issues = sum(_magazine_meta(d)[0] for d in mags)
    page = f'''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:Georgia,'Times New Roman',serif;background:#f4f1eb;color:#2a2a2a}}
header{{background:#2a2622;color:#e8e0d4;padding:26px 24px;border-bottom:3px solid #8b7355}}
.hwrap{{max-width:1200px;margin:0 auto}}
header h1{{font-size:30px;font-weight:800;letter-spacing:.5px}}
header p{{font-size:14px;color:#9a8b74;margin-top:4px;font-family:sans-serif}}
.search{{display:flex;gap:10px;margin-top:16px;max-width:640px}}
.search input{{flex:1;min-width:0;padding:11px 14px;border:1px solid #8b7355;border-radius:6px;
  font-size:15px;font-family:sans-serif;background:#faf8f4;color:#2a2a2a;outline:none}}
.search input:focus{{border-color:#c2a578;box-shadow:0 0 0 3px rgba(194,165,120,.3)}}
.search button{{padding:11px 20px;border:0;border-radius:6px;background:#8b7355;color:#fff;
  font-family:sans-serif;font-weight:700;font-size:15px;cursor:pointer}}
.search button:hover{{filter:brightness(1.08)}}
.container{{max-width:1200px;margin:0 auto;padding:32px 24px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:22px}}
.mag-card{{background:#fff;border:1px solid #d4cabb;border-radius:8px;overflow:hidden;transition:box-shadow .2s,transform .2s}}
.mag-card:hover{{box-shadow:0 6px 24px rgba(0,0,0,.12);transform:translateY(-2px)}}
.mag-card a{{text-decoration:none;color:inherit;display:block}}
.mag-card .thumb{{width:100%;height:320px;overflow:hidden;background:#e8e0d4}}
.mag-card .thumb img{{width:100%;height:100%;object-fit:cover;object-position:top}}
.mag-card .info{{padding:14px 16px}}
.mag-card .title{{font-size:19px;font-weight:800}}
.mag-card .meta{{font-size:12px;color:#8a7d6d;margin-top:4px;font-family:sans-serif}}
</style></head><body>
<header><div class="hwrap"><h1>{html.escape(title)}</h1>
<p>Radical and reform magazines of the American 1910s &middot; {len(mags)} titles &middot; {total_issues} issues &middot; <a href="fiction.html" style="color:#c2a578;text-decoration:none;font-weight:700">Fiction &amp; Poetry &rarr;</a></p>
<form class="search" action="search.html" method="get" role="search">
  <input type="search" name="q" placeholder='Search the full text — try "general strike"' aria-label="Search the full text">
  <button type="submit">Search</button>
</form></div></header>
<div class="container"><div class="grid">{chr(10).join(cards)}</div></div>
</body></html>'''
    (root / "index.html").write_text(page)
    print(f"Built archive landing for {len(mags)} magazines -> {root/'index.html'}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build combined-archive master landing page")
    ap.add_argument("root", help="Archive root holding per-magazine gallery subfolders")
    ap.add_argument("--title", default="Voices of Dissent")
    ap.add_argument("--image-base", default="", help="R2 base for thumbnails (optional)")
    args = ap.parse_args()
    build(args.root, args.title, args.image_base)
