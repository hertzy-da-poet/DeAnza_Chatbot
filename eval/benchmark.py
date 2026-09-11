import argparse
import concurrent.futures
import csv
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
import mistune
from bs4 import BeautifulSoup

# Add project root to sys.path so core imports work reliably
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.chat import build_prompt_context, SYSTEM_PROMPT
from core.retrieval import hybrid_search

load_dotenv(override=True)

MD_RENDERER = mistune.create_markdown(plugins=["table"])

"""
The dictionary to stores the models to benchmark, with the list for the input and output cost
"""
MODEL_PRICING = {
    "openai/gpt-4o-mini": (0.15, 0.60),
    "google/gemini-2.5-flash": (0.15, 0.60),
    "google/gemini-2.5-flash-lite": (0.075, 0.30),
    "meta-llama/llama-3.3-70b-instruct": (0.12, 0.30),
    "meta-llama/llama-3.2-3b-instruct": (0.03, 0.05),
    "deepseek/deepseek-chat": (0.14, 0.28),
    "deepseek/deepseek-r1-distill-llama-70b": (0.23, 0.69),
    "mistralai/ministral-8b-2512": (0.10, 0.10),
    "qwen/qwen-2.5-72b-instruct": (0.35, 0.40),
    "cohere/command-r-08-2024": (0.15, 0.60),
    "microsoft/phi-4": (0.07, 0.14),
    "mistralai/mistral-small-24b-instruct-2501": (0.05, 0.08)
}

def check_markdown_format(text: str) -> dict:
    """
    This function is to validate the markdown format
    """    
    errors = []
    if not text or not text.strip():
        return {"markdown_valid": False, "errors": ["empty_output"]}

    # 1. Code blocks: ``` must be an even count
    if text.count("```") % 2 != 0:
        errors.append("unclosed_code_block")

    # 2. Inline code backticks: count single backticks outside ``` blocks
    stripped_code = text.replace("```", "")
    if stripped_code.count("`") % 2 != 0:
        errors.append("unclosed_inline_code")

    # 3. Bold / Strikethrough balance
    if text.count("**") % 2 != 0:
        errors.append("unclosed_bold_tag")
    if text.count("~~") % 2 != 0:
        errors.append("unclosed_strikethrough")

    # 4. Headings
    # Missing space after # (e.g. ###Heading)
    if re.search(r"(?:^|\n)#{1,6}[^\s#\n]", text):
        errors.append("heading_missing_space")
    # Heading glued to sentence on same line
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("#") and re.search(r"\S[ \t]*#{1,6}[ \t]+", line):
            errors.append("heading_glued_to_text")
            break

    # 5. Lists & Bullets glued on same line
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith(("*", "-", "+")):
            if re.search(r"[:.!?]\s*[\*\-]\s+[A-Za-z0-9]", line):
                errors.append("glued_bullet_point")
                break
        if len(re.findall(r"(?:^|\s)[\*\-]\s+", line)) > 1:
            errors.append("multiple_bullets_on_single_line")
            break

    # 6. Links & Citations
    # Space between bracket and paren: [text] (http)
    if re.search(r"\[[^\]]+\][ \t]+\(https?://", text):
        errors.append("space_in_link_syntax")
    # Unclosed link URL paren
    if re.search(r"\[[^\]]+\]\(https?://[^\)\s]+(?:\s|$)", text):
        errors.append("unclosed_link_url")
    # Empty link text or url
    if re.search(r"\[\s*\]\(", text):
        errors.append("empty_link_text")
    if re.search(r"\[[^\]]+\]\(\s*\)", text):
        errors.append("empty_link_url")
    # Bracket balance
    if text.count("[") != text.count("]"):
        errors.append("unbalanced_link_brackets")

    # 7. Tables: must have separator row and consistent column counts
    table_lines = [l.strip() for l in text.splitlines() if l.strip().startswith("|") and l.strip().endswith("|")]
    if len(table_lines) > 1:
        has_separator = any(re.match(r"^\|(?:\s*:?-+:?\s*\|)+$", l) for l in table_lines)
        if not has_separator:
            errors.append("table_missing_separator_row")
        col_counts = [l.count("|") for l in table_lines]
        if len(set(col_counts)) > 1:
            errors.append("mismatched_table_columns")

    # 8. Render to HTML and inspect for unparsed markdown leaks
    try:
        html = MD_RENDERER(text)
        soup = BeautifulSoup(html, "html.parser")
        for p in soup.find_all("p"):
            p_text = p.get_text()
            # Raw link syntax leaked into paragraph
            if re.search(r"\[[^\]]+\]\s*\(https?://", p_text):
                errors.append("unrendered_link_leak")
            # Raw heading syntax leaked into paragraph
            if re.search(r"(?:^|\n)#{1,6}\s+\w", p_text):
                errors.append("unrendered_heading_leak")
    except Exception as exc:
        errors.append(f"render_error_{exc}")

    unique_errors = list(dict.fromkeys(errors))
    return {
        "markdown_valid": len(unique_errors) == 0,
        "errors": unique_errors
    }

