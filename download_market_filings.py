import argparse
import csv
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from hashlib import sha1
from typing import Dict, Iterable, List, Optional
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from __init__ import DATASET_DIR


METADATA_FIELDS = [
    "market",
    "filing_id",
    "filing_date",
    "company_name",
    "stock_code",
    "title",
    "category",
    "source_url",
    "document_url",
    "local_path",
    "downloaded_at",
    "raw_metadata",
]

DEFAULT_CONFIG_PATH = "market_filings_config.json"
DEFAULT_OUTPUT_FOLDER = "MARKET_FILINGS"
SGX_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)
HKEX_USER_AGENT = SGX_USER_AGENT
TWSE_USER_AGENT = SGX_USER_AGENT


@dataclass
class FilingDocument:
    market: str
    filing_id: str
    filing_date: str
    company_name: str
    stock_code: str
    title: str
    category: str
    source_url: str
    document_url: str
    local_path: str
    downloaded_at: str
    raw_metadata: str


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download non-US market filings and exchange announcements."
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--markets",
        nargs="+",
        help="Limit run to specific markets, e.g. --markets sgx hkex",
    )
    parser.add_argument("--start-date", help="Override market start dates, YYYY-MM-DD")
    parser.add_argument("--end-date", help="Override market end dates, YYYY-MM-DD")
    parser.add_argument("--max-filings", type=int, help="Override per-market max")
    parser.add_argument("--output-folder", help="Override output folder under datasets")
    parser.add_argument("--no-download", action="store_true", help="Metadata only")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.output_folder:
        config["output_folder"] = args.output_folder

    output_dir = os.path.join(
        DATASET_DIR, config.get("output_folder", DEFAULT_OUTPUT_FOLDER)
    )
    raw_dir = os.path.join(output_dir, "raw")
    metadata_dir = os.path.join(output_dir, "metadata")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(metadata_dir, exist_ok=True)

    markets_config = config.get("markets", {})
    selected_markets = [m.lower() for m in args.markets] if args.markets else None
    download_documents = bool(config.get("download_documents", True))
    if args.no_download:
        download_documents = False

    timeout = int(config.get("request_timeout", 30))
    collected_rows: List[Dict[str, str]] = []

    for market_name, market_config in markets_config.items():
        market_name = market_name.lower()
        if selected_markets and market_name not in selected_markets:
            continue
        if not market_config.get("enabled", False):
            print(f"Skipping {market_name.upper()}: disabled in config")
            continue

        market_config = dict(market_config)
        apply_cli_overrides(market_config, args)
        print(
            f"Collecting {market_name.upper()} filings "
            f"from {market_config.get('start_date')} to {market_config.get('end_date')}"
        )

        try:
            rows = collect_market(
                market_name=market_name,
                config=market_config,
                raw_dir=raw_dir,
                download_documents=download_documents,
                timeout=timeout,
            )
        except requests.HTTPError as exc:
            print(f"{market_name.upper()} failed: HTTP {exc.response.status_code}")
            continue
        except requests.RequestException as exc:
            print(f"{market_name.upper()} failed: {exc}")
            continue

        print(f"{market_name.upper()}: collected {len(rows)} document rows")
        collected_rows.extend(asdict(row) for row in rows)

    metadata_path = os.path.join(metadata_dir, "MARKET_FILINGS_METADATA.csv")
    if collected_rows:
        write_metadata(metadata_path, collected_rows)
        print(f"Metadata written to {metadata_path}")
    else:
        print("No market filing rows collected")


def load_config(path: str) -> Dict:
    with open(path) as fin:
        return json.load(fin)


def apply_cli_overrides(config: Dict, args: argparse.Namespace) -> None:
    if args.start_date:
        config["start_date"] = args.start_date
    if args.end_date:
        config["end_date"] = args.end_date
    if args.max_filings is not None:
        config["max_filings"] = args.max_filings


def collect_market(
    market_name: str,
    config: Dict,
    raw_dir: str,
    download_documents: bool,
    timeout: int,
) -> List[FilingDocument]:
    if market_name == "sgx":
        return collect_sgx(config, raw_dir, download_documents, timeout)
    if market_name == "hkex":
        return collect_hkex(config, raw_dir, download_documents, timeout)
    if market_name == "edinet":
        return collect_edinet(config, raw_dir, download_documents, timeout)
    if market_name == "dart":
        return collect_dart(config, raw_dir, download_documents, timeout)
    if market_name == "twse":
        return collect_twse(config, raw_dir, download_documents, timeout)
    print(f"Skipping {market_name.upper()}: unsupported market")
    return []


