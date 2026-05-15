from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class RiskLevel(str, Enum):
    safe = "safe"
    distress = "distress"
    grey_zone = "grey_zone"
    unknown = "unknown"


class FinancialMetrics(BaseModel):
    z_score: Optional[float] = Field(None, description="Altman Z-Score")
    z_score_components: Optional[dict] = Field(
        None, description="X1..X5 components for auditability"
    )
    f_score: Optional[int] = Field(None, ge=0, le=9, description="Piotroski F-Score")
    f_score_breakdown: Optional[dict] = Field(
        None, description="9 binary criteria breakdown"
    )
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    roa: Optional[float] = None
    roe: Optional[float] = None
    gross_margin: Optional[float] = None
    asset_turnover: Optional[float] = None


class DCFScenario(BaseModel):
    name: str
    growth_rate: float
    terminal_growth_rate: float
    wacc: float
    fair_value: float
    weight: float


class ValuationData(BaseModel):
    current_price: Optional[float] = None
    fair_value_dcf: Optional[float] = Field(
        None, description="Weighted average fair value across scenarios"
    )
    safety_margin: Optional[float] = Field(
        None, description="Required safety margin (30% threshold)"
    )
    upside_potential: Optional[float] = Field(
        None, description="Percentage upside from current price to fair value"
    )
    scenarios: Optional[List[DCFScenario]] = None
    is_undervalued: Optional[bool] = None

    @model_validator(mode="after")
    def compute_derived(self) -> "ValuationData":
        if self.current_price and self.fair_value_dcf and self.current_price > 0:
            self.upside_potential = round(
                (self.fair_value_dcf - self.current_price) / self.current_price * 100, 2
            )
            self.is_undervalued = self.fair_value_dcf > self.current_price
        return self


class CashFlowQuality(BaseModel):
    net_income: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    quality_ratio: Optional[float] = Field(
        None,
        description="OCF / Net Income; >0.8 required to pass filters",
    )
    is_suspicious: bool = False
    history: Optional[List[dict]] = Field(
        None, description="Last 3 years of OCF vs Net Income"
    )

    @model_validator(mode="after")
    def compute_quality(self) -> "CashFlowQuality":
        if (
            self.net_income is not None
            and self.operating_cash_flow is not None
            and self.net_income != 0
        ):
            self.quality_ratio = round(self.operating_cash_flow / self.net_income, 4)
            self.is_suspicious = self.quality_ratio < 0.8
        return self


class ScreeningResult(BaseModel):
    ticker: str = Field(..., description="Stock ticker symbol")
    company_name: Optional[str] = None
    screening_date: datetime = Field(default_factory=datetime.utcnow)
    financials: FinancialMetrics = Field(default_factory=FinancialMetrics)
    valuation: ValuationData = Field(default_factory=ValuationData)
    cash_flow_quality: CashFlowQuality = Field(default_factory=CashFlowQuality)
    risk_level: RiskLevel = RiskLevel.unknown
    passes_filters: bool = False
    downside_risks: List[str] = Field(default_factory=list)
    error: Optional[str] = None

    @model_validator(mode="after")
    def evaluate_filters(self) -> "ScreeningResult":
        fin = self.financials
        cfq = self.cash_flow_quality

        z_ok = fin.z_score is not None and fin.z_score > 1.8
        f_ok = fin.f_score is not None and fin.f_score >= 5
        cfq_ok = cfq.quality_ratio is not None and cfq.quality_ratio > 0.8

        self.passes_filters = z_ok and f_ok and cfq_ok

        if fin.z_score is not None:
            if fin.z_score > 2.99:
                self.risk_level = RiskLevel.safe
            elif fin.z_score < 1.81:
                self.risk_level = RiskLevel.distress
            else:
                self.risk_level = RiskLevel.grey_zone

        return self


class DailyScreeningSummary(BaseModel):
    screening_date: datetime = Field(default_factory=datetime.utcnow)
    total_screened: int = 0
    passed_filters: int = 0
    safe_zone: int = 0
    grey_zone: int = 0
    distress_zone: int = 0
    top_opportunities: List[ScreeningResult] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class TickerUniverse(BaseModel):
    sp500: List[str] = Field(default_factory=list)
    bmv: List[str] = Field(default_factory=list)

    @property
    def all_tickers(self) -> List[str]:
        return self.sp500 + self.bmv


class BatchScreenRequest(BaseModel):
    tickers: List[str] = Field(..., min_length=1)
    min_z_score: float = Field(1.8, ge=0)
    min_f_score: int = Field(5, ge=0, le=9)
