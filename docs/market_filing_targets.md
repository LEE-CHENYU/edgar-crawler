# Market filing targets

Initial priority is based on official availability, low friction, and whether the
source can return direct filing documents without browser automation.

## Easy first

- HKEX: listed issuer title search returns JSON metadata and direct PDF links.
  No key required. Implemented in `download_market_filings.py`.
- SGX: company announcements API returns metadata and announcement landing pages
  with document attachments. No user API key required, but SGX requires a short
  authorization token exposed by its public site config. Implemented in
  `download_market_filings.py`.

## Key-gated official APIs

- EDINET: official API v2 exposes daily document lists and document downloads.
  Requires an API key passed as `Subscription-Key`. Implemented and skipped when
  `EDINET_API_KEY` is not set.
- Korea DART/OpenDART: official API exposes disclosure search and original
  document download. Requires `crtfc_key`. Implemented and skipped when
  `DART_API_KEY` is not set.

## Validated next targets

- Mainland China / CNINFO: announcement search returns JSON metadata and direct
  PDF links from `static.cninfo.com.cn/finalpage/...pdf`. Validated with a real
  PDF response. Good next implementation target.
- Philippines / PSE EDGE: `/announcements/search.ax` returns announcement rows,
  `/openDiscViewer.do?edge_no=...` exposes `downloadHtml.do?file_id=...`, and
  `downloadFile.do?file_id=...` returns downloadable HTML filings. Validated with
  a real downloaded HTML response. Good next implementation target.
- New Zealand / NZX: public API at `https://api.nzx.com/public` returns
  announcement JSON and direct attachment PDFs. Validated with a real PDF
  response. Good next implementation target.
- Australia / ASX: Markit ASX research API returns announcement metadata from
  `https://asx.api.markitdigital.com/asx-research/1.0/companies/{symbol}/announcements`.
  Direct PDF URL mapping from `documentKey` still needs validation before
  implementation.
- Taiwan / TWSE-MOPS: TWSE OpenAPI returns material information JSON
  (`t187ap04_L`) and other listed-company datasets. The document PDF server path
  timed out from this environment, so this is currently metadata/text-first
  unless the MOPS document URL flow is validated.

## Blocked or not yet practical from this environment

- Bursa Malaysia: official announcement search is protected by Cloudflare from
  this environment, so it is not a good first automated target.
- Thailand / SET: public page is reachable, but API calls returned Incapsula
  blocks from this environment.
- Indonesia / IDX: attempted announcement API endpoints returned Cloudflare
  blocks from this environment.
