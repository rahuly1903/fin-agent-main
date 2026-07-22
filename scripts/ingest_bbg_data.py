"""
BBG Data Ingestion Script

Ingests all sheets from Bloomberg Excel export (BBG_Jan4th.xlsx format) 
into the database.

Sheets handled:
- sales in bn -> financial_metrics.sales
- ebitda in bn -> financial_metrics.ebitda
- ebit in bn -> financial_metrics.ebit
- PAT in bn -> financial_metrics.pat
- Other -> ev_ebitda, debt_ebitda, net_debt_ebitda, market_cap, share_price

Usage:
    python -m scripts.ingest_bbg_data <path_to_excel> [--dry-run]

Example:
    python -m scripts.ingest_bbg_data "Hard coded Data from BBG_Jan4th.xlsx"
    python -m scripts.ingest_bbg_data "Hard coded Data from BBG_Jan4th.xlsx" --dry-run
"""

import pandas as pd
import sys
import argparse
import os
from datetime import date

# Support running as a module (`python -m scripts.ingest_bbg_data`) or directly (`python scripts/ingest_bbg_data.py`)
try:
    from backend.database import get_db_connection  # type: ignore
except Exception:
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from backend.database import get_db_connection  # type: ignore


def clean_ticker(ticker):
    """Clean and normalize ticker string."""
    if pd.isna(ticker):
        return None
    return str(ticker).strip()


def get_year_cols(columns):
    """Extract year columns (integers between 1990-2050) from column list."""
    years = []
    for col in columns:
        try:
            y = int(col)
            if 1990 <= y <= 2050:
                years.append(y)
        except (ValueError, TypeError):
            pass
    return sorted(years)


# Sheet name -> database column mapping for time-series data
TIMESERIES_SHEETS = {
    'sales in bn': 'sales',
    'ebitda in bn ': 'ebitda',
    'ebit in bn ': 'ebit',
    'PAT in bn': 'pat',
    # If present in the BBG export, ingest trailing P/E into financial_metrics.pe_ratio
    # (structured like other time-series sheets: ticker in first column, year columns thereafter).
    'LTM PE GAAP': 'pe_ratio',
}

# Other sheet column -> database column mapping
OTHER_COLS = {
    'EV/EBITDA': ('financial_metrics', 'ev_ebitda'),
    'Total DEBT/EBITDA': ('financial_metrics', 'debt_ebitda'),
    'NET DEBT/EBITDA': ('financial_metrics', 'net_debt_ebitda'),
    'MC (bn)': ('companies', 'market_cap'),
    'Sh Price': ('share_prices', 'price'),
}


def run_migrations(cur, conn):
    """Run any pending migrations."""
    migration_dir = 'db_migrations'
    if os.path.exists(migration_dir):
        print("Running migrations...")
        migration_files = sorted([f for f in os.listdir(migration_dir) if f.endswith('.sql')])
        for sql_file in migration_files:
            print(f"  Executing {sql_file}...")
            with open(os.path.join(migration_dir, sql_file), 'r') as f:
                try:
                    cur.execute(f.read())
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    # Ignore "already exists" type errors
                    if 'already exists' not in str(e).lower() and 'duplicate' not in str(e).lower():
                        print(f"    Warning: {e}")


def parse_timeseries_sheet(df, sheet_name, db_col):
    """Parse a time-series sheet into ticker -> year -> value dict."""
    year_cols = get_year_cols(df.columns)
    if not year_cols:
        print(f"  No year columns found in {sheet_name}")
        return {}
    
    ticker_col = df.columns[0]
    data = {}
    
    for _, row in df.iterrows():
        ticker = clean_ticker(row[ticker_col])
        if not ticker:
            continue
        
        data[ticker] = {}
        for year in year_cols:
            val = row[year]
            if not pd.isna(val):
                data[ticker][year] = float(val)
    
    print(f"  Parsed {len(data)} tickers, years {year_cols[0]}-{year_cols[-1]}")
    return data


def parse_other_sheet(df):
    """Parse the Other sheet with point-in-time values."""
    ticker_col = df.columns[0]
    data = {}
    
    for _, row in df.iterrows():
        ticker = clean_ticker(row[ticker_col])
        if not ticker:
            continue
        
        data[ticker] = {}
        for excel_col, (table, db_col) in OTHER_COLS.items():
            if excel_col in df.columns:
                val = row[excel_col]
                if not pd.isna(val):
                    data[ticker][(table, db_col)] = float(val)
    
    print(f"  Parsed {len(data)} tickers with point-in-time values")
    return data


