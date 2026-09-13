#!/usr/bin/env python3
"""Rebuild IA-sourced titles from the crisp JP2 derivatives instead of IA's faded
'Text PDF'. IA's downloadable PDFs for these serials are washed-out (Poetry &
Woman's Journal embed two same-size layers; Mother Earth is faded across all
layers), which degraded both the display tiles and the OCR. The 'Single Page
Processed JP2 ZIP' is the crisp source the IA viewer shows.

For each issue: download its _jp2.zip, decode the JP2s to JPEG (via pyvips), and
assemble a clean one-image-per-page PDF that the normal OCR pipeline can ingest.

    python fetch_ia_jp2.py womans-journal 1911 1912 1913
    python fetch_ia_jp2.py poetry 1912 1913
    python fetch_ia_jp2.py mother-earth        # whole run (date-filtered below)

Run on the Longleaf LOGIN node (needs outbound internet). Requires pyvips + PIL.
Overwrites pdfs/<title>/<existing-name>.pdf in place (same names -> OCR dirs match).
"""
import io, os, re, sys, json, zipfile, tempfile, urllib.parse, urllib.request

REPO = os.environ.get("PMREPO", "/work/users/n/c/ncaren/progressive-magazines-ocr")
UA = {"User-Agent": "Mozilla/5.0 (research OCR archive)"}


def get(url, timeout=300):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def meta(identifier):
    return json.loads(get(f"https://archive.org/metadata/{identifier}"))


def dl_file(identifier, name, out_path):
    url = f"https://archive.org/download/{identifier}/" + urllib.parse.quote(name)
    data = get(url)
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


def jp2zip_to_pdf(zip_path, pdf_path):
    """Decode every JP2/JPEG in the zip (page order) and write a one-image-per-page
    PDF. Uses PIL (its build here has JP2/openjpeg support). Re-encodes each page as
    JPEG Q90 so the assembled PDF stays a reasonable size."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    z = zipfile.ZipFile(zip_path)
    names = sorted(n for n in z.namelist() if n.lower().endswith((".jp2", ".jpg", ".jpeg")))
    if not names:
        raise RuntimeError(f"no page images in {zip_path}")
    pages = []
    for n in names:
        im = Image.open(io.BytesIO(z.read(n))).convert("RGB")
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=90)
        pages.append(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))
    pages[0].save(pdf_path, save_all=True, append_images=pages[1:])
    return len(names)


def _search(api):
    """IA advancedsearch with a couple retries — a single flaky response must not
    abort the whole title (that left Woman's Journal at 99/154 once)."""
    last = None
    for _ in range(3):
        try:
            return json.loads(get(api))["response"]["docs"]
        except Exception as e:
            last = e
    print(f"  WARN search failed: {api[:80]}... ({last})", flush=True)
    return []


def issues_womans_journal(years):
    for y in years:
        api = (f"https://archive.org/advancedsearch.php?q=identifier:sim_the-womans-journal_{y}*"
               "&fl[]=identifier&rows=400&output=json")
        for doc in _search(api):
            iid = doc["identifier"]
            m = re.search(r"(\d{4}-\d{2}-\d{2})", iid)
            if m:
                yield iid, f"{iid}_jp2.zip", f"womans-journal_{m.group(1)}.pdf"


def issues_poetry(years):
    for y in years:
        api = (f"https://archive.org/advancedsearch.php?q=identifier:sim_poetry_{y}*"
               "&fl[]=identifier&rows=400&output=json")
        for doc in _search(api):
            iid = doc["identifier"]
            m = re.search(r"(\d{4}-\d{2})", iid)
            if m:
                yield iid, f"{iid}_jp2.zip", f"poetry_{m.group(1)}.pdf"


def issues_mother_earth(years):
    m = meta("mother-earth")
    for f in m.get("files", []):
        n = f.get("name", "")
        if not n.endswith("_jp2.zip"):
            continue
        d = re.search(r"\((\d{4})-(\d{2})\)", n)
        if not d:
            continue
        if years and d.group(1) not in years:
            continue
        yield "mother-earth", n, f"mother-earth-news-{d.group(1)}-{d.group(2)}.pdf"


DISPATCH = {
    "womans-journal": issues_womans_journal,
    "poetry": issues_poetry,
    "mother-earth": issues_mother_earth,
}


def main():
    title = sys.argv[1]
    years = sys.argv[2:]  # optional filter (mother-earth uses default range if given)
    out_dir = f"{REPO}/pdfs/{title}"
    os.makedirs(out_dir, exist_ok=True)
    resume = os.environ.get("RESUME") == "1"
    n_ok = n_fail = n_skip = 0
    for iid, zipname, pdfname in DISPATCH[title](years):
        pdf_out = os.path.join(out_dir, pdfname)
        if resume and os.path.exists(pdf_out) and os.path.getsize(pdf_out) > 100_000:
            n_skip += 1
            continue
        try:
            with tempfile.NamedTemporaryFile(suffix="_jp2.zip", delete=False) as tf:
                zpath = tf.name
            dl_file(iid, zipname, zpath)
            npages = jp2zip_to_pdf(zpath, pdf_out)
            os.unlink(zpath)
            print(f"OK   {pdfname}  ({npages} pp)", flush=True)
            n_ok += 1
        except Exception as e:
            print(f"FAIL {pdfname}: {e}", flush=True)
            n_fail += 1
    print(f"DONE {title}: {n_ok} ok, {n_fail} failed, {n_skip} skipped", flush=True)


if __name__ == "__main__":
    main()
