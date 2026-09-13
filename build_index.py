"""
Build the review website's top-level gallery (index.html) and manifest.json by
scanning every issue subdirectory that contains an index.html.

The gallery mirrors the dangerouspress-ocr review site: a dark header, live
search, sort buttons, thumbnail cards, and a pipeline badge per issue. It is
manifest-driven — index.html fetches manifest.json and renders client-side, so
re-running only needs to refresh the manifest.

Usage:
    python build_index.py site/woman-rebel
    python build_index.py site/woman-rebel --title "The Woman Rebel"
"""

import json, html, argparse, re
from pathlib import Path

_MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]
_MON_ABBR = {m[:3].lower(): i for i, m in enumerate(_MONTHS) if m}

# Woman Rebel dirs carry only volume/number (v1n01..v1n07); the publication
# printed no ISO date, so these come from the OCR'd front-page mastheads.
_WOMAN_REBEL = {
    "v1n01": ("1914-03-01", "March 1914"),
    "v1n02": ("1914-04-01", "April 1914"),
    "v1n03": ("1914-05-01", "May 1914"),
    "v1n04": ("1914-06-01", "June 1914"),
    "v1n05": ("1914-07-01", "July 1914"),
    "v1n06": ("1914-08-01", "August 1914"),
    "v1n07": ("1914-09-01", "September–October 1914"),
}


def issue_date(magazine, name):
    """Normalize a heterogeneous issue-dir name to (iso_date | None, label).

    iso_date (YYYY-MM-DD) is the chronological sort key; label is the natural-
    language display string ("May 1911" for monthlies, "January 4, 1913" for
    weeklies). Handles every naming pattern in the archive; see CLAUDE notes."""
    if magazine == "woman-rebel":
        m = re.search(r"v\d+n\d+", name)
        if m and m.group(0) in _WOMAN_REBEL:
            return _WOMAN_REBEL[m.group(0)]
    # embedded month-name form, e.g. industrial-worker "…-sep-05-1912-iw"
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)-(\d{1,2})-(19\d\d)", name.lower())
    if m:
        mo, d, y = _MON_ABBR[m.group(1)], int(m.group(2)), int(m.group(3))
        return (f"{y:04d}-{mo:02d}-{d:02d}", f"{_MONTHS[mo]} {d}, {y}")
    # full ISO date, e.g. "womans-journal_1913-01-04"
    m = re.search(r"(19\d\d)-(\d\d)-(\d\d)", name)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return (f"{y:04d}-{mo:02d}-{d:02d}", f"{_MONTHS[mo]} {d}, {y}")
    # year-month only, e.g. "the-masses_1911-01" (monthly)
    m = re.search(r"(19\d\d)-(\d\d)(?!\d)", name)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return (f"{y:04d}-{mo:02d}-01", f"{_MONTHS[mo]} {y}")
    return (None, None)


def _issue_meta(issue_dir):
    """page count / version / processed_at from the issue's full_text.json."""
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


