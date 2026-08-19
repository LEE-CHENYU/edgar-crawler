"""Oslo: the annual reports are attachments, not the documents we downloaded.

The crawler fetched /v1/newsreader/pdf?messageId=X for all 8,134 announcements
-- the announcement COVER SHEET. Median 7 KB, max 0.1 MB, 100% under 50 KB; no
annual report is 7 KB, which is why stage 02 yielded 2.2%. The metadata even
recorded numbAttachments, so the crawler knew the real documents existed.

7,690 of 8,134 announcements (94.5%) carry attachments -- 12,784 documents --
and every row is category "ANNUAL FINANCIAL REPORT". Verified end to end: one
attachment is a 20-page report that extracts 11 metrics.
"""

import pytest

from scripts.oslo_attachment_fetch import (
    attachment_url, is_probably_report, message_url, parse_attachments,
)


def test_message_url_shape():
    assert message_url(420146).endswith("/v1/newsreader/message?messageId=420146")


def test_attachment_url_needs_both_ids():
    """attachmentId alone returns 400 -- messageId is required (probed live)."""
    url = attachment_url(420146, 23798)
    assert "messageId=420146" in url and "attachmentId=23798" in url


def test_parse_attachments_reads_the_api_shape():
    payload = {"data": {"message": {"numbAttachments": 2, "attachments": [
        {"id": 23798, "name": "Rapport 4 kvartal 2016.pdf"},
        {"id": 23799, "name": "Presentasjon for 4 kvartal 2016.pdf"}]}}}
    got = parse_attachments(payload)
    assert got == [(23798, "Rapport 4 kvartal 2016.pdf"),
                   (23799, "Presentasjon for 4 kvartal 2016.pdf")]


def test_parse_attachments_tolerates_a_message_with_none():
    assert parse_attachments({"data": {"message": {"numbAttachments": 0}}}) == []
    assert parse_attachments({}) == []


def test_presentations_are_not_mistaken_for_reports():
    """A slide deck carries headline numbers but not statements; keeping both is
    fine, but the report must be identifiable."""
    assert is_probably_report("Rapport 4 kvartal 2016.pdf") is True
    assert is_probably_report("Arsrapport 2019.pdf") is True
    assert is_probably_report("Annual Report 2020.pdf") is True
    assert is_probably_report("Presentasjon for 4 kvartal 2016.pdf") is False
    assert is_probably_report("Q4 2016 presentation.pdf") is False


def test_non_pdf_attachments_are_not_reports():
    assert is_probably_report("kvartalstall.xlsx") is False
