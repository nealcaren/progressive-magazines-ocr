#!/bin/bash
#SBATCH -J progmag_ocr
#SBATCH -n 1
#SBATCH --cpus-per-task=12
#SBATCH --mem=48g
#SBATCH -t 1-00:00:00
#SBATCH -p a100-gpu,l40-gpu
#SBATCH --qos=gpu_access
#SBATCH --gres=gpu:1
#SBATCH -o ocr_%j.out
#SBATCH -e ocr_%j.err

# Usage:
#   sbatch run_ocr.sl woman-rebel
#   sbatch run_ocr.sl appeal-to-reason
#
# The argument is the name of a subfolder under $REPO/pdfs/ that holds the PDFs
# for one collection. Output goes to $REPO/site/<collection>/.

COLLECTION=${1:-woman-rebel}

WORK=/work/users/n/c/ncaren
REPO=$WORK/progressive-magazines-ocr
ENV_PREFIX=$WORK/envs/progressive-magazines-ocr

module purge
module load anaconda/2024.02
eval "$(conda shell.bash hook)"
conda activate $ENV_PREFIX

export HF_HOME=$WORK/hf_cache
export PIP_CACHE_DIR=$WORK/pip_cache
export TMPDIR=$WORK/tmp
export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

echo "Job $SLURM_JOB_ID on $(hostname) at $(date)"
echo "Collection: $COLLECTION"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

cd $REPO
python ocr_newspapers.py \
    --input-dir  "pdfs/$COLLECTION" \
    --output-dir "site/$COLLECTION"

echo "Done at $(date)"
