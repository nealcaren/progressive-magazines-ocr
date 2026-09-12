#!/usr/bin/env python3
"""Download Woman's Journal (Internet Archive) + Progressive Woman (marxists.org)
for 1911-1913 straight into the Longleaf pdfs/ staging dirs. Idempotent: skips
files already present with a valid %PDF header."""
import json, os, re, subprocess, sys, urllib.request

REPO = "/work/users/n/c/ncaren/progressive-magazines-ocr"
WJ_DIR = f"{REPO}/pdfs/womans-journal"
PW_DIR = f"{REPO}/pdfs/progressive-woman"
os.makedirs(WJ_DIR, exist_ok=True)
os.makedirs(PW_DIR, exist_ok=True)

def get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research OCR archive)"})
    return urllib.request.urlopen(req, timeout=timeout).read()

def fetch_pdf(url, out):
    if os.path.exists(out) and os.path.getsize(out) > 10000:
        with open(out, "rb") as f:
            if f.read(4) == b"%PDF":
                return "skip"
    try:
        data = get(url, timeout=300)
    except Exception as e:
        return f"FAIL {e}"
    if data[:4] != b"%PDF":
        return "FAIL not-pdf"
    with open(out, "wb") as f:
        f.write(data)
    return "ok"

# --- Woman's Journal from Internet Archive: 1911, 1912, 1913 ---
wj = {"ok": 0, "skip": 0, "fail": 0}
for year in ("1911", "1912", "1913"):
    api = (f"https://archive.org/advancedsearch.php?q=identifier:sim_the-womans-journal_{year}*"
           f"&fl[]=identifier&rows=200&output=json")
    docs = json.loads(get(api))["response"]["docs"]
    ids = sorted(d["identifier"] for d in docs if "index" not in d["identifier"])
    for iid in ids:
        m = re.search(r"(19\d\d-\d\d-\d\d)", iid)
        if not m:
            continue
        out = f"{WJ_DIR}/womans-journal_{m.group(1)}.pdf"
        r = fetch_pdf(f"https://archive.org/download/{iid}/{iid}.pdf", out)
        key = "ok" if r == "ok" else ("skip" if r == "skip" else "fail")
        wj[key] += 1
        if key == "fail":
            print(f"  WJ {iid}: {r}", flush=True)
    print(f"Woman's Journal {year}: {len(ids)} issues processed", flush=True)
print(f"Woman's Journal totals: {wj}", flush=True)

# --- Progressive Woman 1913 from marxists.org (7 new; 1911/1912 already staged) ---
base = "https://www.marxists.org/history/usa/pubs/socialist-woman"
index = get(f"{base}/index.htm").decode("latin-1")
pw = {"ok": 0, "skip": 0, "fail": 0}
for fn in sorted(set(re.findall(r'href="(13\d{4}-progressivewoman[^"]*\.pdf)"', index))):
    code = fn[:6]                      # 13MMDD-ish (YYMMDD, DD often 00)
    yy, mm = code[:2], code[2:4]
    out = f"{PW_DIR}/progressive-woman_19{yy}-{mm}.pdf"
    r = fetch_pdf(f"{base}/{fn}", out)
    key = "ok" if r == "ok" else ("skip" if r == "skip" else "fail")
    pw[key] += 1
    if key == "fail":
        print(f"  PW {fn}: {r}", flush=True)
print(f"Progressive Woman 1913: {pw}", flush=True)

# --- final counts ---
for name, d in (("womans-journal", WJ_DIR), ("progressive-woman", PW_DIR)):
    n = len([x for x in os.listdir(d) if x.endswith(".pdf")])
    print(f"FINAL pdfs/{name}: {n} PDFs", flush=True)
