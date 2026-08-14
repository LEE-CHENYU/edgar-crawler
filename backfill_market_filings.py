import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests

from __init__ import DATASET_DIR
from download_market_filings import add_months


DEFAULT_CONFIG_PATH = "market_filings_config.json"
DEFAULT_ENV_PATH = "~/.config/edgar-crawler/market_filings.env"
DEFAULT_OUTPUT_FOLDER = "MARKET_FILINGS"
DEFAULT_START_DATE = "2008-01-01"
TWSE_LISTED_COMPANIES_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_OTC_COMPANIES_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a resumable newest-to-oldest market filing backfill."
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--env-file", default=DEFAULT_ENV_PATH)
    parser.add_argument(
        "--markets",
        nargs="+",
        default=["edinet", "dart", "twse"],
        choices=["edinet", "dart", "pse_edge", "twse", "hkex", "twse_reports"],
    )
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--state-file")
    parser.add_argument("--company-file")
    parser.add_argument("--refresh-company-codes", action="store_true")
    parser.add_argument("--edinet-days-per-chunk", type=int, default=1)
    parser.add_argument("--dart-days-per-chunk", type=int, default=7)
    parser.add_argument("--pse-edge-days-per-chunk", type=int, default=31)
    parser.add_argument("--hkex-days-per-chunk", type=int, default=1)
    parser.add_argument("--twse-company-batch-size", type=int, default=20)
    parser.add_argument("--twse-report-company-batch-size", type=int, default=5)
    parser.add_argument("--request-timeout", type=int, default=90)
    parser.add_argument("--sleep-seconds", type=float, default=2.0)
    parser.add_argument("--error-sleep-seconds", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    load_env_file(Path(args.env_file).expanduser())
    base_config = load_json(Path(args.config))
    output_folder = base_config.get("output_folder", DEFAULT_OUTPUT_FOLDER)
    output_dir = Path(DATASET_DIR) / output_folder
    output_dir.mkdir(parents=True, exist_ok=True)

    state_path = Path(args.state_file) if args.state_file else output_dir / "backfill_state.json"
    company_path = (
        Path(args.company_file) if args.company_file else output_dir / "twse_company_codes.json"
    )
    state = load_state(state_path)
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)

    print(
        f"{timestamp()} backfill starting markets={','.join(args.markets)} "
        f"range={start_date}..{end_date}",
        flush=True,
    )

    while True:
        did_work = False
        had_error = False

        if "edinet" in args.markets:
            result = run_date_backfill_chunk(
                market="edinet",
                state_key="edinet_next_end",
                days_per_chunk=args.edinet_days_per_chunk,
                start_date=start_date,
                end_date=end_date,
                state=state,
                state_path=state_path,
                base_config=base_config,
                request_timeout=args.request_timeout,
            )
            did_work = did_work or result == "worked"
            had_error = had_error or result == "error"

        if "dart" in args.markets:
            result = run_date_backfill_chunk(
                market="dart",
                state_key="dart_next_end",
                days_per_chunk=args.dart_days_per_chunk,
                start_date=start_date,
                end_date=end_date,
                state=state,
                state_path=state_path,
                base_config=base_config,
                request_timeout=args.request_timeout,
            )
            did_work = did_work or result == "worked"
            had_error = had_error or result == "error"

        if "pse_edge" in args.markets:
            result = run_date_backfill_chunk(
                market="pse_edge",
                state_key="pse_edge_next_end",
                days_per_chunk=args.pse_edge_days_per_chunk,
                start_date=start_date,
                end_date=end_date,
                state=state,
                state_path=state_path,
                base_config=base_config,
                request_timeout=args.request_timeout,
            )
            did_work = did_work or result == "worked"
            had_error = had_error or result == "error"

        if "hkex" in args.markets:
            result = run_date_backfill_chunk(
                market="hkex",
                state_key="hkex_next_end",
                days_per_chunk=args.hkex_days_per_chunk,
                start_date=start_date,
                end_date=end_date,
                state=state,
                state_path=state_path,
                base_config=base_config,
                request_timeout=args.request_timeout,
            )
            did_work = did_work or result == "worked"
            had_error = had_error or result == "error"

        if "twse" in args.markets:
            company_codes = load_twse_company_codes(
                company_path=company_path,
                refresh=args.refresh_company_codes,
            )
            result = run_twse_backfill_chunk(
                company_codes=company_codes,
                company_batch_size=args.twse_company_batch_size,
                start_date=start_date,
                end_date=end_date,
                state=state,
                state_path=state_path,
                base_config=base_config,
                request_timeout=args.request_timeout,
            )
            did_work = did_work or result == "worked"
            had_error = had_error or result == "error"

        if "twse_reports" in args.markets:
            company_codes = load_twse_company_codes(
                company_path=company_path,
                refresh=args.refresh_company_codes,
            )
            result = run_twse_reports_backfill_chunk(
                company_codes=company_codes,
                company_batch_size=args.twse_report_company_batch_size,
                start_date=start_date,
                end_date=end_date,
                state=state,
                state_path=state_path,
                base_config=base_config,
                request_timeout=args.request_timeout,
            )
            did_work = did_work or result == "worked"
            had_error = had_error or result == "error"

        if not did_work:
            if had_error:
                time.sleep(args.error_sleep_seconds)
                continue
            print(f"{timestamp()} backfill complete through {start_date}", flush=True)
            break

        if args.once:
            break
        time.sleep(args.sleep_seconds)


