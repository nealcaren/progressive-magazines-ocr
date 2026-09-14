#!/bin/bash
#SBATCH -J progmag_fix
#SBATCH --array=0-9
#SBATCH -n 1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32g
#SBATCH -t 1-00:00:00
#SBATCH -p a100-gpu,l40-gpu
#SBATCH --qos=gpu_access
#SBATCH --gres=gpu:1
#SBATCH -o fix_%A_%a.out
#SBATCH -e fix_%A_%a.err

# Remediation re-OCR: the 4 broken magazines (fresh, site dirs pre-deleted) plus
# Mother Earth's new 1911/1913 issues (skip-existing keeps the 12 already done).
# Per-magazine rotation fixes sideways scans.

WORK=/work/users/n/c/ncaren
REPO=$WORK/progressive-magazines-ocr
ENV=$WORK/envs/progressive-magazines-ocr

module purge
module load anaconda/2024.02
eval "$(conda shell.bash hook)"
conda activate $ENV

export HF_HOME=$WORK/hf_cache
export TMPDIR=$WORK/tmp
export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
export PYTHONUTF8=1

echo "Task $SLURM_ARRAY_TASK_ID/$SLURM_ARRAY_TASK_COUNT on $(hostname) at $(date)"
cd $REPO

# magazine : clockwise-rotation degrees
run() {
  local mag=$1 rot=$2
  echo "=== $mag (rotate $rot, shard $SLURM_ARRAY_TASK_ID/$SLURM_ARRAY_TASK_COUNT) ==="
  python ocr_newspapers.py \
    --input-dir "pdfs/$mag" --output-dir "site/$mag" \
    --rotate "$rot" \
    --shard "$SLURM_ARRAY_TASK_ID/$SLURM_ARRAY_TASK_COUNT"
}

run the-crisis 0
run progressive-woman 0
run industrial-worker 0
run appeal-to-reason 0
run mother-earth 0

echo "Done at $(date)"
