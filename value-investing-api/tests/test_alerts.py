"""
Unit tests for alerts.py — WhatsApp alert logic via Twilio.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import CashFlowQuality, FinancialMetrics, RiskLevel, ScreeningResult
from app.services.alerts import maybe_send_alert, send_alert, should_alert

TWILIO_ENV = {
    "TWILIO_ACCOUNT_SID": "ACtest123",
    "TWILIO_AUTH_TOKEN": "authtoken456",
    "TWILIO_FROM_WHATSAPP": "whatsapp:+14155238886",
    "TWILIO_TO_WHATSAPP": "whatsapp:+521234567890",
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
        """Returns False silently when Twilio env vars not set."""
        result = _make_result()
        import os
        for key in TWILIO_ENV:
            os.environ.pop(key, None)
        sent = await send_alert(result)
        assert sent is False

    @pytest.mark.asyncio
    async def test_skips_when_partially_configured(self):
        """Even one missing credential should prevent sending."""
        result = _make_result()
        partial = {"TWILIO_ACCOUNT_SID": "ACtest", "TWILIO_AUTH_TOKEN": "tok"}
        with patch.dict("os.environ", partial):
            sent = await send_alert(result)
        assert sent is False

    @pytest.mark.asyncio
    async def test_sends_when_credentials_set(self):
        result = _make_result()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"sid": "SM123"})

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch.dict("os.environ", TWILIO_ENV),
            patch("app.services.alerts.httpx.AsyncClient", return_value=mock_client),
        ):
            sent = await send_alert(result)

        assert sent is True
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        # Verify correct Twilio endpoint
        assert "ACtest123" in call_args.args[0]
        # Verify form-encoded body (data=), not JSON
        body = call_args.kwargs["data"]
        assert body["From"] == "whatsapp:+14155238886"
        assert body["To"] == "whatsapp:+521234567890"
        assert "TEST" in body["Body"]

    @pytest.mark.asyncio
    async def test_uses_basic_auth(self):
        """Twilio requires HTTP Basic Auth with (account_sid, auth_token)."""
        result = _make_result()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"sid": "SM456"})

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch.dict("os.environ", TWILIO_ENV),
            patch("app.services.alerts.httpx.AsyncClient", return_value=mock_client),
        ):
            await send_alert(result)

        call_kwargs = mock_client.post.call_args.kwargs
        assert call_kwargs["auth"] == ("ACtest123", "authtoken456")

    @pytest.mark.asyncio
    async def test_returns_false_on_http_error(self):
        result = _make_result()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

        with (
            patch.dict("os.environ", TWILIO_ENV),
            patch("app.services.alerts.httpx.AsyncClient", return_value=mock_client),
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
