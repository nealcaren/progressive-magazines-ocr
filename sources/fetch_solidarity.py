#!/usr/bin/env python3
"""Download Solidarity (IWW eastern organ, marxists.org) for 1911-1913 straight
into the Longleaf pdfs/solidarity dir. Idempotent (skips valid PDFs present)."""
import os, re, time, urllib.request

REPO = "/work/users/n/c/ncaren/progressive-magazines-ocr"
DIR = f"{REPO}/pdfs/solidarity"
BASE = "https://www.marxists.org/history/usa/pubs/solidarity-iww"
os.makedirs(DIR, exist_ok=True)
MONTHS = {m: f"{i:02d}" for i, m in enumerate(
    ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"], 1)}

def get(url, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research OCR archive)"})
    return urllib.request.urlopen(req, timeout=timeout).read()

index = get(f"{BASE}/index.htm").decode("latin-1")
# only the 1911/ 1912/ 1913/ subfolders
hrefs = sorted(set(re.findall(r'href="((?:1911|1912|1913)/[^"]+\.pdf)"', index)))
stats = {"ok": 0, "skip": 0, "fail": 0, "nodate": 0}
for href in hrefs:
    fn = href.split("/")[-1]
    m = re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)-?(\d{1,2})-(19\d\d)", fn.lower())
    if not m:
        stats["nodate"] += 1
        print(f"  NODATE {fn}", flush=True)
        continue
    mon, day, year = MONTHS[m.group(1)], int(m.group(2)), m.group(3)
    out = f"{DIR}/solidarity_{year}-{mon}-{day:02d}.pdf"
    if os.path.exists(out) and os.path.getsize(out) > 10000:
        with open(out, "rb") as f:
            if f.read(4) == b"%PDF":
                stats["skip"] += 1
                continue
    try:
        data = get(f"{BASE}/{href}")
        if data[:4] != b"%PDF":
            raise ValueError("not-pdf")
        with open(out, "wb") as f:
            f.write(data)
        stats["ok"] += 1
    except Exception as e:
        stats["fail"] += 1
        print(f"  FAIL {href}: {e}", flush=True)
    time.sleep(0.3)

n = len([x for x in os.listdir(DIR) if x.endswith(".pdf")])
print(f"Solidarity done: {stats}  FINAL pdfs/solidarity: {n} PDFs", flush=True)
