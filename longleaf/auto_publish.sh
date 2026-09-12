#!/bin/bash
# Auto-publish the progressive-magazines archive once OCR array job finishes.
# MUST run on the Longleaf LOGIN node: compute nodes have no outbound internet,
# so the R2 sync can only happen here. Launch detached:
#   nohup bash auto_publish.sh > auto_publish.log 2>&1 &
set -u
JOB=${1:-835022}
WORK=/work/users/n/c/ncaren
REPO=$WORK/progressive-magazines-ocr
DEPLOY=$WORK/deploy_all
PREFIX=https://pages.dangerouspress.org/progressive-magazines
BUCKET=s3://african-american-press-archive/progressive-magazines
log(){ echo "[$(date '+%F %T')] $*"; }

log "watcher start; waiting for OCR job $JOB to leave the queue"
while [ -n "$(squeue -h -j "$JOB" 2>/dev/null)" ]; do
  sleep 300
done
log "OCR job $JOB no longer in queue"

cd "$REPO" || { log "FATAL: no repo"; exit 1; }
staged=$(find pdfs -name '*.pdf' | wc -l)
done=$(find site -name index.html 2>/dev/null | wc -l)
log "coverage: $done issues have index.html vs $staged PDFs staged"

module purge
module load anaconda/2024.02
eval "$(conda shell.bash hook)"
conda activate "$WORK/envs/progmag-publish"
export PYTHONUTF8=1

log "build_iiif over site/ -> $DEPLOY (incremental; reuses existing tiles)"
python build_iiif.py site --out "$DEPLOY" --prefix "$PREFIX"
rc=$?
log "build_iiif exit=$rc; deploy tiles=$(find "$DEPLOY/iiif" -maxdepth 1 -type d 2>/dev/null | wc -l)"
[ $rc -ne 0 ] && { log "FATAL: build_iiif failed; not syncing"; exit 1; }

source ~/.r2env
export AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
log "aws s3 sync $DEPLOY -> $BUCKET (only new/changed objects)"
aws s3 sync "$DEPLOY/" "$BUCKET/" --endpoint-url "$R2_ENDPOINT_URL" --only-show-errors
log "aws sync exit=$?"

mags=$(find "$DEPLOY" -maxdepth 1 -mindepth 1 -type d ! -name iiif | wc -l)
log "AUTO-PUBLISH COMPLETE — $mags magazines live at $PREFIX/index.html"