def collect_sgx(
    config: Dict, raw_dir: str, download_documents: bool, timeout: int
) -> List[FilingDocument]:
    session = build_session()
    app_config = session.get(
        "https://www.sgx.com/config/appconfig.json",
        headers={"User-Agent": SGX_USER_AGENT, "Accept": "application/json"},
        timeout=timeout,
    )
    app_config.raise_for_status()
    sgx_config = app_config.json()
    endpoints = sgx_config.get("endpoints", {})
    api_url = endpoints["ANNOUNCEMENTS_API_URL"]
    token = get_sgx_token(
        session=session,
        cms_api_url=endpoints["CMS_API_URL"],
        cms_version=sgx_config["CMS_VERSION"],
        timeout=timeout,
    )

    headers = sgx_headers(token)
    start_date = parse_date(config.get("start_date"))
    end_date = parse_date(config.get("end_date"))
    page_size = int(config.get("page_size", 250))
    max_filings = int(config.get("max_filings", page_size))
    rows: List[FilingDocument] = []
    seen_filings = 0
    page_start = 0

    while seen_filings < max_filings:
        params = {
            "periodstart": f"{start_date:%Y%m%d}_000000",
            "periodend": f"{end_date:%Y%m%d}_235959",
            "pagesize": page_size,
            "pagestart": page_start,
        }
        response = session.get(api_url, params=params, headers=headers, timeout=timeout)
        if response.status_code == 403:
            token = get_sgx_token(
                session=session,
                cms_api_url=endpoints["CMS_API_URL"],
                cms_version=sgx_config["CMS_VERSION"],
                timeout=timeout,
            )
            headers = sgx_headers(token)
            response = session.get(
                api_url, params=params, headers=headers, timeout=timeout
            )
        response.raise_for_status()

        payload = response.json()
        filings = payload.get("data") or []
        if not filings:
            break

        for filing in filings:
            if seen_filings >= max_filings:
                break
            rows.extend(
                build_sgx_rows(
                    session=session,
                    filing=filing,
                    raw_dir=raw_dir,
                    download_documents=download_documents,
                    timeout=timeout,
                )
            )
            seen_filings += 1
            time.sleep(float(config.get("delay_seconds", 0.2)))

        total_pages = int((payload.get("meta") or {}).get("totalPages") or 0)
        page_start += 1
        if total_pages and page_start >= total_pages:
            break

    return rows


