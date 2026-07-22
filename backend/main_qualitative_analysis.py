import os
from typing import Any, Dict, List, Optional, Tuple
import json
import time
import csv
from io import StringIO
import hashlib
import threading

from fastapi import FastAPI, Body, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Support running as a package (uvicorn backend.main_gpt_all:app) or module (uvicorn main_gpt_all:app)
try:
	from .database import get_db_connection  # type: ignore
except Exception:
	import sys
	import os as _os
	sys.path.append(_os.path.dirname(_os.path.dirname(__file__)))
	from backend.database import get_db_connection  # type: ignore

load_dotenv()

# -----------------------------------------------------------------------------
# External price (Stooq) helpers (same behavior as main_gpt.py, but only used for top-N rerank)
# -----------------------------------------------------------------------------
_STOOQ_CACHE_TTL_S = int(os.getenv("STOOQ_CACHE_TTL_S", "21600"))  # 6 hours
_stooq_cache: Dict[str, Tuple[float, float]] = {}  # symbol -> (fetched_at_epoch_s, price)


def _bloomberg_ticker_to_stooq_symbol(ticker: str) -> Optional[str]:
	"""
	Convert Bloomberg-style ticker like 'AAPL UW Equity' -> 'aapl.us' (Stooq).
	Best-effort; returns None if mapping is unknown.
	"""
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
	"""
	Fetch most recent daily close from Stooq CSV endpoint.
	Symbol example: 'aapl.us'
	"""
	try:
		import requests  # type: ignore
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
	"""
	Get a recent close price from Stooq, with in-memory caching.
	Returns None if unavailable.
	"""
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


# Configure CORS for local dev (Next.js on 3000)
origins = [
	"http://localhost:3000",
	"http://127.0.0.1:3000",
	"http://0.0.0.0:3000",
]

app = FastAPI(title="Fin Agent API (All Companies)", version="0.1.0")
app.add_middleware(
	CORSMiddleware,
	allow_origins=origins,
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)


class CompanyIrr(BaseModel):
	ticker: str
	name: Optional[str] = None
	sector: Optional[str] = None
	country: Optional[str] = None
	market_cap: Optional[float] = None
	current_price: Optional[float] = None
	irr: float
	exit_pe: Optional[float] = None
	exit_pe_confidence: Optional[float] = None
	exit_pe_notes: Optional[str] = None  # "reason"
	exit_pe_breakdown: Optional[List[Dict[str, Any]]] = None
	eps: Optional[float] = None
	eps_year: Optional[int] = None
	pe_avg_10y: Optional[float] = None
	pe_avg_10y_years_total: Optional[int] = None
	pe_avg_10y_years_used: Optional[int] = None


class TopIrrResponse(BaseModel):
	companies: List[CompanyIrr]
	analysis: Optional[str] = None  # kept for compatibility; this endpoint returns None


class NumericRange(BaseModel):
	min: Optional[float] = None
	max: Optional[float] = None


class CountryFilter(BaseModel):
	selection: List[str] = []
	excludeMode: bool = False


class FiltersPayload(BaseModel):
	# Market & Geography
	marketCap: Optional[NumericRange] = None
	country: Optional[CountryFilter] = None
	sectors: List[str] = []
	industries: List[str] = []

	# Historical CAGRs minimums (subset implemented)
	salesCagr3yMin: Optional[float] = None
	epsCagr3yMin: Optional[float] = None
	fcfCagr3yMin: Optional[float] = None

	# Profitability & Returns
	roeMin: Optional[float] = None
	roicMin: Optional[float] = None
	ebitMarginMin: Optional[float] = None

	# Valuation Metrics - maximums
	peMax: Optional[float] = None
	priceToFcfMax: Optional[float] = None
	evToEbitdaMax: Optional[float] = None

	# Capital Structure
	debtToEbitdaMax: Optional[float] = None

	# Performance
	stockReturns5yMin: Optional[float] = None
	stockReturns10yMin: Optional[float] = None

	# Model-Driven Outputs
	assignedExitMultipleMin: Optional[float] = None
	irrMin: Optional[float] = None


class TopIrrRequest(BaseModel):
	# NOTE: backend uses env-driven defaulting in the endpoint; this is only a schema default.
	limit: int = 5
	horizon_years: int = 5
	filters: FiltersPayload


def _get_env_int(name: str, default: int) -> int:
	try:
		v = os.getenv(name)
		if v is None:
			return default
		return int(str(v).strip())
	except Exception:
		return default


