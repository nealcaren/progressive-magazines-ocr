#!/bin/bash
# Run this interactively on the Longleaf login node to create the conda
# environment for the progressive-magazines OCR pipeline.
#
#   bash setup_env.sh
#
# This builds a FRESH, self-contained env at $WORK/envs/progressive-magazines-ocr.
# It does NOT touch the dangerouspress `newspaper-ocr` env (that one is
# hand-balanced torch/paddle/CUDA — never pip into it). Same recipe, new prefix.

set -e

WORK=/work/users/n/c/ncaren
ENV_PREFIX=$WORK/envs/progressive-magazines-ocr
HF_CACHE=$WORK/hf_cache

mkdir -p $HF_CACHE

# Redirect pip/tmp to /work to avoid filling the 50GB home directory quota
export PIP_CACHE_DIR=$WORK/pip_cache
export TMPDIR=$WORK/tmp
mkdir -p $PIP_CACHE_DIR $TMPDIR

module purge
module load anaconda/2024.02
eval "$(conda shell.bash hook)"   # enable `conda activate` in non-interactive shells

# Create conda environment on /work (not home) to avoid quota issues
conda create --yes --prefix $ENV_PREFIX python=3.12
conda activate $ENV_PREFIX

# PyTorch with CUDA 12.1
conda install --yes -c pytorch -c nvidia pytorch torchvision pytorch-cuda=12.1

# HF stack + OCR deps (transformers drives GLM-OCR directly; no HF upload needed)
pip install transformers accelerate huggingface_hub pymupdf pillow numpy

# PaddlePaddle GPU (from Chinese index — may need retries)
for i in 1 2 3 4 5; do
    pip install paddlepaddle-gpu==3.0.0 \
        --extra-index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/ \
        && break
    echo "PaddlePaddle install attempt $i failed, retrying in 10s..."
    sleep 10
done

# PaddleX (no-deps to avoid conflicts)
pip install "paddlex[base]" --no-deps

# PaddleX runtime dependencies
pip install GPUtil colorlog chardet filelock pyyaml requests tqdm \
    opencv-contrib-python scikit-learn scikit-image \
    "langchain<1.0.0" pypdfium2 ruamel.yaml modelscope aistudio-sdk ujson \
    pandas shapely pyclipper Cython lmdb openpyxl \
    pycocotools imgaug lap motmetrics filterpy

# Fix torch — PaddlePaddle overwrites MKL libs causing iJIT_NotifyEvent symbol error.
# Pin to a CUDA wheel that matches the conda-installed CUDA toolkit; otherwise the
# default PyPI wheel pulls in CUDA 13 and nvrtc fails to find libnvrtc-builtins.so.13.0.
pip install --force-reinstall --index-url https://download.pytorch.org/whl/cu121 \
    torch torchvision

# Pre-download models to /work so compute nodes don't need internet
export HF_HOME=$HF_CACHE
echo "Downloading GLM-OCR model..."
python -c "
from transformers import AutoProcessor, AutoModelForImageTextToText
AutoProcessor.from_pretrained('zai-org/GLM-OCR')
AutoModelForImageTextToText.from_pretrained('zai-org/GLM-OCR')
print('GLM-OCR downloaded.')
"

echo "Downloading PP-DocLayout_plus-L..."
python -c "
import os
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
try:
    from paddlex import create_model
    model = create_model('PP-DocLayout_plus-L')
    print('PP-DocLayout_plus-L downloaded.')
except ImportError as e:
    if 'libcuda' in str(e):
        print('PP-DocLayout_plus-L weights cached (GPU driver not on login node — will init on compute node).')
    else:
        raise
"

# ─── Separate PUBLISH env (libvips tiling + manifest + R2 upload) ───
# Kept apart from the OCR env on purpose: conda-forge libvips drags in glib/cairo/
# libffi that clash with the hand-built torch/paddle/CUDA stack and break vips.
# build_iiif.py needs no torch/paddle, so a clean env avoids the whole conflict.
echo "Creating publish env (libvips + iiif-prezi3 + boto3 + awscli)..."
conda create --yes --prefix $WORK/envs/progmag-publish -c conda-forge python=3.12 libvips
conda activate $WORK/envs/progmag-publish
pip install iiif-prezi3 boto3 awscli
conda deactivate

echo ""
echo "Setup complete."
echo "  OCR env:     $ENV_PREFIX"
echo "  Publish env: $WORK/envs/progmag-publish"
echo "  HF cache:    $HF_CACHE"
echo ""
echo "Next: stage PDFs in pdfs/<magazine>/, then: sbatch longleaf/run_ocr_array.sl"
echo "After OCR: build_iiif.py + aws s3 sync from the publish env (PYTHONUTF8=1)."
