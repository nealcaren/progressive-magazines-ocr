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
import os, sys, json, shutil, subprocess, argparse, html, urllib.parse
from pathlib import Path
from iiif_prezi3 import Manifest, config
import build_index  # shares issue_date() for consistent labels/sorting

VIPS = shutil.which("vips") or "/opt/homebrew/bin/vips"

TITLES = {"masses": "The Masses", "woman-rebel": "The Woman Rebel",
          "the-crisis": "The Crisis", "mother-earth": "Mother Earth",
          "the-forerunner": "The Forerunner", "appeal-to-reason": "Appeal to Reason",
          "womans-journal": "Woman's Journal", "progressive-woman": "Progressive Woman"}


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


def build_issue(issue_dir, out, prefix, force=False):
    issue = issue_dir.name
    magazine = issue_dir.parent.name
    page_jsons = sorted(issue_dir.glob("page_*.json"))
    if not page_jsons:
        return 0
    tiles_root = out / "iiif"
    id_base = f"{prefix}/iiif"

    # LLM-corrected reading order (magazines): reorder each page's text annotations
    # so the reader's text pane follows the article flow, not the raw OCR column-sort.
    # Newspapers omit reading_order (compact schema) -> they keep the OCR order.
    ro_map = {}
    _tj = issue_dir / "toc.json"
    if _tj.exists():
        try:
            for e in (json.loads(_tj.read_text()).get("reading_order") or []):
                if isinstance(e, dict) and isinstance(e.get("order"), list):
                    ro_map[e.get("page")] = e["order"]
        except Exception:
            ro_map = {}

    config.configs['helpers.auto_fields.AutoLang'].auto_lang = "en"
    man_id = f"{prefix}/{magazine}/{issue}/manifest.json"
    _, lab = build_index.issue_date(magazine, issue)
    disp = lab or issue
    manifest = Manifest(id=man_id, label={"en": [f"{title_of(magazine)} — {disp}"]})

    thumbs = {}
    for idx, pj in enumerate(page_jsons):
        d = json.loads(pj.read_text())
        n = d["page"]; w = d["width"]; h = d["height"]
        pid = f"{issue}_page_{n:02d}"
        img = issue_dir / "images" / f"page_{n:02d}.jpg"
        # reuse tiles already generated (idempotent re-publish); --force overrides
        already = (tiles_root / pid / "info.json").exists()
        tw = _tile(img, tiles_root / pid, id_base,
                   skip=not img.exists() or (already and not force))
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
    # reader.html = the TIFY page-turner; index.html = the Contents landing page
    (issue_out / "reader.html").write_text(
        _TIFY_HTML.replace("__TITLE__", html.escape(f"{title_of(magazine)} — {disp}"))
                  .replace("__JOURNAL__", html.escape(title_of(magazine)))
                  .replace("__DISP__", html.escape(disp)))
    # carry full_text.json so galleries/search have page counts + text
    ft = issue_dir / "full_text.json"
    version = None
    if ft.exists():
        shutil.copy(ft, issue_out / "full_text.json")
        try:
            fp = json.loads(ft.read_text()).get("pages", [])
            version = fp[0].get("version") if fp else None
        except Exception:
            pass
    # optional enrichment: analyze_issue.py writes toc.json alongside the OCR
    toc_data = None
    tj = issue_dir / "toc.json"
    if tj.exists():
        try:
            toc_data = json.loads(tj.read_text())
            shutil.copy(tj, issue_out / "toc.json")
        except Exception:
            toc_data = None
    (issue_out / "index.html").write_text(
        _landing_html(magazine, issue, disp, toc_data, len(page_jsons), version, prefix))
    # flat cover JPEG at out/<issue>/page_01.jpg — gallery + archive thumbnails
    # load from {prefix}/{issue}/page_01.jpg (the tiled viewer uses IIIF instead)
    cover = issue_dir / "images" / "page_01.jpg"
    if cover.exists():
        cover_out = out / issue
        cover_out.mkdir(parents=True, exist_ok=True)
        shutil.copy(cover, cover_out / "page_01.jpg")
    return len(page_jsons)


