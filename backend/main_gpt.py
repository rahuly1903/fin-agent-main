import os
from typing import Any, Dict, List, Optional, Tuple
import json
import time
import csv
from io import StringIO
import asyncio
from fastapi import FastAPI, Query, Body, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# Import research module
try:
	from .research import build_research_prompt, run_deep_research_streaming, generate_pdf_from_markdown
except Exception:
	try:
		# When running `uvicorn main_gpt:app --app-dir backend`, `backend` isn't a package.
		from research import build_research_prompt, run_deep_research_streaming, generate_pdf_from_markdown  # type: ignore
	except Exception:
		import sys
		import os as _os
		sys.path.append(_os.path.dirname(_os.path.dirname(__file__)))
		from backend.research import build_research_prompt, run_deep_research_streaming, generate_pdf_from_markdown  # type: ignore

# Support running as a package (uvicorn backend.main:app) or module (uvicorn main:app)
try:
	from .database import get_db_connection  # type: ignore
except Exception:
	import sys
	import os as _os
	sys.path.append(_os.path.dirname(_os.path.dirname(__file__)))
	from database import get_db_connection  # type: ignore

load_dotenv()

# -----------------------------------------------------------------------------
# External price (Stooq) helpers
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
	# Common Bloomberg formats: 'BRK/B', 'RDSA', etc. Stooq typically uses '-' for class shares.
	root = root.replace("/", "-")
	# Only handle US equities for now (UW/UN etc). Fall back to .us.
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
		# h=1 includes header row (Symbol,Date,Time,Open,High,Low,Close,Volume,OI)
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

app = FastAPI(title="Fin Agent API", version="0.1.0")
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
	exit_pe_notes: Optional[str] = None
	exit_pe_breakdown: Optional[List[Dict[str, Any]]] = None
	eps: Optional[float] = None
	eps_year: Optional[int] = None
	pe_avg_10y: Optional[float] = None


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
	limit: int = 5
	horizon_years: int = 5
	filters: FiltersPayload

# Research Assistant Models
class CompanyDropdown(BaseModel):
	id: str
	ticker: str
	name: Optional[str] = None


class ResearchRequest(BaseModel):
	company_id: str
	analyst_name: Optional[str] = None


class PdfRequest(BaseModel):
	content: str
	title: str = "Research Report"


def fetch_top_irr(limit: int = 5, horizon_years: int = 5) -> List[CompanyIrr]:
	"""
	Select top tickers by annualized price CAGR over horizon_years using share_prices.
	IRR ≈ CAGR = (latest/start)^(1/years) - 1
	"""
	query = f"""
		WITH latest AS (
			SELECT sp.company_id, sp.price AS latest_price
			FROM (
				SELECT company_id, MAX(date) AS max_date
				FROM share_prices
				WHERE date <= CURRENT_DATE
				GROUP BY company_id
			) m
			JOIN share_prices sp ON sp.company_id = m.company_id AND sp.date = m.max_date
		),
		startp AS (
			SELECT sp.company_id, sp.price AS start_price
			FROM (
				SELECT company_id, MAX(date) AS start_date
				FROM share_prices
				WHERE date <= CURRENT_DATE - INTERVAL '{horizon_years} years'
				GROUP BY company_id
			) s
			JOIN share_prices sp ON sp.company_id = s.company_id AND sp.date = s.start_date
		)
		SELECT
			c.ticker,
			c.name,
			c.sector,
			c.country,
			c.market_cap,
			(POWER(l.latest_price / NULLIF(s.start_price, 0), 1.0 / {horizon_years}) - 1) AS irr
		FROM companies c
		JOIN latest l ON l.company_id = c.id
		JOIN startp s ON s.company_id = c.id
		WHERE s.start_price IS NOT NULL AND s.start_price > 0
		ORDER BY irr DESC NULLS LAST
		LIMIT %s;
	"""
	rows: List[CompanyIrr] = []
	with get_db_connection() as conn:
		with conn.cursor() as cur:
			cur.execute(query, (limit,))
			for r in cur.fetchall():
				irr_value = float(r[5]) if r[5] is not None else 0.0
				rows.append(CompanyIrr(
					ticker=r[0],
					name=r[1],
					sector=r[2],
					country=r[3],
					market_cap=float(r[4]) if r[4] is not None else None,
					irr=irr_value * 100.0
				))
	return rows


