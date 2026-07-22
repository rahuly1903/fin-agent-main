import os
import asyncio
import hashlib
import threading
from typing import Any, Dict, List, Optional, Tuple
import json
import time
from fastapi import FastAPI, Query, Body, Response, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# Import research module
try:
	from .research import build_research_prompt, run_deep_research_streaming, generate_pdf_from_markdown
except Exception:
	try:
		# When running `uvicorn main:app --app-dir backend`, `backend` isn't a package.
		from research import build_research_prompt, run_deep_research_streaming, generate_pdf_from_markdown  # type: ignore
	except Exception:
		import sys
		import os as _os
		sys.path.append(_os.path.dirname(_os.path.dirname(__file__)))
		from backend.research import build_research_prompt, run_deep_research_streaming, generate_pdf_from_markdown  # type: ignore

# Import logging module
try:
	from .logging_config import setup_logging, get_logger, generate_request_id, set_request_id, get_request_id
except Exception:
	try:
		from logging_config import setup_logging, get_logger, generate_request_id, set_request_id, get_request_id  # type: ignore
	except Exception:
		import sys
		import os as _os
		sys.path.append(_os.path.dirname(_os.path.dirname(__file__)))
		from backend.logging_config import setup_logging, get_logger, generate_request_id, set_request_id, get_request_id  # type: ignore

# Support running as a package (uvicorn backend.main:app) or module (uvicorn main:app)
try:
	from .database import get_db_connection  # type: ignore
except Exception:
	import sys
	import os as _os
	sys.path.append(_os.path.dirname(_os.path.dirname(__file__)))
	from database import get_db_connection  # type: ignore

load_dotenv()

# Initialize structured logging
log = setup_logging(
	level=os.getenv("LOG_LEVEL", "INFO"),
	json_format=os.getenv("LOG_JSON", "1").strip() == "1"
)

# -----------------------------------------------------------------------------
# External price (yfinance) helpers
# -----------------------------------------------------------------------------
_YFINANCE_CACHE_TTL_S = int(os.getenv("YFINANCE_CACHE_TTL_S", os.getenv("STOOQ_CACHE_TTL_S", "21600")))  # 6 hours
_yfinance_cache: Dict[str, Tuple[float, float]] = {}  # symbol -> (fetched_at_epoch_s, price)


