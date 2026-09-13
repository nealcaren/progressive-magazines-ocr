"""Detect the IA 'two same-size grayscale layers' PDF signature that caused the
faded-image bug (extract_page_image grabbed one faded layer instead of rendering
the composite). Samples a few pages of one PDF per title.

    python detect_layered_pdfs.py [PDF_ROOT]
PDF_ROOT contains <title>/*.pdf (Longleaf pdfs layout).
"""
import sys, glob, os
import pymupdf

ROOT = sys.argv[1] if len(sys.argv) > 1 else "/work/users/n/c/ncaren/progressive-magazines-ocr/pdfs"

titles = sorted({p.split("/")[-2] for p in glob.glob(f"{ROOT}/*/*.pdf")})
if not titles:  # flat layout fallback
    titles = ["(flat)"]

print("%-20s %-8s %s" % ("title", "layered", "sample (pages: #imgs, dup-size?)"))
for t in titles:
    pdfs = sorted(glob.glob(f"{ROOT}/{t}/*.pdf")) if t != "(flat)" else sorted(glob.glob(f"{ROOT}/*.pdf"))
    if not pdfs:
        continue
    d = pymupdf.open(pdfs[0])
    notes = []
    layered = False
    for i in range(min(4, d.page_count)):
        sizes = []
        for im in d[i].get_images(full=True):
            px = pymupdf.Pixmap(d, im[0]); sizes.append((px.width, px.height))
        dup = len(sizes) >= 2 and len(set(sizes)) < len(sizes)
        if dup:
            layered = True
        notes.append(f"p{i}:{len(sizes)}img{'/DUP' if dup else ''}")
    d.close()
    print("%-20s %-8s %s  [%s]" % (t, "YES" if layered else "no", " ".join(notes), os.path.basename(pdfs[0])))