def run_date_backfill_chunk(
    market: str,
    state_key: str,
    days_per_chunk: int,
    start_date: date,
    end_date: date,
    state: Dict,
    state_path: Path,
    base_config: Dict,
    request_timeout: int,
) -> str:
    next_end = parse_date(state.get(state_key, end_date.isoformat()))
    if next_end < start_date:
        return "done"
    chunk_start = max(start_date, next_end - timedelta(days=days_per_chunk - 1))
    chunk_end = min(next_end, end_date)
    print(f"{timestamp()} {market.upper()} {chunk_start}..{chunk_end}", flush=True)

    config = config_for_market(base_config, market, request_timeout)
    market_config = config["markets"][market]
    market_config["start_date"] = chunk_start.isoformat()
    market_config["end_date"] = chunk_end.isoformat()
    market_config["max_filings"] = int(market_config.get("backfill_max_filings", 1000))

    if run_downloader(config, market):
        state[state_key] = (chunk_start - timedelta(days=1)).isoformat()
        save_state(state_path, state)
        return "worked"
    return "error"


def run_twse_backfill_chunk(
    company_codes: List[str],
    company_batch_size: int,
    start_date: date,
    end_date: date,
    state: Dict,
    state_path: Path,
    base_config: Dict,
    request_timeout: int,
) -> str:
    month_value = state.get("twse_next_month", f"{end_date:%Y-%m}")
    month_start = datetime.strptime(month_value + "-01", "%Y-%m-%d").date()
    stop_month = date(start_date.year, start_date.month, 1)
    if month_start < stop_month:
        return "done"

    company_index = int(state.get("twse_company_index", 0))
    if company_index >= len(company_codes):
        company_index = 0
        month_start = add_months(month_start, -1)
        if month_start < stop_month:
            state["twse_next_month"] = f"{month_start:%Y-%m}"
            state["twse_company_index"] = 0
            save_state(state_path, state)
            return "done"

    chunk_start = max(month_start, start_date)
    chunk_end = min(add_months(month_start, 1) - timedelta(days=1), end_date)
    batch = company_codes[company_index : company_index + company_batch_size]
    if not batch:
        return "done"

    print(
        f"{timestamp()} TWSE {chunk_start:%Y-%m} companies "
        f"{company_index + 1}-{company_index + len(batch)} of {len(company_codes)}",
        flush=True,
    )
    config = config_for_market(base_config, "twse", request_timeout)
    market_config = config["markets"]["twse"]
    market_config.update(
        {
            "source": "mops_historical",
            "start_date": chunk_start.isoformat(),
            "end_date": chunk_end.isoformat(),
            "company_codes": batch,
            "max_filings": int(market_config.get("backfill_max_filings", 5000)),
            "delay_seconds": float(market_config.get("delay_seconds", 0.1)),
        }
    )

    if run_downloader(config, "twse"):
        company_index += len(batch)
        if company_index >= len(company_codes):
            state["twse_next_month"] = f"{add_months(month_start, -1):%Y-%m}"
            state["twse_company_index"] = 0
        else:
            state["twse_next_month"] = f"{month_start:%Y-%m}"
            state["twse_company_index"] = company_index
        save_state(state_path, state)
        return "worked"
    return "error"