_TIFY_HTML = """<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/tify@0.36.2/dist/tify.css">
<style>
  :root{--header:#2a2622;--hink:#e8e0d4;--hdim:#9a8b74;--accent:#8b7355}
  @media(prefers-color-scheme:dark){:root{--header:#161310;--hink:#e9e1d4;--hdim:#9c8f7c;--accent:#c2a578}}
  :root[data-theme=dark]{--header:#161310;--hink:#e9e1d4;--hdim:#9c8f7c;--accent:#c2a578}
  :root[data-theme=light]{--header:#2a2622;--hink:#e8e0d4;--hdim:#9a8b74;--accent:#8b7355}
  html,body{margin:0;height:100%}
  body{display:flex;flex-direction:column}
  .nav{flex:0 0 auto;background:var(--header);color:var(--hink);display:flex;align-items:center;
    justify-content:space-between;gap:12px;padding:9px 16px;font-family:system-ui,sans-serif;font-size:13.5px}
  .crumb{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .crumb a{color:var(--hdim);text-decoration:none}
  .crumb a:hover{color:var(--hink);text-decoration:underline}
  .crumb .sep{color:var(--hdim);margin:0 7px}
  .crumb .cur{color:var(--hink)}
  .toc-link{flex:0 0 auto;color:#fff;background:var(--accent);text-decoration:none;font-weight:700;
    letter-spacing:.02em;padding:6px 13px;border-radius:4px;white-space:nowrap}
  .toc-link:hover{filter:brightness(1.08)}
  .toc-link:focus-visible{outline:2px solid var(--hink);outline-offset:2px}
  #tify{flex:1 1 auto;min-height:0;
    --tify-base-color:#8b7355; --tify-bg-color:#faf8f4; --tify-text-color:#2a2622;
    --tify-border-radius:4px; --tify-body-bg:#e8e0d4;
    font-family:Georgia,'Times New Roman',serif;
  }
</style></head><body>
<nav class="nav">
  <div class="crumb">
    <a href="../../index.html">Progressive Magazines Archive</a><span class="sep">/</span>
    <a href="../index.html">__JOURNAL__</a><span class="sep">/</span>
    <span class="cur">__DISP__</span>
  </div>
  <a class="toc-link" href="index.html">&#9776;&nbsp; Contents</a>
</nav>
<div id="tify"></div>
<script>
// Back-compat: older/bookmarked links put the state in the hash (#?tify=...), but
// TIFY reads location.search only. Rewrite hash->query (no reload) before TIFY inits.
(function(){var h=location.hash;if(h.indexOf('#?')===0&&!location.search){
  try{history.replaceState(null,'',location.pathname+h.slice(1));}catch(e){}}})();
</script>
<script type="module">
import Tify from 'https://cdn.jsdelivr.net/npm/tify@0.36.2/dist/tify.js'
new Tify({container:'#tify', manifestUrl:'manifest.json', language:'en', urlQueryKey:'tify'})
</script>
</body></html>"""


_TYPE_LABEL = {"story": "story", "poem": "poem", "essay": "essay", "article": "article",
               "novel_chapter": "novel", "series": "series", "editorial": "editorial",
               "department": "department", "advertisement": "advertisement"}


def _reader_href(start):
    # TIFY reads its state from location.search (NOT the hash) and only when
    # urlQueryKey is set. Use ?tify=... (query string) to match; reader.html sets
    # urlQueryKey:'tify'. A hash (#?tify=) is silently ignored -> opens page 1.
    q = urllib.parse.quote(json.dumps({"pages": [int(start)]}, separators=(",", ":")))
    return f"reader.html?tify={q}"


