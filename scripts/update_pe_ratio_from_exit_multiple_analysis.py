"""
Update `financial_metrics.pe_ratio` from the Exit Multiple Analysis Excel file,
using the `FTM PE GAAP` tab (fallback to `LTM PE GAAP` if FTM is missing).

Safest mode: updates ONLY `pe_ratio` (does not touch other metrics / share prices / companies).

Usage:
  python -m scripts.update_pe_ratio_from_exit_multiple_analysis "/path/Exit Multiple Analysis 1.xlsx" --dry-run
  python -m scripts.update_pe_ratio_from_exit_multiple_analysis "/path/Exit Multiple Analysis 1.xlsx" --apply
"""

import argparse
import os
import sys
from typing import Dict, List, Tuple

import pandas as pd

# Support running as a module or directly.
try:
	from backend.database import get_db_connection  # type: ignore
except Exception:
	sys.path.append(os.path.dirname(os.path.dirname(__file__)))
	from backend.database import get_db_connection  # type: ignore


def clean_ticker(ticker) -> str | None:
	if pd.isna(ticker):
		return None
	return str(ticker).strip()


def get_year_cols(columns) -> List[int]:
	years: List[int] = []
	for col in columns:
		try:
			y = int(col)
			if 1990 <= y <= 2050:
				years.append(y)
		except Exception:
			pass
	return sorted(years)


def pick_pe_sheet(sheet_names: List[str]) -> str | None:
	if "FTM PE GAAP" in sheet_names:
		return "FTM PE GAAP"
	if "LTM PE GAAP" in sheet_names:
		return "LTM PE GAAP"
	return None


def read_pe_timeseries(file_path: str) -> Tuple[str, Dict[str, Dict[int, float]]]:
	xl = pd.ExcelFile(file_path)
	pe_sheet = pick_pe_sheet(list(xl.sheet_names))
	if not pe_sheet:
		raise ValueError("Neither 'FTM PE GAAP' nor 'LTM PE GAAP' sheet found.")

	df = pd.read_excel(xl, sheet_name=pe_sheet)
	year_cols = get_year_cols(df.columns)
	if not year_cols:
		raise ValueError(f"No year columns found in sheet '{pe_sheet}'.")

	ticker_col = df.columns[0]
	data: Dict[str, Dict[int, float]] = {}
	for _, row in df.iterrows():
		t = clean_ticker(row[ticker_col])
		if not t:
			continue
		for y in year_cols:
			val = row[y]
			if pd.isna(val):
				continue
			try:
				fv = float(val)
			except Exception:
				continue
			data.setdefault(t, {})[y] = fv
	return pe_sheet, data


def update_pe_ratio(*, data: Dict[str, Dict[int, float]], dry_run: bool) -> None:
	# Flatten
	rows: List[Tuple[str, int, float]] = []
	for t, years in data.items():
		for y, v in years.items():
			rows.append((t, int(y), float(v)))

	print(f"[pe_ratio] excel_rows={len(rows)} tickers={len(data)}")
	if not rows:
		print("[pe_ratio] nothing to do")
		return

	with get_db_connection() as conn:
		with conn.cursor() as cur:
			# Resolve tickers -> company_id
			tickers = list(data.keys())
			cur.execute("SELECT ticker, id FROM companies WHERE ticker = ANY(%s)", (tickers,))
			company_map = {r[0]: r[1] for r in cur.fetchall()}
			missing = [t for t in tickers if t not in company_map]
			print(f"[pe_ratio] matched_tickers={len(company_map)}/{len(tickers)} missing={len(missing)}")
			if missing[:5]:
				print(f"[pe_ratio] sample_missing={missing[:5]}")

			to_upsert: List[Tuple[str, int, float]] = []
			for t, y, v in rows:
				cid = company_map.get(t)
				if not cid:
					continue
				to_upsert.append((cid, y, v))

			print(f"[pe_ratio] upsert_rows={len(to_upsert)}")
			if dry_run:
				conn.rollback()
				return

			# Batch UPSERT
			# Use mogrify to avoid psycopg2.extras dependency assumptions.
			chunk_size = 2000
			for i in range(0, len(to_upsert), chunk_size):
				chunk = to_upsert[i : i + chunk_size]
				args_str = ",".join(cur.mogrify("(%s,%s,%s)", x).decode("utf-8") for x in chunk)
				cur.execute(
					f"""
					INSERT INTO financial_metrics (company_id, period, pe_ratio)
					VALUES {args_str}
					ON CONFLICT (company_id, period) DO UPDATE
					SET pe_ratio = EXCLUDED.pe_ratio
					"""
				)
				conn.commit()
				print(f"[pe_ratio] committed_chunk {i//chunk_size + 1}/{(len(to_upsert)-1)//chunk_size + 1}")

			# Refresh MV if present (ignore if missing)
			try:
				cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY company_latest_metrics;")
				conn.commit()
			except Exception:
				conn.rollback()


def main() -> None:
	parser = argparse.ArgumentParser(description="Update financial_metrics.pe_ratio from Exit Multiple Analysis Excel.")
	parser.add_argument("file_path", help="Path to Exit Multiple Analysis 1.xlsx")
	parser.add_argument("--dry-run", action="store_true", help="Parse + report only; no DB writes")
	parser.add_argument("--apply", action="store_true", help="Apply updates to DB")
	args = parser.parse_args()

	if args.dry_run and args.apply:
		raise SystemExit("Use only one of --dry-run or --apply.")
	if not args.dry_run and not args.apply:
		raise SystemExit("Specify --dry-run (recommended first) or --apply.")

	pe_sheet, data = read_pe_timeseries(args.file_path)
	print(f"[pe_ratio] using_sheet={pe_sheet}")
	update_pe_ratio(data=data, dry_run=args.dry_run)
	print("[pe_ratio] done")


if __name__ == "__main__":
	main()