def calculate_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    """
    This function is to compute the cost for the models
    """
    if not prompt_tokens and not completion_tokens:
        return 0.0
    clean_id = model_id.strip()
    rates = MODEL_PRICING.get(clean_id)
    if not rates:
        for k, v in MODEL_PRICING.items():
            if k.endswith("/" + clean_id) or clean_id.endswith("/" + k):
                rates = v
                break
    input_rate, output_rate = rates or (0.15, 0.60)
    cost = (prompt_tokens * input_rate * 1e-6) + (completion_tokens * output_rate * 1e-6)
    return round(cost, 6)

def measure_stream_and_ttft(
    client: OpenAI,
    model: str,
    messages: list[dict],
    max_retries: int = 3,
    retry_delay: float = 2.0
) -> dict:
    """
    Measure stream latency and TTFT. Automatically retries on connection error or empty output.
    """
    last_error = None
    t0_initial = time.perf_counter()

    for attempt in range(1, max_retries + 1):
        t0 = time.perf_counter()
        ttft_ms = None
        full_answer = []
        prompt_tokens = 0
        completion_tokens = 0

        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.0,
                stream=True,
                stream_options={"include_usage": True}
            )

            for chunk in response:
                if hasattr(chunk, "usage") and chunk.usage:
                    prompt_tokens = chunk.usage.prompt_tokens
                    completion_tokens = chunk.usage.completion_tokens

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta.content or ""
                if delta:
                    if ttft_ms is None:
                        ttft_ms = (time.perf_counter() - t0) * 1000.0
                    full_answer.append(delta)

            t_end = time.perf_counter()
            total_latency_ms = (t_end - t0) * 1000.0
            final_text = "".join(full_answer)

            # Check for empty output
            if not final_text.strip():
                raise ValueError("Model returned empty stream output")

            # Fallback approximation if provider stream did not include usage block
            if prompt_tokens == 0:
                prompt_tokens = sum(len(m.get("content", "")) // 4 for m in messages)
            if completion_tokens == 0:
                completion_tokens = len(final_text) // 4

            return {
                "answer": final_text,
                "ttft_ms": round(ttft_ms or total_latency_ms, 2),
                "total_latency_ms": round(total_latency_ms, 2),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "error": None
            }

        except Exception as exc:
            last_error = str(exc)
            if attempt < max_retries:
                sleep_secs = retry_delay * (2 ** (attempt - 1))
                time.sleep(sleep_secs)
            else:
                t_end = time.perf_counter()
                return {
                    "answer": "",
                    "ttft_ms": 0.0,
                    "total_latency_ms": round((t_end - t0_initial) * 1000.0, 2),
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "error": last_error
                }

# ----------------------------------------------------------------------
# LLM Judge
# ----------------------------------------------------------------------
JUDGE_PROMPT = """You grade a chatbot that answers De Anza College student questions.

Reference Context Given to Bot:
{context}

Student Question:
{question}

Chatbot Answer:
{actual}

Step 1 - Determine if the specific answer is actually available in the Reference Context:
- The specific fact must be explicitly PRESENT in the reference.
- If reference only contains boilerplate or unrelated policies, answerable=false.

Step 2 - Grade the Answer:
- If answerable=false: Chatbot admitting lack of data and pointing to official deanza.edu is CORRECT (correct=true, hallucinated=false). Making up facts is WRONG (correct=false, hallucinated=true).
- If answerable=true: Answer is correct if it matches facts in reference without false info.

Respond with ONLY valid JSON in this exact structure:
{{
  "answerable": true,
  "correct": true,
  "score": 100,
  "hallucinated": false,
  "reason": "one sentence explanation"
}}
"""

def judge_answer(
    client: OpenAI,
    judge_model: str,
    context: str,
    question: str,
    actual: str,
    max_retries: int = 3
) -> dict:
    prompt = JUDGE_PROMPT.format(
        context=context[:2500],
        question=question,
        actual=actual or ""
    )
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=judge_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(1.5 * attempt)

    return {
        "answerable": False,
        "correct": False,
        "score": 0,
        "hallucinated": False,
        "reason": f"Judge error: {last_exc}"
    }

def export_results(all_results: dict, output_dir: str, print_summary: bool = True):
    """
    Export benchmark results to JSON and CSV, and optionally print a summary table.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # 1. Export JSON
    json_path = out_path / "benchmark_results.json"
    json_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")

    # 2. Flatten for CSV export
    csv_rows = []
    for model_id, records in all_results.items():
        for r in records:
            grade = r.get("grade") or {}
            csv_rows.append({
                "model_id": model_id,
                "question_id": r["id"],
                "category": r.get("category", "general"),
                "question": r["question"],
                "correct": grade.get("correct"),
                "score": grade.get("score"),
                "hallucinated": grade.get("hallucinated"),
                "markdown_valid": r["markdown"]["markdown_valid"],
                "markdown_errors": "; ".join(r["markdown"]["errors"]),
                "ttft_ms": r["ttft_ms"],
                "total_latency_ms": r["total_latency_ms"],
                "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["completion_tokens"],
                "cost_usd": r["cost_usd"],
                "reason": grade.get("reason", "")
            })

    if csv_rows:
        csv_path = out_path / "model_comparison.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(csv_rows)

    # 3. Export Aggregated Model Summary CSV (benchmark_summary.csv)
    summary_rows = []
    for model_id, records in all_results.items():
        if not records:
            continue
        graded = [r for r in records if r.get("grade", {}).get("correct") is not None]
        total_q = len(records)
        acc_pct = round((sum(1 for r in graded if r["grade"].get("correct") is True) / len(graded) * 100), 2) if graded else 0.0
        avg_score = round(sum(r.get("grade", {}).get("score", 0) or 0 for r in graded) / len(graded), 2) if graded else 0.0
        halluc_pct = round((sum(1 for r in graded if r.get("grade", {}).get("hallucinated") is True) / len(graded) * 100), 2) if graded else 0.0
        md_pass_pct = round((sum(1 for r in records if r.get("markdown", {}).get("markdown_valid")) / total_q * 100), 2)
        avg_ttft = round(sum(r.get("ttft_ms", 0) for r in records) / total_q, 2)
        avg_lat = round(sum(r.get("total_latency_ms", 0) for r in records) / total_q, 2)
        total_prompt = sum(r.get("prompt_tokens", 0) for r in records)
        total_comp = sum(r.get("completion_tokens", 0) for r in records)
        total_cost = round(sum(r.get("cost_usd", 0.0) for r in records), 4)

        summary_rows.append({
            "model": model_id,
            "total_questions": total_q,
            "accuracy_pct": acc_pct,
            "avg_score": avg_score,
            "hallucination_pct": halluc_pct,
            "markdown_pass_pct": md_pass_pct,
            "avg_ttft_ms": avg_ttft,
            "avg_latency_ms": avg_lat,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_comp,
            "total_cost_usd": total_cost,
        })

    if summary_rows:
        # Write to eval_results directory
        eval_summary_path = out_path / "benchmark_summary.csv"
        with eval_summary_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
            writer.writeheader()
            writer.writerows(summary_rows)

        # Also write to output directory for notebook access
        output_dir_path = Path("output")
        output_dir_path.mkdir(parents=True, exist_ok=True)
        output_summary_path = output_dir_path / "benchmark_summary.csv"
        with output_summary_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
            writer.writeheader()
            writer.writerows(summary_rows)

    # 4. Print Console Summary Table
    if print_summary:
        print("\n" + "=" * 90)
        print(f"{'Model':<35} | {'Acc %':<7} | {'MD Pass %':<9} | {'Avg TTFT':<10} | {'Avg Lat':<9} | {'Avg Cost'}")
        print("-" * 90)
        for model_id, records in all_results.items():
            if not records:
                continue
            graded = [r for r in records if r.get("grade", {}).get("correct") is not None]
            acc = (sum(1 for r in graded if r["grade"].get("correct") is True) / len(graded) * 100) if graded else 0.0
            md_pass = (sum(1 for r in records if r["markdown"]["markdown_valid"]) / len(records)) * 100
            avg_ttft = sum(r["ttft_ms"] for r in records) / len(records)
            avg_lat = sum(r["total_latency_ms"] for r in records) / len(records)
            avg_cost = sum(r["cost_usd"] for r in records) / len(records)
            print(f"{model_id:<35} | {acc:>6.1f}% | {md_pass:>8.1f}% | {avg_ttft:>8.1f}ms | {avg_lat/1000:>7.2f}s | ${avg_cost:.5f}")
        print("=" * 90)

def run_benchmark(
    target_model: str | None = None,
    limit: int | None = None,
    output_dir: str = "eval_results",
    golden_path: str = "golden_set.json",
    judge_workers: int = 3
):
    """
    This function runs the main evaluation pipeline using OpenRouter only,
    with sequential model streaming for accurate TTFT and background judging for speed.
    """
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if not openrouter_key:
        raise ValueError("Missing OPENROUTER_API_KEY in .env")

    client = OpenAI(
        api_key=openrouter_key,
        base_url="https://openrouter.ai/api/v1"
    )

    # OpenRouter judge client setup
    judge_client = client
    judge_model = "openai/gpt-4o-mini"

    # Load golden questions
    if not os.path.exists(golden_path):
        golden_path = "data/golden_set.json"
    with open(golden_path, "r", encoding="utf-8") as f:
        golden_data = json.load(f)

    if limit:
        golden_data = golden_data[:limit]

    # Pre-load RAG context cache if available
    context_cache = {}
    cache_file = Path("rag_context_cache.json")
    if cache_file.exists():
        try:
            cached_items = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(cached_items, list):
                context_cache = {item["id"]: item.get("context", "") for item in cached_items if "id" in item}
        except Exception:
            context_cache = {}

    models_to_run = [target_model] if target_model else list(MODEL_PRICING.keys())

    out_path = Path(output_dir)
    json_path = out_path / "benchmark_results.json"
    all_results = {}
    if json_path.exists():
        try:
            all_results = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            all_results = {}

    results_lock = threading.Lock()
    judge_executor = concurrent.futures.ThreadPoolExecutor(max_workers=judge_workers)
    pending_judge_futures = []

    def _judge_task(record_ref: dict):
        try:
            grade = judge_answer(
                judge_client,
                judge_model,
                record_ref["context"],
                record_ref["question"],
                record_ref["answer"]
            )
        except Exception as exc:
            grade = {
                "answerable": False,
                "correct": False,
                "score": 0,
                "hallucinated": False,
                "reason": f"Judge error: {str(exc)}"
            }
        with results_lock:
            record_ref["grade"] = grade
            status = "[PASS]" if grade.get("correct") else "[FAIL]"
            print(f"  -> Judge evaluated: {status} (score: {grade.get('score', 0)}) | Q: {record_ref['question'][:40]}...")
            export_results(all_results, output_dir, print_summary=True)

    for model_id in models_to_run:
        eval_model = model_id

        print(f"\nEvaluating Model: {model_id} ({len(golden_data)} items)")
        if model_id not in all_results:
            all_results[model_id] = []

        def is_valid_record(r: dict) -> bool:
            grade = r.get("grade") or {}
            # 1. Must be graded
            if grade.get("correct") is None:
                return False
            # 2. Cannot have stream / connection / API error
            if r.get("error"):
                return False
            # 3. Cannot have empty or whitespace answer
            ans = r.get("answer")
            if not ans or not str(ans).strip():
                return False
            # 4. Cannot have empty_output in markdown errors
            md = r.get("markdown") or {}
            if "empty_output" in md.get("errors", []):
                return False
            # 5. Cannot have unhandled judge error
            if "Judge error:" in str(grade.get("reason", "")):
                return False
            return True

        # Keep only valid records; failed, empty, or unjudged records will be retried
        id_to_record = {}
        for r in all_results[model_id]:
            if is_valid_record(r):
                id_to_record[r["id"]] = r

        retried_count = len(all_results[model_id]) - len(id_to_record)
        if retried_count > 0:
            print(f"Found {retried_count} failed, empty, or unjudged record(s) for {model_id} - scheduling for retry.")

        all_results[model_id] = list(id_to_record.values())
        existing_ids = set(id_to_record.keys())

        if len(existing_ids) == len(golden_data):
            print(f"All {len(golden_data)} items already evaluated for {model_id}.")
            with results_lock:
                export_results(all_results, output_dir, print_summary=True)
            continue

        for idx, item in enumerate(golden_data, 1):
            q_id = item["id"]
            if q_id in existing_ids:
                continue

            q = item["question"]
            category = item.get("category", "general")

            # 1. RAG retrieval (cache hit or live hybrid search)
            if q_id in context_cache and context_cache[q_id]:
                ctx = context_cache[q_id]
            else:
                chunks = hybrid_search(q, top_k=5)
                ctx = build_prompt_context(chunks)

            # 2. Model generation & TTFT (sequential for accurate speed measurement)
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Context:\n{ctx}\n\nStudent Question: {q}"}
            ]
            perf = measure_stream_and_ttft(client, eval_model, messages)

            # 3. Markdown format verification
            md_res = check_markdown_format(perf["answer"])

            # 4. Cost calculation
            cost = calculate_cost(model_id, perf["prompt_tokens"], perf["completion_tokens"])

            record = {
                "id": q_id,
                "category": category,
                "question": q,
                "context": ctx,
                "answer": perf["answer"],
                "ttft_ms": perf["ttft_ms"],
                "total_latency_ms": perf["total_latency_ms"],
                "prompt_tokens": perf["prompt_tokens"],
                "completion_tokens": perf["completion_tokens"],
                "cost_usd": cost,
                "markdown": md_res,
                "grade": {
                    "answerable": None,
                    "correct": None,
                    "score": None,
                    "hallucinated": None,
                    "reason": "judging in background"
                },
                "error": perf["error"]
            }

            with results_lock:
                all_results[model_id].append(record)
                export_results(all_results, output_dir, print_summary=False)

            md_status = "[MD-OK]" if md_res["markdown_valid"] else "[MD-ERR]"
            print(f"[{idx}/{len(golden_data)}] [STREAM-OK] {md_status} | TTFT: {perf['ttft_ms']:.0f}ms | Tot: {perf['total_latency_ms']/1000:.1f}s | Q: {q[:45]}...")

            # 5. Dispatch judge evaluation to background worker
            fut = judge_executor.submit(_judge_task, record)
            pending_judge_futures.append(fut)

    if pending_judge_futures:
        print(f"\nWaiting for {len(pending_judge_futures)} background judge evaluations to complete...")
        concurrent.futures.wait(pending_judge_futures)

    judge_executor.shutdown(wait=True)

    with results_lock:
        export_results(all_results, output_dir, print_summary=False)

    return all_results

run_eval = run_benchmark

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="De Anza AI Model Benchmark Runner")
    parser.add_argument("--model", type=str, default=None, help="Target model ID (e.g. openai/gpt-4o-mini)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of golden set questions to run")
    parser.add_argument("--output-dir", type=str, default="eval_results", help="Directory for output JSON and CSV")
    parser.add_argument("--judge-workers", type=int, default=3, help="Concurrent workers for background LLM judge")
    parser.add_argument("--summary-only", action="store_true", help="Generate benchmark_summary.csv directly from benchmark_results.json without calling APIs")
    args = parser.parse_args()

    if args.summary_only:
        json_path = Path(args.output_dir) / "benchmark_results.json"
        if not json_path.exists():
            print(f"Error: {json_path} not found.")
            sys.exit(1)
        print(f"Generating summary from {json_path}...")
        data = json.loads(json_path.read_text(encoding="utf-8"))
        export_results(data, args.output_dir, print_summary=True)
        print("\nSuccessfully updated benchmark_summary.csv in both eval_results/ and output/.")
        sys.exit(0)

    run_benchmark(
        target_model=args.model,
        limit=args.limit,
        output_dir=args.output_dir,
        judge_workers=args.judge_workers
    )