_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Georgia', 'Times New Roman', serif; background: #f4f1eb; color: #2a2a2a; }
  header { background: #2a2622; color: #e8e0d4; padding: 20px 24px; border-bottom: 3px solid #8b7355; }
  header h1 { font-size: 26px; font-weight: 800; letter-spacing: 0.5px; }
  header p { font-size: 14px; color: #9a8b74; margin-top: 4px; font-family: sans-serif; }
  .controls { max-width: 1200px; margin: 0 auto; padding: 16px 24px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .controls input[type="search"] { flex: 1; min-width: 200px; padding: 8px 14px; border: 1px solid #c9b896; border-radius: 6px; background: #fff; font-size: 14px; font-family: sans-serif; outline: none; }
  .controls input:focus { border-color: #8b7355; box-shadow: 0 0 0 2px rgba(139,115,85,0.2); }
  .controls .sort-btn { padding: 7px 14px; border: 1px solid #c9b896; border-radius: 6px; background: #fff; cursor: pointer; font-size: 13px; font-family: sans-serif; color: #2a2622; }
  .controls .sort-btn:hover { background: #f0ece4; }
  .controls .sort-btn.active { background: #2a2622; color: #e8e0d4; border-color: #2a2622; }
  .count { font-size: 13px; color: #8a7d6d; font-family: sans-serif; }
  .container { max-width: 1200px; margin: 0 auto; padding: 0 24px 40px; }
  .issues-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 20px; }
  .issue-card { background: #fff; border: 1px solid #d4cabb; border-radius: 8px; overflow: hidden; transition: box-shadow 0.2s, transform 0.2s; }
  .issue-card:hover { box-shadow: 0 6px 24px rgba(0,0,0,0.12); transform: translateY(-2px); }
  .issue-card a { text-decoration: none; color: inherit; display: block; }
  .issue-card .thumb { width: 100%; height: 300px; overflow: hidden; background: #e8e0d4; }
  .issue-card .thumb img { width: 100%; height: 100%; object-fit: cover; object-position: top; }
  .issue-card .info { padding: 12px 14px; }
  .issue-card .title { font-size: 16px; font-weight: 700; color: #1a1a1a; }
  .issue-card .meta { font-size: 12px; color: #8a7d6d; font-family: sans-serif; margin-top: 4px; }
  .issue-card .pipeline-badge { display: inline-block; font-size: 10px; font-family: sans-serif; font-weight: 600; padding: 2px 6px; border-radius: 3px; margin-top: 6px; letter-spacing: 0.3px; }
  .issue-card .pipeline-badge.current { background: #d4edda; color: #155724; }
  .issue-card .pipeline-badge.old { background: #f8d7da; color: #721c24; }
  .issue-card.stale { opacity: 0.5; }
  .issue-card.stale:hover { opacity: 1; }
  .empty { text-align: center; padding: 60px 20px; color: #8a7d6d; }
  .empty h2 { font-size: 20px; margin-bottom: 8px; color: #6a5d4d; }
  .loading { text-align: center; padding: 60px; color: #8a7d6d; font-family: sans-serif; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <p>Layout detection + GLM-OCR
  &middot; <a href="../index.html" style="color:#c9b896">Archive</a>
  &middot; <a href="../search.html?pub=__PUB__" style="color:#e8d9a8;font-weight:600">Search this publication &rarr;</a></p>
</header>
<div class="controls">
  <input type="search" id="search" placeholder="Filter by issue name...">
  <button class="sort-btn active" data-sort="date">By date</button>
  <button class="sort-btn" data-sort="name">A-Z</button>
  <button class="sort-btn" data-sort="pages">By pages</button>
  <span class="count" id="count"></span>
</div>
<div class="container">
  <div class="issues-grid" id="grid"><div class="loading">Loading issues...</div></div>
</div>
<script>
let issues = [];
let currentSort = 'date';
const IMG_BASE = __IMG_BASE__;  // "" -> local images/, or an R2 base URL

function displayTitle(name) {
  return name.replace(/[-_]+/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
}
function issueLabel(issue) { return issue.label || displayTitle(issue.name); }
function thumbUrl(name) {
  return IMG_BASE ? `${IMG_BASE}/${name}/page_01.jpg` : `${name}/images/page_01.jpg`;
}

function renderGrid(filtered) {
  const grid = document.getElementById('grid');
  if (filtered.length === 0) {
    grid.innerHTML = '<div class="empty"><h2>No matching issues</h2><p>Try a different search term.</p></div>';
    return;
  }
  grid.innerHTML = filtered.map(issue => {
    const hasVersion = !!issue.version;
    const cardClass = hasVersion ? 'issue-card' : 'issue-card stale';
    const badge = hasVersion
      ? `<div class="pipeline-badge current">OCR v${issue.version}</div>`
      : `<div class="pipeline-badge old">Needs reprocessing</div>`;
    return `<div class="${cardClass}">
      <a href="${issue.name}/index.html">
        <div class="thumb"><img src="${thumbUrl(issue.name)}" loading="lazy" alt="${issueLabel(issue)}"></div>
        <div class="info">
          <div class="title">${issueLabel(issue)}</div>
          <div class="meta">${issue.pages} pages</div>
          ${badge}
        </div>
      </a>
    </div>`;
  }).join('');
  const current = filtered.filter(i => i.version).length;
  document.getElementById('count').textContent =
    `${filtered.length} issues — ${current} current, ${filtered.length - current} unprocessed`;
}

function sortIssues(list, mode) {
  const sorted = [...list];
  const byDate = (a, b) => (a.date || '9999-99-99').localeCompare(b.date || '9999-99-99') || a.name.localeCompare(b.name);
  if (mode === 'pages') sorted.sort((a, b) => b.pages - a.pages || byDate(a, b));
  else if (mode === 'name') sorted.sort((a, b) => a.name.localeCompare(b.name));
  else sorted.sort(byDate);
  return sorted;
}

function update() {
  const query = document.getElementById('search').value.toLowerCase();
  let filtered = issues;
  if (query) filtered = issues.filter(i =>
    i.name.toLowerCase().includes(query) || issueLabel(i).toLowerCase().includes(query));
  renderGrid(sortIssues(filtered, currentSort));
}

document.getElementById('search').addEventListener('input', update);
document.querySelectorAll('.sort-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentSort = btn.dataset.sort;
    update();
  });
});

fetch('manifest.json')
  .then(r => r.json())
  .then(data => { issues = data; update(); })
  .catch(() => {
    document.getElementById('grid').innerHTML =
      '<div class="empty"><h2>No manifest.json found</h2><p>Run ocr_newspapers.py to generate issues.</p></div>';
  });
</script>
</body>
</html>
"""


def build(output_dir, title="Voices of Dissent", image_base=""):
    """image_base: if set (e.g. an R2 URL), gallery thumbnails load from
    {image_base}/{issue}/page_01.jpg instead of the local {issue}/images/."""
    output_dir = Path(output_dir)
    issues = sorted(d for d in output_dir.iterdir()
                    if d.is_dir() and (d / "index.html").exists())

    manifest = []
    for d in issues:
        n_pages, version, processed_at = _issue_meta(d)
        iso, label = issue_date(output_dir.name, d.name)
        manifest.append({
            "name": d.name,
            "pages": n_pages,
            "version": version,
            "processed_at": processed_at,
            "date": iso,
            "label": label,
        })
    # chronological by default (undated issues sort last, then by name)
    manifest.sort(key=lambda r: (r["date"] or "9999-99-99", r["name"]))

    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    page = (_PAGE_TEMPLATE
            .replace("__TITLE__", html.escape(title))
            .replace("__PUB__", output_dir.name)
            .replace("__IMG_BASE__", json.dumps(image_base.rstrip("/"))))
    (output_dir / "index.html").write_text(page)
    print(f"Built index for {len(issues)} issues -> {output_dir/'index.html'}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build top-level gallery + manifest")
    parser.add_argument("output_dir", help="Review site directory (holds issue subfolders)")
    parser.add_argument("--title", default="Voices of Dissent")
    args = parser.parse_args()
    build(args.output_dir, args.title)
