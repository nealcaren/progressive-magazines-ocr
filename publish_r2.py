# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3"]
# ///
"""
Publish an OCR review site to Cloudflare R2 (page images) + a light deploy
directory (HTML/JSON/MD only). Reuses the dangerouspress R2 bucket.

Split, like the dangerouspress process/publish flow:
  * page images  -> R2 under  <prefix>/<issue>/page_NN.jpg
  * HTML/JSON/MD -> --deploy-dir, with image refs rewritten to the R2 base
    (no images copied, so the deploy dir is tiny and safe to commit)

Credentials come from the environment (source ~/.r2env first):
  R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY

Usage:
  source ~/.r2env
  uv run publish_r2.py site/masses \
      --deploy-dir ~/Dropbox/save-the-masses-public/read \
      --prefix save-the-masses \
      --public-base https://pages.dangerouspress.org \
      --title "The Masses — Read the Issues"

  # re-deploy HTML only (no re-upload):   --skip-images
  # upload images only (no deploy):       --skip-deploy
"""

import os, sys, json, shutil, argparse
from pathlib import Path

R2_BUCKET = "african-american-press-archive"


def s3_client():
    import boto3
    endpoint = os.environ.get("R2_ENDPOINT_URL")
    if not endpoint:
        sys.exit("ERROR: R2_ENDPOINT_URL not set. Run: source ~/.r2env")
    try:
        return boto3.client(
            "s3", endpoint_url=endpoint,
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"])
    except KeyError as e:
        sys.exit(f"ERROR: {e} not set. Run: source ~/.r2env")


def _issues(site):
    return [p for p in sorted(site.iterdir())
            if p.is_dir() and (p / "index.html").exists()]


def upload_images(site, prefix, force=False):
    client = s3_client()
    up = skip = 0
    for issue in _issues(site):
        imgs = issue / "images"
        if not imgs.is_dir():
            continue
        for jpg in sorted(imgs.glob("*.jpg")):
            key = f"{prefix}/{issue.name}/{jpg.name}"
            if not force:
                try:
                    client.head_object(Bucket=R2_BUCKET, Key=key)
                    skip += 1
                    continue
                except Exception:
                    pass
            client.upload_file(str(jpg), R2_BUCKET, key,
                               ExtraArgs={"ContentType": "image/jpeg"})
            up += 1
        print(f"  {issue.name}", flush=True)
    print(f"images: {up} uploaded, {skip} already present", flush=True)


def deploy(site, deploy_dir, prefix, public_base, title):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import build_index
    base = f"{public_base.rstrip('/')}/{prefix}"
    deploy_dir.mkdir(parents=True, exist_ok=True)
    for a in ("viewer.css", "viewer.js"):
        if (site / a).exists():
            shutil.copy(site / a, deploy_dir / a)
    issues = _issues(site)
    for issue in issues:
        dst = deploy_dir / issue.name
        dst.mkdir(parents=True, exist_ok=True)
        for f in issue.iterdir():
            if f.suffix == ".html":
                # OSD IMG_URL, hOCR title, and per-issue thumbnails all use the
                # relative token "images/page_NN.jpg"; point them at R2.
                text = f.read_text().replace(
                    "images/page_", f"{base}/{issue.name}/page_")
                (dst / f.name).write_text(text)
            elif f.suffix in (".json", ".md"):
                shutil.copy(f, dst / f.name)
            # images/ dir is intentionally skipped
    build_index.build(deploy_dir, title=title, image_base=base)
    print(f"deployed {len(issues)} issues -> {deploy_dir}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Publish OCR site to R2 + deploy dir")
    ap.add_argument("site", help="Local review site dir (holds issue subfolders)")
    ap.add_argument("--deploy-dir", required=True, help="Where to write light HTML/JSON copy")
    ap.add_argument("--prefix", default="save-the-masses", help="R2 key prefix")
    ap.add_argument("--public-base", default="https://pages.dangerouspress.org",
                    help="Public base URL that fronts the R2 bucket")
    ap.add_argument("--title", default="The Masses — Read the Issues")
    ap.add_argument("--skip-images", action="store_true", help="Don't upload images")
    ap.add_argument("--skip-deploy", action="store_true", help="Don't write deploy dir")
    ap.add_argument("--force", action="store_true", help="Re-upload images already on R2")
    args = ap.parse_args()

    site = Path(args.site).expanduser().resolve()
    if not _issues(site):
        sys.exit(f"No processed issues found in {site}")

    if not args.skip_images:
        upload_images(site, args.prefix, force=args.force)
    if not args.skip_deploy:
        deploy(site, Path(args.deploy_dir).expanduser(), args.prefix,
               args.public_base, args.title)
    print("Done.", flush=True)
