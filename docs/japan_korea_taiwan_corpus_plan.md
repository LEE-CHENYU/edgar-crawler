# Japan, Korea, Taiwan corpus plan

Goal: build a comprehensive non-US filings corpus while prioritizing new,
high-value disclosures first. Store both raw documents and normalized metadata;
do not put API keys in the repo.

## Priority order

1. Recent high-value issuer reports.
   - Japan EDINET: securities reports, quarterly reports, semiannual reports,
     extraordinary reports, limited to listed issuers with `secCode` first.
   - Korea OpenDART: periodic reports first via `pblntf_ty=A`; then major
     corporate actions and equity issuance reports.
   - Taiwan TWSE-MOPS: material information (`t187ap04_L`) first because it is
     public, current, and text-rich; then validated financial-report/document
     endpoints.
2. Backfill by year/month after the recent pipeline is reliable.
3. Expand to lower-value but comprehensive categories: funds, corrections,
   registrations, exchange notices, and amended filings.

## Japan / EDINET

- Source: EDINET API v2.
- Auth: `EDINET_API_KEY` environment variable.
- Raw document choice:
  - Use PDF (`document_type=2`) for quick text/PDF corpus.
  - Add XBRL ZIP (`document_type=1`) for structured financial extraction.
- Initial filters:
  - `require_sec_code=true` to focus listed issuers first.
  - Include document descriptions containing `有価証券報告書`, `四半期報告書`,
    `半期報告書`, or `臨時報告書`.
  - Exclude investment-trust and correction-only filings for the first pass.
- Comprehensive pass:
  - Remove the first-pass exclusions.
  - Save all document-list rows, including no-document metadata rows, for audit.

## Korea / OpenDART

- Source: OpenDART disclosure list and original document API.
- Auth: `DART_API_KEY` environment variable.
- Raw document choice: `document.xml` endpoint returns ZIP archives containing
  XML disclosure documents.
- Initial filters:
  - `pblntf_ty=A` for periodic reports.
  - Then add major reports and issuance categories as a second tier.
- Comprehensive pass:
  - Iterate all `pblntf_ty` categories and retain `corp_cls`, `stock_code`,
    `report_nm`, and `rcept_no` for deduplication.

## Taiwan / TWSE-MOPS

- Source: TWSE OpenAPI, starting with `t187ap04_L` material information.
- Auth: none for the tested OpenAPI endpoint.
- Raw document choice: each disclosure row is stored as a JSON document because
  the body text is embedded in the OpenAPI response.
- Initial filters:
  - No company filter; take newest material information rows.
  - Optional keyword filters can focus on earnings, major contracts, M&A,
    capital changes, legal events, and trading halts.
- Comprehensive pass:
  - Validate historical MOPS endpoints or document-server flows for financial
    reports and shareholder-meeting documents.
  - Keep OpenAPI material-information snapshots as a high-frequency text corpus
    even before PDF/report endpoints are complete.

## Operational rules

- Use newest dates first, then walk backward by day/month.
- Keep per-market metadata in one append-only CSV keyed by market, filing ID,
  and document URL.
- Keep raw files grouped as `raw/{MARKET}/{YYYYMMDD}/{filing_id}/...`.
- Run small smoke batches before increasing `max_filings`.
- Never commit generated filing data or API keys.
