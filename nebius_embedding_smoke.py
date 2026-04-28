import argparse
import os
import statistics
import time
from typing import List

from openai import OpenAI


DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
DEFAULT_MODEL = "BAAI/bge-en-icl"
DEFAULT_TEXT = (
    "トヨタ自動車株式会社の有価証券報告書における事業等のリスクと"
    "財政状態、経営成績及びキャッシュ・フローの状況の分析を検索する。"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test Nebius OpenAI-compatible embeddings."
    )
    parser.add_argument("--model", default=os.environ.get("NEBIUS_EMBEDDING_MODEL", DEFAULT_MODEL))
    parser.add_argument("--base-url", default=os.environ.get("NEBIUS_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--input-file")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--batches", type=int, default=2)
    args = parser.parse_args()

    api_key = os.environ.get("NEBIUS_API_KEY")
    if not api_key:
        raise SystemExit(
            "NEBIUS_API_KEY is not set. Export it in the process environment and rerun."
        )

    text = args.text
    if args.input_file:
        with open(args.input_file, encoding="utf-8") as fin:
            text = fin.read().strip()
    if not text:
        raise SystemExit("Input text is empty")

    client = OpenAI(base_url=args.base_url, api_key=api_key)
    durations: List[float] = []
    dimensions: List[int] = []
    total_inputs = 0
    total_tokens = 0

    for batch_index in range(args.batches):
        batch = [
            f"{text}\n\n[batch={batch_index} item={item_index}]"
            for item_index in range(args.batch_size)
        ]
        started = time.perf_counter()
        response = client.embeddings.create(
            model=args.model,
            input=batch,
            encoding_format="float",
        )
        elapsed = time.perf_counter() - started
        durations.append(elapsed)
        total_inputs += len(batch)
        total_tokens += int(getattr(response.usage, "total_tokens", 0) or 0)
        dimensions.extend(len(item.embedding) for item in response.data)
        print(
            f"batch={batch_index} inputs={len(batch)} "
            f"elapsed={elapsed:.3f}s dims={sorted(set(dimensions))} "
            f"tokens={getattr(response.usage, 'total_tokens', '')}"
        )

    print(
        "summary "
        f"model={args.model} inputs={total_inputs} "
        f"avg_batch_seconds={statistics.mean(durations):.3f} "
        f"inputs_per_second={total_inputs / sum(durations):.2f} "
        f"tokens={total_tokens} dimensions={sorted(set(dimensions))}"
    )


if __name__ == "__main__":
    main()
