"""
Trader's Edge API - Fast, Deterministic Exit P/E Model

This is an alternative backend that uses the empirically-validated Trader's Edge
exit P/E model instead of GPT-based prediction.

Benefits:
- ⚡ Instant (~100ms vs ~2-3min with GPT)
- 💰 Zero LLM cost
- 🔍 100% transparent & auditable
- ✅ Backtested on 3,161 observations (MAE=10.82)

Run with:
    uvicorn main_traders_edge:app --host 0.0.0.0 --port 8000 --reload
"""

import os
from typing import Any, Dict, List, Optional, Tuple
import time
import csv
from io import StringIO
import hashlib
import threading

from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Import the Trader's Edge model
try:
    from .exit_pe_model import traders_edge_exit_pe, ExitPEResult
except ImportError:
    from exit_pe_model import traders_edge_exit_pe, ExitPEResult

# Support running as a package or module
try:
    from .database import get_db_connection  # type: ignore
except Exception:
    import sys
    import os as _os
    sys.path.append(_os.path.dirname(_os.path.dirname(__file__)))
    from backend.database import get_db_connection  # type: ignore

load_dotenv()

# -----------------------------------------------------------------------------
# Stooq Price Helpers (copied from main_gpt_all.py for compatibility)
# -----------------------------------------------------------------------------
_STOOQ_CACHE_TTL_S = int(os.getenv("STOOQ_CACHE_TTL_S", "21600"))  # 6 hours
_stooq_cache: Dict[str, Tuple[float, float]] = {}


def _bloomberg_ticker_to_stooq_symbol(ticker: str) -> Optional[str]:
    if not ticker:
        return None
    parts = ticker.strip().split()
    if not parts:
        return None
    root = parts[0].strip()
    if not root:
        return None
    root = root.replace("/", "-")
    return f"{root.lower()}.us"


def _fetch_stooq_close(symbol: str) -> Optional[float]:
    try:
        import requests
    except Exception:
        return None
    try:
        url = f"https://stooq.com/q/d/l/?s={symbol}&i=d&h=1"
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        text = r.text.strip()
        if not text or "Close" not in text:
            return None
        reader = csv.DictReader(StringIO(text))
        rows = list(reader)
        if not rows:
            return None
        last = rows[-1]
        close_str = (last.get("Close") or "").strip()
        if not close_str or close_str in ("N/A", "-"):
            return None
        price = float(close_str)
        if price <= 0:
            return None
        return price
    except Exception:
        return None


def get_current_price_stooq(ticker: str) -> Optional[float]:
    symbol = _bloomberg_ticker_to_stooq_symbol(ticker)
    if not symbol:
        return None
    now = time.time()
    if symbol in _stooq_cache:
        ts, price = _stooq_cache[symbol]
        if now - ts <= _STOOQ_CACHE_TTL_S:
            return price
    price = _fetch_stooq_close(symbol)
    if price is None:
        return None
    _stooq_cache[symbol] = (now, price)
    return price


# -----------------------------------------------------------------------------
# FastAPI Setup
# -----------------------------------------------------------------------------
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://0.0.0.0:3000",
]

