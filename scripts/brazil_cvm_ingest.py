#!/usr/bin/env python3
"""Ingest Brazilian CVM DFP filings into canonical_metrics_wide form.

dados.cvm.gov.br publishes annual financial statements (DFP) as yearly ZIPs of
CSV, 2010-2026, under open data terms. Unlike every PDF corpus in this program
it ships the four fields whose absence caused most of the defects:

    MOEDA         reporting currency
    ESCALA_MOEDA  unit scale  (MIL / UNIDADE / MILHAO)
    DT_FIM_EXERC  real period end
    con / ind     consolidated vs parent, in separate files

Three traps the format sets:

  1. ORDEM_EXERC is ÚLTIMO or PENÚLTIMO -- every file carries the current AND
     prior year. Keeping both is the HK comparative-column defect that merged
     values 1000x apart; here it is labelled, so it is filtered.
  2. Column ORDER differs by statement -- DRE and DFC carry DT_INI_EXERC, the
     balance sheets do not. Everything is parsed by header name.
  3. Account CODES are not stable across sectors: 2.03 is "Patrimônio Líquido"
     for an industrial and "Passivos Financeiros ao Custo Amortizado" for a
     bank. Mapping leads with the DESCRIPTION and uses the code only to reject
     deep sub-accounts.

Usage:
    python scripts/brazil_cvm_ingest.py --years 2023
    python scripts/brazil_cvm_ingest.py            # all years
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import unicodedata
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS"
UA = "stock-journal research crawler (fretin13@gmail.com)"
OUT_ROOT = Path("/Users/lichenyu/datasets/markets/br/01_raw/cvm_dfp")
DERIVED = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived/BR_CVM_FINANCIALS")

SCALES = {"MIL": 1000, "UNIDADE": 1, "MILHAO": 1_000_000, "MILHÃO": 1_000_000}


def _fold(text: str) -> str:
    """Accent-insensitive upper: Latin-1 round-trips have burned this corpus."""
    norm = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in norm if not unicodedata.combining(c)).strip().upper()


def is_current_period(ordem: str) -> bool:
    """ÚLTIMO only. PENÚLTIMO is the comparative column."""
    return _fold(ordem) == "ULTIMO"


def parse_scale(escala: str) -> Optional[int]:
    """Multiplier, or None when unstated -- never a default of 1."""
    return SCALES.get(_fold(escala))


def reporting_basis_for(grupo: str) -> str:
    folded = _fold(grupo)
    if "CONSOLIDADO" in folded:
        return "consolidated"
    if "INDIVIDUAL" in folded:
        return "parent"
    return "unknown"


# Description-first mapping. Codes vary by sector; descriptions are stable.
_BY_DESCRIPTION = {
    "ATIVO TOTAL": "total_assets",
    "PASSIVO TOTAL": "total_liabilities",
    "ATIVO CIRCULANTE": "current_assets",
    "PASSIVO CIRCULANTE": "current_liabilities",
    "ATIVO NAO CIRCULANTE": "non_current_assets",
    "PASSIVO NAO CIRCULANTE": "non_current_liabilities",
    "PATRIMONIO LIQUIDO": "total_equity",
    "PATRIMONIO LIQUIDO CONSOLIDADO": "total_equity",
    "CAIXA E EQUIVALENTES DE CAIXA": "cash_and_equivalents",
    "ESTOQUES": "inventories",
    "CONTAS A RECEBER": "accounts_receivable",
    "RECEITA DE VENDA DE BENS E/OU SERVICOS": "revenue",
    "CUSTO DOS BENS E/OU SERVICOS VENDIDOS": "cost_of_revenue",
    "RESULTADO BRUTO": "gross_profit",
    "RESULTADO ANTES DOS TRIBUTOS SOBRE O LUCRO": "profit_before_tax",
    "LUCRO/PREJUIZO DO PERIODO": "net_income",
    "LUCRO/PREJUIZO CONSOLIDADO DO PERIODO": "net_income",
    "CAIXA LIQUIDO ATIVIDADES OPERACIONAIS": "operating_cash_flow",
    "CAIXA LIQUIDO ATIVIDADES DE INVESTIMENTO": "investing_cash_flow",
    "CAIXA LIQUIDO ATIVIDADES DE FINANCIAMENTO": "financing_cash_flow",
}
# A line this deep is a sub-account, never a statement total.
_MAX_CODE_DEPTH = 3


def metric_for(code: str, description: str) -> Optional[str]:
    """Canonical metric for one account line, or None."""
    if str(code or "").count(".") >= _MAX_CODE_DEPTH:
        return None
    return _BY_DESCRIPTION.get(_fold(description))


def rows_from_zip(data: bytes, year: int, drops: Counter) -> List[dict]:
    """Panel-shaped rows from one yearly DFP zip."""
    grouped: Dict[tuple, dict] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if not re.search(r"_(BPA|BPP|DRE|DFC_M[ID])_(con|ind)_", name):
                continue
            with zf.open(name) as fh:
                text = io.TextIOWrapper(fh, encoding="latin-1", newline="")
                for rec in csv.DictReader(text, delimiter=";"):
                    if not is_current_period(rec.get("ORDEM_EXERC", "")):
                        drops["comparative_period"] += 1
                        continue
                    metric = metric_for(rec.get("CD_CONTA"), rec.get("DS_CONTA"))
                    if metric is None:
                        drops["unmapped_account"] += 1
                        continue
                    basis = reporting_basis_for(rec.get("GRUPO_DFP", ""))
                    key = (rec.get("CD_CVM"), rec.get("DT_FIM_EXERC"), basis)
                    row = grouped.get(key)
                    if row is None:
                        row = {
                            "ticker": str(rec.get("CD_CVM") or "").strip(),
                            "company_name": (rec.get("DENOM_CIA") or "").strip() or None,
                            "cnpj": rec.get("CNPJ_CIA"),
                            "period_end": rec.get("DT_FIM_EXERC"),
                            "fiscal_year": int(str(rec.get("DT_FIM_EXERC"))[:4] or year),
                            "reporting_basis": basis,
                            "currency": "BRL" if _fold(rec.get("MOEDA")) == "REAL" else rec.get("MOEDA"),
                            "unit_scale": parse_scale(rec.get("ESCALA_MOEDA", "")),
                            "unit_scale_source": "declared" if parse_scale(rec.get("ESCALA_MOEDA", "")) else "unknown",
                            "doc_id": f"{rec.get('CD_CVM')}_{rec.get('DT_FIM_EXERC')}_{basis}",
                            "n_metrics": 0,
                        }
                        grouped[key] = row
                    try:
                        row[metric] = float(rec.get("VL_CONTA") or 0)
                        row["n_metrics"] += 1
                    except (TypeError, ValueError):
                        drops["unparseable_value"] += 1
    return list(grouped.values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--years", nargs="*", type=int, default=list(range(2010, 2027)))
    ap.add_argument("--out-dir", type=Path, default=DERIVED)
    ap.add_argument("--raw-dir", type=Path, default=OUT_ROOT)
    args = ap.parse_args()

    import requests
    import pandas as pd

    args.raw_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    all_rows: List[dict] = []
    drops: Counter = Counter()

    for year in args.years:
        url = f"{BASE}/dfp_cia_aberta_{year}.zip"
        local = args.raw_dir / f"dfp_cia_aberta_{year}.zip"
        if local.exists() and local.stat().st_size > 0:
            data = local.read_bytes()
        else:
            try:
                resp = session.get(url, timeout=180)
                resp.raise_for_status()
                data = resp.content
                local.write_bytes(data)
            except Exception as exc:
                print(f"  {year}: FAILED {type(exc).__name__}", flush=True)
                drops[f"year_failed_{year}"] += 1
                continue
        rows = rows_from_zip(data, year, drops)
        all_rows.extend(rows)
        print(f"  {year}: {len(rows):,} company-periods "
              f"({len(data)/1048576:.1f} MB)", flush=True)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(all_rows)
    out = args.out_dir / "canonical_metrics_wide.parquet"
    df.to_parquet(out, index=False)
    print(f"\nwrote {out} ({len(df):,} rows)")
    print(f"  basis: {df.reporting_basis.value_counts().to_dict() if len(df) else {}}")
    print(f"  scale: {df.unit_scale.value_counts(dropna=False).to_dict() if len(df) else {}}")
    print(f"  drops: {dict(drops.most_common(5))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