def _bloomberg_ticker_to_yahoo_symbol(ticker: str) -> Optional[str]:
	"""
	Convert Bloomberg-style ticker like 'AAPL UW Equity' -> 'AAPL' (Yahoo Finance).
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
	# Yahoo uses '-' for class shares (e.g. BRK/B -> BRK-B)
	return root.replace("/", "-").upper()


def _fetch_yfinance_close(symbol: str) -> Optional[float]:
	"""
	Fetch most recent price from Yahoo Finance via yfinance.
	Symbol example: 'AAPL'
	"""
	try:
		import yfinance as yf  # type: ignore
	except Exception:
		return None
	try:
		t = yf.Ticker(symbol)
		# Prefer fast_info last price when available
		try:
			fi = getattr(t, "fast_info", None)
			if fi is not None:
				price = getattr(fi, "last_price", None)
				if price is None and hasattr(fi, "get"):
					price = fi.get("last_price")
				if price is not None:
					price = float(price)
					if price > 0:
						return price
		except Exception:
			pass
		# Fallback: recent daily history close
		hist = t.history(period="5d")
		if hist is None or hist.empty:
			return None
		closes = hist["Close"].dropna()
		if closes.empty:
			return None
		price = float(closes.iloc[-1])
		if price <= 0:
			return None
		return price
	except Exception:
		return None


def get_current_price_yfinance(ticker: str) -> Optional[float]:
	"""
	Get a recent price from Yahoo Finance (yfinance), with in-memory caching.
	Returns None if unavailable.
	"""
	symbol = _bloomberg_ticker_to_yahoo_symbol(ticker)
	if not symbol:
		return None
	now = time.time()
	if symbol in _yfinance_cache:
		ts, price = _yfinance_cache[symbol]
		if now - ts <= _YFINANCE_CACHE_TTL_S:
			return price
	price = _fetch_yfinance_close(symbol)
	if price is None:
		return None
	_yfinance_cache[symbol] = (now, price)
	return price

# Configure CORS for local dev (Next.js on 3000) and production
origins = [
	"http://localhost:3000",
	"http://127.0.0.1:3000",
	"http://0.0.0.0:3000",
	"http://13.127.240.35",
]

app = FastAPI(title="Fin Agent API", version="0.1.0")
app.add_middleware(
	CORSMiddleware,
	allow_origins=origins,
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
	"""
	Middleware that:
	1. Generates a unique request ID for each request
	2. Logs the incoming request
	3. Logs the response with timing information
	"""
	request_id = generate_request_id()
	set_request_id(request_id)
	
	start_time = time.time()
	
	# Log incoming request
	log.log_request(
		endpoint=str(request.url.path),
		method=request.method,
		params=dict(request.query_params) if request.query_params else None
	)
	
	try:
		response = await call_next(request)
		
		# Log response
		duration_ms = (time.time() - start_time) * 1000
		log.log_response(
			endpoint=str(request.url.path),
			status_code=response.status_code,
			duration_ms=duration_ms
		)
		
		# Add request ID to response headers for traceability
		response.headers["X-Request-ID"] = request_id
		
		return response
		
	except Exception as e:
		duration_ms = (time.time() - start_time) * 1000
		log.error(f"Request failed: {str(e)}", {
			"endpoint": str(request.url.path),
			"duration_ms": round(duration_ms, 2),
		}, exc_info=True)
		raise


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


def _get_gemini_model_name() -> str:
	"""
	Model name for Gemini API v1 (google-genai SDK). Override with GEMINI_MODEL.
	Examples: models/gemini-2.0-flash, models/gemini-2.5-flash, models/gemini-2.5-pro
	"""
	# Use a v1-available default. Users can override with GEMINI_MODEL.
	# Note: ListModels typically returns names prefixed with "models/".
	return os.getenv("GEMINI_MODEL", "models/gemini-2.5-flash")


def _get_genai_client(api_key: str):
	"""
	Create a google-genai client that uses Gemini API v1 (not v1beta).
	Keeping imports inside the function preserves optional dependency behavior.
	"""
	try:
		from google import genai  # type: ignore
	except Exception as e:
		raise ImportError(
			"Failed to import `google.genai`. Install the v1 SDK with "
			"`pip install -U google-genai` and ensure the deprecated "
			"`google-generativeai` package is not being used."
		) from e
	return genai.Client(api_key=api_key, http_options={"api_version": "v1"})


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


# -----------------------------------------------------------------------------
# GPT (OpenAI) helpers for batch exit-P/E prediction
# -----------------------------------------------------------------------------

def _get_gpt_model_name() -> str:
	"""Model name for ChatGPT. Override with GPT_MODEL env var."""
	return os.getenv("GPT_MODEL", "o3")


def _get_openai_client(api_key: str):
	"""Create an OpenAI client for ChatGPT."""
	try:
		from openai import OpenAI  # type: ignore
	except Exception as e:
		raise ImportError("Failed to import `openai`. Install with `pip install -U openai`.") from e
	return OpenAI(api_key=api_key)


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


# -----------------------------------------------------------------------------
# Single-flight / result caching for GPT-based top-IRR computation
# -----------------------------------------------------------------------------
_TOP_IRR_LOCK = threading.Lock()
_TOP_IRR_INFLIGHT: Dict[str, Dict[str, Any]] = {}  # key -> {event, started_at, completed_at?, result?, error?}


def _top_irr_cache_ttl_s() -> float:
	return float(_get_env_int("TOP_IRR_CACHE_TTL_S", 900))  # 15 min default


def _top_irr_key(payload: "TopIrrRequest") -> str:
	"""Stable key for deduping identical requests."""
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
	for k in list(_TOP_IRR_INFLIGHT.keys()):
		entry = _TOP_IRR_INFLIGHT.get(k)
		if not entry:
			continue
		evt = entry.get("event")
		completed_at = entry.get("completed_at")
		if evt is not None and getattr(evt, "is_set", lambda: False)() and isinstance(completed_at, (int, float)):
			if now_s - float(completed_at) > ttl:
				_TOP_IRR_INFLIGHT.pop(k, None)


# -----------------------------------------------------------------------------
# GPT batch exit-P/E helper
# -----------------------------------------------------------------------------

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
	Predict exit P/E for ALL candidates by splitting into batches of 50.
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
		"   \"notes\": \"A rich narrative (3-4 sentences). NO FORMULAS.\",\n"
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

		gpt_payload = {"horizon_years": horizon_years, "candidates": candidate_objs}

		text: Optional[str] = None
		for attempt in range(0, max_retries + 1):
			text = _chatgpt_text(
				client,
				model=model,
				system=system_instructions,
				user=json.dumps(gpt_payload),
				expect_json=True,
				timeout_s=float(os.getenv("GPT_TIMEOUT_S", "240")),
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


def _generate_content_text(
	client,
	*,
	model: str,
	contents: str,
	config: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
	"""
	Wrapper around google-genai `client.models.generate_content` that returns `.text`.
	Some Gemini API versions reject JSON-mode fields; callers can pass config=None
	or rely on fallback behavior higher up.
	"""
	resp = client.models.generate_content(model=model, contents=contents, config=config)
	return getattr(resp, "text", None)


def _generate_content_text_with_json_mode_fallback(client, *, model: str, contents: str) -> Optional[str]:
	"""
	Try JSON-mode first, but fall back to plain text if the API rejects responseMimeType.
	This avoids hard failures on API versions/models that don't support JSON mode.
	"""
	try:
		return _generate_content_text(
			client,
			model=model,
			contents=contents,
			config={"response_mime_type": "application/json"},
		)
	except Exception as ex:
		msg = str(ex)
		# Common server-side error: Unknown name "responseMimeType" at 'generation_config'
		if ("responseMimeType" in msg) or ("response_mime_type" in msg):
			return _generate_content_text(client, model=model, contents=contents, config=None)
		raise


def run_gemini_analysis(rows: List[CompanyIrr], horizon_years: int, details: Optional[Dict[str, Dict[str, Any]]] = None) -> Optional[str]:
	api_key = os.getenv("GENAI_API_KEY") or os.getenv("GOOGLE_API_KEY")
	if not api_key or not rows:
		return None
	try:
		client = _get_genai_client(api_key)
		model = _get_gemini_model_name()
		prompt = build_gemini_prompt(rows, horizon_years, details=details)
		return _generate_content_text(client, model=model, contents=prompt, config=None)
	except Exception as e:
		# In production, log exception details
		return None


@app.get("/api/health")
def health():
	return {"status": "ok"}


@app.post("/api/upload-data")
async def upload_data(file: UploadFile = File(...)):
	"""
	Upload Exit Multiple Analysis Excel file to replace database data.
	
	Validates file structure and replaces all existing data with uploaded data.
	"""
	# Import here to avoid circular imports
	try:
		from .upload import (
			validate_file_extension, 
			validate_excel_structure, 
			save_upload_to_temp,
			ValidationResult
		)
		from .ingest import ingest_excel_file
	except ImportError:
		from upload import (
			validate_file_extension, 
			validate_excel_structure, 
			save_upload_to_temp,
			ValidationResult
		)
		from ingest import ingest_excel_file
	
	# Check file extension
	if not file.filename or not validate_file_extension(file.filename):
		raise HTTPException(
			status_code=400, 
			detail="Invalid file type. Please upload an Excel file (.xlsx or .xls)"
		)
	
	# Save to temp file
	try:
		temp_path = await save_upload_to_temp(file)
	except Exception as e:
		raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")
	
	# Validate Excel structure
	validation = validate_excel_structure(temp_path)
	
	if not validation.valid:
		# Clean up temp file
		try:
			os.remove(temp_path)
		except Exception:
			pass
		return {
			"success": False,
			"message": "File validation failed",
			"errors": validation.errors,
			"warnings": validation.warnings,
			"sheets_found": validation.sheets_found,
			"company_count": validation.company_count
		}
	
	# Run ingestion (replace existing data)
	stats = ingest_excel_file(temp_path, replace_existing=True)
	
	# Clean up temp file
	try:
		os.remove(temp_path)
	except Exception:
		pass
	
	# Check for errors
	if stats.errors:
		log.log_data_upload(
			filename=file.filename or "unknown",
			companies_count=stats.companies_processed,
			status="completed_with_errors"
		)
		log.error("Data upload completed with errors", {
			"filename": file.filename,
			"errors": stats.errors,
			"companies_processed": stats.companies_processed,
		})
		return {
			"success": False,
			"message": "Ingestion completed with errors",
			"errors": stats.errors,
			"warnings": validation.warnings,
			"companies_inserted": stats.companies_processed,
			"metrics_inserted": stats.metrics_inserted,
			"prices_inserted": stats.prices_inserted
		}
	
	log.log_data_upload(
		filename=file.filename or "unknown",
		companies_count=stats.companies_processed,
		status="success"
	)
	log.info("Data upload successful", {
		"filename": file.filename,
		"companies_processed": stats.companies_processed,
		"metrics_inserted": stats.metrics_inserted,
		"prices_inserted": stats.prices_inserted,
	})
	
	return {
		"success": True,
		"message": f"Successfully replaced data with {stats.companies_processed} companies",
		"warnings": validation.warnings,
		"companies_inserted": stats.companies_processed,
		"metrics_inserted": stats.metrics_inserted,
		"prices_inserted": stats.prices_inserted,
		"year_range": list(validation.year_range) if validation.year_range else None
	}


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


def _clamp_exit_pe(pe: float) -> float:
	"""
	Business rule: Exit P/E must not exceed EXIT_PE_MAX (default 40).
	Also applies a minimum of 3 to avoid nonsensical multiples.
	"""
	exit_pe_max = _get_env_float("EXIT_PE_MAX", 40.0)
	if exit_pe_max <= 0:
		exit_pe_max = 40.0
	return max(3.0, min(float(pe), float(exit_pe_max)))


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
		/* Latest completed financial year per company */
		latest_fm AS (
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
			latest_fm.roe,
			latest_fm.roic,
			latest_fm.ebit_margin,
			latest_fm.pe_ratio,
			latest_fm.p_fcf_ratio,
			latest_fm.ev_ebitda,
			latest_fm.debt_ebitda,
			calculate_stock_return(c.id, INTERVAL '5 years') AS return_5y,
			calculate_stock_return(c.id, INTERVAL '10 years') AS return_10y,
			cagr3.eps_cagr_3y,
			cagr3.sales_cagr_3y,
			cagr3.fcf_cagr_3y,
			(SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', latest_fm.period) LIMIT 1) AS sector_median_pe,
			eps_fwd.period AS eps_forward_year,
			eps_fwd.eps AS eps_forward,
			latest_price.latest_price AS current_price,
			LEAST(
				GREATEST(
					0.5 * COALESCE(latest_fm.pe_ratio, 0) +
					0.5 * COALESCE((SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', latest_fm.period) LIMIT 1), 0),
					COALESCE((SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', latest_fm.period) LIMIT 1), 0) * 0.7
				),
				COALESCE((SELECT metric_median FROM get_sector_stats(c.sector, 'pe_ratio', latest_fm.period) LIMIT 1), 0) * 1.5
			) AS exit_pe_estimate,
			pe10.pe_avg_10y,
			pe10.pe_avg_10y_years_total,
			pe10.pe_avg_10y_years_used,
			latest_fm.period AS eps_year,
			latest_fm.eps AS eps_latest
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
		LEFT JOIN latest_fm ON latest_fm.company_id = c.id
		LEFT JOIN LATERAL (
			SELECT
				CASE
					WHEN fm_start.eps IS NOT NULL AND fm_start.eps > 0
					 AND latest_fm.eps IS NOT NULL AND latest_fm.eps > 0
					THEN ROUND(((POWER(latest_fm.eps / fm_start.eps, 1.0 / 3) - 1) * 100)::numeric, 2)
					ELSE NULL
				END AS eps_cagr_3y,
				CASE
					WHEN fm_start.sales IS NOT NULL AND fm_start.sales > 0
					 AND latest_fm.sales IS NOT NULL AND latest_fm.sales > 0
					THEN ROUND(((POWER(latest_fm.sales / fm_start.sales, 1.0 / 3) - 1) * 100)::numeric, 2)
					ELSE NULL
				END AS sales_cagr_3y,
				CASE
					WHEN fm_start.fcf IS NOT NULL AND fm_start.fcf > 0
					 AND latest_fm.fcf IS NOT NULL AND latest_fm.fcf > 0
					THEN ROUND(((POWER(latest_fm.fcf / fm_start.fcf, 1.0 / 3) - 1) * 100)::numeric, 2)
					ELSE NULL
				END AS fcf_cagr_3y
			FROM (
				SELECT eps, sales, fcf
				FROM financial_metrics
				WHERE company_id = c.id
				  AND period = (latest_fm.period - 3)
				LIMIT 1
			) fm_start
		) cagr3 ON TRUE
		LEFT JOIN LATERAL (
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
		  AND latest_fm.company_id IS NOT NULL
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
			NULL::NUMERIC AS return_5y,
			NULL::NUMERIC AS return_10y,
			NULL::NUMERIC AS eps_cagr_3y,
			NULL::NUMERIC AS fcf_cagr_3y
		FROM companies c
		JOIN latest_price ON latest_price.company_id = c.id
		JOIN start_price ON start_price.company_id = c.id
		LEFT JOIN latest_fm ON latest_fm.company_id = c.id
		WHERE start_price.start_price IS NOT NULL AND start_price.start_price > 0
		  AND latest_price.latest_price IS NOT NULL AND latest_price.latest_price > 0
		  AND latest_fm.eps IS NOT NULL AND latest_fm.eps > 0
		  AND (latest_fm.pe_ratio IS NULL OR latest_fm.pe_ratio <= 100)
	"""
	# %s: used for IRR exponent
	params.append(horizon_years)

	where_clause, params = _build_filters_where(filters, params, horizon_years)
	if where_clause:
		base += f"\nAND {where_clause}\n"

	# Query is ready - no complex string replacement needed

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
					# Simplified query doesn't include these - set to None
					"sector_median_pe": None,
					"quality_score": None,
					"eps_n": None,
					"exit_pe_estimate": None,
					"current_price": None,
					"pe_avg_10y": None,
					"eps_year": None,
					"eps_latest": None
				}
	return rows, details