def build_gemini_prompt(rows: List[CompanyIrr], horizon_years: int, details: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
	tickers = ", ".join([r.ticker for r in rows])
	lines = [
		"Act as a disciplined equity analyst.",
		"Task: Provide a concise trend analysis for the following tickers based on observed performance and plausible drivers.",
		"Only use general market knowledge and the provided structured data; do not fabricate numbers.",
		"Prioritize quantifiable drivers (growth, margins, capital intensity, leverage) and clear risks.",
		"Output should be crisp, with bullets per ticker; avoid marketing language.",
		f"Horizon: {horizon_years} years.",
		"",
		"Structured data (annualized price IRR approximation, based on historical prices):"
	]
	for r in rows:
		d = details.get(r.ticker) if details else None
		suffix = ""
		if d:
			roe = d.get("roe")
			roic = d.get("roic")
			margin = d.get("ebit_margin")
			pe = d.get("pe_ratio")
			ev_e = d.get("ev_ebitda")
			de = d.get("debt_ebitda")
			ret5 = d.get("return_5y")
			ret10 = d.get("return_10y")
			eps_cagr_3y = d.get("eps_cagr_3y")
			fcf_cagr_3y = d.get("fcf_cagr_3y")
			extras = []
			if roe is not None: extras.append(f"ROE={roe:.1f}%")
			if roic is not None: extras.append(f"ROIC={roic:.1f}%")
			if margin is not None: extras.append(f"EBIT Margin={margin:.1f}%")
			if pe is not None: extras.append(f"P/E={pe:.1f}x")
			if ev_e is not None: extras.append(f"EV/EBITDA={ev_e:.1f}x")
			if de is not None: extras.append(f"Debt/EBITDA={de:.1f}x")
			if ret5 is not None: extras.append(f"5Y Ret={ret5:.1f}%")
			if ret10 is not None: extras.append(f"10Y Ret={ret10:.1f}%")
			if eps_cagr_3y is not None: extras.append(f"EPS CAGR 3Y={eps_cagr_3y:.1f}%")
			if fcf_cagr_3y is not None: extras.append(f"FCF CAGR 3Y={fcf_cagr_3y:.1f}%")
			if extras:
				suffix = " | " + " ".join(extras)
		lines.append(f"- {r.ticker}: IRR≈{r.irr:.2f}% | Sector={r.sector or 'NA'} | Country={r.country or 'NA'} | MktCap={r.market_cap or 'NA'} | Name={r.name or ''}{suffix}")
	lines += [
		"",
		"Now:",
		"- For each ticker: list 2–3 key drivers and 1–2 principal risks.",
		"- End with a one-line summary on which tickers appear most promising and why."
	]
	return "\n".join(lines)


def _get_gpt_model_name() -> str:
	"""
	Model name for ChatGPT. Override with GPT_MODEL.
	Default chosen to be a strong "thinking" general model.
	"""
	# User requested "gpt oss" – interpret as OpenAI's omni model family.
	# You can override (examples): gpt-4o, gpt-4.1, o1, o3-mini
	return os.getenv("GPT_MODEL", "o3")


def _get_openai_client(api_key: str):
	"""
	Create an OpenAI client for ChatGPT.
	"""
	try:
		from openai import OpenAI  # type: ignore
	except Exception as e:
		raise ImportError(
			"Failed to import `openai`. Install with `pip install -U openai`."
		) from e
	return OpenAI(api_key=api_key)


def _clean_json_text(text: str) -> str:
	"""
	Best-effort cleanup for JSON responses that may include code fences or extra text.
	"""
	t = text.strip()
	if t.startswith("```"):
		# Strip leading/trailing code fences
		t = t.strip("`").strip()
	# If there's extra prose, try to extract the first JSON object
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
	"""
	Call ChatGPT and return the message text.
	If expect_json=True, we request JSON output when supported, but still parse defensively.
	"""
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
		# Many OpenAI chat models support response_format for strict JSON.
		if expect_json:
			kwargs["response_format"] = {"type": "json_object"}
			kwargs["temperature"] = 1
		else:
			kwargs["temperature"] = 1
		resp = client.chat.completions.create(**kwargs)
		# print("GPT response: ", resp.choices[0].message.content)
		return resp.choices[0].message.content
	except Exception as ex:
		print("Exception2: ", ex)
		return None


def run_gemini_analysis(rows: List[CompanyIrr], horizon_years: int, details: Optional[Dict[str, Dict[str, Any]]] = None) -> Optional[str]:
	api_key = os.getenv("GPT_API_SECRET")
	print("GPT_API_SECRET: ", api_key)
	if not api_key or not api_key.strip() or not rows:
		return None
	try:
		client = _get_openai_client(api_key)
		model = _get_gpt_model_name()
		prompt = build_gemini_prompt(rows, horizon_years, details=details)
		return _chatgpt_text(
			client,
			model=model,
			system="You are a disciplined equity analyst.",
			user=prompt,
			expect_json=False,
			timeout_s=90.0,
		)
	except Exception as ex:
		print("Exception: ", ex)
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
			conds.append(f"c.market_cap >= %s")
		if filters.marketCap.max is not None:
			params.append(filters.marketCap.max)
			conds.append(f"c.market_cap <= %s")

	# Country include/exclude
	if filters.country and filters.country.selection:
		if filters.country.excludeMode:
			placeholders = ", ".join(["%s"] * len(filters.country.selection))
			params.extend(filters.country.selection)
			conds.append(f"COALESCE(c.country, '') NOT IN ({placeholders})")
		else:
			placeholders = ", ".join(["%s"] * len(filters.country.selection))
			params.extend(filters.country.selection)
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

	# Latest financial metrics constraints
	# These use latest_fm alias
	if filters.roeMin is not None:
		params.append(filters.roeMin)
		conds.append("latest_fm.roe >= %s")
	if filters.roicMin is not None:
		params.append(filters.roicMin)
		conds.append("latest_fm.roic >= %s")
	if filters.ebitMarginMin is not None:
		params.append(filters.ebitMarginMin)
		conds.append("latest_fm.ebit_margin >= %s")
	if filters.peMax is not None:
		params.append(filters.peMax)
		conds.append("latest_fm.pe_ratio <= %s")
	if filters.priceToFcfMax is not None:
		params.append(filters.priceToFcfMax)
		conds.append("latest_fm.p_fcf_ratio <= %s")
	if filters.evToEbitdaMax is not None:
		params.append(filters.evToEbitdaMax)
		conds.append("latest_fm.ev_ebitda <= %s")
	if filters.debtToEbitdaMax is not None:
		params.append(filters.debtToEbitdaMax)
		conds.append("latest_fm.debt_ebitda <= %s")
	if filters.assignedExitMultipleMin is not None:
		params.append(filters.assignedExitMultipleMin)
		conds.append("latest_fm.assigned_exit_multiple >= %s")

	# Returns filters using function (percent)
	if filters.stockReturns5yMin is not None:
		params.append(filters.stockReturns5yMin)
		conds.append("calculate_stock_return(c.id, INTERVAL '5 years') >= %s")
	if filters.stockReturns10yMin is not None:
		params.append(filters.stockReturns10yMin)
		conds.append("calculate_stock_return(c.id, INTERVAL '10 years') >= %s")

	# CAGR filters (subset) using function over 3 years ending latest period
	if filters.salesCagr3yMin is not None:
		params.append(filters.salesCagr3yMin)
		conds.append("calculate_cagr(c.id, 'sales', 3, latest_fm.period) >= %s")
	if filters.epsCagr3yMin is not None:
		params.append(filters.epsCagr3yMin)
		conds.append("calculate_cagr(c.id, 'eps', 3, latest_fm.period) >= %s")
	if filters.fcfCagr3yMin is not None:
		params.append(filters.fcfCagr3yMin)
		conds.append("calculate_cagr(c.id, 'fcf', 3, latest_fm.period) >= %s")

	# IRR min (compare with decimal)
	if filters.irrMin is not None:
		# order of params must match placeholders: horizon_years, irr threshold
		params.append(horizon_years)
		params.append(filters.irrMin / 100.0)
		conds.append("(POWER(latest_price.latest_price / NULLIF(start_price.start_price, 0), 1.0 / %s) - 1) >= %s")

	where_clause = " AND ".join(conds) if conds else ""
	return where_clause, params


def fetch_top_irr_filtered(limit: int, horizon_years: int, filters: FiltersPayload) -> Tuple[List[CompanyIrr], Dict[str, Dict[str, Any]]]:
	params: List[Any] = []

	# CTEs and base selection
	base = f"""
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
		latest_fm AS (
			SELECT DISTINCT ON (fm.company_id)
				fm.*
			FROM financial_metrics fm
			ORDER BY fm.company_id, fm.period DESC
		),
		target_year AS (
			/* In-progress current year: for 3Y horizon and 2026 current year, target year is 2028 (current + 3 - 1). */
			SELECT (EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER + %s - 1) AS y
		)
		SELECT
			c.ticker,
			c.name,
			c.sector,
			c.country,
			c.market_cap,
			(POWER(latest_price.latest_price / NULLIF(start_price.start_price, 0), 1.0 / %s) - 1) AS irr,
			latest_fm.roe,
			latest_fm.roic,
			latest_fm.ebit_margin,
			latest_fm.pe_ratio,
			latest_fm.p_fcf_ratio,
			latest_fm.ev_ebitda,
			latest_fm.debt_ebitda,
			calculate_stock_return(c.id, INTERVAL '5 years') AS return_5y,
			calculate_stock_return(c.id, INTERVAL '10 years') AS return_10y,
			calculate_cagr(c.id, 'eps', 3, latest_fm.period) AS eps_cagr_3y,
			calculate_cagr(c.id, 'fcf', 3, latest_fm.period) AS fcf_cagr_3y
		FROM companies c
		JOIN latest_price ON latest_price.company_id = c.id
		JOIN start_price ON start_price.company_id = c.id
		LEFT JOIN latest_fm ON latest_fm.company_id = c.id
		WHERE start_price.start_price IS NOT NULL AND start_price.start_price > 0
		  AND latest_price.latest_price IS NOT NULL AND latest_price.latest_price > 0
		  AND latest_fm.eps IS NOT NULL AND latest_fm.eps > 0
	"""
	# First %s: used for growth factor; Second %s: used for IRR exponent
	params.append(horizon_years)
	params.append(horizon_years)

	where_clause, params = _build_filters_where(filters, params, horizon_years)
	if where_clause:
		base += f"\nAND {where_clause}\n"

	# Extend SELECT to compute forward EPS, sector median PE, quality, exit PE and predicted IRR
	base = base.replace(
		"SELECT\n\t\t\tc.ticker,\n\t\t\tc.name,\n\t\t\tc.sector,\n\t\t\tc.country,\n\t\t\tc.market_cap,\n\t\t\t(POWER(latest_price.latest_price / NULLIF(start_price.start_price, 0), 1.0 / %s) - 1) AS irr,\n\t\t\tlatest_fm.roe,\n\t\t\tlatest_fm.roic,\n\t\t\tlatest_fm.ebit_margin,\n\t\t\tlatest_fm.pe_ratio,\n\t\t\tlatest_fm.p_fcf_ratio,\n\t\t\tlatest_fm.ev_ebitda,\n\t\t\tlatest_fm.debt_ebitda,\n\t\t\tcalculate_stock_return(c.id, INTERVAL '5 years') AS return_5y,\n\t\t\tcalculate_stock_return(c.id, INTERVAL '10 years') AS return_10y,\n\t\t\tcalculate_cagr(c.id, 'eps', 3, latest_fm.period) AS eps_cagr_3y",
		"""SELECT
			c.ticker,
			c.name,
			c.sector,
			c.country,
			c.market_cap,
			/* Projected IRR using EPS growth and exit PE mean-reversion with quality adjustment */
			(
				POWER(
					(
						/* EPS_N */
						latest_fm.eps * POWER(1 + COALESCE(LEAST(GREATEST(calculate_cagr(c.id, 'eps', 3, latest_fm.period), -50), 50) / 100.0, 0), %s)
					)
					*
					/* exit PE */
					LEAST(
						GREATEST(
							/* candidate exit PE: blend current PE and sector median adjusted by quality */
							0.5 * COALESCE(latest_fm.pe_ratio, 0) +
							0.5 * (
								(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', latest_fm.period) LIMIT 1) *
								(1 + 0.4 * ((COALESCE(calculate_quality_score(c.id, latest_fm.period), 50) / 100.0) - 0.5))
							),
							/* lower bound relative to sector */
							(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', latest_fm.period) LIMIT 1) * 0.7
						),
						/* upper bound relative to sector */
						(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', latest_fm.period) LIMIT 1) * 1.5
					)
					/* absolute sanity bounds */
					/* final clamp */
					  / NULLIF(latest_price.latest_price, 0),
					1.0 / %s
				) - 1
			) AS irr,
			latest_fm.roe,
			latest_fm.roic,
			latest_fm.ebit_margin,
			latest_fm.pe_ratio,
			latest_fm.p_fcf_ratio,
			latest_fm.ev_ebitda,
			latest_fm.debt_ebitda,
			calculate_stock_return(c.id, INTERVAL '5 years') AS return_5y,
			calculate_stock_return(c.id, INTERVAL '10 years') AS return_10y,
			calculate_cagr(c.id, 'eps', 3, latest_fm.period) AS eps_cagr_3y,
			(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', latest_fm.period) LIMIT 1) AS sector_median_pe,
			calculate_quality_score(c.id, latest_fm.period) AS quality_score,
			/* expose EPS_N and an approximate exit PE for details */
			latest_fm.eps * POWER(1 + COALESCE(LEAST(GREATEST(calculate_cagr(c.id, 'eps', 3, latest_fm.period), -50), 50) / 100.0, 0), %s) AS eps_n,
			LEAST(
				GREATEST(
					0.5 * COALESCE(latest_fm.pe_ratio, 0) +
					0.5 * (
						(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', latest_fm.period) LIMIT 1) *
						(1 + 0.4 * ((COALESCE(calculate_quality_score(c.id, latest_fm.period), 50) / 100.0) - 0.5))
					),
					(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', latest_fm.period) LIMIT 1) * 0.7
				),
				(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', latest_fm.period) LIMIT 1) * 1.5
			) AS exit_pe_estimate,
			latest_price.latest_price AS current_price"""
	)

	# Order and limit
	base += "\nORDER BY irr DESC NULLS LAST\nLIMIT %s;"
	params.append(limit)

	rows: List[CompanyIrr] = []
	details: Dict[str, Dict[str, Any]] = {}
	with get_db_connection() as conn:
		with conn.cursor() as cur:
			cur.execute(base, tuple(params))
			for r in cur.fetchall():
				irr_value = float(r[5]) if r[5] is not None else 0.0
				company = CompanyIrr(
					ticker=r[0],
					name=r[1],
					sector=r[2],
					country=r[3],
					market_cap=float(r[4]) if r[4] is not None else None,
					irr=irr_value * 100.0
				)
				rows.append(company)
				details[company.ticker] = {
					"roe": float(r[6]) if r[6] is not None else None,
					"roic": float(r[7]) if r[7] is not None else None,
					"ebit_margin": float(r[8]) if r[8] is not None else None,
					"pe_ratio": float(r[9]) if r[9] is not None else None,
					"p_fcf_ratio": float(r[10]) if r[10] is not None else None,
					"ev_ebitda": float(r[11]) if r[11] is not None else None,
					"debt_ebitda": float(r[12]) if r[12] is not None else None,
					"return_5y": float(r[13]) if r[13] is not None else None,
					"return_10y": float(r[14]) if r[14] is not None else None,
					"eps_cagr_3y": float(r[15]) if r[15] is not None else None,
					"sector_median_pe": float(r[16]) if r[16] is not None else None,
					"quality_score": float(r[17]) if r[17] is not None else None,
					"eps_n": float(r[18]) if r[18] is not None else None,
					"exit_pe_estimate": float(r[19]) if r[19] is not None else None,
					"current_price": float(r[20]) if r[20] is not None else None
				}
	return rows, details


def fetch_candidates_filtered(candidate_cap: int, horizon_years: int, filters: FiltersPayload) -> Tuple[List[CompanyIrr], Dict[str, Dict[str, Any]]]:
	"""
	Similar to fetch_top_irr_filtered but returns a capped candidate set without relying on IRR ordering for selection.
	Uses a heuristic ORDER BY only to reduce the set size for the LLM prompt.
	"""
	params: List[Any] = []
	base = f"""
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
		/* Strategy 4: use last completed financial year metrics (<= current_year-1) for quality/leverage screens. */
		completed_fm AS (
			SELECT DISTINCT ON (fm.company_id)
				fm.*
			FROM financial_metrics fm
			WHERE fm.period <= (EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER - 1)
			ORDER BY fm.company_id, fm.period DESC
		),
		target_year AS (
			/* In-progress current year: for 3Y horizon and 2026 current year, target year is 2028 (current + 3 - 1). */
			SELECT (EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER + %s - 1) AS y
		)
		SELECT
			c.ticker,
			c.name,
			c.sector,
			c.country,
			c.market_cap,
			(
				POWER(
					(
						completed_fm.eps * POWER(1 + COALESCE(LEAST(GREATEST(calculate_cagr(c.id, 'eps', 3, completed_fm.period), -50), 50) / 100.0, 0), %s)
					)
					*
					LEAST(
						GREATEST(
							0.5 * COALESCE(completed_fm.pe_ratio, 0) +
							0.5 * (
								(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', completed_fm.period) LIMIT 1) *
								(1 + 0.4 * ((
									COALESCE(
										/* Simple quality score (0..100): ROE + EBIT margin + low leverage, no ROIC */
										ROUND((
											COALESCE(
												LEAST(
													GREATEST((CASE WHEN ABS(completed_fm.roe) <= 2 THEN completed_fm.roe * 100 ELSE completed_fm.roe END), 0),
													40
												) / 40.0,
												0
											) * 34
											+
											COALESCE(
												LEAST(
													GREATEST((CASE WHEN ABS(completed_fm.ebit_margin) <= 2 THEN completed_fm.ebit_margin ELSE completed_fm.ebit_margin / 100 END), 0),
													0.30
												) / 0.30,
												0
											) * 33
											+
											COALESCE(
												(1 - LEAST(GREATEST(completed_fm.debt_ebitda, 0) / 5.0, 1)),
												0
											) * 33
										)::numeric, 2),
										50
									) / 100.0
								) - 0.5))
							),
							(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', completed_fm.period) LIMIT 1) * 0.7
						),
						(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', completed_fm.period) LIMIT 1) * 1.5
					)
					  / NULLIF(latest_price.latest_price, 0),
					1.0 / %s
				) - 1
			) AS irr,
			completed_fm.roe,
			completed_fm.roic,
			completed_fm.ebit_margin,
			completed_fm.pe_ratio,
			completed_fm.p_fcf_ratio,
			completed_fm.ev_ebitda,
			completed_fm.debt_ebitda,
			calculate_stock_return(c.id, INTERVAL '5 years') AS return_5y,
			calculate_stock_return(c.id, INTERVAL '10 years') AS return_10y,
			calculate_cagr(c.id, 'eps', 3, completed_fm.period) AS eps_cagr_3y,
			(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', completed_fm.period) LIMIT 1) AS sector_median_pe,
			ROUND((
				COALESCE(
					LEAST(
						GREATEST((CASE WHEN ABS(completed_fm.roe) <= 2 THEN completed_fm.roe * 100 ELSE completed_fm.roe END), 0),
						40
					) / 40.0,
					0
				) * 34
				+
				COALESCE(
					LEAST(
						GREATEST((CASE WHEN ABS(completed_fm.ebit_margin) <= 2 THEN completed_fm.ebit_margin ELSE completed_fm.ebit_margin / 100 END), 0),
						0.30
					) / 0.30,
					0
				) * 33
				+
				COALESCE(
					(1 - LEAST(GREATEST(completed_fm.debt_ebitda, 0) / 5.0, 1)),
					0
				) * 33
			)::numeric, 2) AS quality_score,
			completed_fm.eps * POWER(1 + COALESCE(LEAST(GREATEST(calculate_cagr(c.id, 'eps', 3, completed_fm.period), -50), 50) / 100.0, 0), %s) AS eps_n,
			eps_fwd.period AS eps_forward_year,
			eps_fwd.eps AS eps_forward,
			LEAST(
				GREATEST(
					0.5 * COALESCE(completed_fm.pe_ratio, 0) +
					0.5 * (
						(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', completed_fm.period) LIMIT 1) *
						(1 + 0.4 * ((COALESCE(
							ROUND((
								COALESCE(
									LEAST(
										GREATEST((CASE WHEN ABS(completed_fm.roe) <= 2 THEN completed_fm.roe * 100 ELSE completed_fm.roe END), 0),
										40
									) / 40.0,
									0
								) * 34
								+
								COALESCE(
									LEAST(
										GREATEST((CASE WHEN ABS(completed_fm.ebit_margin) <= 2 THEN completed_fm.ebit_margin ELSE completed_fm.ebit_margin / 100 END), 0),
										0.30
									) / 0.30,
									0
								) * 33
								+
								COALESCE(
									(1 - LEAST(GREATEST(completed_fm.debt_ebitda, 0) / 5.0, 1)),
									0
								) * 33
							)::numeric, 2),
							50
						) / 100.0) - 0.5))
					),
					(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', completed_fm.period) LIMIT 1) * 0.7
				),
				(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', completed_fm.period) LIMIT 1) * 1.5
			) AS exit_pe_estimate,
			latest_price.latest_price AS current_price,
			/* Average trailing P/E over the last 10 *completed* years.
			   Example: in 2026, average 2016-2025 (skip 2026 since it's in-progress/estimate). */
			(
				SELECT AVG(fm10.pe_ratio)
				FROM financial_metrics fm10
				WHERE fm10.company_id = c.id
				  AND fm10.pe_ratio IS NOT NULL
				  AND fm10.period BETWEEN (EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER - 10)
				                      AND (EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER - 1)
			) AS pe_avg_10y
			,
			completed_fm.period AS eps_year,
			completed_fm.eps AS eps_latest,
			COUNT(*) OVER () AS balance_sheet_gate_count
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
		WHERE start_price.start_price IS NOT NULL AND start_price.start_price > 0
		  AND latest_price.latest_price IS NOT NULL AND latest_price.latest_price > 0
		  AND completed_fm.company_id IS NOT NULL
		  /* Strategy 4: loose balance-sheet safety gate (null-tolerant) */
		  AND (completed_fm.debt_ebitda IS NULL OR completed_fm.debt_ebitda <= 6.0)
		  AND (
			  completed_fm.ebit_margin IS NULL OR
			  (CASE WHEN ABS(completed_fm.ebit_margin) <= 2 THEN completed_fm.ebit_margin ELSE completed_fm.ebit_margin / 100 END) >= 0.06
		  )
		  AND (
			  completed_fm.roe IS NULL OR
			  (CASE WHEN ABS(completed_fm.roe) <= 2 THEN completed_fm.roe * 100 ELSE completed_fm.roe END) >= 8.0
		  )
	"""
	# Placeholders (in-order): target_year horizon, irr eps-growth horizon, irr exponent horizon, eps_n horizon
	params.extend([horizon_years, horizon_years, horizon_years, horizon_years])

	where_clause, params = _build_filters_where(filters, params, horizon_years)
	if where_clause:
		base += f"\nAND {where_clause}\n"

	# Heuristic pre-sort to cap candidates
	base += "ORDER BY quality_score DESC NULLS LAST, eps_cagr_3y DESC NULLS LAST, pe_ratio ASC NULLS LAST\nLIMIT %s;"
	params.append(candidate_cap)

	rows: List[CompanyIrr] = []
	details: Dict[str, Dict[str, Any]] = {}
	gate_count: int = 0
	with get_db_connection() as conn:
		with conn.cursor() as cur:
			cur.execute(base, tuple(params))
			for r in cur.fetchall():
				# last column is the window count of companies passing the balance-sheet gate (pre-LIMIT)
				if gate_count == 0 and r[-1] is not None:
					try:
						gate_count = int(r[-1])
					except Exception:
						gate_count = 0
				irr_value = float(r[5]) if r[5] is not None else 0.0
				company = CompanyIrr(
					ticker=r[0],
					name=r[1],
					sector=r[2],
					country=r[3],
					market_cap=float(r[4]) if r[4] is not None else None,
					irr=irr_value * 100.0
				)
				rows.append(company)
				details[company.ticker] = {
					"roe": float(r[6]) if r[6] is not None else None,
					"roic": float(r[7]) if r[7] is not None else None,
					"ebit_margin": float(r[8]) if r[8] is not None else None,
					"pe_ratio": float(r[9]) if r[9] is not None else None,
					"p_fcf_ratio": float(r[10]) if r[10] is not None else None,
					"ev_ebitda": float(r[11]) if r[11] is not None else None,
					"debt_ebitda": float(r[12]) if r[12] is not None else None,
					"return_5y": float(r[13]) if r[13] is not None else None,
					"return_10y": float(r[14]) if r[14] is not None else None,
					"eps_cagr_3y": float(r[15]) if r[15] is not None else None,
					"sector_median_pe": float(r[16]) if r[16] is not None else None,
					"quality_score": float(r[17]) if r[17] is not None else None,
					"eps_n": float(r[18]) if r[18] is not None else None,
					"eps_forward_year": int(r[19]) if r[19] is not None else None,
					"eps_forward": float(r[20]) if r[20] is not None else None,
					"exit_pe_estimate": float(r[21]) if r[21] is not None else None,
					"current_price": float(r[22]) if r[22] is not None else None,
					"pe_avg_10y": float(r[23]) if r[23] is not None else None,
					"eps_year": int(r[24]) if r[24] is not None else None,
					"eps_latest": float(r[25]) if r[25] is not None else None
				}
	# Always print how many companies pass the balance-sheet gate, regardless of DEBUG_GPT.
	print(f"[balance-sheet gate] pass_count={gate_count} returned={len(rows)}")
	return rows, details


def run_gemini_selection(candidates: List[CompanyIrr], details: Dict[str, Dict[str, Any]], horizon_years: int, k: int = 5) -> List[Tuple[str, str]]:
	"""
	Ask Gemini to choose k tickers from candidates using only provided data.
	Returns list of (ticker, reason) in ranked order. Empty list on failure.
	"""
	api_key = os.getenv("GPT_API_SECRET")
	if not api_key or not api_key.strip() or not candidates:
		return []
	try:
		client = _get_openai_client(api_key)
		model = _get_gpt_model_name()

		# Build structured candidate payload
		candidate_objs = []
		for c in candidates:
			d = details.get(c.ticker, {})
			candidate_objs.append({
				"ticker": c.ticker,
				"name": c.name,
				"sector": c.sector,
				"country": c.country,
				"market_cap": c.market_cap,
				"projected_irr_pct": c.irr,
				"roe": d.get("roe"),
				"roic": d.get("roic"),
				"ebit_margin": d.get("ebit_margin"),
				"pe_ratio": d.get("pe_ratio"),
				"p_fcf_ratio": d.get("p_fcf_ratio"),
				"ev_ebitda": d.get("ev_ebitda"),
				"debt_ebitda": d.get("debt_ebitda"),
				"return_5y": d.get("return_5y"),
				"return_10y": d.get("return_10y"),
				"eps_cagr_3y": d.get("eps_cagr_3y"),
				"sector_median_pe": d.get("sector_median_pe"),
				"quality_score": d.get("quality_score"),
				"eps_n": d.get("eps_n"),
				"exit_pe_estimate": d.get("exit_pe_estimate"),
				"current_price": d.get("current_price"),
			})

		system_instructions = (
			"Select exactly K tickers that are most likely to outperform over the next "
			f"{horizon_years} years based on the structured data provided. "
			"Use ONLY the candidate data; do not invent any values. "
			"Prioritize sustainable EPS growth (EPS CAGR), quality_score, reasonable valuation vs sector_median_pe, "
			"healthy margins, and low leverage (debt_ebitda). Penalize excessive current multiples unless justified "
			"by superior growth + quality. Avoid relying purely on past returns. Consider sector diversification if candidates are close. "
			"Return strict JSON with: {\"picks\": [{\"ticker\": \"...\", \"reason\": \"...\"}, ...]} and nothing else. "
			"Tickers must be a subset of the provided candidates. Order picks by expected future performance."
		)
		user_payload = {
			"k": k,
			"horizon_years": horizon_years,
			"candidates": candidate_objs
		}

		text = _chatgpt_text(
			client,
			model=model,
			system=system_instructions,
			user=json.dumps(user_payload),
			expect_json=True,
			timeout_s=90.0,
		)
		if not text:
			return []
		data = json.loads(_clean_json_text(text))
		picks = data.get("picks", [])
		out: List[Tuple[str, str]] = []
		allowed = {c.ticker for c in candidates}
		for p in picks:
			t = p.get("ticker")
			rsn = p.get("reason", "")
			if isinstance(t, str) and t in allowed:
				out.append((t, rsn))
		return out[:k]
	except Exception:
		return []


def run_gpt_predict_exit_pe(candidates: List[CompanyIrr], details: Dict[str, Dict[str, Any]], horizon_years: int) -> Dict[str, Dict[str, Any]]:
	"""
	Ask GPT to predict exit P/E for each candidate based on structured fundamentals and forward EPS.
	Returns dict: { ticker: { exit_pe: float, confidence: float, breakdown: list, notes: str } }
	"""
	api_key = os.getenv("GPT_API_SECRET")
	result: Dict[str, Dict[str, Any]] = {}
	debug = os.getenv("DEBUG_GPT", "").strip() == "1"

	if not api_key or not api_key.strip() or not candidates:
		return result
	# print("Candidates: ", candidates)
	try:
		client = _get_openai_client(api_key)
		model = _get_gpt_model_name()
		if debug:
			print("GPT model: ", model)
		candidate_objs = []
		for c in candidates:
			d = details.get(c.ticker, {})
			candidate_objs.append({
				"ticker": c.ticker,
				"sector": c.sector,
				"country": c.country,
				"market_cap": d.get("market_cap"),
				"pe_current": d.get("pe_ratio"),
				"sector_median_pe": d.get("sector_median_pe"),
				"quality_score": d.get("quality_score"),
				"roe": d.get("roe"),
				"roic": d.get("roic"),
				"ebit_margin": d.get("ebit_margin"),
				"ev_ebitda": d.get("ev_ebitda"),
				"debt_ebitda": d.get("debt_ebitda"),
				"eps_cagr_3y": d.get("eps_cagr_3y"),
				"eps_3y": d.get("eps_forward"),
				"eps_3y_year": d.get("eps_forward_year"),
				"eps_n_cagr_based": d.get("eps_n"),
				"return_5y": d.get("return_5y"),
				"return_10y": d.get("return_10y"),
			})

		system_instructions = (
			f"You are a buy-side equity analyst. Predict the {horizon_years}-year exit P/E multiple for each candidate strictly using the provided structured data. "
			"Your output must be client-auditable and finance-grade.\n\n"
			"Rules:\n"
			"- Use ONLY provided fields; do not invent numbers.\n"
			"- IMPORTANT: `quality_score` is a computed composite (0..100) from the last completed financial year (<= current_year-1), designed to avoid ROIC.\n"
			"  Quality score formula (higher is better):\n"
			"  - ROE component (~34 pts): normalize ROE to percent if stored as decimal (|roe|<=2 => roe*100), floor at 0, cap at 40%, then scale to 0..34.\n"
			"  - EBIT margin component (~33 pts): normalize margin to decimal if stored as percent (|margin|>2 => margin/100), floor at 0, cap at 30%, then scale to 0..33.\n"
			"  - Leverage component (~33 pts): use Debt/EBITDA; 0x => full points, >=5x => 0 points via (1 - clamp(debt_ebitda/5,0,1)), then scale to 0..33.\n"
			"  - Missing inputs: if a component metric is missing/NULL it contributes 0 for that component.\n"
			"  Use `quality_score` as a summary signal, but sanity-check it against the raw ROE/EBIT margin/Debt/EBITDA fields provided.\n"
			"- Be explicit about the anchor (sector vs current multiple), mean-reversion, and why you allow/deny premium/discount.\n"
			"- Discuss growth quality/sustainability using eps_cagr_3y and eps_3y vs eps_n_cagr_based (if they diverge, explain).\n"
			"- Use leverage and profitability to justify multiple compression/expansion risk.\n\n"
			"Return STRICT JSON only:\n"
			"{\"predictions\": [\n"
			"  {\"ticker\":\"...\",\n"
			"   \"exit_pe\": float,\n"
			"   \"confidence\": 0..1,\n"
			"   \"notes\": \"3-6 sentences written for a finance client; reference specific provided values\",\n"
			"   \"breakdown\": [\n"
			"     {\"label\":\"Current P/E Ratio\", \"value\": \"<number or NA>\"},\n"
			"     {\"label\":\"Sector Median P/E\", \"value\": \"<number or NA>\"},\n"
			"     {\"label\":\"Quality Score\", \"value\": \"<number or NA>\"},\n"
			"     {\"label\":\"EPS CAGR (3Y)\", \"value\": \"<number or NA>\"},\n"
			"     {\"label\":\"Projected EPS (3Y)\", \"value\": \"<number or NA>\"},\n"
			"     {\"label\":\"Final Exit P/E\", \"value\": \"<number or NA>\"}\n"
			"   ]}\n"
			"]}\n"
			"Notes must mention any pattern/mean-reversion and growth trend explicitly."
		)
		if debug:
			print("System instructions: ", system_instructions)
		payload = {
			"horizon_years": horizon_years,
			"candidates": candidate_objs
		}
		if debug:
			print("Payload candidates:", len(payload["candidates"]))
		text = _chatgpt_text(
			client,
			model=model,
			system=system_instructions,
			user=json.dumps(payload),
			expect_json=True,
			timeout_s=90.0,
		)
		if debug and text:
			print("GPT response: ", text)
		if not text:
			return result
		data = json.loads(_clean_json_text(text))
		for item in data.get("predictions", []):
			t = item.get("ticker")
			pe = item.get("exit_pe")
			conf = item.get("confidence", None)
			notes = item.get("notes", "")
			breakdown = item.get("breakdown", None)
			if isinstance(t, str) and isinstance(pe, (int, float)):
				# sanity clamp
				exit_pe_max = float(os.getenv("EXIT_PE_MAX", "40") or "40")
				if exit_pe_max <= 0:
					exit_pe_max = 40.0
				pe_clamped = max(3.0, min(float(pe), exit_pe_max))
				out: Dict[str, Any] = {"exit_pe": pe_clamped, "confidence": conf, "notes": notes}
				if isinstance(breakdown, list):
					out["breakdown"] = breakdown
				result[t] = out
		return result
	except Exception as ex:
		if debug:
			print("Exception1: ", ex)
		return result


@app.post("/api/top-irr", response_model=TopIrrResponse)
def api_top_irr_post(payload: TopIrrRequest = Body(...)):
	limit = min(max(payload.limit, 1), 20)
	horizon_years = min(max(payload.horizon_years, 1), 15)
	# Step 1: get candidate pool using math filters
	candidates, details = fetch_candidates_filtered(candidate_cap=max(75, limit * 5), horizon_years=horizon_years, filters=payload.filters)
	print("Candidates: ", len(candidates))
	if not candidates:
		return TopIrrResponse(companies=[], analysis="No candidates match the filters.")

	# Step 2: ask Gemini to predict exit P/E for each candidate
	print("Predicting exit P/E for candidates")
	pe_preds = run_gpt_predict_exit_pe(candidates, details, horizon_years=horizon_years)
	print("Pe preds: ", pe_preds)
	# If the model produced at least one prediction, only keep companies with model-assigned exit P/E.
	# Use the DB fallback only when the model produced zero predictions (e.g., quota exhausted).
	model_only = len(pe_preds) > 0
	# Step 3: compute projected IRR from predicted exit P/E and forward EPS
	scored: List[Tuple[CompanyIrr, float, float, float]] = []  # (company, irr_decimal, exit_price, db_price)
	current_year = time.gmtime().tm_year
	for c in candidates:
		d = details.get(c.ticker, {})
		db_price = d.get("current_price")
		# Prefer EPS at the target year (current_year + horizon_years - 1), with fallback to latest EPS.
		eps_year = d.get("eps_forward_year") or d.get("eps_year")
		eps_val = d.get("eps_forward") or d.get("eps_latest")
		if not db_price or not eps_val:
			continue
		# Keep the annualization consistent with the requested horizon (EPS(3Y) -> 3 years).
		years_to_exit = max(1, int(horizon_years))
		exit_pe = None
		exit_pe_conf = None
		exit_pe_notes = None
		exit_pe_breakdown = None
		if c.ticker in pe_preds:
			exit_pe = pe_preds[c.ticker].get("exit_pe")
			exit_pe_conf = pe_preds[c.ticker].get("confidence")
			exit_pe_notes = pe_preds[c.ticker].get("notes")
			exit_pe_breakdown = pe_preds[c.ticker].get("breakdown")
		elif model_only:
			# Model returned some predictions, but not for this ticker -> exclude it (no per-ticker DB fallback).
			continue
		if exit_pe is None:
			exit_pe = d.get("exit_pe_estimate")  # fallback to math estimate
			exit_pe_conf = None
			exit_pe_notes = (
				"Model unavailable; using rules-based estimate: blend of current P/E and quality-adjusted sector median, "
				"clamped to a sector-relative band."
			)
			exit_pe_breakdown = [
				{"label": "Current P/E Ratio", "value": d.get("pe_ratio")},
				{"label": "Sector Median P/E", "value": d.get("sector_median_pe")},
				{"label": "Quality Score", "value": d.get("quality_score")},
				{"label": "EPS CAGR (3Y)", "value": d.get("eps_cagr_3y")},
				{"label": "Projected EPS (3Y)", "value": eps_val},
				{"label": "Final Exit P/E", "value": exit_pe},
			]
		# Enforce business rule cap for exit P/E (default 40)
		try:
			exit_pe_max = float(os.getenv("EXIT_PE_MAX", "40") or "40")
			if exit_pe_max <= 0:
				exit_pe_max = 40.0
			exit_pe = max(3.0, min(float(exit_pe), exit_pe_max))
		except Exception:
			exit_pe = None

		if not exit_pe or exit_pe <= 0:
			continue
		try:
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
				pe_avg_10y=float(d.get("pe_avg_10y")) if d.get("pe_avg_10y") is not None else None
			)
			scored.append((ci, irr_db, float(exit_price), float(db_price)))
		except Exception:
			continue

	# Step 4: (optional) replace current_price with Stooq close (fallback to DB), then rank by IRR
	use_stooq = os.getenv("USE_STOOQ_PRICE", "1").strip() == "1"
	log_prices = os.getenv("LOG_CURRENT_PRICE", "1").strip() == "1"
	# Start from DB-based ranking, then re-rank using Stooq for a limited subset to avoid too many HTTP calls.
	scored.sort(key=lambda x: x[1], reverse=True)
	subset_n = min(len(scored), int(os.getenv("STOOQ_RERANK_TOP_N", str(max(50, limit * 20)))))
	price_used: Dict[str, Tuple[Optional[float], str]] = {}

	if use_stooq and subset_n > 0:
		updated: List[Tuple[CompanyIrr, float, float, float]] = []
		for (ci, irr_db, exit_price, db_price) in scored[:subset_n]:
			ext = get_current_price_stooq(ci.ticker)
			if ext is not None and ext > 0:
				try:
					# Recompute annualization using the same horizon (do not derive from eps_year).
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

		rest = scored[subset_n:]
		scored = updated + rest

	# Final rank by (possibly updated) IRR
	scored.sort(key=lambda x: x[0].irr if x[0].irr is not None else float("-inf"), reverse=True)
	top_companies = [ci for (ci, _, _, _) in scored[:limit]]

	if log_prices:
		for ci in top_companies:
			p, src = price_used.get(ci.ticker, (None, "db"))
			sym = _bloomberg_ticker_to_stooq_symbol(ci.ticker) if src == "stooq" else None
			if src == "stooq":
				print(f"[price] {ci.ticker} stooq_symbol={sym} current_price={p}")
			else:
				print(f"[price] {ci.ticker} current_price(db)={p}")

	# Step 5: produce analysis summary (AI narrative only; math already done)
	analysis = run_gemini_analysis(top_companies, horizon_years=horizon_years, details={c.ticker: details.get(c.ticker, {}) for c in top_companies})
	return TopIrrResponse(companies=top_companies, analysis=analysis)


