#!/usr/bin/env python3
"""Mother Earth 1911 + 1913 from the IA 'mother-earth' item (Harvard DSR scans,
clean per-issue PDFs) -> Longleaf pdfs/mother-earth. Idempotent."""
import json, os, re, urllib.request, urllib.parse
REPO = "/work/users/n/c/ncaren/progressive-magazines-ocr"
DIR = f"{REPO}/pdfs/mother-earth"
os.makedirs(DIR, exist_ok=True)
def get(url, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research OCR archive)"})
    return urllib.request.urlopen(req, timeout=timeout).read()
files = json.loads(get("https://archive.org/metadata/mother-earth/files"))["result"]
stats = {"ok": 0, "skip": 0, "fail": 0}
for f in files:
    name = f.get("name", "")
    if not name.lower().endswith(".pdf"):
        continue
    m = re.search(r"\((19(?:11|13))-(\d\d)\)", name)
    if not m:
        continue
    y, mo = m.group(1), m.group(2)
    out = f"{DIR}/mother-earth_{y}-{mo}.pdf"
    if os.path.exists(out) and os.path.getsize(out) > 10000 and open(out, "rb").read(4) == b"%PDF":
        stats["skip"] += 1; continue
    url = "https://archive.org/download/mother-earth/" + urllib.parse.quote(name)
    try:
        data = get(url)
        if data[:4] != b"%PDF": raise ValueError("not-pdf")
        open(out, "wb").write(data); stats["ok"] += 1
        print(f"  {y}-{mo} <- {name}", flush=True)
    except Exception as e:
        stats["fail"] += 1; print(f"  FAIL {name}: {e}", flush=True)
n = len([x for x in os.listdir(DIR) if x.endswith(".pdf")])
print(f"Mother Earth done: {stats}  FINAL pdfs/mother-earth: {n} PDFs", flush=True)