def fetch_candidates_filtered(candidate_cap: int, horizon_years: int, filters: FiltersPayload) -> Tuple[List[CompanyIrr], Dict[str, Dict[str, Any]]]:
	"""
	Fast version: returns a capped candidate set using simple CTEs.
	Avoids slow correlated subqueries for calculate_stock_return, calculate_cagr, get_sector_stats.
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
		latest_fm AS (
			SELECT DISTINCT ON (fm.company_id)
				fm.*
			FROM financial_metrics fm
			ORDER BY fm.company_id, fm.period DESC
		),
		-- Pre-compute sector median PE (fast aggregate)
		sector_pe AS (
			SELECT 
				c.sector,
				PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY fm.pe_ratio) AS pe_median
			FROM companies c
			JOIN financial_metrics fm ON fm.company_id = c.id
			WHERE fm.pe_ratio IS NOT NULL AND fm.pe_ratio > 0
			  AND fm.period = (SELECT MAX(fm2.period) FROM financial_metrics fm2 WHERE fm2.company_id = c.id)
			GROUP BY c.sector
		)
		SELECT
			c.ticker,
			c.name,
			c.sector,
			c.country,
			c.market_cap,
			-- Simple historical IRR (price-based CAGR)
			(POWER(latest_price.latest_price / NULLIF(start_price.start_price, 0), 1.0 / %s) - 1) AS irr,
			latest_fm.roe,
			latest_fm.roic,
			latest_fm.ebit_margin,
			latest_fm.pe_ratio,
			latest_fm.p_fcf_ratio,
			latest_fm.ev_ebitda,
			latest_fm.debt_ebitda,
			NULL::NUMERIC AS return_5y,
			NULL::NUMERIC AS return_10y,
			NULL::NUMERIC AS eps_cagr_3y,
			spe.pe_median AS sector_median_pe,
			50.0 AS quality_score,  -- Default quality score
			latest_fm.eps AS eps_n,
			latest_fm.period AS eps_forward_year,
			latest_fm.eps AS eps_forward,
			COALESCE(spe.pe_median, latest_fm.pe_ratio, 15.0) AS exit_pe_estimate,
			latest_price.latest_price AS current_price,
			latest_fm.period AS eps_year,
			latest_fm.eps AS eps_latest
		FROM companies c
		JOIN latest_price ON latest_price.company_id = c.id
		JOIN start_price ON start_price.company_id = c.id
		LEFT JOIN latest_fm ON latest_fm.company_id = c.id
		LEFT JOIN sector_pe spe ON spe.sector = c.sector
		WHERE start_price.start_price IS NOT NULL AND start_price.start_price > 0
		  AND latest_price.latest_price IS NOT NULL AND latest_price.latest_price > 0
		  AND latest_fm.eps IS NOT NULL AND latest_fm.eps > 0
		  AND (latest_fm.pe_ratio IS NULL OR latest_fm.pe_ratio <= 100)
	"""
	params.append(horizon_years)

	where_clause, params = _build_filters_where(filters, params, horizon_years)
	if where_clause:
		base += f"\nAND {where_clause}\n"

	# Simple ordering by pe_ratio (lower is better)
	base += "ORDER BY pe_ratio ASC NULLS LAST\nLIMIT %s;"
	params.append(candidate_cap)

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
					"eps_forward_year": int(r[19]) if r[19] is not None else None,
					"eps_forward": float(r[20]) if r[20] is not None else None,
					"exit_pe_estimate": float(r[21]) if r[21] is not None else None,
					"current_price": float(r[22]) if r[22] is not None else None,
					"eps_year": int(r[23]) if r[23] is not None else None,
					"eps_latest": float(r[24]) if r[24] is not None else None
				}
	return rows, details