# For local run: uvicorn backend.main:app --reload


# =============================================================================
# Research Assistant Endpoints
# =============================================================================

@app.get("/api/companies", response_model=List[CompanyDropdown])
def get_companies():
	"""
	Get list of companies for the dropdown selector.
	Returns id, ticker, and name for each company.
	"""
	query = """
		SELECT id::text, ticker, name 
		FROM companies 
		ORDER BY name ASC NULLS LAST, ticker ASC
	"""
	companies = []
	with get_db_connection() as conn:
		with conn.cursor() as cur:
			cur.execute(query)
			for row in cur.fetchall():
				companies.append(CompanyDropdown(
					id=row[0],
					ticker=row[1],
					name=row[2]
				))
	return companies


async def research_event_generator(company_id: str, analyst_name: Optional[str] = None):
	"""
	Generator for SSE events during research.
	"""
	# Lookup company name
	query = "SELECT name, ticker FROM companies WHERE id = %s"
	company_name = None
	company_ticker = None
	with get_db_connection() as conn:
		with conn.cursor() as cur:
			cur.execute(query, (company_id,))
			row = cur.fetchone()
			if row:
				company_name = row[0] or row[1]  # Use name, fallback to ticker
				company_ticker = row[1]
	
	if not company_name:
		yield f"data: {json.dumps({'type': 'error', 'data': 'Company not found'})}\n\n"
		return
	
	# Build prompt and run research
	prompt = build_research_prompt(company_name, analyst_name)
	
	async for event in run_deep_research_streaming(prompt):
		yield f"data: {json.dumps(event)}\n\n"
		await asyncio.sleep(0.01)  # Small yield to prevent blocking


