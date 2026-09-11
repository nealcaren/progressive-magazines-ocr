"""
Build the top-level gallery (index.html) and manifest.json for the review site
by scanning every issue subdirectory that contains an index.html.

Usage:
    python build_index.py site
    python build_index.py site --title "Progressive Magazines"
"""

import json, html, argparse
from pathlib import Path


def _issue_meta(issue_dir):
    """Pull page count / version / processed_at from the issue's full_text.json."""
    ft = issue_dir / "full_text.json"
    n_pages = len(list(issue_dir.glob("page_*.html")))
    version, processed_at = None, None
    if ft.exists():
        try:
            data = json.loads(ft.read_text())
            pages = data.get("pages", [])
            n_pages = len(pages) or n_pages
            if pages:
                version = pages[0].get("version")
                processed_at = pages[-1].get("processed_at")
        except Exception:
            pass
    return n_pages, version, processed_at


def build(output_dir, title="Progressive Magazines — OCR"):
    output_dir = Path(output_dir)
    issues = sorted(d for d in output_dir.iterdir()
                    if d.is_dir() and (d / "index.html").exists())

    manifest = []
    cards = []
    for d in issues:
        n_pages, version, processed_at = _issue_meta(d)
        thumb = "images/page_01.jpg"
        display = d.name.replace("-", " ").replace("_", " ").title()
        manifest.append({
            "issue": d.name,
            "pages": n_pages,
            "version": version,
            "processed_at": processed_at,
        })
        badge = f'<span class="badge ok">v{html.escape(version)}</span>' if version \
            else '<span class="badge old">processed</span>'
        cards.append(
            f'<div class="card"><a href="{html.escape(d.name)}/index.html">'
            f'<div class="thumb"><img src="{html.escape(d.name)}/{thumb}" loading="lazy"></div>'
            f'<div class="info"><span class="t">{html.escape(display)}</span>'
            f'<span class="meta">{n_pages} pages {badge}</span></div></a></div>')

    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    page = f'''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:Georgia,serif;background:#f4f1eb;color:#2a2a2a}}
.container{{max-width:1100px;margin:0 auto;padding:40px 24px}}
h1{{font-size:32px;font-weight:800;border-bottom:3px solid #8b7355;padding-bottom:10px;margin-bottom:6px}}
.sub{{color:#6a5d4d;font-size:14px;margin-bottom:32px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:20px}}
.card{{background:#fff;border:1px solid #d4cabb;border-radius:6px;overflow:hidden}}
.card:hover{{box-shadow:0 4px 16px rgba(0,0,0,.12)}}
.card a{{text-decoration:none;color:inherit}}
.thumb{{aspect-ratio:3/4;overflow:hidden;background:#eee}}
.thumb img{{width:100%;height:100%;object-fit:cover;display:block}}
.info{{padding:10px 12px}}.info .t{{display:block;font-weight:700;font-size:15px}}
.info .meta{{display:block;font-size:11px;color:#8a7d6d;margin-top:4px;font-family:sans-serif}}
.badge{{display:inline-block;padding:1px 6px;border-radius:3px;font-size:10px;margin-left:4px}}
.badge.ok{{background:#d8e8d0;color:#2e5b1e}}.badge.old{{background:#eee;color:#777}}
</style></head><body>
<div class="container"><h1>{html.escape(title)}</h1>
<p class="sub">{len(issues)} issues &middot; PP-DocLayout_plus-L + GLM-OCR</p>
<div class="grid">{chr(10).join(cards)}</div></div></body></html>'''
    (output_dir / "index.html").write_text(page)
    print(f"Built index for {len(issues)} issues -> {output_dir/'index.html'}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build top-level gallery + manifest")
    parser.add_argument("output_dir", help="Review site directory (holds issue subfolders)")
    parser.add_argument("--title", default="Progressive Magazines — OCR")
    args = parser.parse_args()
    build(args.output_dir, args.title)
