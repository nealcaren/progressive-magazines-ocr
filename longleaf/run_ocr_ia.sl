#!/bin/bash
#SBATCH -J ia_reocr
#SBATCH --array=0-9
#SBATCH -n 1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32g
#SBATCH -t 1-00:00:00
#SBATCH -p a100-gpu,l40-gpu
#SBATCH --qos=gpu_access
#SBATCH --gres=gpu:1
#SBATCH -o ia_reocr_%A_%a.out
#SBATCH -e ia_reocr_%A_%a.err

# Re-OCR ONLY the three IA-sourced titles after rebuilding them from crisp JP2s
# (Woman's Journal, Poetry, Mother Earth). Each array task processes a 1/N shard.
# Their site/<mag> dirs must be cleared first so nothing is skipped.
#   sbatch run_ocr_ia.sl

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
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

cd $REPO
for name in womans-journal poetry mother-earth; do
    echo "=== $name (shard $SLURM_ARRAY_TASK_ID/$SLURM_ARRAY_TASK_COUNT) ==="
    python ocr_newspapers.py \
        --input-dir "pdfs/$name" --output-dir "site/$name" \
        --shard "$SLURM_ARRAY_TASK_ID/$SLURM_ARRAY_TASK_COUNT"
done
echo "shard $SLURM_ARRAY_TASK_ID done at $(date)"
