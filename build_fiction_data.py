"""Run on the cluster (in the repo dir). Scan every issue's toc.json, collect
fiction & poetry articles, and write fiction.json for the browse page."""
import json, glob, os, sys
sys.path.insert(0, ".")
from build_index import issue_date
from build_iiif import title_of

CATEGORY = {           # article type -> section
    "story": "Short Stories",
    "novel_chapter": "Serialized Fiction",
    "series": "Serialized Fiction",
    "poem": "Poetry",
}

items = []
for f in sorted(glob.glob("site/*/*/toc.json")):
    parts = f.split("/")
    mag, issue = parts[1], parts[2]
    try:
        d = json.load(open(f))
    except Exception:
        continue
    iso, label = issue_date(mag, issue)
    for a in d.get("articles", []):
        if a.get("is_advertisement"):
            continue
        t = (a.get("type") or "").lower()
        cat = CATEGORY.get(t)
        if not cat:
            continue
        title = (a.get("title") or "").strip()
        if not title:
            continue
        # scan-page the reader opens to: first region_ids page (falls back to start_page)
        pages = [g.get("page") for g in (a.get("region_ids") or [])
                 if isinstance(g, dict) and isinstance(g.get("page"), int)]
        page = min(pages) if pages else (a.get("start_page") or 1)
        items.append({
            "type": t,
            "category": cat,
            "title": title,
            "author": (a.get("author") or "").strip() or None,
            "magazine": mag,
            "mag_title": title_of(mag),
            "issue": issue,
            "issue_label": label or issue,
            "date": iso or "",
            "page": int(page),
        })

# sort: by title within each section is done client-side; keep a stable order here
items.sort(key=lambda x: (x["category"], x["mag_title"], x["date"], x["title"].lower()))
json.dump(items, open("fiction.json", "w"), indent=0)
from collections import Counter
c = Counter(x["category"] for x in items)
print("total:", len(items), dict(c))
