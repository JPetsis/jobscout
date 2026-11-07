"""Detail extraction: the JSON-LD tag shape, and explaining an empty pane.

The regex bug this guards was invisible and expensive. `_LD` required `type=` to
be the first attribute of the script tag, but XING ships
`<script data-ch type="application/ld+json">`. The JSON-LD path therefore never
ran there, the extractor fell through to og:description -- a social-sharing
blurb -- and XING postings looked like they had no description at all while the
page was carrying thousands of characters. Measured on live pages: the old
pattern missed 4 of 4 that loaded.
"""
import pytest

from jobscout.detail import _LD, html_detail, html_detail_verbose


def test_the_early_applicant_sentence_is_recognised():
    """LinkedIn states it in the advert ("Be among the first 25 applicants",
    "Gehören Sie zu den ersten 25 Bewerber:innen"); a detail fetch is where a
    posting whose card did not mention it can still learn it."""
    from jobscout.detail import says_early_applicant

    assert says_early_applicant("Gehören Sie zu den ersten 25 Bewerber:innen.")
    assert says_early_applicant("Be among the first 25 applicants")
    # XING's detail pill, in both locales, as it actually ships
    assert says_early_applicant(
        '<span class="job-details-x" data-xds="BodyCopy">Be an early applicant</span>')
    assert says_early_applicant(
        '<span class="job-details-x" data-xds="BodyCopy">Zu den Ersten gehören</span>')
    assert says_early_applicant('"type":"job_highlight_early_applicant"')
    assert not says_early_applicant("Bewerben Sie sich mit Ihren Unterlagen.")
    assert not says_early_applicant("")


class FakeFetcher:
    """Stands in for http.Fetcher: one canned response, no network."""

    def __init__(self, status=200, body=""):
        self.status = status
        self.body = body
        self.requested = []

    def get(self, url, **kw):
        self.requested.append(url)
        return self.status, self.body


# --- the tag shape ---------------------------------------------------------

@pytest.mark.parametrize("tag", [
    '<script type="application/ld+json">',
    '<script data-ch type="application/ld+json">',          # the XING shape
    "<script type='application/ld+json'>",
    '<script data-ch type="application/ld+json" nonce="abc">',
    '<script  type="application/ld+json"  id="x">',
])
def test_any_attribute_order_is_matched(tag):
    assert _LD.search(tag + "{}" + "</script>")


@pytest.mark.parametrize("tag", [
    '<script type="application/json">',
    '<script src="x.js">',
    '<script data-ldjson>',
])
def test_other_script_tags_are_not_matched(tag):
    assert not _LD.search(tag + "{}" + "</script>")


# --- what the extractor returns -------------------------------------------

def _page(ld=None, og=None, body=""):
    parts = ["<html><head>"]
    if og:
        parts.append(f'<meta property="og:description" content="{og}">')
    if ld:
        parts.append(f'<script data-ch type="application/ld+json">{ld}</script>')
    parts.append("</head><body>" + body + "</body></html>")
    return "".join(parts)


def test_the_template_nulls_after_the_description_are_dropped():
    """Reported on the Liotec advert: the text ended with "null null null …".

    XING's own JSON-LD carries them, ten times, after the last paragraph: a
    server-side template printing a missing value. Stripped in extraction, so
    every advert opened from now on is clean.
    """
    import json
    from jobscout.detail import html_detail_page
    ld = json.dumps({"@type": "JobPosting",
                     "description": "<p>Schick uns gern Links zu GitHub.</p>"
                                    "\n null\n null\n null\n null"})
    text, why, _raw = html_detail_page("https://example.test/job",
                                       FakeFetcher(body=_page(ld=ld, og="")))
    assert why == ""
    assert text.endswith("Schick uns gern Links zu GitHub.")
    assert "null" not in text


def test_json_ld_description_wins_over_the_social_blurb():
    """The whole bug in one test: both are present, the long real one must win."""
    ld = '{"@type": "JobPosting", "description": "<p>' + ("Real advert text. " * 30) + '</p>"}'
    page = _page(ld=ld, og="Short social blurb")
    text, note = html_detail_verbose("https://x/1", FakeFetcher(200, page))
    assert "Real advert text." in text
    assert len(text) > 300
    assert note == ""


def test_a_withdrawn_posting_says_so():
    text, note = html_detail_verbose("https://x/1", FakeFetcher(410, "<html></html>"))
    assert text == ""
    assert "410" in note and "withdrawn" in note


def test_a_404_also_reads_as_gone():
    text, note = html_detail_verbose("https://x/1", FakeFetcher(404, ""))
    assert text == ""
    assert "gone" in note or "withdrawn" in note


def test_a_listing_with_no_link_is_explained_without_asking_anyone():
    f = FakeFetcher(200, "<html></html>")
    text, note = html_detail_verbose("", f)
    assert text == ""
    assert "no link" in note
    assert f.requested == [], "there is nothing to request"


def test_a_page_that_publishes_no_text_says_that():
    text, note = html_detail_verbose("https://x/1", FakeFetcher(200, _page()))
    assert text == ""
    assert "does not publish" in note


def test_a_network_error_is_reported_not_raised():
    class Boom:
        def get(self, url, **kw):
            raise ValueError("unknown url type: ''")

    text, note = html_detail_verbose("https://x/1", Boom())
    assert text == ""
    assert "Could not reach" in note


def test_other_http_errors_are_reported():
    text, note = html_detail_verbose("https://x/1", FakeFetcher(503, ""))
    assert text == ""
    assert "503" in note


def test_the_plain_extractor_still_returns_just_text():
    """The source adapters' contract is `str | None`, and it must stay that."""
    ld = '{"@type": "JobPosting", "description": "Some text"}'
    out = html_detail("https://x/1", FakeFetcher(200, _page(ld=ld)))
    assert out == "Some text"
    assert html_detail("https://x/1", FakeFetcher(410, "")) is None


# --- the source that dropped listings it could not link --------------------

def test_xing_drops_a_card_with_no_link():
    """Such a row can never be opened, verified or fetched, so it reaches the
    window as a match that dead-ends the user."""
    from jobscout.sources import xing
    card = ('data-testid="job-search-result" '
            '<div data-testid="job-teaser-list-title">Broken card</div>')
    good = ('data-testid="job-search-result" '
            'href="/jobs/berlin-php-developer-123456" '
            '<div data-testid="job-teaser-list-title">PHP Developer</div>')
    postings = xing.parse(card + good)
    assert len(postings) == 1
    assert postings[0].url.endswith("123456")


def test_xing_counts_what_it_dropped():
    """Silently returning fewer postings looks like a quiet day on XING."""
    from jobscout.sources import xing
    from jobscout.http import Fetcher

    card = ('data-testid="job-search-result" '
            '<div data-testid="job-teaser-list-title">Broken card</div>')
    f = Fetcher()
    f.get = lambda url, **kw: (200, card)          # every card is unlinkable

    out = xing.fetch({"queries": [{"keywords": "php", "pages": 1}]}, f)
    assert out == []
    # A skipped card is a note, not a failure: it must not turn the health row
    # or the scan summary into "XING is broken".
    assert any("no link" in n for n in f.notes), f.notes
    assert not any("no link" in e for e in f.errors), f.errors