@app.post("/api/research/start")
async def start_research(request: ResearchRequest):
	"""
	Start a deep research task for a company.
	Returns Server-Sent Events (SSE) stream with research progress.
	"""
	return StreamingResponse(
		research_event_generator(request.company_id, request.analyst_name),
		media_type="text/event-stream",
		headers={
			"Cache-Control": "no-cache",
			"Connection": "keep-alive",
			"X-Accel-Buffering": "no"
		}
	)


@app.post("/api/research/pdf")
def generate_research_pdf(request: PdfRequest):
	"""
	Generate a PDF from markdown content.
	Returns the PDF file as a downloadable attachment.
	"""
	try:
		pdf_bytes = generate_pdf_from_markdown(request.content, request.title)
		
		# Create safe filename from title
		safe_title = "".join(c for c in request.title if c.isalnum() or c in (' ', '-', '_')).strip()
		safe_title = safe_title.replace(' ', '_')[:50] or "research_report"
		filename = f"{safe_title}.pdf"
		
		return Response(
			content=pdf_bytes,
			media_type="application/pdf",
			headers={
				"Content-Disposition": f"attachment; filename={filename}"
			}
		)
	except ImportError as e:
		return Response(
			content=json.dumps({"error": str(e)}),
			status_code=500,
			media_type="application/json"
		)
	except Exception as e:
		return Response(
			content=json.dumps({"error": f"PDF generation failed: {str(e)}"}),
			status_code=500,
			media_type="application/json"
		)