def _pg_badge(pages, start):
    if not pages:
        return f'p.&nbsp;<b>{start}</b>'
    pages = sorted(set(pages))
    # collapse consecutive pages into runs: [[1,2,3,4,5],[7]] -> ranges
    runs = [[pages[0]]]
    for p in pages[1:]:
        (runs[-1].append(p) if p == runs[-1][-1] + 1 else runs.append([p]))
    def fmt(run, jump):
        cls = ' class="jump"' if jump else ""
        s = f'{run[0]}' if len(run) == 1 else f'{run[0]}&ndash;{run[-1]}'
        return f'<b{cls}>{s}</b>'
    if len(runs) == 1:                                   # fully continuous
        lead = "p." if len(pages) == 1 else "pp."
        return f'{lead}&nbsp;{fmt(runs[0], False)}'
    parts = [fmt(runs[0], False)] + [fmt(r, True) for r in runs[1:]]  # gaps -> jump-coloured
    return f'pp.&nbsp;{", ".join(parts)} &#8599;'


def _toc_entry(e):
    esc = html.escape
    start = e.get("start_page") or (e.get("pages") or [1])[0]
    title = esc(e.get("title", "Untitled"))
    meta = []
    if e.get("author"):
        conf = ' <span class="attrib">author?</span>' if e.get("author_confidence") == "low" else ""
        meta.append(f'<span class="byline">{esc(e["author"])}{conf}</span>')
    t = e.get("type")
    if t and t not in ("article", "department"):
        meta.append(f'<span class="kind">{_TYPE_LABEL.get(t, t)}</span>')
    metahtml = " &middot; ".join(meta)
    metahtml = f'<span class="byline-wrap">{metahtml}</span>' if metahtml else ""
    return (f'<a class="entry" href="{_reader_href(start)}">'
            f'<span><span class="title">{title}</span>{metahtml}</span>'
            f'<span class="pg">{_pg_badge(e.get("pages"), start)}</span></a>')


def _render_contents(toc):
    tops = [e for e in toc if not e.get("parent")]
    kids = {}
    for e in toc:
        if e.get("parent"):
            kids.setdefault(e["parent"], []).append(e)
    items = []
    for e in tops:
        sub = kids.get(e.get("title"), [])
        li = f'<li class="{ "dept" if sub else "" }'.rstrip() + '">' + _toc_entry(e)
        if sub:
            li += '<ul class="sub">' + "".join(f"<li>{_toc_entry(s)}</li>" for s in sub) + "</ul>"
        li += "</li>"
        items.append(li)
    return "\n".join(items)


def _toc_items(toc_data):
    """Unified contents list. Newspapers emit an explicit `toc`; magazines emit
    `articles` (with ads flagged) and no `toc`, so derive one by dropping ads."""
    d = toc_data or {}
    if isinstance(d.get("toc"), list) and d["toc"]:
        return d["toc"]
    arts = d.get("articles")
    if isinstance(arts, list) and arts:
        return [a for a in arts if not a.get("is_advertisement")]
    return None


def _landing_html(magazine, issue, disp, toc_data, n_pages, version, prefix):
    title = title_of(magazine)
    cover = f"{prefix.rstrip('/')}/{issue}/page_01.jpg" if (prefix and issue) else "page_01.jpg"
    toc = _toc_items(toc_data)
    n_articles = len(toc) if toc else None
    if toc:
        contents = f'<ul class="toc">\n{_render_contents(toc)}\n</ul>'
    else:
        contents = ('<p class="pending">Contents for this issue are being prepared. '
                    'Use &ldquo;Read this issue&rdquo; to page through it now.</p>')
    meta_rows = [f"<dt>Pages</dt><dd>{n_pages}</dd>"]
    if n_articles:
        meta_rows.append(f"<dt>Articles</dt><dd>{n_articles}</dd>")
    meta_rows.append(f"<dt>Issue</dt><dd>{html.escape(disp)}</dd>")
    if version:
        meta_rows.append(f"<dt>OCR</dt><dd>{html.escape(version)}</dd>")
    return _LANDING_TMPL \
        .replace("__TITLE__", html.escape(title)) \
        .replace("__DISP__", html.escape(disp)) \
        .replace("__PUB__", magazine) \
        .replace("__COVER__", html.escape(cover)) \
        .replace("__CONTENTS__", contents) \
        .replace("__META__", "".join(meta_rows)) \
        .replace("__READ__", _reader_href(1))