def _resolve_limit(requested: Optional[int]) -> Tuple[int, int, int]:
	"""
	Resolve the effective limit based on env defaults + caps.
	Returns (limit_used, default_limit, max_limit).
	"""
	default_limit = _get_env_int("TOP_IRR_LIMIT_DEFAULT", 25)
	max_limit = _get_env_int("TOP_IRR_LIMIT_MAX", 25)
	if max_limit < 1:
		max_limit = 25
	if default_limit < 1:
		default_limit = 25
	raw = requested if isinstance(requested, int) and requested > 0 else default_limit
	used = min(max(raw, 1), max_limit)
	return used, default_limit, max_limit


def _get_env_float(name: str, default: float) -> float:
	try:
		v = os.getenv(name)
		if v is None:
			return default
		return float(str(v).strip())
	except Exception:
		return default


def _clamp_exit_pe(pe: float) -> float:
	"""
	Business rule: Exit P/E must not exceed EXIT_PE_MAX (default 80).
	Also applies a minimum of 3 to avoid nonsensical multiples.
	"""
	exit_pe_max = _get_env_float("EXIT_PE_MAX", 40.0)
	if exit_pe_max <= 0:
		exit_pe_max = 40.0
	return max(3.0, min(float(pe), float(exit_pe_max)))


# -----------------------------------------------------------------------------
# Single-flight / caching for expensive top-irr computations (dev StrictMode safe)
# -----------------------------------------------------------------------------
_TOP_IRR_LOCK = threading.Lock()
_TOP_IRR_INFLIGHT: Dict[str, Dict[str, Any]] = {}  # key -> {event, started_at, completed_at?, result?, error?}


def _top_irr_cache_ttl_s() -> float:
	return float(_get_env_int("TOP_IRR_CACHE_TTL_S", 900))  # 15 min default


def _top_irr_key(payload: "TopIrrRequest") -> str:
	"""
	Stable key for deduping identical requests (prevents duplicate work from React StrictMode/dev reloads).
	"""
	try:
		body = payload.model_dump()
	except Exception:
		body = {"limit": getattr(payload, "limit", None), "horizon_years": getattr(payload, "horizon_years", None), "filters": {}}
	raw = json.dumps(body, sort_keys=True, default=str)
	return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cleanup_top_irr_cache(now_s: float) -> None:
	ttl = _top_irr_cache_ttl_s()
	if ttl <= 0:
		return
	# Remove completed entries older than TTL
	for k in list(_TOP_IRR_INFLIGHT.keys()):
		entry = _TOP_IRR_INFLIGHT.get(k)
		if not entry:
			continue
		evt = entry.get("event")
		completed_at = entry.get("completed_at")
		if evt is not None and getattr(evt, "is_set", lambda: False)() and isinstance(completed_at, (int, float)):
			if now_s - float(completed_at) > ttl:
				_TOP_IRR_INFLIGHT.pop(k, None)


def _get_gpt_model_name() -> str:
	"""
	Model name for ChatGPT. Override with GPT_MODEL.
	"""
	return os.getenv("GPT_MODEL", "o3")


def _get_openai_client(api_key: str):
	"""
	Create an OpenAI client for ChatGPT.
	"""
	try:
		from openai import OpenAI  # type: ignore
	except Exception as e:
		raise ImportError("Failed to import `openai`. Install with `pip install -U openai`.") from e
	return OpenAI(api_key=api_key)


def _clean_json_text(text: str) -> str:
	t = text.strip()
	if t.startswith("```"):
		t = t.strip("`").strip()
	start = t.find("{")
	end = t.rfind("}")
	if start != -1 and end != -1 and end > start:
		return t[start : end + 1]
	return t


def _chatgpt_text(
	client,
	*,
	model: str,
	system: Optional[str],
	user: str,
	expect_json: bool = False,
	timeout_s: float = 90.0,
) -> Optional[str]:
	messages: List[Dict[str, str]] = []
	if system:
		messages.append({"role": "system", "content": system})
	messages.append({"role": "user", "content": user})
	try:
		kwargs: Dict[str, Any] = {
			"model": model,
			"messages": messages,
			"timeout": timeout_s,
		}
		if expect_json:
			kwargs["response_format"] = {"type": "json_object"}
			kwargs["temperature"] = 1
		else:
			kwargs["temperature"] = 1
		resp = client.chat.completions.create(**kwargs)
		return resp.choices[0].message.content
	except Exception as ex:
		print("GPT exception:", ex)
		return None