def ingest_bbg_data(file_path, dry_run=False):
    """
    Ingest all sheets from BBG Excel file into database.
    
    Args:
        file_path: Path to the Excel file
        dry_run: If True, only validate and report without making DB changes
    """
    print(f"Reading file: {file_path}")
    
    try:
        xl = pd.ExcelFile(file_path)
    except Exception as e:
        print(f"Error opening Excel file: {e}")
        return False
    
    print(f"Found sheets: {xl.sheet_names}")
    
    # Parse all time-series sheets
    timeseries_data = {}  # db_col -> {ticker -> {year -> value}}
    for sheet_name, db_col in TIMESERIES_SHEETS.items():
        if sheet_name in xl.sheet_names:
            print(f"\nProcessing '{sheet_name}' -> {db_col}")
            df = pd.read_excel(xl, sheet_name=sheet_name)
            timeseries_data[db_col] = parse_timeseries_sheet(df, sheet_name, db_col)
        else:
            print(f"  Sheet '{sheet_name}' not found, skipping")
    
    # Parse Other sheet
    other_data = {}
    if 'Other' in xl.sheet_names:
        print(f"\nProcessing 'Other' sheet")
        df = pd.read_excel(xl, sheet_name='Other')
        other_data = parse_other_sheet(df)
    
    # Summary
    total_ts_records = sum(
        sum(len(years) for years in ticker_data.values())
        for ticker_data in timeseries_data.values()
    )
    print(f"\n=== Summary ===")
    print(f"Time-series records: {total_ts_records}")
    print(f"Other sheet tickers: {len(other_data)}")
    
    if dry_run:
        print("\n=== DRY RUN MODE - No changes will be made ===")
        for db_col, data in timeseries_data.items():
            print(f"  Would upsert {sum(len(y) for y in data.values())} {db_col} records")
        print(f"  Would update {len(other_data)} tickers with point-in-time values")
        return True
    
    # Database ingestion
    print("\nStarting database ingestion...")
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Skip migrations - should be run separately or by ingest_data.py first
                # run_migrations(cur, conn)
                
                # Get all tickers from data
                all_tickers = set()
                for data in timeseries_data.values():
                    all_tickers.update(data.keys())
                all_tickers.update(other_data.keys())
                
                # Get company_id mapping
                cur.execute(
                    "SELECT ticker, id FROM companies WHERE ticker = ANY(%s)",
                    (list(all_tickers),)
                )
                company_map = {row[0]: row[1] for row in cur.fetchall()}
                
                print(f"Found {len(company_map)}/{len(all_tickers)} tickers in database")
                
                # Upsert time-series data
                updated = 0
                inserted = 0
                
                for db_col, ticker_data in timeseries_data.items():
                    print(f"  Upserting {db_col}...")
                    
                    for ticker, years_data in ticker_data.items():
                        company_id = company_map.get(ticker)
                        if not company_id:
                            continue
                        
                        for year, val in years_data.items():
                            # Check if record exists
                            cur.execute("""
                                SELECT id FROM financial_metrics 
                                WHERE company_id = %s AND period = %s
                            """, (company_id, year))
                            existing = cur.fetchone()
                            
                            if existing:
                                cur.execute(f"""
                                    UPDATE financial_metrics 
                                    SET {db_col} = %s 
                                    WHERE company_id = %s AND period = %s
                                """, (val, company_id, year))
                                updated += 1
                            else:
                                cur.execute(f"""
                                    INSERT INTO financial_metrics (company_id, period, {db_col})
                                    VALUES (%s, %s, %s)
                                """, (company_id, year, val))
                                inserted += 1
                
                conn.commit()
                print(f"  Time-series: {updated} updated, {inserted} inserted")
                
                # Handle Other sheet data
                if other_data:
                    print("  Upserting Other sheet data...")
                    
                    # Get current year for point-in-time metrics
                    current_year = date.today().year
                    
                    for ticker, values in other_data.items():
                        company_id = company_map.get(ticker)
                        if not company_id:
                            continue
                        
                        for (table, db_col), val in values.items():
                            if table == 'companies':
                                # Update companies table
                                cur.execute(f"""
                                    UPDATE companies SET {db_col} = %s WHERE id = %s
                                """, (val, company_id))
                            
                            elif table == 'financial_metrics':
                                # Update current year's financial metrics
                                cur.execute("""
                                    SELECT id FROM financial_metrics 
                                    WHERE company_id = %s AND period = %s
                                """, (company_id, current_year))
                                existing = cur.fetchone()
                                
                                if existing:
                                    cur.execute(f"""
                                        UPDATE financial_metrics 
                                        SET {db_col} = %s 
                                        WHERE company_id = %s AND period = %s
                                    """, (val, company_id, current_year))
                                else:
                                    cur.execute(f"""
                                        INSERT INTO financial_metrics (company_id, period, {db_col})
                                        VALUES (%s, %s, %s)
                                    """, (company_id, current_year, val))
                            
                            elif table == 'share_prices':
                                # Upsert latest share price
                                today = date.today()
                                cur.execute("""
                                    INSERT INTO share_prices (company_id, date, price)
                                    VALUES (%s, %s, %s)
                                    ON CONFLICT (company_id, date) 
                                    DO UPDATE SET price = EXCLUDED.price
                                """, (company_id, today, val))
                    
                    conn.commit()
                    print(f"  Other sheet: {len(other_data)} tickers updated")
                
                # Refresh materialized view
                print("Refreshing materialized view...")
                cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY company_latest_metrics;")
                conn.commit()
                
                print("\n✓ Ingestion completed successfully!")
                return True
                
    except Exception as e:
        print(f"ERROR during database operation: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Ingest BBG data from Excel file into database"
    )
    parser.add_argument(
        "file_path",
        help="Path to the Excel file (BBG_Jan4th.xlsx format)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate file without making database changes"
    )
    
    args = parser.parse_args()
    
    success = ingest_bbg_data(args.file_path, dry_run=args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