_LANDING_TMPL = """<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ — __DISP__</title>
<style>
:root{--paper:#f4f1eb;--ink:#2a2622;--accent:#8b7355;--rule:#c9b896;--muted:#8a7d6d;
 --card:#fbf9f4;--line:#d9cebb;--jump:#8c3a2b;--header:#2a2622;--hink:#e8e0d4;--hdim:#9a8b74}
@media(prefers-color-scheme:dark){:root{--paper:#1c1916;--ink:#e9e1d4;--accent:#c2a578;
 --rule:#4a4034;--muted:#9c8f7c;--card:#252019;--line:#3a332a;--jump:#d68b6f;--header:#161310;--hink:#e9e1d4;--hdim:#9c8f7c}}
:root[data-theme=dark]{--paper:#1c1916;--ink:#e9e1d4;--accent:#c2a578;--rule:#4a4034;--muted:#9c8f7c;--card:#252019;--line:#3a332a;--jump:#d68b6f;--header:#161310;--hink:#e9e1d4;--hdim:#9c8f7c}
:root[data-theme=light]{--paper:#f4f1eb;--ink:#2a2622;--accent:#8b7355;--rule:#c9b896;--muted:#8a7d6d;--card:#fbf9f4;--line:#d9cebb;--jump:#8c3a2b;--header:#2a2622;--hink:#e8e0d4;--hdim:#9a8b74}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:Georgia,'Times New Roman',serif;line-height:1.5}
a{color:inherit}
.dbl{border:0;border-top:2px solid var(--ink);box-shadow:0 3px 0 -1px var(--paper),0 4px 0 -1px var(--ink);margin:0}
header{background:var(--header);color:var(--hink);padding:20px 24px 26px}
.wrap{max-width:1080px;margin:0 auto}
.crumb{font-size:12.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--hdim);font-family:system-ui,sans-serif}
.crumb a{text-decoration:none}.crumb a:hover{text-decoration:underline}
.mast{font-size:clamp(30px,5vw,50px);font-weight:800;letter-spacing:.02em;margin:14px 0 2px;text-wrap:balance}
.iline{font-size:15px;color:var(--hdim);letter-spacing:.04em;font-family:system-ui,sans-serif}
.grid{max-width:1080px;margin:32px auto 60px;padding:0 24px;display:grid;grid-template-columns:1fr 288px;gap:44px;align-items:start}
@media(max-width:760px){.grid{grid-template-columns:1fr;gap:32px}}
.lbl{font-family:system-ui,sans-serif;font-size:12px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:var(--accent);margin:0 0 10px}
.toc{list-style:none;margin:0;padding:0}
.toc>li{padding:11px 0;border-bottom:1px solid var(--line)}
.toc>li:last-child{border-bottom:0}
.entry{display:grid;grid-template-columns:1fr auto;gap:14px;align-items:baseline;text-decoration:none}
.entry:hover .title{color:var(--accent)}
.title{font-size:19px;font-weight:700;text-wrap:balance;transition:color .12s}
.byline-wrap{display:block;font-size:14px;color:var(--muted);margin-top:2px}
.byline{font-style:italic}.kind{font-family:system-ui,sans-serif;font-size:12.5px;letter-spacing:.03em}
.pg{font-variant-numeric:tabular-nums;font-size:14px;color:var(--muted);font-family:system-ui,sans-serif;white-space:nowrap;padding-top:3px}
.pg b{color:var(--ink);font-weight:700}.jump{color:var(--jump);font-weight:700}
.dept>.entry .title{font-size:16px}
.sub{list-style:none;margin:8px 0 2px;padding:0 0 0 22px;border-left:2px solid var(--rule)}
.sub li{padding:7px 0}.sub .title{font-size:15.5px;font-weight:600}.sub .byline-wrap{font-size:13px}
.attrib{font-size:11px;color:var(--muted);font-family:system-ui,sans-serif;border:1px solid var(--line);border-radius:3px;padding:0 5px;margin-left:6px}
.pending{color:var(--muted);font-style:italic}
.rail{display:flex;flex-direction:column;gap:18px}
.cover{background:var(--card);border:1px solid var(--line);padding:10px;box-shadow:0 2px 10px rgba(0,0,0,.06)}
.cover img{display:block;width:100%;height:auto;border:1px solid var(--line)}
.read{display:block;text-align:center;text-decoration:none;background:var(--accent);color:#fff;font-family:system-ui,sans-serif;font-weight:700;font-size:15px;letter-spacing:.03em;padding:13px;border-radius:4px}
.read:hover{filter:brightness(1.07)}.read:focus-visible{outline:3px solid var(--jump);outline-offset:2px}
.meta{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:14px 16px;font-family:system-ui,sans-serif;font-size:13px}
.meta dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:6px 12px}
.meta dt{color:var(--muted)}.meta dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}
.slink{font-family:system-ui,sans-serif;font-size:13.5px;text-align:center}
.slink a{color:var(--accent);font-weight:600;text-decoration:none}.slink a:hover{text-decoration:underline}
</style></head><body>
<header><div class="wrap">
 <div class="crumb"><a href="../../index.html">Progressive Magazines Archive</a> / <a href="../index.html">__TITLE__</a></div>
 <div class="mast">__TITLE__</div>
 <div class="iline">__DISP__</div>
</div></header><hr class="dbl">
<div class="grid">
 <main><p class="lbl">Contents</p>__CONTENTS__</main>
 <aside class="rail">
  <a class="read" href="__READ__">Read this issue &rarr;</a>
  <div class="cover"><img src="__COVER__" loading="lazy" alt="Cover"></div>
  <div class="meta"><dl>__META__</dl></div>
  <div class="slink"><a href="../search.html?pub=__PUB__">Search this publication &rarr;</a></div>
 </aside>
</div>
<script>
// keep the reader's #?tify=… fragment when arriving via a deep link is handled by reader.html;
// here we simply ensure in-page anchors work. No-op placeholder for future reflow toggle.
</script>
</body></html>"""