def run_gemini_selection(candidates: List[CompanyIrr], details: Dict[str, Dict[str, Any]], horizon_years: int, k: int = 5) -> List[Tuple[str, str]]:
	"""
	Ask Gemini to choose k tickers from candidates using only provided data.
	Returns list of (ticker, reason) in ranked order. Empty list on failure.
	"""
	api_key = os.getenv("GENAI_API_KEY") or os.getenv("GOOGLE_API_KEY")
	if not api_key or not candidates:
		return []
	try:
		client = _get_genai_client(api_key)
		model = _get_gemini_model_name()

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
				"quality_score": d.get("quality_score"),
				"eps_n": d.get("eps_n"),
				"exit_pe_estimate": d.get("exit_pe_estimate"),
				"current_price": d.get("current_price"),
			})

		system_instructions = (
			"Select exactly K tickers that are most likely to outperform over the next "
			f"{horizon_years} years based on the structured data provided. "
			"Use ONLY the candidate data; do not invent any values. "
			"Prioritize sustainable EPS growth (EPS CAGR), quality_score, reasonable valuation, "
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

		combined = system_instructions + "\n\n" + json.dumps(user_payload)
		# JSON-mode (responseMimeType) is not available for all v1 model endpoints.
		# We rely on instruction-following + best-effort JSON extraction instead.
		text = _generate_content_text(client, model=model, contents=combined, config=None)
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


def run_gemini_predict_exit_pe(candidates: List[CompanyIrr], details: Dict[str, Dict[str, Any]], horizon_years: int) -> Dict[str, Dict[str, Any]]:
	"""
	Ask Gemini to predict exit P/E for each candidate based on structured fundamentals and forward EPS.
	Returns dict: { ticker: { exit_pe: float, confidence: float, reason: str } }
	"""
	api_key = os.getenv("GENAI_API_KEY") or os.getenv("GOOGLE_API_KEY")
	result: Dict[str, Dict[str, Any]] = {}
	debug = os.getenv("DEBUG_GEMINI", "").strip() == "1"

	if not api_key or not candidates:
		return result
	# print("Candidates: ", candidates)
	try:
		client = _get_genai_client(api_key)
		model = _get_gemini_model_name()
		if debug:
			print("Gemini model: ", model)
		candidate_objs = []
		for c in candidates:
			d = details.get(c.ticker, {})
			candidate_objs.append({
				"ticker": c.ticker,
				"sector": c.sector,
				"country": c.country,
				"market_cap": d.get("market_cap"),
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
			f"Predict the {horizon_years}-year exit P/E multiple for each candidate strictly using the provided structured data. "
			"You MUST also explain the rationale for each predicted multiple in a way a client can audit.\n\n"
			"Rules:\n"
			"- Use ONLY the provided candidate fields; do not invent values.\n"
			"- Consider: quality (quality_score + ROE/ROIC/margins), "
			"leverage (debt_ebitda), and growth trend (eps_cagr_3y, eps_3y vs eps_n_cagr_based).\n"
			"- Apply mild mean-reversion to extremes.\n\n"
			"Return STRICT JSON and nothing else:\n"
			"{\"predictions\": [\n"
			"  {\"ticker\":\"...\",\n"
			"   \"exit_pe\": float,\n"
			"   \"confidence\": 0..1,\n"
			"   \"reason\": \"2-4 sentence explanation referencing specific provided values\",\n"
			"   \"drivers\": [\n"
			"     {\"factor\":\"Quality\",\"input\":\"quality_score/roe/roic/ebit_margin\",\"value\":\"<numbers or NA>\",\"impact\":\"down|neutral|up\",\"note\":\"short\"},\n"
			"     {\"factor\":\"Leverage\",\"input\":\"debt_ebitda\",\"value\":\"<number or NA>\",\"impact\":\"down|neutral|up\",\"note\":\"short\"},\n"
			"     {\"factor\":\"Growth trend\",\"input\":\"eps_cagr_3y & eps_3y\",\"value\":\"<numbers or NA>\",\"impact\":\"down|neutral|up\",\"note\":\"short\"}\n"
			"   ]\n"
			"  }\n"
			"]}\n"
			"Drivers is a table: each row must reference ONLY provided fields and describe how it pushed exit_pe."
		)
		if debug:
			print("System instructions: ", system_instructions)
		payload = {
			"horizon_years": horizon_years,
			"candidates": candidate_objs
		}
		if debug:
			print("Payload: ", payload)
		combined = system_instructions + "\n\n" + json.dumps(payload)
		# JSON-mode (responseMimeType) is not available for all v1 model endpoints.
		# We rely on instruction-following + best-effort JSON extraction instead.
		text = _generate_content_text(client, model=model, contents=combined, config=None)
		if debug and text:
			print("Gemini response: ", text)
		if not text:
			return result
		data = json.loads(_clean_json_text(text))
		for item in data.get("predictions", []):
			t = item.get("ticker")
			pe = item.get("exit_pe")
			conf = item.get("confidence", None)
			rsn = item.get("reason", "")
			drivers = item.get("drivers", None)
			if isinstance(t, str) and isinstance(pe, (int, float)):
				# Guardrails for Exit PE predictions
				pe_val = float(pe)
				pe_warning = None
				
				# Hard floor at 3x (sanity check)
				if pe_val < 3.0:
					pe_val = 3.0
					pe_warning = "PE floored from unrealistic low value"
				
				# Soft cap with warning for high values
				if pe_val > 50.0:
					pe_warning = f"PE prediction ({pe_val:.1f}x) exceeds 50x - flagged as potentially unreliable"
					# Hard cap at 80x to prevent extreme outliers
					if pe_val > 80.0:
						pe_val = 80.0
						pe_warning = f"PE prediction capped from {pe:.1f}x to 80x - original value flagged as unreliable"
				
				out: Dict[str, Any] = {"exit_pe": pe_val, "confidence": conf, "reason": rsn}
				if pe_warning:
					out["warning"] = pe_warning
				if isinstance(drivers, list):
					out["drivers"] = drivers
				result[t] = out
		return result
	except Exception as ex:
		if debug:
			print("Exception: ", ex)
		return result


@app.post("/api/top-irr", response_model=TopIrrResponse)
def api_top_irr_post(payload: TopIrrRequest = Body(...)):
	"""
	Evaluate ALL companies (no quality-score gating). Exit P/E is predicted via GPT in batches of 50,
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

	# Single-flight: dedupe identical expensive computations
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
		raise Exception(entry.get("error") or "Top-IRR computation failed")

	try:
		# Step 1: fetch all candidates (constrained by user filters)
		candidates, details = fetch_candidates_all(horizon_years=horizon_years, filters=payload.filters)
		if not candidates:
			out = TopIrrResponse(companies=[], analysis="No candidates match the filters.")
			entry["result"] = out
			return out

		# Step 2: predict exit P/E in batches of 50 via GPT
		log.log_llm_call(model="gpt", action="predicting exit P/E", prompt_tokens=len(candidates))
		batch_size = int(os.getenv("GPT_BATCH_SIZE", "50"))
		pe_preds = run_gpt_predict_exit_pe_batched(
			candidates,
			details,
			horizon_years=horizon_years,
			batch_size=batch_size,
			log_prefix=f"req={req_id}",
		)

		# Step 3: compute projected IRR from predicted exit P/E and forward EPS
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
				exit_pe = d.get("exit_pe_estimate")
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
					{"label": "P/E Avg (10Y, excl >100)", "value": pe_anchor, "meta": f"years_used={pe_used} years_total={pe_total}"},
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

		# Step 4: optional yfinance rerank for top-N only (avoid hundreds of HTTP calls)
		# USE_YFINANCE_PRICE preferred; USE_STOOQ_PRICE kept as legacy alias
		use_live = (
			os.getenv("USE_YFINANCE_PRICE", os.getenv("USE_STOOQ_PRICE", "1")).strip() == "1"
		)
		log_prices = os.getenv("LOG_CURRENT_PRICE", "1").strip() == "1"
		scored.sort(key=lambda x: x[1], reverse=True)
		subset_n = min(
			len(scored),
			int(os.getenv("YFINANCE_RERANK_TOP_N", os.getenv("STOOQ_RERANK_TOP_N", str(max(50, limit * 20))))),
		)
		price_used: Dict[str, Tuple[Optional[float], str]] = {}

		if use_live and subset_n > 0:
			updated: List[Tuple[CompanyIrr, float, float, float]] = []
			for (ci, irr_db, exit_price, db_price) in scored[:subset_n]:
				ext = get_current_price_yfinance(ci.ticker)
				if ext is not None and ext > 0:
					try:
						years_to_exit = max(1, int(horizon_years))
						irr_ext = (exit_price / ext) ** (1.0 / years_to_exit) - 1.0
						ci.current_price = float(ext)
						ci.irr = irr_ext * 100.0
						price_used[ci.ticker] = (ext, "yfinance")
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
				sym = _bloomberg_ticker_to_yahoo_symbol(ci.ticker) if src == "yfinance" else None
				log.log_financial_action(
					action="price_lookup",
					ticker=ci.ticker,
					details={"source": src, "yahoo_symbol": sym, "price": p}
				)

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