# Value Investing API

Automated Factor Investing analysis system using FastAPI. Implements **Value + Quality** factors:
- **Altman Z-Score** — bankruptcy risk / financial distress detection
- **Piotroski F-Score** — 9-criteria operational quality score
- **DCF Multi-Scenario** — Pessimistic / Base / Optimistic intrinsic value
- **Cash Flow Quality** — Value trap detection (OCF/Net Income ratio)
- **PDF Reports** — Professional ReportLab-generated investment reports

Supports **S&P 500** and **Mexican BMV** tickers.

## Quick Start

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs

## Key Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/screen/{ticker}` | Full analysis for one ticker |
| `POST` | `/screen/batch` | Batch screen list of tickers |
| `GET` | `/screen/opportunities/top` | Top value opportunities from universe |
| `POST` | `/screen/update-universe` | Refresh S&P500 + BMV ticker list |
| `GET` | `/reports/{ticker}/pdf` | Download PDF investment report |
| `GET` | `/health` | Health check |

## Example

```bash
curl -X POST "http://localhost:8000/screen/NVDA"
```

## Filter Criteria

A stock `passes_filters=true` only when ALL three conditions hold:
- `z_score > 1.8` (not in financial distress)
- `f_score >= 5` (solid operational quality)
- `cash_flow_quality_ratio > 0.8` (earnings are backed by real cash)

## Deploy to Railway

```bash
railway up
```

The `railway.json` is pre-configured. Set `DATABASE_URL` in Railway environment variables.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | SQLite | PostgreSQL connection string |
| `RISK_FREE_RATE` | `0.045` | 10Y Treasury for WACC/CAPM |
| `TAX_RATE_MEX` | `0.10` | ISR for Mexican stocks |
| `COMMISSION_RATE` | `0.0025` | Broker commission |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
