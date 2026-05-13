"""
Unit tests for alerts.py — Telegram alert logic.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import CashFlowQuality, FinancialMetrics, RiskLevel, ScreeningResult
from app.services.alerts import maybe_send_alert, send_alert, should_alert


def _make_result(z_score=3.5, f_score=8, cf_ratio=1.2, passes=True) -> ScreeningResult:
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
        result = _make_result(z_score=3.5, f_score=8)
        assert should_alert(result) is True

    def test_does_not_trigger_below_z_threshold(self):
        result = _make_result(z_score=2.5, f_score=8)
        assert should_alert(result) is False

    def test_does_not_trigger_below_f_threshold(self):
        result = _make_result(z_score=3.5, f_score=6)
        assert should_alert(result) is False

    def test_does_not_trigger_when_fails_filters(self):
        # CF ratio < 0.8 → passes_filters will be False
        result = _make_result(z_score=3.5, f_score=8, cf_ratio=0.5)
        assert should_alert(result) is False

    def test_boundary_z_score_exactly_3(self):
        """Z-Score exactly 3.0 must NOT trigger (requires strictly > 3.0)."""
        result = _make_result(z_score=3.0, f_score=8)
        assert should_alert(result) is False

    def test_boundary_z_score_just_above_3(self):
        result = _make_result(z_score=3.001, f_score=8)
        assert should_alert(result) is True


class TestSendAlert:
    @pytest.mark.asyncio
    async def test_skips_when_no_credentials(self):
        """Should return False silently when env vars not set."""
        result = _make_result()
        with patch.dict("os.environ", {}, clear=True):
            # Remove any existing TELEGRAM vars
            import os
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("TELEGRAM_CHAT_ID", None)
            sent = await send_alert(result)
        assert sent is False

    @pytest.mark.asyncio
    async def test_sends_when_credentials_set(self):
        result = _make_result()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token123", "TELEGRAM_CHAT_ID": "456"}),
            patch("app.services.alerts.httpx.AsyncClient", return_value=mock_client),
        ):
            sent = await send_alert(result)

        assert sent is True
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs.args[1] if call_kwargs.args else call_kwargs.kwargs["json"]
        assert "TEST" in body["text"]
        assert body["chat_id"] == "456"

    @pytest.mark.asyncio
    async def test_returns_false_on_http_error(self):
        result = _make_result()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

        with (
            patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "1"}),
            patch("app.services.alerts.httpx.AsyncClient", return_value=mock_client),
        ):
            sent = await send_alert(result)

        assert sent is False  # graceful failure


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