def run_twse_reports_backfill_chunk(
    company_codes: List[str],
    company_batch_size: int,
    start_date: date,
    end_date: date,
    state: Dict,
    state_path: Path,
    base_config: Dict,
    request_timeout: int,
) -> str:
    report_year = int(state.get("twse_reports_next_year", end_date.year))
    stop_year = start_date.year
    if report_year < stop_year:
        return "done"

    company_index = int(state.get("twse_reports_company_index", 0))
    if company_index >= len(company_codes):
        company_index = 0
        report_year -= 1
        if report_year < stop_year:
            state["twse_reports_next_year"] = report_year
            state["twse_reports_company_index"] = 0
            save_state(state_path, state)
            return "done"

    batch = company_codes[company_index : company_index + company_batch_size]
    if not batch:
        return "done"

    print(
        f"{timestamp()} TWSE_REPORTS {report_year} companies "
        f"{company_index + 1}-{company_index + len(batch)} of {len(company_codes)}",
        flush=True,
    )
    config = config_for_market(base_config, "twse_reports", request_timeout)
    market_config = config["markets"]["twse_reports"]
    market_config.update(
        {
            "start_date": date(report_year, 1, 1).isoformat(),
            "end_date": date(report_year, 12, 31).isoformat(),
            "start_year": report_year,
            "end_year": report_year,
            "company_codes": batch,
            "max_filings": int(market_config.get("backfill_max_filings", 5000)),
            "delay_seconds": float(market_config.get("delay_seconds", 0.2)),
        }
    )

    if run_downloader(config, "twse_reports"):
        company_index += len(batch)
        if company_index >= len(company_codes):
            state["twse_reports_next_year"] = report_year - 1
            state["twse_reports_company_index"] = 0
        else:
            state["twse_reports_next_year"] = report_year
            state["twse_reports_company_index"] = company_index
        save_state(state_path, state)
        return "worked"
    return "error"


def config_for_market(base_config: Dict, market: str, request_timeout: int) -> Dict:
    config = copy.deepcopy(base_config)
    config["request_timeout"] = request_timeout
    market_config = copy.deepcopy((base_config.get("markets") or {}).get(market) or {})
    market_config["enabled"] = True
    config["markets"] = {market: market_config}
    return config


def run_downloader(config: Dict, market: str) -> bool:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="market_backfill_", delete=False
    ) as fout:
        json.dump(config, fout, ensure_ascii=False, indent=2)
        config_path = fout.name
    try:
        command = [
            sys.executable,
            "-u",
            "download_market_filings.py",
            "--config",
            config_path,
            "--markets",
            market,
        ]
        completed = subprocess.run(command, cwd=Path(__file__).parent)
        return completed.returncode == 0
    finally:
        os.remove(config_path)


def load_twse_company_codes(company_path: Path, refresh: bool = False) -> List[str]:
    if company_path.exists() and not refresh:
        return load_json(company_path)["company_codes"]

    company_codes = sorted(set(fetch_twse_listed_codes() + fetch_tpex_otc_codes()))
    if not company_codes:
        raise RuntimeError("No TWSE/TPEx company codes could be fetched")
    company_path.parent.mkdir(parents=True, exist_ok=True)
    with open(company_path, "w", encoding="utf-8") as fout:
        json.dump(
            {"generated_at": timestamp(), "company_codes": company_codes},
            fout,
            ensure_ascii=False,
            indent=2,
        )
        fout.write("\n")
    print(f"{timestamp()} wrote {len(company_codes)} TWSE/TPEx company codes", flush=True)
    return company_codes


def fetch_twse_listed_codes() -> List[str]:
    rows = fetch_json_with_retries(TWSE_LISTED_COMPANIES_URL)
    return [
        str(row.get("公司代號") or "").strip()
        for row in rows
        if str(row.get("公司代號") or "").strip().isdigit()
    ]


def fetch_tpex_otc_codes() -> List[str]:
    rows = fetch_json_with_retries(TPEX_OTC_COMPANIES_URL)
    return [
        str(row.get("SecuritiesCompanyCode") or "").strip()
        for row in rows
        if str(row.get("SecuritiesCompanyCode") or "").strip().isdigit()
    ]


def fetch_json_with_retries(url: str, attempts: int = 5) -> List[Dict]:
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "stock-mcp/1.0", "Accept": "application/json,*/*"},
                timeout=(20, 180),
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            print(
                f"{timestamp()} company-code fetch failed "
                f"attempt={attempt}/{attempts} url={url}: {exc}",
                flush=True,
            )
            time.sleep(min(30, attempt * 5))
    print(f"{timestamp()} company-code fetch abandoned url={url}: {last_error}", flush=True)
    return []


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    with open(path, encoding="utf-8") as fin:
        for line in fin:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :]
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip("'").strip('"')
            os.environ.setdefault(key.strip(), value)


def load_json(path: Path) -> Dict:
    with open(path, encoding="utf-8") as fin:
        return json.load(fin)


def load_state(path: Path) -> Dict:
    if not path.exists():
        return {}
    return load_json(path)


def save_state(path: Path, state: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as fout:
        json.dump(state, fout, ensure_ascii=False, indent=2, sort_keys=True)
        fout.write("\n")
    os.replace(temp_path, path)


def parse_date(value: Optional[str]) -> date:
    if value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    return date.today()


def timestamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


if __name__ == "__main__":
    main()
