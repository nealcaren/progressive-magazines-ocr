#!/bin/bash
#SBATCH -J progmag_ocr
#SBATCH --array=0-9
#SBATCH -n 1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32g
#SBATCH -t 1-00:00:00
#SBATCH -p a100-gpu,l40-gpu
#SBATCH --qos=gpu_access
#SBATCH --gres=gpu:1
#SBATCH -o ocr_%A_%a.out
#SBATCH -e ocr_%A_%a.err

# Array OCR over ALL magazines staged under $REPO/pdfs/<magazine>/.
# Each of the N array tasks processes a 1/N shard of every magazine's PDFs and
# writes to site/<magazine>/<issue>/. Issues with an index.html are skipped, so
# resubmitting fills any gaps (e.g. after a 6h A100 timeout).
#
#   sbatch run_ocr_array.sl              # 10 tasks (array 0-9)
#   sbatch --array=0-19 run_ocr_array.sl # more parallelism
#
# Follows the unc-hpc skill: flexible GPU partition, gpu_access QOS, HF cache on
# /work. Smoke-test one shard interactively first (srun ... --pty bash).

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
export PYTHONUTF8=1   # Longleaf locale is ASCII; force UTF-8 file I/O (em-dashes etc.)

echo "Task $SLURM_ARRAY_TASK_ID/$SLURM_ARRAY_TASK_COUNT on $(hostname) at $(date)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

cd $REPO
for mag in pdfs/*/; do
    name=$(basename "$mag")
    echo "=== $name (shard $SLURM_ARRAY_TASK_ID/$SLURM_ARRAY_TASK_COUNT) ==="
    python ocr_newspapers.py \
        --input-dir "pdfs/$name" --output-dir "site/$name" \
        --shard "$SLURM_ARRAY_TASK_ID/$SLURM_ARRAY_TASK_COUNT"
done

echo "Done at $(date)"
