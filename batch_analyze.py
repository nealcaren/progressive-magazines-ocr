# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""
Run analyze_issue's TEXT pass over many issues via the OpenRouter Batch API
(half price, async, text-only). Best for the dense newspapers where the image
passes aren't needed. Submits chunked batches, polls to completion, and writes
each issue's toc.json — original OCR untouched.

    uv run batch_analyze.py site/solidarity/*/ site/womans-journal/*/ --kind newspaper
    uv run batch_analyze.py <issue_dirs...> --model google/gemini-3.8-flash:batch --chunk 50

Needs OPENROUTER_API_KEY. Each issue dir must contain full_text.json.
"""
import os, sys, json, time, argparse
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_issue as A

BASE = "https://openrouter.ai/api/beta/batches"
FILES = "https://openrouter.ai/api/v1/files"
KEY = os.environ.get("OPENROUTER_API_KEY") or sys.exit("OPENROUTER_API_KEY not set")
H = {"Authorization": f"Bearer {KEY}"}


def build_requests(dirs, kind, single_author):
    reqs, cmap = [], {}
    for i, d in enumerate(dirs):
        pages = A.load_pages(d)
        ptoc, _ = A.recover_contents(pages, d, None, allow_image=False)  # text-only
        prompt = A.build_prompt(pages, ptoc, single_author, kind)
        cid = f"iss{i:04d}"
        cmap[cid] = str(d)
        reqs.append({"custom_id": cid, "body": {
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}}})
    return reqs, cmap


def submit(reqs, model):
    body = {"endpoint": "/v1/chat/completions", "model": model, "requests": reqs}
    r = requests.post(BASE, headers={**H, "Content-Type": "application/json"},
                      data=json.dumps(body), timeout=600)
    if r.status_code not in (200, 202):     # 202 Accepted = batch queued
        raise RuntimeError(f"submit failed {r.status_code}: {r.text[:300]}")
    return r.json()["id"]


def results_of(st):
    if isinstance(st.get("results"), list):
        return st["results"]
    ofid = st.get("output_file_id")
    if ofid:
        txt = requests.get(f"{FILES}/{ofid}/content", headers=H, timeout=180).text
        return [json.loads(l) for l in txt.splitlines() if l.strip()]
    return []


def write_results(st, cmap):
    ok = fail = 0
    for rec in results_of(st):
        d = cmap.get(rec.get("custom_id"))
        if not d:
            continue
        try:
            content = rec["response"]["body"]["choices"][0]["message"]["content"]
            data = A.parse_json(content)
            data["_batch"] = True
            Path(d, "toc.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
            ok += 1
        except Exception as e:
            fail += 1
            print(f"  parse fail {rec.get('custom_id')} ({Path(d).name}): {e}", flush=True)
    return ok, fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--kind", choices=["magazine", "newspaper"], default="newspaper")
    ap.add_argument("--single-author", default=None)
    ap.add_argument("--model", default="google/gemini-3.8-flash:batch")
    ap.add_argument("--chunk", type=int, default=50)
    ap.add_argument("--state", default="batch_state.json")
    ap.add_argument("--fetch", action="store_true", help="poll+write batches from --state; do NOT submit")
    a = ap.parse_args()

    if a.fetch:
        s = json.loads(Path(a.state).read_text())
        batches, cmap = s["batches"], s["map"]
        print(f"fetch mode: {len(batches)} batches, {len(cmap)} issues", flush=True)
    else:
        dirs = [Path(d) for d in a.dirs if (Path(d) / "full_text.json").exists()]
        print(f"{len(dirs)} issues with OCR", flush=True)
        reqs, cmap = build_requests(dirs, a.kind, a.single_author)
        batches = []
        for i in range(0, len(reqs), a.chunk):
            bid = submit(reqs[i:i + a.chunk], a.model)
            batches.append(bid)
            print(f"submitted {bid} ({len(reqs[i:i+a.chunk])} reqs)", flush=True)
        Path(a.state).write_text(json.dumps({"batches": batches, "map": cmap}, indent=2))

    pending = set(batches); tot_ok = tot_fail = 0
    while pending:
        time.sleep(30)
        for bid in list(pending):
            try:
                st = requests.get(f"{BASE}/{bid}", headers=H, timeout=60).json()
            except Exception:
                continue
            status = st.get("status")
            if status == "completed":
                pending.discard(bid)
                ok, fail = write_results(st, cmap)
                tot_ok += ok; tot_fail += fail
                print(f"[{bid}] completed -> {ok} toc.json ({fail} failed). {len(pending)} batches left.", flush=True)
            elif status in ("failed", "expired", "cancelled"):
                pending.discard(bid)
                print(f"[{bid}] {status}: {st.get('request_counts')}", flush=True)
    print(f"ALL DONE: {tot_ok} toc.json written, {tot_fail} failures.", flush=True)


if __name__ == "__main__":
    main()
