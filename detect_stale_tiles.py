#!/usr/bin/env python3
"""Find issues whose page images no longer match their tiled info.json dims.

build_iiif is incremental (reuses a page's tiles when its info.json exists), so
re-extracting or rotating a page image leaves STALE tiles showing the old image.
This flags those titles so you know which to `build_iiif.py site/<title> … --force`.

    python detect_stale_tiles.py [SITE_DIR] [DEPLOY_DIR]
Defaults to the Longleaf publish layout.
"""
import json, glob, os, sys
from collections import defaultdict
from PIL import Image
Image.MAX_IMAGE_PIXELS = None

SITE = sys.argv[1] if len(sys.argv) > 1 else "/work/users/n/c/ncaren/progressive-magazines-ocr/site"
DEPLOY = sys.argv[2] if len(sys.argv) > 2 else "/work/users/n/c/ncaren/deploy_all"
IIIF = os.path.join(DEPLOY, "iiif")

bad = defaultdict(int); tot = defaultdict(int); missing = defaultdict(int)
for img in glob.glob(f"{SITE}/*/*/images/page_*.jpg"):
    parts = img.split("/")
    mag, iss = parts[-4], parts[-3]
    pg = os.path.basename(img).replace("page_", "").replace(".jpg", "")
    tot[mag] += 1
    info = f"{IIIF}/{iss}_page_{pg}/info.json"
    if not os.path.exists(info):
        missing[mag] += 1; continue
    try:
        d = json.load(open(info)); tw, th = d.get("width"), d.get("height")
        with Image.open(img) as im:
            iw, ih = im.size
        if (iw, ih) != (tw, th):
            bad[mag] += 1
    except Exception:
        bad[mag] += 1

print("%-20s %6s %6s %8s" % ("title", "pages", "stale", "notiled"))
for m in sorted(tot):
    print("%-20s %6d %6d %8d" % (m, tot[m], bad[m], missing[m]))
print("NEEDS_RETILE:", [m for m in sorted(tot) if bad[m] or missing[m]])