@app.get("/api/health")
def health():
	return {"status": "ok"}


def _build_filters_where(filters: FiltersPayload, params: List[Any], horizon_years: int) -> Tuple[str, List[Any]]:
	conds: List[str] = []

	# MarketCap
	if filters.marketCap:
		if filters.marketCap.min is not None:
			params.append(filters.marketCap.min)
			conds.append("c.market_cap >= %s")
		if filters.marketCap.max is not None:
			params.append(filters.marketCap.max)
			conds.append("c.market_cap <= %s")

	# Country include/exclude
	if filters.country and filters.country.selection:
		placeholders = ", ".join(["%s"] * len(filters.country.selection))
		params.extend(filters.country.selection)
		if filters.country.excludeMode:
			conds.append(f"COALESCE(c.country, '') NOT IN ({placeholders})")
		else:
			conds.append(f"COALESCE(c.country, '') IN ({placeholders})")

	# Sectors / Industries
	if filters.sectors:
		placeholders = ", ".join(["%s"] * len(filters.sectors))
		params.extend(filters.sectors)
		conds.append(f"COALESCE(c.sector, '') IN ({placeholders})")
	if filters.industries:
		placeholders = ", ".join(["%s"] * len(filters.industries))
		params.extend(filters.industries)
		conds.append(f"COALESCE(c.industry, '') IN ({placeholders})")

	# Completed financial metrics constraints (null-intolerant where appropriate)
	# These use completed_fm alias (latest completed year <= current_year-1)
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
	if filters.assignedExitMultipleMin is not None:
		params.append(filters.assignedExitMultipleMin)
		conds.append("completed_fm.assigned_exit_multiple >= %s")

	# Returns filters using function (percent)
	if filters.stockReturns5yMin is not None:
		params.append(filters.stockReturns5yMin)
		conds.append("calculate_stock_return(c.id, INTERVAL '5 years') >= %s")
	if filters.stockReturns10yMin is not None:
		params.append(filters.stockReturns10yMin)
		conds.append("calculate_stock_return(c.id, INTERVAL '10 years') >= %s")

	# CAGR filters over 3 years ending completed_fm.period
	# IMPORTANT: We do NOT call calculate_cagr() here because it can throw when end_value < 0.
	# Instead we filter on safe precomputed CAGRs (see `cagr3` LATERAL join in fetch_candidates_all).
	if filters.salesCagr3yMin is not None:
		params.append(filters.salesCagr3yMin)
		conds.append("cagr3.sales_cagr_3y >= %s")
	if filters.epsCagr3yMin is not None:
		params.append(filters.epsCagr3yMin)
		conds.append("cagr3.eps_cagr_3y >= %s")
	if filters.fcfCagr3yMin is not None:
		params.append(filters.fcfCagr3yMin)
		conds.append("cagr3.fcf_cagr_3y >= %s")

	# IRR min gate using historical prices (decimal)
	if filters.irrMin is not None:
		params.append(horizon_years)
		params.append(filters.irrMin / 100.0)
		conds.append("(POWER(latest_price.latest_price / NULLIF(start_price.start_price, 0), 1.0 / %s) - 1) >= %s")

	where_clause = " AND ".join(conds) if conds else ""
	return where_clause, params


