#!/usr/bin/env python3
"""LLM Benchmark Runner - Ollama"""
import json, time, subprocess, requests, os

OLLAMA_URL = "http://localhost:11434"

QUESTIONS = {
    "logic": {
        "title": "基礎邏輯",
        "prompt": "請用繁體中文回答：一個房間有3盞燈，對應3個在門外的開關，你無法從門外看到燈的狀態。你可以任意操作開關，但只能進入房間一次。請問如何判斷哪個開關控制哪盞燈？"
    },
    "reasoning": {
        "title": "多步推理",
        "prompt": "請用繁體中文回答：有5個嫌疑人，已知：(1)A或B犯案 (2)如果A犯案則C也犯案 (3)如果B犯案則D也犯案 (4)C和D不可能同時犯案 (5)E無罪。請問誰犯案？請列出推理過程。"
    },
    "coding": {
        "title": "程式設計",
        "prompt": "請用繁體中文說明，並用 Python 寫一個函數：輸入一個字串，輸出該字串中所有出現次數超過一次的字元及其出現次數，結果按出現次數由多到少排序。"
    },
    "debugging": {
        "title": "程式除錯",
        "prompt": "請用繁體中文說明以下 Python 程式碼的 bug 並修正：\n```python\ndef fibonacci(n):\n    if n <= 0:\n        return []\n    elif n == 1:\n        return [0]\n    fibs = [0, 1]\n    for i in range(2, n):\n        fibs.append(fibs[i-1] + fibs[i-2])\n    return fibs\nprint(fibonacci(5))  # 預期 [0,1,1,2,3]\n```"
    }
}

CTX_SIZES = [2048, 4096, 8192, 16384, 32768]

def get_gpu_stats():
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.free,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        parts = r.stdout.strip().split(", ")
        if len(parts) >= 4:
            return {"gpu_util": int(parts[0]), "mem_used": int(parts[1]), "mem_free": int(parts[2]), "temp": int(parts[3])}
    except: pass
    return {}

def run_inference(model, prompt, ctx_size, timeout=180):
    start = time.time()
    try:
        response = requests.post(f"{OLLAMA_URL}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "options": {"num_ctx": ctx_size}},
            timeout=timeout)
        data = response.json()
        wall = time.time() - start
        eval_count = data.get("eval_count", 0)
        eval_duration = data.get("eval_duration", 1)
        prompt_eval_count = data.get("prompt_eval_count", 0)
        prompt_eval_duration = data.get("prompt_eval_duration", 1)
        tok_s = eval_count / (eval_duration / 1e9) if eval_duration > 0 else 0
        ttft = prompt_eval_duration / 1e9 if prompt_eval_duration else 0
        return {
            "status": "ok", "response": data.get("response", ""),
            "tokens_per_sec": round(tok_s, 1), "eval_count": eval_count,
            "prompt_eval_count": prompt_eval_count,
            "ttft_s": round(ttft, 2), "wall_time_s": round(wall, 1)
        }
    except requests.exceptions.Timeout:
        return {"status": "timeout", "tokens_per_sec": 0, "eval_count": 0, "wall_time_s": round(time.time() - start, 1)}
    except Exception as e:
        return {"status": "error", "response": str(e), "tokens_per_sec": 0}

def benchmark_model(model):
    print(f"\n{'='*60}")
    print(f"  模型: {model}")
    print(f"{'='*60}")
    results = {}
    for ctx in CTX_SIZES:
        print(f"\n  --- ctx={ctx} ---")
        gpu_before = get_gpu_stats()
        questions = {}
        for qk, qdata in QUESTIONS.items():
            print(f"    [{qdata['title']}] ...", end="", flush=True)
            r = run_inference(model, qdata["prompt"], ctx)
            r["title"] = qdata["title"]
            questions[qk] = r
            print(f" {r['tokens_per_sec']} tok/s | {r['eval_count']} tok | TTFT {r.get('ttft_s',0)}s | wall {r['wall_time_s']}s")
        gpu_after = get_gpu_stats()
        results[str(ctx)] = {"questions": questions, "gpu_before": gpu_before, "gpu_after": gpu_after}
    return results

if __name__ == "__main__":
    import sys
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen3-vl:8b-instruct"
    data = benchmark_model(model)
    outpath = os.path.expanduser(f"~/benchmark_{model.replace(':','_').replace('/','_')}.json")
    with open(outpath, "w") as f:
        json.dump({"model": model, "ctx_results": data}, f, ensure_ascii=False, indent=2)
    print(f"\n結果存至 {outpath}")
