# /// script
# requires-python = ">=3.11"
# dependencies = ["pymupdf"]
# ///
"""
Split HathiTrust annual volumes of The Forerunner (Charlotte Perkins Gilman)
into monthly issue PDFs for the OCR pipeline.

Each volume is 12 monthly issues of 28 pages, starting at PDF page 10 (the
January cover — pages 1-9 are HathiTrust/Google front matter). Covers carry a
unique "A MONTHLY MAGAZINE / AUTHOR, OWNER & PUBLISHER / COPYRIGHT ... C.P.
GILMAN" masthead, which confirmed the regular 28-page cadence.

    uv run split_forerunner.py \
      1912:/path/mdp-...911875.pdf 1913:/path/mdp-...168648.pdf \
      --out pdfs/the-forerunner
"""
import sys, argparse
from pathlib import Path
import pymupdf

FIRST_COVER = 10          # 1-indexed PDF page of the January cover
ISSUE_LEN = 28            # pages per monthly issue
MONTHS = 12


def split(year, src, out):
    d = pymupdf.open(src)
    n = len(d)
    starts = [FIRST_COVER - 1 + ISSUE_LEN * k for k in range(MONTHS)]  # 0-indexed
    made = []
    for k, s in enumerate(starts):
        e = starts[k + 1] - 1 if k + 1 < MONTHS else n - 1   # last issue runs to end
        e = min(e, n - 1)
        sub = pymupdf.open()
        sub.insert_pdf(d, from_page=s, to_page=e)
        name = f"the-forerunner_{year}-{k+1:02d}.pdf"
        sub.save(str(out / name))
        made.append((name, e - s + 1))
    d.close()
    return made


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("volumes", nargs="+", help="YEAR:/path/to/volume.pdf")
    ap.add_argument("--out", default="pdfs/the-forerunner")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    total = 0
    for v in a.volumes:
        year, path = v.split(":", 1)
        made = split(year, path, out)
        total += len(made)
        print(f"{year}: {len(made)} issues", flush=True)
        for name, np in made:
            print(f"    {name}  ({np}p)", flush=True)
    print(f"Done. {total} issue PDFs -> {out}", flush=True)
