#!/usr/bin/env python3
"""Probe official filing sources for markets the corpus does not yet cover.

Follows the format established by europe_source_discovery.{md,json}: one row per
source with a status from the shared vocabulary, whether a REAL DOCUMENT was
obtained, the evidence, and the next action.

The status that matters is `validated_docs`. Per practices 1.6, a 200 on a
landing page is not a validated source -- ten European sources returned 200 and
were listed "reachable" while Bundesanzeiger turned out to be captcha-guarded
and CRO returned 403, and meanwhile opendata.cro.ie (never probed) served 1.39M
rows. So each candidate is probed for a downloadable artifact, not a homepage.

Markets covered here are the gaps identified 2026-08-18: Switzerland and Saudi
Arabia (the two largest absences, ~$2T and ~$2.4T), India NSE (we hold BSE
only), all of ASEAN except the Philippines, Latin America, and the
Middle East / Africa exchanges.

Rate: one request at a time with a descriptive UA and a contact address.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

UA = "stock-journal research crawler (fretin13@gmail.com)"
MONITORING = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/monitoring")

# source_id -> {countries, why, candidates:[{url, kind, note}]}
# kind: "api" (expect JSON), "doc" (expect a binary document), "page" (HTML)
SOURCES: Dict[str, dict] = {
    "CH_SIX_EXCHANGE": {
        "countries": ["CH"],
        "why": "~$2T; Nestle/Roche/Novartis/ABB. Not EU, so ESEF never reached it -- the largest gap created purely by regulatory geography.",
        "candidates": [
            {"url": "https://www.six-group.com/fqs/ref.json?select=ValorSymbol,ShortName&where=ValorId&api_key=", "kind": "api"},
            {"url": "https://www.ser-ag.com/en/resources/notifications-market-participants.html", "kind": "page"},
            {"url": "https://www.six-exchange-regulation.com/en/home/publications/explorer.html", "kind": "page"},
        ],
    },
    "SA_TADAWUL": {
        "countries": ["SA"],
        "why": "~$2.4T incl. Aramco -- larger than any European exchange we cover.",
        "candidates": [
            {"url": "https://www.saudiexchange.sa/wps/portal/saudiexchange/newsandreports/issuer-news", "kind": "page"},
            {"url": "https://www.saudiexchange.sa/Resources/fsPdf/", "kind": "page"},
        ],
    },
    "IN_NSE": {
        "countries": ["IN"],
        "why": "We hold BSE only (68,091 filings). NSE is the larger exchange by volume with better large-cap coverage.",
        "candidates": [
            {"url": "https://www.nseindia.com/api/annual-reports?index=equities&symbol=RELIANCE", "kind": "api"},
            {"url": "https://www.nseindia.com/companies-listing/corporate-filings-annual-reports", "kind": "page"},
        ],
    },
    "ID_IDX": {
        "countries": ["ID"],
        "why": "~$700B, largest ASEAN market absent from the corpus.",
        "candidates": [
            {"url": "https://www.idx.co.id/primary/ListedCompany/GetAnnualReport?indexFrom=1&pageSize=10&year=2023&reportType=rdf", "kind": "api"},
            {"url": "https://www.idx.co.id/en/listed-companies/financial-statements-and-annual-report", "kind": "page"},
        ],
    },
    "SG_SGX": {
        "countries": ["SG"],
        "why": "~$600B; regional HQ listings (DBS, Singtel, Jardine).",
        "candidates": [
            {"url": "https://api.sgx.com/announcements/v1.0/?periodstart=2024-01-01T00%3A00%3A00Z&periodend=2024-01-31T23%3A59%3A59Z&cat=ANNC&sub=ANNC13&pagestart=0&pagesize=50", "kind": "api"},
            {"url": "https://www.sgx.com/securities/company-announcements", "kind": "page"},
        ],
    },
    "TH_SET": {
        "countries": ["TH"],
        "why": "ASEAN top-3 by cap; SET publishes 56-1 One Report annually.",
        "candidates": [
            {"url": "https://www.set.or.th/api/set/company/list?securityType=S&language=en", "kind": "api"},
            {"url": "https://www.set.or.th/en/market/news-and-alert/news", "kind": "page"},
        ],
    },
    "MY_BURSA": {
        "countries": ["MY"],
        "why": "ASEAN top-4; Bursa announcements carry annual reports.",
        "candidates": [
            {"url": "https://www.bursamalaysia.com/api/v1/announcements/search?ann_type=company&cat=AR&sub_type=&company=&keyword=&from_date=2024-01-01&to_date=2024-12-31&page=1&per_page=20", "kind": "api"},
            {"url": "https://www.bursamalaysia.com/market_information/announcements/company_announcement", "kind": "page"},
        ],
    },
    "VN_HOSE": {
        "countries": ["VN"],
        "why": "Fast-growing frontier market; HOSE is the main board.",
        "candidates": [
            {"url": "https://www.hsx.vn/Modules/Listed/Web/SymbolView/", "kind": "page"},
            {"url": "https://congbothongtin.ssc.gov.vn/faces/NewsSearch", "kind": "page"},
        ],
    },
    "BR_CVM_B3": {
        "countries": ["BR"],
        "why": "~$800B, largest Latin American market. CVM publishes open data (dados abertos).",
        "candidates": [
            {"url": "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/", "kind": "page"},
            {"url": "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_2023.zip", "kind": "doc"},
        ],
    },
    "MX_BMV": {
        "countries": ["MX"],
        "why": "Second-largest Latin American market.",
        "candidates": [
            {"url": "https://www.bmv.com.mx/es/emisoras/informacion-financiera", "kind": "page"},
            {"url": "https://www.gob.mx/cnbv", "kind": "page"},
        ],
    },
    "ZA_JSE": {
        "countries": ["ZA"],
        "why": "Largest African market; many dual-listed majors.",
        "candidates": [
            {"url": "https://clientportal.jse.co.za/communication/sens-announcements", "kind": "page"},
            {"url": "https://www.jse.co.za/news/sens", "kind": "page"},
        ],
    },
    "TR_KAP": {
        "countries": ["TR"],
        "why": "BIST disclosures are centralised in KAP, which is usually open.",
        "candidates": [
            {"url": "https://www.kap.org.tr/tr/api/company/list", "kind": "api"},
            {"url": "https://www.kap.org.tr/en/bildirim-sorgu", "kind": "page"},
        ],
    },
    "IL_TASE_MAYA": {
        "countries": ["IL"],
        "why": "TASE's MAYA carries issuer reports; TASE also has a documented data API.",
        "candidates": [
            {"url": "https://mayaapi.tase.co.il/api/company/companies?lang=1", "kind": "api"},
            {"url": "https://maya.tase.co.il/reports/company", "kind": "page"},
        ],
    },
    "AE_ADX_DFM": {
        "countries": ["AE"],
        "why": "ADX + DFM; large listed energy/banking issuers.",
        "candidates": [
            {"url": "https://www.adx.ae/english/pages/products/equity/disclosures.aspx", "kind": "page"},
            {"url": "https://www.dfm.ae/en/the-exchange/news-disclosures/company-disclosures", "kind": "page"},
        ],
    },
    "QA_QSE": {
        "countries": ["QA"],
        "why": "Qatar Stock Exchange financial statements page.",
        "candidates": [
            {"url": "https://www.qe.com.qa/financial-statements", "kind": "page"},
            {"url": "https://www.qe.com.qa/company-announcement", "kind": "page"},
        ],
    },
}

DOC_MAGIC = (b"%PDF", b"PK\x03\x04")


def classify(resp, kind: str, body: bytes) -> str:
    """Status from the shared vocabulary used by europe_source_discovery."""
    code = resp.status_code
    ctype = (resp.headers.get("content-type") or "").lower()
    lowered = body[:4000].lower()
    if code in (401, 403):
        if b"incapsula" in lowered or b"cloudflare" in lowered or b"radware" in lowered:
            return "antibot_blocked"
        return "blocked_requires_session_or_key"
    if code == 404:
        return "wrong_path_or_needs_discovery"
    if code >= 500:
        return "server_error"
    if code != 200:
        return f"http_{code}"
    if body[:4] in DOC_MAGIC or "pdf" in ctype or "zip" in ctype or "octet-stream" in ctype:
        return "validated_docs"
    if "json" in ctype:
        return "validated_api" if len(body) > 200 else "api_empty_response"
    if b"captcha" in lowered or b"are you a robot" in lowered:
        return "reachable_but_antibot_risk"
    if b"__next_data__" in lowered or b"ng-version" in lowered or b"window.__nuxt__" in lowered:
        return "reachable_spa_needs_api_reverse_engineering"
    return "reachable_html_needs_discovery"


def probe(url: str, kind: str, timeout: int, sleep: float) -> dict:
    import requests
    time.sleep(sleep)
    out = {"url": url, "kind": kind}
    try:
        resp = requests.get(url, headers={"User-Agent": UA, "Accept": "*/*"},
                            timeout=timeout, allow_redirects=True)
        body = resp.content[:200000]
        out.update({
            "http": resp.status_code,
            "content_type": (resp.headers.get("content-type") or "")[:60],
            "bytes": len(resp.content),
            "status": classify(resp, kind, body),
        })
    except Exception as exc:
        name = type(exc).__name__
        out.update({"http": None, "bytes": 0,
                    "status": {"ConnectTimeout": "timeout", "ReadTimeout": "timeout",
                               "SSLError": "ssl_certificate_verification_failed",
                               "ConnectionError": "connection_closed_or_timeout"}.get(name, f"error:{name}"),
                    "error": f"{name}: {str(exc)[:120]}"})
    return out


RANK = ["validated_docs", "validated_api", "reachable_html_needs_discovery",
        "reachable_spa_needs_api_reverse_engineering", "reachable_but_antibot_risk",
        "api_empty_response", "blocked_requires_session_or_key", "antibot_blocked",
        "wrong_path_or_needs_discovery", "server_error", "timeout",
        "connection_closed_or_timeout", "ssl_certificate_verification_failed"]


def best(results: List[dict]) -> str:
    for status in RANK:
        if any(r["status"] == status for r in results):
            return status
    return results[0]["status"] if results else "not_probed"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=MONITORING)
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--sleep", type=float, default=1.5)
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()

    rows = []
    for sid, cfg in SOURCES.items():
        if args.only and sid not in args.only:
            continue
        print(f"--- {sid} ({','.join(cfg['countries'])})", flush=True)
        results = [probe(c["url"], c["kind"], args.timeout, args.sleep)
                   for c in cfg["candidates"]]
        for r in results:
            print(f"      {r['status']:45} http={r.get('http')} {r.get('bytes')}B  {r['url'][:70]}", flush=True)
        rows.append({
            "source_id": sid, "countries": cfg["countries"], "why": cfg["why"],
            "status": best(results), "direct_docs": any(r["status"] == "validated_docs" for r in results),
            "probes": results,
        })

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    (args.out_dir / "global_source_discovery.json").write_text(
        json.dumps({"generated": stamp, "sources": rows}, indent=1))

    lines = ["# Global Market Source Discovery (non-covered markets)", "",
             f"Generated: {stamp}", "",
             "Markets absent from the corpus as of 2026-08-18. Follows the format of",
             "`europe_source_discovery.md`. A source counts as usable only at",
             "`validated_docs` -- a 200 on a landing page is not a validated source",
             "(practices 1.6).", "",
             "| Source | Countries | Status | Direct Docs | Why it matters | Best probe |",
             "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: RANK.index(x["status"]) if x["status"] in RANK else 99):
        top = min(r["probes"], key=lambda p: RANK.index(p["status"]) if p["status"] in RANK else 99)
        lines.append(f"| {r['source_id']} | {', '.join(r['countries'])} | {r['status']} | "
                     f"{r['direct_docs']} | {r['why']} | `{top['url'][:60]}` http={top.get('http')} {top.get('bytes')}B |")
    (args.out_dir / "global_source_discovery.md").write_text("\n".join(lines) + "\n")
    print(f"\nwrote {args.out_dir/'global_source_discovery.md'}")
    ok = sum(1 for r in rows if r["direct_docs"])
    api = sum(1 for r in rows if r["status"] == "validated_api")
    print(f"{len(rows)} sources probed: {ok} with direct docs, {api} validated APIs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
