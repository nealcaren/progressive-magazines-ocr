"""
Build a client-side full-text search index (search-index.json) over every OCR'd
page in the archive, plus the search UI (search.html). One record per page.

Scans <root> for per-magazine gallery subfolders (those with manifest.json) and
reads each issue's full_text.json.

Usage:
    python build_search.py site
    python build_search.py site --title "Progressive Magazines — Search"
"""

import json, re, argparse, urllib.parse
from pathlib import Path

_WS = re.compile(r"\s+")


def _title(slug):
    return slug.replace("-", " ").replace("_", " ").title()


def build(root, title="Progressive Magazines — Search"):
    root = Path(root)
    magazines = sorted(d for d in root.iterdir()
                       if d.is_dir() and (d / "manifest.json").exists())

    records = []
    mags = {}  # slug -> display title
    for mag in magazines:
        mags[mag.name] = _title(mag.name)
        for issue in sorted(mag.iterdir()):
            ft = issue / "full_text.json"
            if not (issue.is_dir() and ft.exists()):
                continue
            try:
                data = json.loads(ft.read_text())
            except Exception:
                continue
            for pg in data.get("pages", []):
                text = " ".join(r.get("text", "") for r in pg.get("regions", []))
                text = _WS.sub(" ", text).strip()
                if not text:
                    continue
                p = pg.get("page")
                # deep-link into the TIFY reader at this page (page_NN.html doesn't
                # exist on this site — that was the old per-page-HTML viewer scheme).
                tify = urllib.parse.quote(json.dumps({"pages": [p]}, separators=(",", ":")))
                records.append({
                    "m": mag.name,                                   # magazine slug
                    "i": issue.name,                                 # issue slug
                    "p": p,                                          # page number
                    "u": f"{mag.name}/{issue.name}/reader.html?tify={tify}",  # reader deep link
                    "t": text,                                       # page text
                })

    index = {"magazines": mags, "pages": records}
    (root / "search-index.json").write_text(json.dumps(index, separators=(",", ":")))
    (root / "search.html").write_text(_SEARCH_HTML.replace("__TITLE__", title))
    size = (root / "search-index.json").stat().st_size
    print(f"Search index: {len(records)} pages across {len(mags)} magazines "
          f"({size/1e6:.1f} MB) -> {root/'search-index.json'}", flush=True)


