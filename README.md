# Progressive Magazines — OCR

A two-stage OCR pipeline for digitizing early-20th-century progressive and
radical magazines (*The Woman Rebel*, *Mother Earth*, *The Masses*, *Appeal to
Reason*, *The Crisis*, and the rest of the SOCI 274 "Role Readings" set).

It reuses the models and layout post-processing proven on the
[dangerouspress-ocr](../dangerouspress-ocr) project, but is deliberately
smaller: PDFs come from a **local folder** and the review website is written to
a **local folder** — no Hugging Face up/download, no R2. These collections fit
comfortably in a single Longleaf GPU job.

## Pipeline

1. **Layout** — PaddleX `PP-DocLayout_plus-L` detects text regions.
2. **Post-process** — the same chain as dangerouspress: rescue low-confidence
   detections → deduplicate → fill column gaps → column-aware reading order →
   merge adjacent blocks (capped at 600px to avoid OCR timeouts).
3. **OCR** — GLM-OCR, 25s timeout per region with repetition detection + retry.
   Two interchangeable backends: a local MLX server over HTTP (Mac) or
   `zai-org/GLM-OCR` in-process via 🤗 transformers (Longleaf/GPU).
4. **Output** — per issue: an OpenSeadragon review page per page, markdown,
   and JSON with full provenance, plus a per-issue `index.html`. A top-level
   gallery `index.html` + `manifest.json` ties the collection together.

## Layout

```
ocr_newspapers.py      # main pipeline: local PDFs -> review site
build_index.py         # top-level gallery + manifest.json
assets/
  viewer.js            # shared OpenSeadragon viewer (copied into each site root)
  viewer.css
longleaf/
  setup_env.sh         # one-time conda env build on Longleaf (fresh prefix)
  run_ocr.sl           # SLURM batch script, one collection per invocation
pdfs/                  # (gitignored) input PDFs, one subfolder per collection
site/                  # (gitignored) rendered review website
```

## Running locally (Mac)

No conda env needed — `uv` reads the inline dependency block at the top of
`ocr_newspapers.py` and builds a throwaway environment on first run. Layout
(PaddleX) runs on CPU; OCR goes to a local **GLM-OCR MLX server** over HTTP,
exactly as the dangerouspress `process_issue.py` does.

```bash
# 1. Start the OCR server in its own terminal and leave it running.
#    GLM-OCR is a vision-language model, so it is served by mlx_vlm (not mlx_lm):
uv run --with mlx-vlm python -m mlx_vlm.server --model mlx-community/GLM-OCR-bf16 --port 8080

# 2. Run the pipeline (auto-selects the mlx backend on macOS):
uv run ocr_newspapers.py \
    --input-dir  "/path/to/Role Readings/Woman Rebel" \
    --output-dir site/woman-rebel

# rebuild just the gallery after the fact
uv run build_index.py site/woman-rebel
```

Each PDF becomes one issue (subfolder named after the PDF stem). Re-running
skips issues that already have an `index.html`; pass `--force` to redo them.
Use `--recursive` to pull PDFs from nested subfolders.

The backend is chosen by `--backend` (default `auto`): `mlx` on macOS,
`transformers` on Longleaf/GPU. Pass it explicitly to override. If the MLX
server isn't running, the script fails fast with the command to start it.

## Running on Longleaf (recommended)

One-time setup on the **login node**:

```bash
# copy this repo to /work
rsync -av --exclude pdfs --exclude site \
    ./ longleaf:/work/users/n/c/ncaren/progressive-magazines-ocr/

# build the conda env + pre-download models
bash longleaf/setup_env.sh
```

Stage a collection's PDFs and submit:

```bash
# on Longleaf
WORK=/work/users/n/c/ncaren
mkdir -p $WORK/progressive-magazines-ocr/pdfs/woman-rebel
# ...copy the Woman Rebel PDFs into that folder...

cd $WORK/progressive-magazines-ocr/longleaf
sbatch run_ocr.sl woman-rebel        # arg = subfolder name under pdfs/
```

> Smoke-test interactively before the first `sbatch`: `srun` onto a GPU node,
> activate the env, and run `ocr_newspapers.py` on a single PDF to confirm the
> models load before committing a batch job.

Pull the results back:

```bash
rsync -av longleaf:/work/users/n/c/ncaren/progressive-magazines-ocr/site/ ./site/
```

## Adding more collections

Drop a new folder of PDFs under `pdfs/<collection-name>/` and run
`sbatch run_ocr.sl <collection-name>`. Everything else is automatic. The
dangerouspress pipeline logic (`PIPELINE_VERSION = "2025-03-07-col-fix"`) is
copied verbatim into `ocr_newspapers.py`; if that logic is improved upstream,
port the change here too.

## Credits

Models: [PP-DocLayout_plus-L](https://github.com/PaddlePaddle/PaddleX) ·
[GLM-OCR](https://huggingface.co/zai-org/GLM-OCR). Layout post-processing
adapted from the dangerouspress-ocr project.