def fetch_candidates_all(horizon_years: int, filters: FiltersPayload) -> Tuple[List[CompanyIrr], Dict[str, Dict[str, Any]]]:
	"""
	Return ALL companies matching filters (no quality-score gating / no heuristic LIMIT),
	with enough fundamentals to batch-predict exit P/E in the LLM.
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
		/* Latest completed financial year per company to avoid in-progress current year estimates */
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
			calculate_stock_return(c.id, INTERVAL '5 years') AS return_5y,
			calculate_stock_return(c.id, INTERVAL '10 years') AS return_10y,
			cagr3.eps_cagr_3y,
			cagr3.sales_cagr_3y,
			cagr3.fcf_cagr_3y,
			(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', completed_fm.period) LIMIT 1) AS sector_median_pe,
			eps_fwd.period AS eps_forward_year,
			eps_fwd.eps AS eps_forward,
			latest_price.latest_price AS current_price,
			/* Simple rules-based fallback exit PE: blend current and sector median, clamped to a sector-relative band. */
			LEAST(
				GREATEST(
					0.5 * COALESCE(completed_fm.pe_ratio, 0) +
					0.5 * COALESCE((SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', completed_fm.period) LIMIT 1), 0),
					COALESCE((SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', completed_fm.period) LIMIT 1), 0) * 0.7
				),
				COALESCE((SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', completed_fm.period) LIMIT 1), 0) * 1.5
			) AS exit_pe_estimate,
			pe10.pe_avg_10y,
			pe10.pe_avg_10y_years_total,
			pe10.pe_avg_10y_years_used,
			completed_fm.period AS eps_year,
			completed_fm.eps AS eps_latest
		FROM companies c
		JOIN latest_price ON latest_price.company_id = c.id
		JOIN start_price ON start_price.company_id = c.id
		CROSS JOIN target_year ty
		LEFT JOIN LATERAL (
			/* Prefer exact target year EPS, else nearest future estimate, else nearest past. */
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
			/* Safe 3Y CAGRs (pct). Avoid calculate_cagr() to prevent POWER() errors on negative end values. */
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
			/* 10Y trailing average P/E excluding outliers > 100.
			   - years_total: how many years have non-null P/E in the 10Y window
			   - years_used: how many of those years have P/E <= 100 (used in the average)
			   UI rule:
			   - if years_total=0 -> show '—' (missing)
			   - if years_used=0 but years_total>0 -> show 'NM'
			*/
			SELECT
				(AVG(fm10.pe_ratio) FILTER (WHERE fm10.pe_ratio <= 100))::numeric AS pe_avg_10y,
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
	# first param is horizon_years for target_year CTE
	params.append(horizon_years)

	where_clause, params = _build_filters_where(filters, params, horizon_years)
	if where_clause:
		query += f"\nAND {where_clause}\n"
	query += "\nORDER BY c.ticker ASC;"

	rows: List[CompanyIrr] = []
	details: Dict[str, Dict[str, Any]] = {}
	with get_db_connection() as conn:
		with conn.cursor() as cur:
			cur.execute(query, tuple(params))
			for r in cur.fetchall():
				company = CompanyIrr(
					ticker=r[0],
					name=r[1],
					sector=r[2],
					country=r[3],
					market_cap=float(r[4]) if r[4] is not None else None,
					irr=0.0,
				)
				rows.append(company)
				details[company.ticker] = {
					"roe": float(r[5]) if r[5] is not None else None,
					"roic": float(r[6]) if r[6] is not None else None,
					"ebit_margin": float(r[7]) if r[7] is not None else None,
					"pe_ratio": float(r[8]) if r[8] is not None else None,
					"p_fcf_ratio": float(r[9]) if r[9] is not None else None,
					"ev_ebitda": float(r[10]) if r[10] is not None else None,
					"debt_ebitda": float(r[11]) if r[11] is not None else None,
					"return_5y": float(r[12]) if r[12] is not None else None,
					"return_10y": float(r[13]) if r[13] is not None else None,
					"eps_cagr_3y": float(r[14]) if r[14] is not None else None,
					"sales_cagr_3y": float(r[15]) if r[15] is not None else None,
					"fcf_cagr_3y": float(r[16]) if r[16] is not None else None,
					"sector_median_pe": float(r[17]) if r[17] is not None else None,
					"eps_forward_year": int(r[18]) if r[18] is not None else None,
					"eps_forward": float(r[19]) if r[19] is not None else None,
					"current_price": float(r[20]) if r[20] is not None else None,
					"exit_pe_estimate": float(r[21]) if r[21] is not None else None,
					"pe_avg_10y": float(r[22]) if r[22] is not None else None,
					"pe_avg_10y_years_total": int(r[23]) if r[23] is not None else None,
					"pe_avg_10y_years_used": int(r[24]) if r[24] is not None else None,
					"eps_year": int(r[25]) if r[25] is not None else None,
					"eps_latest": float(r[26]) if r[26] is not None else None,
				}
	return rows, details


def _chunked(items: List[Any], size: int) -> List[List[Any]]:
	return [items[i : i + size] for i in range(0, len(items), size)]


def run_gpt_predict_exit_pe_batched(
	candidates: List[CompanyIrr],
	details: Dict[str, Dict[str, Any]],
	*,
	horizon_years: int,
	batch_size: int = 50,
	log_prefix: str = "",
) -> Dict[str, Dict[str, Any]]:
	"""
	Predict exit P/E for ALL candidates by splitting into batches of 50 to keep prompts manageable.
	Returns dict: { ticker: { exit_pe, confidence, notes, breakdown } }
	"""
	api_key = os.getenv("GPT_API_SECRET")
	result: Dict[str, Dict[str, Any]] = {}
	debug = os.getenv("DEBUG_GPT", "").strip() == "1"

	if not api_key or not api_key.strip() or not candidates:
		return result

	client = _get_openai_client(api_key)
	model = _get_gpt_model_name()
	if debug:
		print("GPT model:", model)

	batches = _chunked(candidates, batch_size)
	pfx = f"{log_prefix} " if log_prefix else ""
	print(f"{pfx}[exit-pe] batching candidates={len(candidates)} batch_size={batch_size} batches={len(batches)}")
	start_all = time.time()
	exit_pe_max = _get_env_float("EXIT_PE_MAX", 40.0)
	if exit_pe_max <= 0:
		exit_pe_max = 40.0
	max_retries = max(0, _get_env_int("GPT_MAX_RETRIES", 2))
	retry_base_sleep_s = float(os.getenv("GPT_RETRY_BASE_SLEEP_S", "6") or "6")
	inter_batch_sleep_s = float(os.getenv("GPT_INTER_BATCH_SLEEP_S", "0") or "0")

	system_instructions = (
		"You are a buy-side equity analyst. Predict the {horizon_years}-year exit P/E multiple for each candidate based on their fundamental quality.\n\n"
		"Client Request:\n"
		"- Provide a **detailed, multi-sentence reasoning** (3-4 sentences).\n"
		"- **Adopt a Trader's Voice**: Be authoritative, decisive, and focused on risk/reward.\n"
		"- **NO FORMULAS** or weights. Focus on the *implications* of the metrics.\n"
		"- **MAX Exit P/E is 40.0x** (Hard cap. Never exceed this).\n"
		"- Show key metrics (ROE, ROIC, Margin, Debt, Growth, Hist PE) in the breakdown.\n\n"
		"Guidelines for Prediction:\n"
		"- Start with the **10Y Average P/E** (if valid/non-outlier) as a baseline.\n"
		"- Adjust based on **Quality** (ROE, ROIC, Margins) and **Growth** (EPS CAGR, Forward EPS).\n"
		"- High Quality (High ROE/ROIC/Margin) + Stable Growth = Premium multiple (near 10Y avg or slightly higher, capped at 40).\n"
		"- Low Quality (Low Margins, High Debt) or Declining Growth = Discount multiple (lower than 10Y avg).\n"
		"- If 10Y Avg is missing/NM, use sector norms or a conservative 15-18x base.\n\n"
		"Return STRICT JSON only:\n"
		"{\"predictions\": [\n"
		"  {\"ticker\":\"...\",\n"
		"   \"exit_pe\": float, (Model's prediction, max 40.0)\n"
		"   \"confidence\": 0.25..0.95,\n"
		"   \"notes\": \"A rich narrative. E.g. 'Company X commands a premium 30x multiple due to its fortress balance sheet and 40% EBIT margins, which confirm its widening moat. Although growth has moderated to 8%, the market will pay up for this level of capital efficiency (25% ROE). We anchor to the 10Y average but cap upside due to regulatory risks. The risk/reward remains favorable primarily due to pricing power.' NO FORMULAS.\",\n"
		"   \"breakdown\": [\n"
		"     {\"label\":\"P/E Avg (10Y)\", \"value\": \"<value>x\", \"meta\":\"Historical Baseline\"},\n"
		"     {\"label\":\"ROE\", \"value\": \"<value>%\", \"meta\":\"Efficiency\"},\n"
		"     {\"label\":\"ROIC\", \"value\": \"<value>%\", \"meta\":\"Capital Return\"},\n"
		"     {\"label\":\"EBIT Margin\", \"value\": \"<value>%\", \"meta\":\"Profitability\"},\n"
		"     {\"label\":\"Debt/EBITDA\", \"value\": \"<value>x\", \"meta\":\"Leverage\"},\n"
		"     {\"label\":\"EPS CAGR (3Y)\", \"value\": \"<value>%\", \"meta\":\"Hist Growth\"},\n"
		"     {\"label\":\"Proj EPS (Fwd)\", \"value\": \"<value>\", \"meta\":\"Forward Estimate\"},\n"
		"     {\"label\":\"Target Exit P/E\", \"value\": \"<value>x\", \"meta\":\"Prediction (Max 40x)\"}\n"
		"   ]\n"
		"  }\n"
		"]}\n"
	)

	for idx, batch in enumerate(batches, start=1):
		batch_start = time.time()
		print(f"{pfx}[exit-pe] batch_start {idx}/{len(batches)} size={len(batch)}")
		candidate_objs: List[Dict[str, Any]] = []
		for c in batch:
			d = details.get(c.ticker, {})
			candidate_objs.append(
				{
					"ticker": c.ticker,
					"sector": c.sector,
					"country": c.country,
					"market_cap": c.market_cap,
					"roe": d.get("roe"),
					"roic": d.get("roic"),
					"ebit_margin": d.get("ebit_margin"),
					"ev_ebitda": d.get("ev_ebitda"),
					"debt_ebitda": d.get("debt_ebitda"),
					"eps_cagr_3y": d.get("eps_cagr_3y"),
					"eps_forward": d.get("eps_forward"),
					"eps_forward_year": d.get("eps_forward_year"),
					"pe_avg_10y": d.get("pe_avg_10y"),
					"pe_avg_10y_years_total": d.get("pe_avg_10y_years_total"),
					"pe_avg_10y_years_used": d.get("pe_avg_10y_years_used"),
				}
			)

		payload = {"horizon_years": horizon_years, "candidates": candidate_objs}
		if debug:
			print(f"[exit-pe] batch={idx}/{len(batches)} tickers={len(candidate_objs)}")

		text: Optional[str] = None
		for attempt in range(0, max_retries + 1):
			text = _chatgpt_text(
				client,
				model=model,
				system=system_instructions,
				user=json.dumps(payload),
				expect_json=True,
				timeout_s=float(os.getenv("GPT_TIMEOUT_S", "180")),
			)
			if text:
				break
			if attempt >= max_retries:
				print(f"{pfx}[exit-pe] batch={idx}/{len(batches)} failed: empty response (attempts={max_retries+1})")
				break
			sleep_s = min(60.0, (retry_base_sleep_s * (2**attempt)) + (time.time() % 1.0))
			print(f"{pfx}[exit-pe] batch_retry {idx}/{len(batches)} attempt={attempt+1}/{max_retries+1} sleep_s={sleep_s:.1f}")
			time.sleep(sleep_s)

		if not text:
			continue

		try:
			data = json.loads(_clean_json_text(text))
			added = 0
			for item in data.get("predictions", []):
				t = item.get("ticker")
				pe = item.get("exit_pe")
				conf = item.get("confidence", None)
				notes = item.get("notes", "")
				breakdown = item.get("breakdown", None)
				if isinstance(t, str) and isinstance(pe, (int, float)):
					pe_clamped = _clamp_exit_pe(float(pe))
					out: Dict[str, Any] = {"exit_pe": pe_clamped, "confidence": conf, "notes": notes}
					if isinstance(breakdown, list):
						out["breakdown"] = breakdown
					result[t] = out
					added += 1
			print(
				f"{pfx}[exit-pe] batch_done {idx}/{len(batches)} "
				f"added={added} total_preds={len(result)}/{len(candidates)} "
				f"elapsed_s={time.time() - batch_start:.1f}"
			)
		except Exception as ex:
			print(f"{pfx}[exit-pe] batch={idx}/{len(batches)} parse_error:", ex)
			continue
		if inter_batch_sleep_s > 0:
			time.sleep(inter_batch_sleep_s)

	print(
		f"{pfx}[exit-pe] all_done predictions_received={len(result)} out_of={len(candidates)} "
		f"elapsed_s={time.time() - start_all:.1f}"
	)
	return result


@app.post("/api/top-irr", response_model=TopIrrResponse)
def api_top_irr_all(payload: TopIrrRequest = Body(...)):
	"""
	Evaluate ALL companies (no quality-score gating). Exit P/E is predicted via o3 in batches of 50,
	then IRR is computed for all companies and the top-N are returned.
	"""
	req_started = time.time()
	req_key = _top_irr_key(payload)
	req_id = req_key[:8]
	limit, default_limit, max_limit = _resolve_limit(getattr(payload, "limit", None))
	horizon_years = min(max(payload.horizon_years, 1), 15)
	print(
		f"[top-irr] req={req_id} key={req_id} "
		f"limit_requested={getattr(payload, 'limit', None)} "
		f"limit_default_env={default_limit} limit_max_env={max_limit} limit_used={limit}"
	)

	# Single-flight: dedupe identical expensive computations (common in Next.js dev StrictMode)
	with _TOP_IRR_LOCK:
		_cleanup_top_irr_cache(time.time())
		entry = _TOP_IRR_INFLIGHT.get(req_key)
		if entry and isinstance(entry.get("event"), threading.Event):
			evt: threading.Event = entry["event"]
			if evt.is_set() and entry.get("result") is not None:
				print(f"[top-irr] req={req_id} cache_hit age_s={time.time() - float(entry.get('completed_at', time.time())):.1f}")
				return entry["result"]
			if not evt.is_set():
				print(f"[top-irr] req={req_id} join_inflight")
				leader = False
			else:
				leader = True
		else:
			evt = threading.Event()
			entry = {"event": evt, "started_at": time.time(), "result": None, "error": None}
			_TOP_IRR_INFLIGHT[req_key] = entry
			leader = True

	if not leader:
		evt.wait()
		if entry.get("result") is not None:
			print(f"[top-irr] req={req_id} joined_done elapsed_s={time.time() - req_started:.1f}")
			return entry["result"]
		# If leader errored, surface a stable error
		raise Exception(entry.get("error") or "Top-IRR computation failed")

	try:
		# Step 1: fetch all candidates (optionally still constrained by user filters)
		candidates, details = fetch_candidates_all(horizon_years=horizon_years, filters=payload.filters)
		if not candidates:
			out = TopIrrResponse(companies=[], analysis="No candidates match the filters.")
			entry["result"] = out
			return out

		# Step 2: predict exit P/E in batches of 50 (wait for all batches)
		batch_size = int(os.getenv("GPT_BATCH_SIZE", "50"))
		pe_preds = run_gpt_predict_exit_pe_batched(
			candidates,
			details,
			horizon_years=horizon_years,
			batch_size=batch_size,
			log_prefix=f"req={req_id}",
		)

		# Step 3: compute projected IRR for each company using predicted exit P/E (fallback to rules-based estimate)
		scored: List[Tuple[CompanyIrr, float, float, float]] = []  # (company, irr_decimal, exit_price, db_price)
		have_any_model_preds = len(pe_preds) > 0
		for c in candidates:
			d = details.get(c.ticker, {})
			db_price = d.get("current_price")
			eps_year = d.get("eps_forward_year") or d.get("eps_year")
			eps_val = d.get("eps_forward") or d.get("eps_latest")
			if not db_price or not eps_val:
				continue

			exit_pe = None
			exit_pe_conf = None
			exit_pe_notes = None
			exit_pe_breakdown = None

			if c.ticker in pe_preds:
				exit_pe = pe_preds[c.ticker].get("exit_pe")
				exit_pe_conf = pe_preds[c.ticker].get("confidence")
				exit_pe_notes = pe_preds[c.ticker].get("notes")
				exit_pe_breakdown = pe_preds[c.ticker].get("breakdown")
			else:
				# If the model returned nothing at all, fall back globally.
				# If the model returned some predictions but missed this ticker, also fall back so we still "evaluate all companies".
				exit_pe = d.get("exit_pe_estimate")
				# Quantified fallback notes (same scoring model as the LLM instructions, but without an LLM run).
				exit_pe_notes = (
					"Model unavailable or missing for this ticker; using rules-based exit P/E model anchored to P/E Avg (10Y, excl >100) "
					"(or BaseMultiple=18.0x if NM/NA). Multiplier = 1 + 0.25*GrowthScore + 0.15*(ProfitScore-0.5) + 0.10*(LevScore-0.5), "
					"exit_pe = clamp(BaseMultiple*Multiplier, 3, EXIT_PE_MAX). Missing inputs treated as neutral (0 contribution)."
				)
				pe_total = d.get("pe_avg_10y_years_total")
				pe_used = d.get("pe_avg_10y_years_used")
				pe_avg = d.get("pe_avg_10y")
				pe_anchor = pe_avg if pe_used not in (None, 0) else "NM" if (isinstance(pe_total, (int, float)) and pe_total > 0) else "NA"
				base_multiple = pe_avg if pe_used not in (None, 0) and pe_avg is not None else 18.0
				exit_pe_breakdown = [
					{
						"label": "P/E Avg (10Y, excl >100)",
						"value": pe_anchor,
						"meta": f"years_used={pe_used} years_total={pe_total}",
					},
					{"label": "BaseMultiple Used", "value": base_multiple, "meta": "18.0 if NM/NA"},
					{"label": "ROE (input)", "value": d.get("roe")},
					{"label": "ROIC (input)", "value": d.get("roic")},
					{"label": "EBIT Margin (input)", "value": d.get("ebit_margin")},
					{"label": "Debt/EBITDA (input)", "value": d.get("debt_ebitda")},
					{"label": "EPS CAGR (3Y) (input)", "value": d.get("eps_cagr_3y")},
					{"label": "Projected EPS (Forward) (input)", "value": eps_val},
					{"label": "Final Exit P/E", "value": exit_pe},
				]

			# Enforce business rule cap for both model and fallback values
			try:
				exit_pe = _clamp_exit_pe(float(exit_pe))
			except Exception:
				exit_pe = None

			if not exit_pe or exit_pe <= 0:
				continue

			try:
				years_to_exit = max(1, int(horizon_years))
				exit_price = float(eps_val) * float(exit_pe)
				irr_db = (exit_price / float(db_price)) ** (1.0 / years_to_exit) - 1.0
				ci = CompanyIrr(
					ticker=c.ticker,
					name=c.name,
					sector=c.sector,
					country=c.country,
					market_cap=c.market_cap,
					current_price=float(db_price),
					irr=irr_db * 100.0,
					exit_pe=float(exit_pe),
					exit_pe_confidence=float(exit_pe_conf) if isinstance(exit_pe_conf, (int, float)) else None,
					exit_pe_notes=str(exit_pe_notes) if exit_pe_notes is not None else None,
					exit_pe_breakdown=exit_pe_breakdown if isinstance(exit_pe_breakdown, list) else None,
					eps=float(eps_val),
					eps_year=int(eps_year) if eps_year is not None else None,
					pe_avg_10y=float(d.get("pe_avg_10y")) if d.get("pe_avg_10y") is not None else None,
					pe_avg_10y_years_total=int(d.get("pe_avg_10y_years_total")) if d.get("pe_avg_10y_years_total") is not None else None,
					pe_avg_10y_years_used=int(d.get("pe_avg_10y_years_used")) if d.get("pe_avg_10y_years_used") is not None else None,
				)
				scored.append((ci, irr_db, float(exit_price), float(db_price)))
			except Exception:
				continue

		if not scored:
			out = TopIrrResponse(companies=[], analysis="Unable to compute IRR for any company (missing prices/EPS).")
			entry["result"] = out
			return out

		# Step 4: optional Stooq rerank for top-N only (avoid hundreds of HTTP calls)
		use_stooq = os.getenv("USE_STOOQ_PRICE", "1").strip() == "1"
		log_prices = os.getenv("LOG_CURRENT_PRICE", "1").strip() == "1"
		scored.sort(key=lambda x: x[1], reverse=True)
		subset_n = min(len(scored), int(os.getenv("STOOQ_RERANK_TOP_N", str(max(50, limit * 20)))))
		price_used: Dict[str, Tuple[Optional[float], str]] = {}

		if use_stooq and subset_n > 0:
			updated: List[Tuple[CompanyIrr, float, float, float]] = []
			for (ci, irr_db, exit_price, db_price) in scored[:subset_n]:
				ext = get_current_price_stooq(ci.ticker)
				if ext is not None and ext > 0:
					try:
						years_to_exit = max(1, int(horizon_years))
						irr_ext = (exit_price / ext) ** (1.0 / years_to_exit) - 1.0
						ci.current_price = float(ext)
						ci.irr = irr_ext * 100.0
						price_used[ci.ticker] = (ext, "stooq")
						updated.append((ci, irr_ext, exit_price, db_price))
						continue
					except Exception:
						pass
				ci.current_price = float(db_price)
				price_used[ci.ticker] = (db_price, "db")
				updated.append((ci, irr_db, exit_price, db_price))
			scored = updated + scored[subset_n:]

		# Final rank by (possibly updated) IRR
		scored.sort(key=lambda x: x[0].irr if x[0].irr is not None else float("-inf"), reverse=True)
		top_companies = [ci for (ci, _, _, _) in scored[:limit]]

		if log_prices:
			for ci in top_companies:
				p, src = price_used.get(ci.ticker, (ci.current_price, "db"))
				sym = _bloomberg_ticker_to_stooq_symbol(ci.ticker) if src == "stooq" else None
				if src == "stooq":
					print(f"[price] {ci.ticker} stooq_symbol={sym} current_price={p}")
				else:
					print(f"[price] {ci.ticker} current_price(db)={p}")

		# No extra trend-analysis call; "reason" is in exit_pe_notes/breakdown
		out = TopIrrResponse(companies=top_companies, analysis=None)
		entry["result"] = out
		return out
	except Exception as ex:
		entry["error"] = str(ex)
		raise
	finally:
		entry["completed_at"] = time.time()
		evt.set()


# If PDF/research endpoints are needed here too, import/copy them from main_gpt.py.


