"""
Unit tests for alerts.py — Gmail email alert logic.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import CashFlowQuality, FinancialMetrics, ScreeningResult
from app.services.alerts import maybe_send_alert, send_alert, should_alert

SMTP_ENV = {
    "SMTP_USER": "pereiraelian18@gmail.com",
    "SMTP_PASSWORD": "test_app_password",
}


def _make_result(z_score=3.5, f_score=8, cf_ratio=1.2) -> ScreeningResult:
    return ScreeningResult(
        ticker="TEST",
        company_name="Test Corp",
        financials=FinancialMetrics(z_score=z_score, f_score=f_score),
        cash_flow_quality=CashFlowQuality(
            net_income=100, operating_cash_flow=int(100 * cf_ratio)
        ),
    )


class TestShouldAlert:
    def test_triggers_when_all_thresholds_met(self):
        assert should_alert(_make_result(z_score=3.5, f_score=8)) is True

    def test_does_not_trigger_below_z_threshold(self):
        assert should_alert(_make_result(z_score=2.5, f_score=8)) is False

    def test_does_not_trigger_below_f_threshold(self):
        assert should_alert(_make_result(z_score=3.5, f_score=6)) is False

    def test_does_not_trigger_when_fails_filters(self):
        # CF ratio < 0.8 → passes_filters=False
        assert should_alert(_make_result(z_score=3.5, f_score=8, cf_ratio=0.5)) is False

    def test_boundary_z_score_exactly_3(self):
        """Z=3.0 must NOT trigger (strictly > 3.0 required)."""
        assert should_alert(_make_result(z_score=3.0, f_score=8)) is False

    def test_boundary_z_score_just_above_3(self):
        assert should_alert(_make_result(z_score=3.001, f_score=8)) is True


class TestSendAlert:
    @pytest.mark.asyncio
    async def test_skips_when_no_credentials(self):
        result = _make_result()
        os.environ.pop("SMTP_USER", None)
        os.environ.pop("SMTP_PASSWORD", None)
        sent = await send_alert(result)
        assert sent is False

    @pytest.mark.asyncio
    async def test_skips_when_password_missing(self):
        result = _make_result()
        with patch.dict("os.environ", {"SMTP_USER": "a@b.com"}, clear=False):
            os.environ.pop("SMTP_PASSWORD", None)
            sent = await send_alert(result)
        assert sent is False

    @pytest.mark.asyncio
    async def test_sends_when_credentials_set(self):
        result = _make_result()
        with (
            patch.dict("os.environ", SMTP_ENV),
            patch("app.services.alerts.aiosmtplib.send", new=AsyncMock()) as mock_send,
        ):
            sent = await send_alert(result)

        assert sent is True
        mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_email_addressed_to_smtp_user(self):
        result = _make_result()
        with (
            patch.dict("os.environ", SMTP_ENV),
            patch("app.services.alerts.aiosmtplib.send", new=AsyncMock()) as mock_send,
        ):
            await send_alert(result)

        msg = mock_send.call_args.args[0]
        assert msg["To"] == "pereiraelian18@gmail.com"
        assert msg["From"] == "pereiraelian18@gmail.com"

    @pytest.mark.asyncio
    async def test_subject_contains_ticker_and_scores(self):
        result = _make_result(z_score=3.5, f_score=8)
        with (
            patch.dict("os.environ", SMTP_ENV),
            patch("app.services.alerts.aiosmtplib.send", new=AsyncMock()) as mock_send,
        ):
            await send_alert(result)

        subject = mock_send.call_args.args[0]["Subject"]
        assert "TEST" in subject
        assert "3.50" in subject
        assert "8" in subject

    @pytest.mark.asyncio
    async def test_uses_starttls_on_port_587(self):
        result = _make_result()
        with (
            patch.dict("os.environ", SMTP_ENV),
            patch("app.services.alerts.aiosmtplib.send", new=AsyncMock()) as mock_send,
        ):
            await send_alert(result)

        kwargs = mock_send.call_args.kwargs
        assert kwargs["hostname"] == "smtp.gmail.com"
        assert kwargs["port"] == 587
        assert kwargs["start_tls"] is True

    @pytest.mark.asyncio
    async def test_returns_false_on_smtp_error(self):
        result = _make_result()
        with (
            patch.dict("os.environ", SMTP_ENV),
            patch("app.services.alerts.aiosmtplib.send", new=AsyncMock(side_effect=Exception("auth failed"))),
        ):
            sent = await send_alert(result)
        assert sent is False


class TestMaybeSendAlert:
    @pytest.mark.asyncio
    async def test_fires_for_qualifying_result(self):
        result = _make_result(z_score=3.5, f_score=8)
        with patch("app.services.alerts.send_alert", new=AsyncMock(return_value=True)) as mock_send:
            await maybe_send_alert(result)
            mock_send.assert_awaited_once_with(result)

    @pytest.mark.asyncio
    async def test_does_not_fire_below_threshold(self):
        result = _make_result(z_score=2.0, f_score=8)
        with patch("app.services.alerts.send_alert", new=AsyncMock()) as mock_send:
            await maybe_send_alert(result)
            mock_send.assert_not_awaited()