app = FastAPI(
    title="Fin Agent API (Trader's Edge)",
    version="2.0.0",
    description="Fast, deterministic exit P/E model - no LLM required"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Pydantic Models (same as main_gpt_all.py for frontend compatibility)
# -----------------------------------------------------------------------------
class CompanyIrr(BaseModel):
    ticker: str
    name: Optional[str] = None
    sector: Optional[str] = None
    country: Optional[str] = None
    market_cap: Optional[float] = None
    current_price: Optional[float] = None
    irr: float
    exit_pe: Optional[float] = None
    ebit_margin: Optional[float] = None  # New field for display
    exit_pe_low: Optional[float] = None   # Lower bound of confidence band
    exit_pe_high: Optional[float] = None  # Upper bound of confidence band
    exit_pe_confidence: Optional[float] = None
    exit_pe_conviction: Optional[str] = None  # "High", "Medium", "Low"
    exit_pe_notes: Optional[str] = None
    exit_pe_breakdown: Optional[List[Dict[str, Any]]] = None
    eps: Optional[float] = None
    eps_year: Optional[int] = None
    pe_avg_10y: Optional[float] = None
    pe_avg_10y_years_total: Optional[int] = None
    pe_avg_10y_years_used: Optional[int] = None


class TopIrrResponse(BaseModel):
    companies: List[CompanyIrr]
    analysis: Optional[str] = None


class NumericRange(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None


class CountryFilter(BaseModel):
    selection: List[str] = []
    excludeMode: bool = False


class FiltersPayload(BaseModel):
    marketCap: Optional[NumericRange] = None
    country: Optional[CountryFilter] = None
    sectors: List[str] = []
    industries: List[str] = []
    salesCagr3yMin: Optional[float] = None
    epsCagr3yMin: Optional[float] = None
    fcfCagr3yMin: Optional[float] = None
    roeMin: Optional[float] = None
    roicMin: Optional[float] = None
    ebitMarginMin: Optional[float] = None
    peMax: Optional[float] = None
    priceToFcfMax: Optional[float] = None
    evToEbitdaMax: Optional[float] = None
    debtToEbitdaMax: Optional[float] = None
    stockReturns5yMin: Optional[float] = None
    stockReturns10yMin: Optional[float] = None
    assignedExitMultipleMin: Optional[float] = None
    irrMin: Optional[float] = None


class TopIrrRequest(BaseModel):
    limit: int = 5
    horizon_years: int = 3  # Default to 3 years for Trader's Edge
    filters: FiltersPayload


# -----------------------------------------------------------------------------
# Utility Functions
# -----------------------------------------------------------------------------
def _get_env_int(name: str, default: int) -> int:
    try:
        v = os.getenv(name)
        if v is None:
            return default
        return int(str(v).strip())
    except Exception:
        return default


def _get_env_float(name: str, default: float) -> float:
    try:
        v = os.getenv(name)
        if v is None:
            return default
        return float(str(v).strip())
    except Exception:
        return default


def _resolve_limit(requested: Optional[int]) -> Tuple[int, int, int]:
    default_limit = _get_env_int("TOP_IRR_LIMIT_DEFAULT", 25)
    max_limit = _get_env_int("TOP_IRR_LIMIT_MAX", 100)
    if max_limit < 1:
        max_limit = 100
    if default_limit < 1:
        default_limit = 25
    raw = requested if isinstance(requested, int) and requested > 0 else default_limit
    used = min(max(raw, 1), max_limit)
    return used, default_limit, max_limit


# -----------------------------------------------------------------------------
# Database Query (same SQL as main_gpt_all.py)
# -----------------------------------------------------------------------------
def _build_filters_where(filters: FiltersPayload, params: List[Any], horizon_years: int) -> Tuple[str, List[Any]]:
    conds: List[str] = []

    if filters.marketCap:
        if filters.marketCap.min is not None:
            params.append(filters.marketCap.min)
            conds.append("c.market_cap >= %s")
        if filters.marketCap.max is not None:
            params.append(filters.marketCap.max)
            conds.append("c.market_cap <= %s")

    if filters.country and filters.country.selection:
        placeholders = ", ".join(["%s"] * len(filters.country.selection))
        params.extend(filters.country.selection)
        if filters.country.excludeMode:
            conds.append(f"COALESCE(c.country, '') NOT IN ({placeholders})")
        else:
            conds.append(f"COALESCE(c.country, '') IN ({placeholders})")

    if filters.sectors:
        placeholders = ", ".join(["%s"] * len(filters.sectors))
        params.extend(filters.sectors)
        conds.append(f"COALESCE(c.sector, '') IN ({placeholders})")

    if filters.industries:
        placeholders = ", ".join(["%s"] * len(filters.industries))
        params.extend(filters.industries)
        conds.append(f"COALESCE(c.industry, '') IN ({placeholders})")

    if filters.roeMin is not None:
        params.append(filters.roeMin)
        conds.append("completed_fm.roe >= %s")
    if filters.roicMin is not None:
        params.append(filters.roicMin)
        conds.append("completed_fm.roic >= %s")
    if filters.ebitMarginMin is not None:
        params.append(filters.ebitMarginMin)
        conds.append("completed_fm.ebit_margin >= %s")
    if filters.peMax is not None:
        params.append(filters.peMax)
        conds.append("completed_fm.pe_ratio <= %s")
    if filters.priceToFcfMax is not None:
        params.append(filters.priceToFcfMax)
        conds.append("completed_fm.p_fcf_ratio <= %s")
    if filters.evToEbitdaMax is not None:
        params.append(filters.evToEbitdaMax)
        conds.append("completed_fm.ev_ebitda <= %s")
    if filters.debtToEbitdaMax is not None:
        params.append(filters.debtToEbitdaMax)
        conds.append("completed_fm.debt_ebitda <= %s")

    if filters.stockReturns5yMin is not None:
        params.append(filters.stockReturns5yMin)
        conds.append("calculate_stock_return(c.id, INTERVAL '5 years') >= %s")
    if filters.stockReturns10yMin is not None:
        params.append(filters.stockReturns10yMin)
        conds.append("calculate_stock_return(c.id, INTERVAL '10 years') >= %s")

    if filters.salesCagr3yMin is not None:
        params.append(filters.salesCagr3yMin)
        conds.append("cagr3.sales_cagr_3y >= %s")
    if filters.epsCagr3yMin is not None:
        params.append(filters.epsCagr3yMin)
        conds.append("cagr3.eps_cagr_3y >= %s")
    if filters.fcfCagr3yMin is not None:
        params.append(filters.fcfCagr3yMin)
        conds.append("cagr3.fcf_cagr_3y >= %s")

    if filters.irrMin is not None:
        params.append(horizon_years)
        params.append(filters.irrMin / 100.0)
        conds.append("(POWER(latest_price.latest_price / NULLIF(start_price.start_price, 0), 1.0 / %s) - 1) >= %s")

    where_clause = " AND ".join(conds) if conds else ""
    return where_clause, params


def fetch_candidates_all(horizon_years: int, filters: FiltersPayload) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Fetch all companies matching filters with fundamentals for exit P/E calculation.
    """
    params: List[Any] = []
    query = f"""
        WITH latest_price AS (
            SELECT sp.company_id, sp.price AS latest_price
            FROM (
                SELECT company_id, MAX(date) AS max_date
                FROM share_prices
                WHERE date <= CURRENT_DATE
                GROUP BY company_id
            ) m
            JOIN share_prices sp ON sp.company_id = m.company_id AND sp.date = m.max_date
        ),
        start_price AS (
            SELECT sp.company_id, sp.price AS start_price
            FROM (
                SELECT company_id, MAX(date) AS start_date
                FROM share_prices
                WHERE date <= CURRENT_DATE - INTERVAL '{horizon_years} years'
                GROUP BY company_id
            ) s
            JOIN share_prices sp ON sp.company_id = s.company_id AND sp.date = s.start_date
        ),
        completed_fm AS (
            SELECT DISTINCT ON (fm.company_id)
                fm.*
            FROM financial_metrics fm
            WHERE fm.period <= (EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER - 1)
            ORDER BY fm.company_id, fm.period DESC
        ),
        target_year AS (
            SELECT (EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER + %s - 1) AS y
        )
        SELECT
            c.ticker,
            c.name,
            c.sector,
            c.country,
            c.market_cap,
            completed_fm.roe,
            completed_fm.roic,
            completed_fm.ebit_margin,
            completed_fm.pe_ratio,
            completed_fm.p_fcf_ratio,
            completed_fm.ev_ebitda,
            completed_fm.debt_ebitda,
            cagr3.eps_cagr_3y,
            cagr3.sales_cagr_3y,
            cagr3.fcf_cagr_3y,
            eps_fwd.period AS eps_forward_year,
            eps_fwd.eps AS eps_forward,
            latest_price.latest_price AS current_price,
            pe10.pe_avg_10y,
            pe10.pe_avg_10y_years_total,
            pe10.pe_avg_10y_years_used,
            pe10.pe_std_10y,
            completed_fm.period AS eps_year,
            completed_fm.eps AS eps_latest
        FROM companies c
        JOIN latest_price ON latest_price.company_id = c.id
        JOIN start_price ON start_price.company_id = c.id
        CROSS JOIN target_year ty
        LEFT JOIN LATERAL (
            SELECT fm2.period, fm2.eps
            FROM financial_metrics fm2
            WHERE fm2.company_id = c.id AND fm2.eps IS NOT NULL
            ORDER BY
                CASE
                    WHEN fm2.period = ty.y THEN 0
                    WHEN fm2.period > ty.y THEN 1
                    ELSE 2
                END,
                ABS(fm2.period - ty.y)
            LIMIT 1
        ) eps_fwd ON TRUE
        LEFT JOIN completed_fm ON completed_fm.company_id = c.id
        LEFT JOIN LATERAL (
            SELECT
                CASE
                    WHEN fm_start.eps IS NOT NULL AND fm_start.eps > 0
                     AND completed_fm.eps IS NOT NULL AND completed_fm.eps > 0
                    THEN ROUND(((POWER(completed_fm.eps / fm_start.eps, 1.0 / 3) - 1) * 100)::numeric, 2)
                    ELSE NULL
                END AS eps_cagr_3y,
                CASE
                    WHEN fm_start.sales IS NOT NULL AND fm_start.sales > 0
                     AND completed_fm.sales IS NOT NULL AND completed_fm.sales > 0
                    THEN ROUND(((POWER(completed_fm.sales / fm_start.sales, 1.0 / 3) - 1) * 100)::numeric, 2)
                    ELSE NULL
                END AS sales_cagr_3y,
                CASE
                    WHEN fm_start.fcf IS NOT NULL AND fm_start.fcf > 0
                     AND completed_fm.fcf IS NOT NULL AND completed_fm.fcf > 0
                    THEN ROUND(((POWER(completed_fm.fcf / fm_start.fcf, 1.0 / 3) - 1) * 100)::numeric, 2)
                    ELSE NULL
                END AS fcf_cagr_3y
            FROM (
                SELECT eps, sales, fcf
                FROM financial_metrics
                WHERE company_id = c.id
                  AND period = (completed_fm.period - 3)
                LIMIT 1
            ) fm_start
        ) cagr3 ON TRUE
        LEFT JOIN LATERAL (
            SELECT
                (AVG(fm10.pe_ratio) FILTER (WHERE fm10.pe_ratio <= 100))::numeric AS pe_avg_10y,
                (STDDEV(fm10.pe_ratio) FILTER (WHERE fm10.pe_ratio <= 100))::numeric AS pe_std_10y,
                COUNT(fm10.pe_ratio)::integer AS pe_avg_10y_years_total,
                COUNT(fm10.pe_ratio) FILTER (WHERE fm10.pe_ratio <= 100)::integer AS pe_avg_10y_years_used
            FROM financial_metrics fm10
            WHERE fm10.company_id = c.id
              AND fm10.pe_ratio IS NOT NULL
              AND fm10.period BETWEEN (EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER - 10)
                                  AND (EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER - 1)
        ) pe10 ON TRUE
        WHERE start_price.start_price IS NOT NULL AND start_price.start_price > 0
          AND latest_price.latest_price IS NOT NULL AND latest_price.latest_price > 0
          AND completed_fm.company_id IS NOT NULL
    """
    params.append(horizon_years)

    where_clause, params = _build_filters_where(filters, params, horizon_years)
    if where_clause:
        query += f"\nAND {where_clause}\n"
    query += "\nORDER BY c.ticker ASC;"

    rows: List[Dict[str, Any]] = []
    details: Dict[str, Dict[str, Any]] = {}
    
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, tuple(params))
            for r in cur.fetchall():
                company = {
                    "ticker": r[0],
                    "name": r[1],
                    "sector": r[2],
                    "country": r[3],
                    "market_cap": float(r[4]) if r[4] is not None else None,
                }
                rows.append(company)
                details[company["ticker"]] = {
                    "roe": float(r[5]) if r[5] is not None else None,
                    "roic": float(r[6]) if r[6] is not None else None,
                    "ebit_margin": float(r[7]) if r[7] is not None else None,
                    "pe_ratio": float(r[8]) if r[8] is not None else None,
                    "p_fcf_ratio": float(r[9]) if r[9] is not None else None,
                    "ev_ebitda": float(r[10]) if r[10] is not None else None,
                    "debt_ebitda": float(r[11]) if r[11] is not None else None,
                    "eps_cagr_3y": float(r[12]) if r[12] is not None else None,
                    "sales_cagr_3y": float(r[13]) if r[13] is not None else None,
                    "fcf_cagr_3y": float(r[14]) if r[14] is not None else None,
                    "eps_forward_year": int(r[15]) if r[15] is not None else None,
                    "eps_forward": float(r[16]) if r[16] is not None else None,
                    "current_price": float(r[17]) if r[17] is not None else None,
                    "pe_avg_10y": float(r[18]) if r[18] is not None else None,
                    "pe_avg_10y_years_total": int(r[19]) if r[19] is not None else None,
                    "pe_avg_10y_years_used": int(r[20]) if r[20] is not None else None,
                    "pe_std_10y": float(r[21]) if r[21] is not None else None,
                    "eps_year": int(r[22]) if r[22] is not None else None,
                    "eps_latest": float(r[23]) if r[23] is not None else None,
                }
    return rows, details


# -----------------------------------------------------------------------------
# API Endpoints
# -----------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "model": "traders_edge", "version": "2.0.0"}


@app.post("/api/top-irr", response_model=TopIrrResponse)
def api_top_irr(payload: TopIrrRequest = Body(...)):
    """
    Evaluate ALL companies using the Trader's Edge exit P/E model.
    
    This is MUCH faster than the GPT-based endpoint because:
    - No LLM API calls (deterministic formula)
    - All computation is local
    
    Typical response time: ~100-500ms vs 2-3min with GPT.
    """
    req_started = time.time()
    limit, default_limit, max_limit = _resolve_limit(getattr(payload, "limit", None))
    horizon_years = min(max(payload.horizon_years, 1), 15)
    pe_max = _get_env_float("EXIT_PE_MAX", 80.0)
    
    print(f"[traders-edge] Starting request: limit={limit}, horizon={horizon_years}")
    
    # Step 1: Fetch all candidates from database
    candidates, details = fetch_candidates_all(horizon_years=horizon_years, filters=payload.filters)
    
    if not candidates:
        return TopIrrResponse(companies=[], analysis="No candidates match the filters.")
    
    print(f"[traders-edge] Fetched {len(candidates)} candidates in {time.time() - req_started:.2f}s")
    
    # Step 2: Calculate exit P/E for each company using Trader's Edge model
    scored: List[Tuple[CompanyIrr, float]] = []
    
    for company in candidates:
        ticker = company["ticker"]
        d = details.get(ticker, {})
        
        db_price = d.get("current_price")
        eps_year = d.get("eps_forward_year") or d.get("eps_year")
        eps_val = d.get("eps_forward") or d.get("eps_latest")
        current_pe = d.get("pe_ratio")
        pe_avg_10y = d.get("pe_avg_10y")
        pe_std = d.get("pe_std_10y")
        roe = d.get("roe")
        ebit_margin = d.get("ebit_margin")
        eps_growth = d.get("eps_cagr_3y")
        years_of_data = d.get("pe_avg_10y_years_used") or 5
        
        # Skip if missing critical data
        if not db_price or not eps_val:
            continue
        
        # Use Trader's Edge model
        if current_pe and current_pe > 0 and pe_avg_10y and pe_avg_10y > 0:
            result = traders_edge_exit_pe(
                current_pe=current_pe,
                pe_avg_10y=pe_avg_10y,
                pe_std=pe_std,
                roe=roe,
                eps_growth=eps_growth,
                ebit_margin=ebit_margin,
                years_of_data=years_of_data,
                pe_max=pe_max,
            )
            exit_pe = result.exit_pe
            exit_pe_low = result.exit_pe_low
            exit_pe_high = result.exit_pe_high
            exit_pe_confidence = result.confidence
            exit_pe_conviction = result.conviction
            exit_pe_notes = result.justification
            exit_pe_breakdown = result.breakdown
        else:
            # Fallback: use market average 18x
            exit_pe = 18.0
            exit_pe_low = 15.0
            exit_pe_high = 22.0
            exit_pe_confidence = 0.50
            exit_pe_conviction = "Low"
            exit_pe_notes = "Missing historical P/E data; using market average 18x as fallback."
            exit_pe_breakdown = [{"label": "Exit P/E", "value": "18.0x", "meta": "Market average fallback"}]
        
        # Calculate projected IRR
        try:
            years_to_exit = max(1, int(horizon_years))
            exit_price = float(eps_val) * float(exit_pe)
            irr = (exit_price / float(db_price)) ** (1.0 / years_to_exit) - 1.0
            
            ci = CompanyIrr(
                ticker=ticker,
                name=company.get("name"),
                sector=company.get("sector"),
                country=company.get("country"),
                market_cap=company.get("market_cap"),
                current_price=float(db_price),
                irr=irr * 100.0,
                exit_pe=exit_pe,
                ebit_margin=ebit_margin,
                exit_pe_low=exit_pe_low,
                exit_pe_high=exit_pe_high,
                exit_pe_confidence=exit_pe_confidence,
                exit_pe_conviction=exit_pe_conviction,
                exit_pe_notes=exit_pe_notes,
                exit_pe_breakdown=exit_pe_breakdown,
                eps=float(eps_val),
                eps_year=eps_year,
                pe_avg_10y=pe_avg_10y,
                pe_avg_10y_years_total=d.get("pe_avg_10y_years_total"),
                pe_avg_10y_years_used=d.get("pe_avg_10y_years_used"),
            )
            scored.append((ci, irr))
        except Exception:
            continue
    
    if not scored:
        return TopIrrResponse(
            companies=[],
            analysis="Unable to compute IRR for any company (missing prices/EPS)."
        )
    
    # Step 3: Optional Stooq rerank for top companies
    use_stooq = os.getenv("USE_STOOQ_PRICE", "1").strip() == "1"
    scored.sort(key=lambda x: x[1], reverse=True)
    
    if use_stooq:
        subset_n = min(len(scored), max(50, limit * 5))
        updated: List[Tuple[CompanyIrr, float]] = []
        
        for ci, irr_db in scored[:subset_n]:
            ext = get_current_price_stooq(ci.ticker)
            if ext is not None and ext > 0 and ci.eps:
                try:
                    years_to_exit = max(1, int(horizon_years))
                    exit_price = float(ci.eps) * float(ci.exit_pe)
                    irr_ext = (exit_price / ext) ** (1.0 / years_to_exit) - 1.0
                    ci.current_price = float(ext)
                    ci.irr = irr_ext * 100.0
                    updated.append((ci, irr_ext))
                    continue
                except Exception:
                    pass
            updated.append((ci, irr_db))
        
        scored = updated + scored[subset_n:]
    
    # Final sort and limit
    scored.sort(key=lambda x: x[0].irr if x[0].irr is not None else float("-inf"), reverse=True)
    top_companies = [ci for ci, _ in scored[:limit]]
    
    elapsed = time.time() - req_started
    print(f"[traders-edge] Completed: {len(top_companies)} companies in {elapsed:.2f}s")
    
    return TopIrrResponse(
        companies=top_companies,
        analysis=f"Trader's Edge Model: Processed {len(candidates)} companies in {elapsed:.1f}s. "
                 f"Exit P/E based on bounded mean reversion to 10Y average with quality adjustment."
    )