def main(inp, out, prefix, force=False):
    inp = Path(inp); out = Path(out)
    if list(inp.glob("page_*.json")):          # single issue
        issues = [inp]
    else:                                        # magazine (or archive) dir
        issues = [d for d in sorted(inp.rglob("*"))
                  if d.is_dir() and list(d.glob("page_*.json"))]
    total_pages = 0
    for iss in issues:
        p = build_issue(iss, out, prefix, force=force)
        total_pages += p
        print(f"  {iss.parent.name}/{iss.name}: {p} pages", flush=True)

    # build per-magazine galleries + archive landing + full-text search, all
    # pointing at the TIFY viewers, thumbnails from the flat JPEGs already on R2
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import build_index, build_archive, build_search
    for mag in sorted(d for d in out.iterdir()
                      if d.is_dir() and d.name != "iiif"
                      and any(c.is_dir() and (c / "index.html").exists() for c in d.iterdir())):
        build_index.build(mag, title=title_of(mag.name), image_base=prefix)
    build_archive.build(out, title="Progressive Magazines — OCR Archive", image_base=prefix)
    build_search.build(out, title="Progressive Magazines — Search")
    print(f"Done. {len(issues)} issues, {total_pages} pages -> {out}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="issue dir, magazine dir, or archive root")
    ap.add_argument("--out", required=True)
    ap.add_argument("--prefix", required=True, help="public base URL (R2) the IIIF ids resolve against")
    ap.add_argument("--force", action="store_true", help="re-tile even if tiles already exist")
    a = ap.parse_args()
    main(a.input, a.out, a.prefix, force=a.force)
