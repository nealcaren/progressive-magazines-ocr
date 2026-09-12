#!/usr/bin/env python3
"""Fetch missing The Crisis issues (1911 + 1913-02..12) from the Modernist
Journals Project / Brown Digital Repository. Each issue is an IIIF page set;
we pull full-res page JPEGs in manifest order and assemble a per-issue PDF.

  python fetch_crisis_mjp.py            # all target issues
  python fetch_crisis_mjp.py --limit 1  # test: just the first target
"""
import io, os, re, sys, time, json, argparse, urllib.request
from PIL import Image

REPO = "/work/users/n/c/ncaren/progressive-magazines-ocr"
DIR = f"{REPO}/pdfs/the-crisis"
API = "https://repository.library.brown.edu/api/items/{}/"
MANIFEST = "https://repository.library.brown.edu/iiif/presentation/{}/manifest.json"
os.makedirs(DIR, exist_ok=True)

def get(url, timeout=180, tries=3):
    for t in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research OCR archive)"})
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:
            if t == tries - 1:
                raise
            time.sleep(2)

def wanted(ym):  # 'YYYY-MM'
    return ym[:4] in ("1911", "1912", "1913")   # full run, MJP quality

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    page = get("https://modjourn.org/journal/crisis/").decode("utf-8", "ignore")
    issue_pids = sorted(set(re.findall(r"issue/(bdr\d+)", page)))
    print(f"{len(issue_pids)} issues on MJP journal page", flush=True)

    targets = []
    for pid in issue_pids:
        pidc = pid.replace("bdr", "bdr:")
        try:
            meta = json.loads(get(API.format(pidc)))
            ym = (meta.get("mods_dateIssued_ssim") or [""])[0]
        except Exception as e:
            print(f"  meta fail {pid}: {e}", flush=True); continue
        if ym and wanted(ym):
            targets.append((ym, pidc))
    targets.sort()
    print(f"{len(targets)} target issues (1911-1913): {[t[0] for t in targets]}", flush=True)
    if a.limit:
        targets = targets[:a.limit]

    stats = {"ok": 0, "skip": 0, "fail": 0}
    for ym, pidc in targets:
        out = f"{DIR}/crisis-{ym}.pdf"
        if os.path.exists(out) and os.path.getsize(out) > 20000:
            stats["skip"] += 1; continue
        try:
            man = json.loads(get(MANIFEST.format(pidc)))
            canvases = man["sequences"][0]["canvases"]
            urls = [c["images"][0]["resource"]["@id"] for c in canvases]
            imgs = []
            for u in urls:
                imgs.append(Image.open(io.BytesIO(get(u))).convert("RGB"))
            imgs[0].save(out, "PDF", save_all=True, append_images=imgs[1:])
            stats["ok"] += 1
            print(f"  {ym}: {len(imgs)} pages -> {os.path.basename(out)}", flush=True)
        except Exception as e:
            stats["fail"] += 1; print(f"  FAIL {ym} {pidc}: {e}", flush=True)
        time.sleep(0.3)
    print(f"Crisis MJP done: {stats}", flush=True)

if __name__ == "__main__":
    main()