def get_sgx_token(
    session: requests.Session, cms_api_url: str, cms_version: str, timeout: int
) -> str:
    response = session.get(
        cms_api_url,
        params={"queryId": f"{cms_version}:we_chat_qr_validator"},
        headers={
            "User-Agent": SGX_USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://www.sgx.com/",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    token = ((response.json().get("data") or {}).get("qrValidator") or "").strip()
    if not token:
        raise RuntimeError("SGX did not return an announcement authorization token")
    return rot13(token)


def sgx_headers(token: str) -> Dict[str, str]:
    return {
        "User-Agent": SGX_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.sgx.com/securities/company-announcements",
        "Origin": "https://www.sgx.com",
        "authorizationToken": token,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }


def build_sgx_rows(
    session: requests.Session,
    filing: Dict,
    raw_dir: str,
    download_documents: bool,
    timeout: int,
) -> List[FilingDocument]:
    filing_id = str(filing.get("ref_id") or filing.get("id") or "")
    source_url = filing.get("url") or ""
    filing_date = parse_yyyymmdd(filing.get("submission_date") or "")
    title = clean_text(filing.get("title") or "")
    category = clean_text(filing.get("category_name") or filing.get("cat") or "")
    company_name = clean_text(filing.get("issuer_name") or "")
    stock_code = join_unique(
        issuer.get("stock_code") for issuer in filing.get("issuers") or []
    )

    attachments = find_sgx_attachments(session, source_url, timeout)
    if not attachments and source_url:
        attachments = [{"url": source_url, "title": title}]

    rows = []
    for attachment in attachments:
        document_url = attachment["url"]
        local_path = ""
        if download_documents and document_url:
            filename = attachment_filename(document_url, attachment.get("title"))
            local_path = os.path.join(
                raw_dir,
                "SGX",
                filing_date.replace("-", "") or "unknown_date",
                safe_filename(filing_id),
                filename,
            )
            if not try_download_binary(
                session=session,
                url=document_url,
                path=local_path,
                headers={"User-Agent": SGX_USER_AGENT, "Referer": source_url},
                timeout=timeout,
            ):
                local_path = ""

        rows.append(
            FilingDocument(
                market="SGX",
                filing_id=filing_id,
                filing_date=filing_date,
                company_name=company_name,
                stock_code=stock_code,
                title=title,
                category=category,
                source_url=source_url,
                document_url=document_url,
                local_path=local_path,
                downloaded_at=utc_now(),
                raw_metadata=json.dumps(filing, ensure_ascii=False, sort_keys=True),
            )
        )
    return rows


def find_sgx_attachments(
    session: requests.Session, source_url: str, timeout: int
) -> List[Dict[str, str]]:
    if not source_url:
        return []
    response = session.get(
        source_url,
        headers={"User-Agent": SGX_USER_AGENT, "Referer": "https://www.sgx.com/"},
        timeout=timeout,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    attachments: List[Dict[str, str]] = []
    seen = set()
    for link in soup.find_all("a", href=True):
        href = urljoin(source_url, link["href"])
        if "corporate-announcements" not in href:
            continue
        if href.rstrip("/") == source_url.rstrip("/"):
            continue
        if href in seen:
            continue
        seen.add(href)
        attachments.append({"url": href, "title": clean_text(link.get_text(" "))})
    return attachments


def collect_hkex(
    config: Dict, raw_dir: str, download_documents: bool, timeout: int
) -> List[FilingDocument]:
    session = build_session()
    start_date = parse_date(config.get("start_date"))
    end_date = parse_date(config.get("end_date"))
    max_filings = int(config.get("max_filings", 100))
    stock_ids = config.get("stock_ids") or [""]
    rows: List[FilingDocument] = []
    seen_news = set()

    for stock_id in stock_ids:
        params = {
            "sortDir": "0",
            "sortByOptions": "DateTime",
            "category": config.get("category", "0"),
            "market": config.get("market", "SEHK"),
            "stockId": stock_id,
            "documentType": config.get("document_type", "-1"),
            "from": f"{start_date:%Y%m%d}",
            "to": f"{end_date:%Y%m%d}",
            "title": config.get("title", ""),
        }
        response = session.get(
            "https://www1.hkexnews.hk/search/titleSearchServlet.do",
            params=params,
            headers={
                "User-Agent": HKEX_USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        filings = json.loads(data.get("result") or "[]")
        for filing in filings:
            news_id = str(filing.get("NEWS_ID") or "")
            if not news_id or news_id in seen_news:
                continue
            filing_day = parse_hkex_datetime(filing.get("DATE_TIME") or "")
            if filing_day:
                filing_day_date = datetime.strptime(filing_day, "%Y-%m-%d").date()
                if filing_day_date < start_date or filing_day_date > end_date:
                    continue
            seen_news.add(news_id)
            rows.append(
                build_hkex_row(
                    session=session,
                    filing=filing,
                    raw_dir=raw_dir,
                    download_documents=download_documents,
                    timeout=timeout,
                )
            )
            if len(seen_news) >= max_filings:
                return rows
            time.sleep(float(config.get("delay_seconds", 0.2)))

    return rows


def build_hkex_row(
    session: requests.Session,
    filing: Dict,
    raw_dir: str,
    download_documents: bool,
    timeout: int,
) -> FilingDocument:
    news_id = str(filing.get("NEWS_ID") or "")
    document_url = urljoin("https://www1.hkexnews.hk", filing.get("FILE_LINK") or "")
    filing_date = parse_hkex_datetime(filing.get("DATE_TIME") or "")
    title = clean_text(filing.get("TITLE") or filing.get("LONG_TEXT") or "")
    stock_code = str(filing.get("STOCK_CODE") or "")
    company_name = clean_text(filing.get("STOCK_NAME") or "")
    category = clean_text(filing.get("LONG_TEXT") or filing.get("SHORT_TEXT") or "")
    local_path = ""

    if download_documents and document_url:
        filename = attachment_filename(document_url, title)
        local_path = os.path.join(
            raw_dir,
            "HKEX",
            filing_date.replace("-", "") or "unknown_date",
            safe_filename(news_id),
            filename,
        )
        if not try_download_binary(
            session=session,
            url=document_url,
            path=local_path,
            headers={
                "User-Agent": HKEX_USER_AGENT,
                "Referer": "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en",
            },
            timeout=timeout,
        ):
            local_path = ""

    return FilingDocument(
        market="HKEX",
        filing_id=news_id,
        filing_date=filing_date,
        company_name=company_name,
        stock_code=stock_code,
        title=title,
        category=category,
        source_url="https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en",
        document_url=document_url,
        local_path=local_path,
        downloaded_at=utc_now(),
        raw_metadata=json.dumps(filing, ensure_ascii=False, sort_keys=True),
    )


def collect_edinet(
    config: Dict, raw_dir: str, download_documents: bool, timeout: int
) -> List[FilingDocument]:
    api_key = get_api_key(config)
    if not api_key:
        print("Skipping EDINET: set EDINET_API_KEY or config api_key")
        return []

    session = build_session()
    rows: List[FilingDocument] = []
    max_filings = int(config.get("max_filings", 100))
    list_type = str(config.get("list_type", "2"))
    document_type = str(config.get("document_type", "2"))
    allowed_form_codes = set(config.get("form_codes") or [])

    for filing_day in date_range(
        parse_date(config.get("start_date")), parse_date(config.get("end_date"))
    ):
        response = session.get(
            "https://api.edinet-fsa.go.jp/api/v2/documents.json",
            params={
                "date": filing_day.isoformat(),
                "type": list_type,
                "Subscription-Key": api_key,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("statusCode", "200")) != "200":
            print(f"EDINET {filing_day}: {payload.get('message')}")
            continue

        for filing in payload.get("results") or []:
            if len(rows) >= max_filings:
                return rows
            if allowed_form_codes and filing.get("formCode") not in allowed_form_codes:
                continue
            if not edinet_filing_matches_filters(filing, config):
                continue
            rows.append(
                build_edinet_row(
                    session=session,
                    filing=filing,
                    raw_dir=raw_dir,
                    document_type=document_type,
                    api_key=api_key,
                    download_documents=download_documents,
                    timeout=timeout,
                )
            )
            time.sleep(float(config.get("delay_seconds", 0.2)))
    return rows


def edinet_filing_matches_filters(filing: Dict, config: Dict) -> bool:
    doc_type_codes = set(str(code) for code in config.get("doc_type_codes") or [])
    if doc_type_codes and str(filing.get("docTypeCode") or "") not in doc_type_codes:
        return False
    if config.get("require_sec_code") and not filing.get("secCode"):
        return False

    description = str(filing.get("docDescription") or "")
    include_keywords = config.get("include_doc_description_keywords") or []
    if include_keywords and not any(keyword in description for keyword in include_keywords):
        return False
    exclude_keywords = config.get("exclude_doc_description_keywords") or []
    if exclude_keywords and any(keyword in description for keyword in exclude_keywords):
        return False
    return True


def build_edinet_row(
    session: requests.Session,
    filing: Dict,
    raw_dir: str,
    document_type: str,
    api_key: str,
    download_documents: bool,
    timeout: int,
) -> FilingDocument:
    doc_id = str(filing.get("docID") or "")
    filing_date = parse_edinet_datetime(filing.get("submitDateTime") or "")
    document_url = (
        f"https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}?type={document_type}"
    )
    local_path = ""
    if download_documents and doc_id:
        extension = edinet_extension(document_type)
        filename = f"{safe_filename(doc_id)}.{extension}"
        local_path = os.path.join(
            raw_dir,
            "EDINET",
            filing_date.replace("-", "") or "unknown_date",
            safe_filename(doc_id),
            filename,
        )
        if not try_download_binary(
            session=session,
            url=f"https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}",
            path=local_path,
            params={"type": document_type, "Subscription-Key": api_key},
            timeout=timeout,
        ):
            local_path = ""

    return FilingDocument(
        market="EDINET",
        filing_id=doc_id,
        filing_date=filing_date,
        company_name=clean_text(filing.get("filerName") or ""),
        stock_code=str(filing.get("secCode") or ""),
        title=clean_text(filing.get("docDescription") or ""),
        category=str(filing.get("formCode") or ""),
        source_url="https://api.edinet-fsa.go.jp/api/v2/documents.json",
        document_url=document_url,
        local_path=local_path,
        downloaded_at=utc_now(),
        raw_metadata=json.dumps(filing, ensure_ascii=False, sort_keys=True),
    )


def collect_dart(
    config: Dict, raw_dir: str, download_documents: bool, timeout: int
) -> List[FilingDocument]:
    api_key = get_api_key(config)
    if not api_key:
        print("Skipping DART: set DART_API_KEY or config api_key")
        return []

    session = build_session()
    rows: List[FilingDocument] = []
    max_filings = int(config.get("max_filings", 100))
    page_count = int(config.get("page_count", 100))
    page_no = 1

    while len(rows) < max_filings:
        params = {
            "crtfc_key": api_key,
            "bgn_de": parse_date(config.get("start_date")).strftime("%Y%m%d"),
            "end_de": parse_date(config.get("end_date")).strftime("%Y%m%d"),
            "page_no": page_no,
            "page_count": page_count,
        }
        for optional_name in ("corp_cls", "pblntf_ty", "pblntf_detail_ty", "sort", "sort_mth"):
            optional_value = config.get(optional_name)
            if optional_value:
                params[optional_name] = optional_value

        response = session.get(
            "https://opendart.fss.or.kr/api/list.json",
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "000":
            print(f"DART page {page_no}: {payload.get('message')}")
            break

        filings = payload.get("list") or []
        for filing in filings:
            if len(rows) >= max_filings:
                break
            if not dart_filing_matches_filters(filing, config):
                continue
            rows.append(
                build_dart_row(
                    session=session,
                    filing=filing,
                    raw_dir=raw_dir,
                    api_key=api_key,
                    download_documents=download_documents,
                    timeout=timeout,
                )
            )
            time.sleep(float(config.get("delay_seconds", 0.2)))

        total_pages = int(payload.get("total_page") or 0)
        page_no += 1
        if not filings or (total_pages and page_no > total_pages):
            break

    return rows


def dart_filing_matches_filters(filing: Dict, config: Dict) -> bool:
    if config.get("require_stock_code") and not filing.get("stock_code"):
        return False

    report_name = str(filing.get("report_nm") or "")
    include_keywords = config.get("include_report_keywords") or []
    if include_keywords and not any(keyword in report_name for keyword in include_keywords):
        return False
    exclude_keywords = config.get("exclude_report_keywords") or []
    if exclude_keywords and any(keyword in report_name for keyword in exclude_keywords):
        return False
    return True


def build_dart_row(
    session: requests.Session,
    filing: Dict,
    raw_dir: str,
    api_key: str,
    download_documents: bool,
    timeout: int,
) -> FilingDocument:
    receipt_no = str(filing.get("rcept_no") or "")
    filing_date = parse_yyyymmdd(filing.get("rcept_dt") or "")
    document_url = f"https://opendart.fss.or.kr/api/document.xml?rcept_no={receipt_no}"
    local_path = ""
    if download_documents and receipt_no:
        filename = f"{safe_filename(receipt_no)}.zip"
        local_path = os.path.join(
            raw_dir,
            "DART",
            filing_date.replace("-", "") or "unknown_date",
            safe_filename(receipt_no),
            filename,
        )
        if not try_download_binary(
            session=session,
            url="https://opendart.fss.or.kr/api/document.xml",
            path=local_path,
            params={"crtfc_key": api_key, "rcept_no": receipt_no},
            timeout=timeout,
        ):
            local_path = ""

    return FilingDocument(
        market="DART",
        filing_id=receipt_no,
        filing_date=filing_date,
        company_name=clean_text(filing.get("corp_name") or ""),
        stock_code=str(filing.get("stock_code") or ""),
        title=clean_text(filing.get("report_nm") or ""),
        category=str(filing.get("corp_cls") or ""),
        source_url="https://opendart.fss.or.kr/api/list.json",
        document_url=document_url,
        local_path=local_path,
        downloaded_at=utc_now(),
        raw_metadata=json.dumps(filing, ensure_ascii=False, sort_keys=True),
    )


def collect_twse(
    config: Dict, raw_dir: str, download_documents: bool, timeout: int
) -> List[FilingDocument]:
    dataset = config.get("dataset", "t187ap04_L")
    source_url = f"https://openapi.twse.com.tw/v1/opendata/{dataset}"
    session = build_session()
    response = session.get(
        source_url,
        headers={"User-Agent": TWSE_USER_AGENT, "Accept": "application/json,*/*"},
        timeout=timeout,
    )
    response.raise_for_status()
    filings = sorted(response.json(), key=twse_sort_key, reverse=True)
    max_filings = int(config.get("max_filings", 100))
    rows: List[FilingDocument] = []

    for filing in filings:
        if len(rows) >= max_filings:
            break
        if not twse_filing_matches_filters(filing, config):
            continue
        rows.append(
            build_twse_row(
                filing=filing,
                source_url=source_url,
                raw_dir=raw_dir,
                download_documents=download_documents,
            )
        )
        time.sleep(float(config.get("delay_seconds", 0.0)))
    return rows


def twse_filing_matches_filters(filing: Dict, config: Dict) -> bool:
    company_codes = set(str(code) for code in config.get("company_codes") or [])
    if company_codes and str(filing.get("公司代號") or "") not in company_codes:
        return False

    filing_date = parse_twse_roc_date(filing.get("發言日期") or filing.get("出表日期") or "")
    if config.get("start_date") or config.get("end_date"):
        if not filing_date:
            return False
        filing_day = parse_date(filing_date)
        if config.get("start_date") and filing_day < parse_date(config.get("start_date")):
            return False
        if config.get("end_date") and filing_day > parse_date(config.get("end_date")):
            return False

    title = clean_text(filing.get("主旨 ") or filing.get("主旨") or "")
    body = clean_text(filing.get("說明") or "")
    combined_text = f"{title} {body}"
    include_keywords = config.get("include_keywords") or []
    if include_keywords and not any(keyword in combined_text for keyword in include_keywords):
        return False
    exclude_keywords = config.get("exclude_keywords") or []
    if exclude_keywords and any(keyword in combined_text for keyword in exclude_keywords):
        return False
    return True


def twse_sort_key(filing: Dict) -> tuple:
    filing_date = parse_twse_roc_date(filing.get("發言日期") or filing.get("出表日期") or "")
    filing_time = str(filing.get("發言時間") or "").zfill(6)
    company_code = str(filing.get("公司代號") or "")
    return (filing_date, filing_time, company_code)


def build_twse_row(
    filing: Dict, source_url: str, raw_dir: str, download_documents: bool
) -> FilingDocument:
    company_code = str(filing.get("公司代號") or "")
    filing_date = parse_twse_roc_date(filing.get("發言日期") or filing.get("出表日期") or "")
    filing_time = str(filing.get("發言時間") or "")
    title = clean_text(filing.get("主旨 ") or filing.get("主旨") or "")
    category = clean_text(filing.get("符合條款") or "")
    digest = sha1(
        json.dumps(filing, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    filing_id = "_".join(
        part for part in [company_code, filing_date.replace("-", ""), filing_time, digest] if part
    )
    local_path = ""

    if download_documents:
        local_path = os.path.join(
            raw_dir,
            "TWSE",
            filing_date.replace("-", "") or "unknown_date",
            safe_filename(filing_id),
            f"{safe_filename(filing_id)}.json",
        )
        write_json_document(local_path, filing)

    return FilingDocument(
        market="TWSE",
        filing_id=filing_id,
        filing_date=filing_date,
        company_name=clean_text(filing.get("公司名稱") or ""),
        stock_code=company_code,
        title=title,
        category=category,
        source_url=source_url,
        document_url=source_url,
        local_path=local_path,
        downloaded_at=utc_now(),
        raw_metadata=json.dumps(filing, ensure_ascii=False, sort_keys=True),
    )


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        read=5,
        connect=5,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def download_binary(
    session: requests.Session,
    url: str,
    path: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> None:
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return

    os.makedirs(os.path.dirname(path), exist_ok=True)
    response = session.get(
        url,
        params=params,
        headers=headers,
        timeout=timeout,
        stream=True,
    )
    response.raise_for_status()

    temp_fd, temp_path = tempfile.mkstemp(
        prefix=".download_", dir=os.path.dirname(path)
    )
    try:
        with os.fdopen(temp_fd, "wb") as fout:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    fout.write(chunk)
        shutil.move(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def try_download_binary(
    session: requests.Session,
    url: str,
    path: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> bool:
    try:
        download_binary(
            session=session,
            url=url,
            path=path,
            headers=headers,
            params=params,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        print(f"Download failed for {url}: {exc}")
        return False
    return True


def write_json_document(path: str, payload: Dict) -> None:
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return

    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(
        prefix=".download_", dir=os.path.dirname(path), text=True
    )
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as fout:
            json.dump(payload, fout, ensure_ascii=False, indent=2, sort_keys=True)
            fout.write("\n")
        shutil.move(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def write_metadata(path: str, rows: List[Dict[str, str]]) -> None:
    existing_rows: Dict[tuple, Dict[str, str]] = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fin:
            for row in csv.DictReader(fin):
                existing_rows[metadata_key(row)] = row

    for row in rows:
        previous_row = existing_rows.get(metadata_key(row))
        if previous_row and not row.get("local_path") and previous_row.get("local_path"):
            row = dict(row)
            row["local_path"] = previous_row["local_path"]
        existing_rows[metadata_key(row)] = row

    temp_path = f"{path}.tmp"
    with open(temp_path, "w", newline="", encoding="utf-8") as fout:
        writer = csv.DictWriter(fout, fieldnames=METADATA_FIELDS)
        writer.writeheader()
        for row in sorted(existing_rows.values(), key=sort_key):
            writer.writerow({field: row.get(field, "") for field in METADATA_FIELDS})
    shutil.move(temp_path, path)


def metadata_key(row: Dict[str, str]) -> tuple:
    return (row.get("market", ""), row.get("filing_id", ""), row.get("document_url", ""))


def sort_key(row: Dict[str, str]) -> tuple:
    return (row.get("market", ""), row.get("filing_date", ""), row.get("filing_id", ""))


def get_api_key(config: Dict) -> str:
    if config.get("api_key"):
        return str(config["api_key"])
    env_name = config.get("api_key_env")
    return os.environ.get(env_name, "") if env_name else ""


def parse_date(value: Optional[str]) -> date:
    if value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    return date.today()


def date_range(start_date: date, end_date: date) -> Iterable[date]:
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def parse_yyyymmdd(value: str) -> str:
    if not value:
        return ""
    return datetime.strptime(str(value)[:8], "%Y%m%d").date().isoformat()


def parse_hkex_datetime(value: str) -> str:
    if not value:
        return ""
    return datetime.strptime(value, "%d/%m/%Y %H:%M").date().isoformat()


def parse_edinet_datetime(value: str) -> str:
    if not value:
        return ""
    return datetime.strptime(value[:10], "%Y-%m-%d").date().isoformat()


def parse_twse_roc_date(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        if len(value) >= 8 and value[:4].isdigit() and value[:2] in ("19", "20"):
            return datetime.strptime(value[:8], "%Y%m%d").date().isoformat()
        if len(value) >= 7 and value[:3].isdigit():
            year = int(value[:3]) + 1911
            return date(year, int(value[3:5]), int(value[5:7])).isoformat()
    except ValueError:
        return ""
    return ""


def edinet_extension(document_type: str) -> str:
    return {"1": "zip", "2": "pdf", "3": "zip", "4": "zip", "5": "zip"}.get(
        str(document_type), "bin"
    )


def attachment_filename(url: str, fallback: Optional[str] = None) -> str:
    path = unquote(urlparse(url).path)
    filename = os.path.basename(path)
    if not filename or "." not in filename:
        filename = fallback or "document.bin"
    return safe_filename(filename, max_length=180)


def safe_filename(value: str, max_length: int = 120) -> str:
    value = unquote(str(value or "")).strip()
    value = re.sub(r"[^\w.\-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("._")
    if not value:
        value = "unknown"
    return value[:max_length]


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def join_unique(values: Iterable[Optional[str]]) -> str:
    seen = []
    for value in values:
        if value and value not in seen:
            seen.append(str(value))
    return ",".join(seen)


def rot13(value: str) -> str:
    alphabet = str.maketrans(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
        "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm",
    )
    return value.translate(alphabet)


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


if __name__ == "__main__":
    main()
