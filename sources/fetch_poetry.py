#!/usr/bin/env python3
"""Poetry (Harriet Monroe, Chicago) 1912-1913 from Internet Archive -> Longleaf
pdfs/poetry. Monthly; idempotent."""
import json, os, re, urllib.request
REPO = "/work/users/n/c/ncaren/progressive-magazines-ocr"
DIR = f"{REPO}/pdfs/poetry"
os.makedirs(DIR, exist_ok=True)
def get(url, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research OCR archive)"})
    return urllib.request.urlopen(req, timeout=timeout).read()
ids = []
for y in ("1912", "1913"):
    docs = json.loads(get(f"https://archive.org/advancedsearch.php?q=identifier:sim_poetry_{y}*&fl[]=identifier&rows=200&output=json"))["response"]["docs"]
    ids += [d["identifier"] for d in docs if "index" not in d["identifier"]]
stats = {"ok": 0, "skip": 0, "fail": 0}
for iid in sorted(ids):
    m = re.search(r"(19\d\d-\d\d)", iid)
    out = f"{DIR}/poetry_{m.group(1)}.pdf"
    if os.path.exists(out) and os.path.getsize(out) > 10000 and open(out, "rb").read(4) == b"%PDF":
        stats["skip"] += 1; continue
    try:
        data = get(f"https://archive.org/download/{iid}/{iid}.pdf")
        if data[:4] != b"%PDF": raise ValueError("not-pdf")
        open(out, "wb").write(data); stats["ok"] += 1
    except Exception as e:
        stats["fail"] += 1; print(f"  FAIL {iid}: {e}", flush=True)
n = len([x for x in os.listdir(DIR) if x.endswith(".pdf")])
print(f"Poetry done: {stats}  FINAL pdfs/poetry: {n} PDFs", flush=True)
