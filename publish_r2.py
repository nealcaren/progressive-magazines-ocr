# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3"]
# ///
"""
Publish the combined OCR archive to Cloudflare R2 (page images) + a light deploy
directory (HTML/JSON/MD only). Reuses the dangerouspress R2 bucket.

Input is the ARCHIVE ROOT (e.g. site/), which holds one subfolder per magazine,
each of which is a gallery of issue subfolders. Split, like the dangerouspress
process/publish flow:

  * page images  -> R2 under  <prefix>/<issue>/page_NN.jpg   (issue slugs are
    globally unique, so one flat prefix serves the archive AND the game site)
  * everything else -> --deploy-dir, mirroring <magazine>/<issue>/, with image
    refs rewritten to the R2 base. Galleries, the archive landing page, and the
    full-text search index/UI are all regenerated in the deploy dir.

Credentials come from the environment (source ~/.r2env first):
  R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY

Usage:
  source ~/.r2env
  uv run publish_r2.py site --deploy-dir ../progressive-magazines-site
  # re-deploy HTML only (no re-upload):   --skip-images
  # upload images only (no deploy):       --skip-deploy
"""

import os, sys, json, shutil, argparse
from pathlib import Path

R2_BUCKET = "african-american-press-archive"
# Same Cloudflare R2 setup the dangerouspress-ingest repo uploads through.
R2_ENDPOINT = "https://ae978df95b073e0e6a0d595c996900ca.r2.cloudflarestorage.com"
R2_PROFILE = "r2"  # AWS CLI profile in ~/.aws (holds the R2 key/secret)

# Nicer display titles; fall back to slug.title() for anything not listed.
TITLES = {
    "masses": "The Masses",
    "woman-rebel": "The Woman Rebel",
    "the-crisis": "The Crisis",
    "mother-earth": "Mother Earth",
    "appeal-to-reason": "Appeal to Reason",
    "industrial-worker": "Industrial Worker",
    "the-freewoman": "The Freewoman",
}


def title_of(slug):
    return TITLES.get(slug, slug.replace("-", " ").replace("_", " ").title())


def s3_client(profile=R2_PROFILE, endpoint=R2_ENDPOINT):
    """Prefer the AWS CLI 'r2' profile (~/.aws); fall back to R2_* env vars."""
    import boto3
    if os.environ.get("R2_ENDPOINT_URL") and os.environ.get("R2_ACCESS_KEY_ID"):
        return boto3.client(
            "s3", endpoint_url=os.environ["R2_ENDPOINT_URL"],
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"])
    try:
        return boto3.Session(profile_name=profile).client("s3", endpoint_url=endpoint)
    except Exception as e:
        sys.exit(f"ERROR: no R2 credentials (AWS profile '{profile}' or R2_* env): {e}")


def _magazines(root):
    return [d for d in sorted(root.iterdir())
            if d.is_dir() and (d / "manifest.json").exists()]


def _issues(mag):
    return [d for d in sorted(mag.iterdir())
            if d.is_dir() and (d / "index.html").exists()]


def upload_images(root, prefix, force=False):
    client = s3_client()
    up = skip = 0
    for mag in _magazines(root):
        for issue in _issues(mag):
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
            print(f"  {mag.name}/{issue.name}", flush=True)
    print(f"images: {up} uploaded, {skip} already present", flush=True)


def deploy(root, deploy_dir, prefix, public_base, archive_title):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import build_index, build_archive, build_search
    base = f"{public_base.rstrip('/')}/{prefix}"
    deploy_dir.mkdir(parents=True, exist_ok=True)

    for mag in _magazines(root):
        mag_out = deploy_dir / mag.name
        mag_out.mkdir(parents=True, exist_ok=True)
        # viewer assets live at each magazine root (page HTML uses ../viewer.*)
        for a in ("viewer.css", "viewer.js"):
            if (mag / a).exists():
                shutil.copy(mag / a, mag_out / a)
        for issue in _issues(mag):
            dst = mag_out / issue.name
            dst.mkdir(parents=True, exist_ok=True)
            for f in issue.iterdir():
                if f.suffix == ".html":
                    text = f.read_text().replace(
                        "images/page_", f"{base}/{issue.name}/page_")
                    (dst / f.name).write_text(text)
                elif f.suffix in (".json", ".md"):
                    shutil.copy(f, dst / f.name)
                # images/ intentionally skipped (served from R2)
        # regenerate this magazine's gallery with R2 thumbnails
        build_index.build(mag_out, title=title_of(mag.name), image_base=base)

    # archive landing + full-text search over the deploy copy
    build_archive.build(deploy_dir, title=archive_title, image_base=base)
    build_search.build(deploy_dir, title=f"{archive_title} — Search")
    n = len(_magazines(deploy_dir))
    print(f"deployed {n} magazines -> {deploy_dir}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Publish OCR archive to R2 + deploy dir")
    ap.add_argument("root", help="Archive root (holds per-magazine gallery subfolders)")
    ap.add_argument("--deploy-dir", required=True, help="Where to write the light HTML/JSON copy")
    ap.add_argument("--prefix", default="progressive-magazines",
                    help="R2 key prefix (shared; issue slugs are unique)")
    ap.add_argument("--public-base", default="https://pages.dangerouspress.org",
                    help="Public base URL that fronts the R2 bucket")
    ap.add_argument("--archive-title", default="Progressive Magazines — OCR Archive")
    ap.add_argument("--skip-images", action="store_true", help="Don't upload images")
    ap.add_argument("--skip-deploy", action="store_true", help="Don't write deploy dir")
    ap.add_argument("--force", action="store_true", help="Re-upload images already on R2")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not _magazines(root):
        sys.exit(f"No magazine galleries found in {root}")

    if not args.skip_images:
        upload_images(root, args.prefix, force=args.force)
    if not args.skip_deploy:
        deploy(root, Path(args.deploy_dir).expanduser(), args.prefix,
               args.public_base, args.archive_title)
    print("Done.", flush=True)
