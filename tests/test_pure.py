"""Tests for the pure helpers -- no API key, no network, no cost.

These cover the functions that fail silently rather than loudly:
`_mentions_container` decides whether expiry recovery happens at all, and
`strip_sandbox_links` is what stops users clicking dead download links. Both
would keep "working" while doing nothing if they regressed, which is exactly
what the paid smoke test is too coarse to catch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import (  # noqa: E402
    DEFAULT_PRICE,
    PRICE_PER_MTOK,
    SANDBOX_PRICE_PER_SESSION,
    estimate_cost,
    sandbox_session_cost,
)
from core.files import guess_mime, safe_filename  # noqa: E402
from core.openai_client import clean_key, describe  # noqa: E402
from core.rendering import strip_sandbox_links  # noqa: E402
from core.session import TurnResult, _mentions_container  # noqa: E402


class TestStripSandboxLinks:
    def test_markdown_link_becomes_its_label(self):
        text = "Download [report.xlsx](sandbox:/mnt/data/report.xlsx) for details."
        assert strip_sandbox_links(text) == "Download report.xlsx for details."

    def test_attachment_scheme_is_defused_too(self):
        text = "See [chart.png](attachment:/mnt/data/chart.png)."
        assert "attachment:" not in strip_sandbox_links(text)

    def test_empty_label_gets_a_readable_stand_in(self):
        assert strip_sandbox_links("[](sandbox:/mnt/data/x.csv)") == "the file below"

    def test_bare_url_collapses_to_the_filename(self):
        assert strip_sandbox_links("Saved to sandbox:/mnt/data/out.pdf") == "Saved to out.pdf"

    def test_ordinary_links_survive(self):
        text = "See [the docs](https://example.com/guide)."
        assert strip_sandbox_links(text) == text

    def test_prose_without_links_is_untouched(self):
        text = "Generation peaked in March at 412 MWh."
        assert strip_sandbox_links(text) == text


class TestMentionsContainer:
    @pytest.mark.parametrize(
        "message",
        [
            "Container cntr_abc123 has expired",
            "container not found",
            "The container is no longer available",
            "This container was deleted",
            "invalid container id",
        ],
    )
    def test_expiry_phrasings_are_recognised(self, message):
        assert _mentions_container(message)

    @pytest.mark.parametrize(
        "message",
        [
            "Rate limit exceeded",
            "invalid api key",
            "model not found",
            # Mentions a container but is not about it having died -- retrying
            # with a fresh sandbox would not help here.
            "container memory_limit must be one of 1g, 4g, 16g, 64g",
        ],
    )
    def test_unrelated_errors_are_not_mistaken_for_expiry(self, message):
        assert not _mentions_container(message)


class TestSafeFilename:
    @pytest.mark.parametrize(
        "raw",
        ["../../etc/passwd", "/etc/passwd", "..\\..\\windows\\system32\\config"],
    )
    def test_directory_traversal_is_stripped(self, raw):
        result = safe_filename(raw)
        assert "/" not in result and "\\" not in result
        assert result not in {".", ".."}

    def test_plain_name_is_preserved(self):
        assert safe_filename("monthly_report.xlsx") == "monthly_report.xlsx"

    def test_mnt_data_prefix_is_stripped(self):
        assert safe_filename("/mnt/data/chart.png") == "chart.png"

    @pytest.mark.parametrize("raw", ["", "   ", ".", ".."])
    def test_unusable_names_fall_back(self, raw):
        assert safe_filename(raw, fallback="download.bin") == "download.bin"


class TestGuessMime:
    def test_known_extension(self):
        assert guess_mime("data.csv") == "text/csv"

    def test_extension_case_is_ignored(self):
        assert guess_mime("REPORT.XLSX") == guess_mime("report.xlsx")

    def test_unknown_extension_falls_back_to_octet_stream(self):
        assert guess_mime("mystery.zzz") == "application/octet-stream"


class TestCleanKey:
    def test_whole_env_line_is_reduced_to_the_key(self):
        assert clean_key("OPENAI_API_KEY=sk-test123") == "sk-test123"

    def test_surrounding_quotes_are_removed(self):
        assert clean_key('"sk-test123"') == "sk-test123"
        assert clean_key("'sk-test123'") == "sk-test123"

    def test_env_line_with_quotes_and_spaces(self):
        assert clean_key('  OPENAI_API_KEY = "sk-test123"  ') == "sk-test123"

    def test_a_normal_key_is_left_alone(self):
        assert clean_key("sk-test123") == "sk-test123"

    def test_blank_input_stays_blank(self):
        assert clean_key("   ") == ""


class TestDescribeNeverLeaksTheKey:
    def test_the_middle_of_the_key_is_never_shown(self):
        key = "sk-proj-SECRETMIDDLESECTION-1234"
        assert "SECRETMIDDLESECTION" not in describe(key)

    def test_absent_and_empty_are_distinguishable(self):
        assert describe(None) == "absent"
        assert "empty" in describe("   ")


class TestEstimateCost:
    def test_priced_model_uses_its_own_rate(self):
        price = PRICE_PER_MTOK["gpt-4.1"]
        expected = (1_000_000 * price["input"] + 1_000_000 * price["output"]) / 1_000_000
        assert estimate_cost("gpt-4.1", 1_000_000, 1_000_000) == pytest.approx(expected)

    def test_unknown_model_falls_back_rather_than_raising(self):
        expected = (1_000_000 * DEFAULT_PRICE["input"]) / 1_000_000
        assert estimate_cost("some-unreleased-model", 1_000_000, 0) == pytest.approx(expected)

    def test_zero_usage_is_free(self):
        assert estimate_cost("gpt-4.1", 0, 0) == 0.0


class TestSandboxSessionCost:
    @pytest.mark.parametrize("tier", list(SANDBOX_PRICE_PER_SESSION))
    def test_every_tier_is_priced(self, tier):
        assert sandbox_session_cost(tier) == SANDBOX_PRICE_PER_SESSION[tier]

    def test_unknown_tier_does_not_silently_become_free(self):
        # A meter that drops a charge it does not recognise reads low, which is
        # the failure mode this whole feature exists to prevent.
        assert sandbox_session_cost("128g") > 0


class TestTurnCost:
    def test_sandbox_fee_lands_on_the_turn_that_opened_the_container(self):
        result = TurnResult(input_tokens=1000, output_tokens=500, container_started=True)
        assert result.sandbox_cost_usd == sandbox_session_cost()
        assert result.cost_usd == pytest.approx(result.token_cost_usd + result.sandbox_cost_usd)

    def test_later_turns_carry_tokens_only(self):
        result = TurnResult(input_tokens=1000, output_tokens=500, container_started=False)
        assert result.sandbox_cost_usd == 0.0
        assert result.cost_usd == pytest.approx(result.token_cost_usd)

    def test_sandbox_fee_dominates_a_short_conversation(self):
        # The reason token-only accounting was wrong: at typical turn sizes the
        # container costs more than everything the model said.
        result = TurnResult(input_tokens=2000, output_tokens=500, container_started=True)
        assert result.sandbox_cost_usd > result.token_cost_usd

    def test_cost_follows_the_model_actually_used(self):
        # Not config.MODEL -- the smoke test can override the model per run.
        cheap = TurnResult(input_tokens=1_000_000, output_tokens=0, model="gpt-4.1-mini")
        dear = TurnResult(input_tokens=1_000_000, output_tokens=0, model="gpt-4.1")
        assert cheap.token_cost_usd < dear.token_cost_usd