_SEARCH_HTML = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Georgia,'Times New Roman',serif;background:#f4f1eb;color:#2a2a2a}
header{background:#2a2622;color:#e8e0d4;padding:20px 24px;border-bottom:3px solid #8b7355}
header h1{font-size:24px;font-weight:800}
header a{color:#c9b896;font-size:13px;font-family:sans-serif;text-decoration:none}
.controls{max-width:900px;margin:0 auto;padding:20px 24px;display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.controls input[type=search]{flex:1;min-width:220px;padding:10px 14px;border:1px solid #c9b896;border-radius:6px;font-size:15px;font-family:sans-serif;outline:none}
.controls input:focus{border-color:#8b7355;box-shadow:0 0 0 2px rgba(139,115,85,.2)}
.controls select{padding:10px 12px;border:1px solid #c9b896;border-radius:6px;font-size:14px;font-family:sans-serif;background:#fff}
.count{max-width:900px;margin:0 auto;padding:0 24px;color:#8a7d6d;font-size:13px;font-family:sans-serif}
.results{max-width:900px;margin:0 auto;padding:12px 24px 48px}
.hit{background:#fff;border:1px solid #d4cabb;border-radius:6px;padding:12px 16px;margin-bottom:12px}
.hit a{text-decoration:none;color:inherit;display:block}
.hit:hover{box-shadow:0 4px 16px rgba(0,0,0,.1)}
.hit .src{font-size:12px;font-family:sans-serif;color:#8b7355;font-weight:600;text-transform:uppercase;letter-spacing:.4px}
.hit .snip{margin-top:6px;font-size:15px;line-height:1.5;color:#33302b}
.hit mark{background:#ffe9a8;padding:0 1px}
.empty{max-width:900px;margin:0 auto;padding:40px 24px;color:#8a7d6d;font-family:sans-serif}
</style></head><body>
<header><h1>__TITLE__</h1> &nbsp; <a href="index.html">&larr; Archive</a></header>
<div class="controls">
  <input type="search" id="q" placeholder="Search the full text of every page…" autofocus>
  <select id="pub"><option value="">All publications</option></select>
</div>
<div class="count" id="count"></div>
<div class="results" id="results"></div>
<script>
let PAGES=[], MAGS={}, LC=[];
const qEl=document.getElementById('q'), pubEl=document.getElementById('pub'),
      resEl=document.getElementById('results'), countEl=document.getElementById('count');
const MAX_RESULTS=300;

function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function title(slug){return slug.replace(/[-_]+/g,' ').replace(/\b\w/g,c=>c.toUpperCase());}

function snippet(text, terms){
  const lc=text.toLowerCase();
  let at=-1;
  for(const t of terms){const j=lc.indexOf(t); if(j>=0&&(at<0||j<at))at=j;}
  if(at<0)at=0;
  const start=Math.max(0, at-70), end=Math.min(text.length, at+180);
  let s=(start>0?'… ':'')+esc(text.slice(start,end))+(end<text.length?' …':'');
  for(const t of terms){
    s=s.replace(new RegExp('('+t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','ig'),'<mark>$1</mark>');
  }
  return s;
}

function run(){
  const raw=qEl.value.trim().toLowerCase();
  const pub=pubEl.value;
  const terms=raw.split(/\s+/).filter(Boolean);
  if(!terms.length){resEl.innerHTML='';countEl.textContent='';return;}
  const hits=[];
  for(let k=0;k<PAGES.length;k++){
    const rec=PAGES[k];
    if(pub&&rec.m!==pub)continue;
    const lc=LC[k];
    let ok=true, score=0;
    for(const t of terms){const c=lc.split(t).length-1; if(!c){ok=false;break;} score+=c;}
    if(ok)hits.push({rec,score});
  }
  hits.sort((a,b)=>b.score-a.score);
  const shown=hits.slice(0,MAX_RESULTS);
  countEl.textContent=`${hits.length} page${hits.length!==1?'s':''} match${hits.length!==1?'':'es'}`+
    (hits.length>MAX_RESULTS?` (showing first ${MAX_RESULTS})`:'');
  resEl.innerHTML=shown.map(({rec})=>{
    const src=`${MAGS[rec.m]||title(rec.m)} — ${title(rec.i)}, p.${rec.p}`;
    return `<div class="hit"><a href="${rec.u}"><div class="src">${esc(src)}</div>`+
           `<div class="snip">${snippet(rec.t,terms)}</div></a></div>`;
  }).join('') || '<div class="empty">No matches.</div>';
}

let timer;
qEl.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(run,120);});
pubEl.addEventListener('change',run);

fetch('search-index.json').then(r=>r.json()).then(d=>{
  PAGES=d.pages; MAGS=d.magazines||{};
  LC=PAGES.map(r=>r.t.toLowerCase());
  const opts=Object.keys(MAGS).sort().map(s=>`<option value="${s}">${esc(MAGS[s])}</option>`).join('');
  pubEl.insertAdjacentHTML('beforeend',opts);
  // preselect publication + query from URL (?pub=slug&q=term)
  const params=new URLSearchParams(location.search);
  if(params.get('pub')&&MAGS[params.get('pub')])pubEl.value=params.get('pub');
  if(params.get('q')){qEl.value=params.get('q');}
  countEl.textContent=`${PAGES.length} pages indexed`;
  run();
}).catch(()=>{countEl.textContent='Could not load search index.';});
</script></body></html>"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build full-text search index + UI")
    ap.add_argument("root", help="Archive root holding per-magazine gallery subfolders")
    ap.add_argument("--title", default="Progressive Magazines — Search")
    args = ap.parse_args()
    build(args.root, args.title)
