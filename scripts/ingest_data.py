import pandas as pd
import psycopg2
from database import get_db_connection
import sys
import uuid
import os
import datetime

def clean_ticker(ticker):
    if pd.isna(ticker):
        return None
    return str(ticker).strip()

def get_year_cols(columns):
    years = []
    for col in columns:
        try:
            y = int(col)
            if 1990 <= y <= 2050:
                years.append(y)
        except:
            pass
    return sorted(years)

def ingest_data(file_path):
    print(f"Reading file: {file_path}")
    
    try:
        xl = pd.ExcelFile(file_path)
    except Exception as e:
        print(f"Error opening Excel file: {e}")
        return

    all_companies = {} # Ticker -> { 'sector': ..., 'industry': ... }
    # Structure: Ticker -> Year -> { metric: value }
    historical_data = {} 

    # 1. Read Sector sheet for base Company info
    print("Reading Sector sheet...")
    if 'Sector' in xl.sheet_names:
        try:
            df_sector = pd.read_excel(xl, sheet_name='Sector', header=None)
            for _, row in df_sector.iterrows():
                ticker = clean_ticker(row[0])
                if not ticker: continue
                
                # Skip header if it exists
                if ticker == 'Ticker' or ticker == 'ID': continue

                all_companies[ticker] = {
                    'sector': row[3] if len(row) > 3 else None,
                    'industry': row[4] if len(row) > 4 else None
                }
            print(f"Found {len(all_companies)} companies in Sector sheet.")
        except Exception as e:
            print(f"Error reading Sector sheet: {e}")

    # 2. Read Financial Metrics (Historical)
    # Excel sheet -> financial_metrics column mapping.
    # NOTE: For P/E we prefer LTM (trailing) over FTM (forward).
    metrics_map = {
        'EPS': 'eps',
        'ROE': 'roe',
        'ROIC': 'roic',
        'FCFPS': 'fcf',
    }
    
    # ... (Existing metrics loop) ...
    for sheet_name, metric_field in metrics_map.items():
        if sheet_name in xl.sheet_names:
            print(f"Reading {sheet_name} sheet...")
            try:
                df_metric = pd.read_excel(xl, sheet_name=sheet_name)
                
                # Find all year columns
                year_cols = get_year_cols(df_metric.columns)
                if not year_cols:
                    print(f"  No year columns found in {sheet_name}")
                    continue
                    
                print(f"  Found years: {year_cols[0]} to {year_cols[-1]}")
                
                ticker_col = df_metric.columns[0]
                
                for _, row in df_metric.iterrows():
                    ticker = clean_ticker(row[ticker_col])
                    if not ticker or ticker not in all_companies: continue
                    
                    if ticker not in historical_data:
                        historical_data[ticker] = {}
                        
                    for year in year_cols:
                        val = row[year]
                        if pd.isna(val): continue
                        
                        if year not in historical_data[ticker]:
                            historical_data[ticker][year] = {}
                        
                        historical_data[ticker][year][metric_field] = val
                        
            except Exception as e:
                print(f"Error reading {sheet_name}: {e}")

    # P/E ratio (prefer FTM PE GAAP; fallback to LTM PE GAAP for backward compatibility)
    pe_sheet = None
    if 'FTM PE GAAP' in xl.sheet_names:
        pe_sheet = 'FTM PE GAAP'
    elif 'LTM PE GAAP' in xl.sheet_names:
        pe_sheet = 'LTM PE GAAP'
        print("  NOTE: 'FTM PE GAAP' not found; falling back to 'LTM PE GAAP' for pe_ratio.")

    if pe_sheet:
        print(f"Reading {pe_sheet} sheet (-> pe_ratio)...")
        try:
            df_metric = pd.read_excel(xl, sheet_name=pe_sheet)

            year_cols = get_year_cols(df_metric.columns)
            if not year_cols:
                print(f"  No year columns found in {pe_sheet}")
            else:
                print(f"  Found years: {year_cols[0]} to {year_cols[-1]}")
                ticker_col = df_metric.columns[0]

                for _, row in df_metric.iterrows():
                    ticker = clean_ticker(row[ticker_col])
                    if not ticker or ticker not in all_companies:
                        continue

                    if ticker not in historical_data:
                        historical_data[ticker] = {}

                    for year in year_cols:
                        val = row[year]
                        if pd.isna(val):
                            continue
                        
                        # Ignore PE ratios > 100 as they are unreliable
                        if val > 100:
                            continue

                        if year not in historical_data[ticker]:
                            historical_data[ticker][year] = {}

                        historical_data[ticker][year]['pe_ratio'] = val
        except Exception as e:
            print(f"Error reading {pe_sheet}: {e}")

    # 2b. Read Monthly Share Prices
    share_prices_data = [] # List of (ticker, date, price)
    if 'Monthly Share prices' in xl.sheet_names:
        print("Reading Monthly Share prices sheet...")
        try:
            # Read header to get company names (not used for mapping but good for debug)
            # Read data starting from row 1 (which contains "Date", "Close" labels)
            # Actually, row 0 is names, row 1 is labels, row 2 is data.
            # Let's read with header=1 to get "Date", "Close" as columns? No, duplicate names.
            # Best to read without header and slice.
            df_prices = pd.read_excel(xl, sheet_name='Monthly Share prices', header=None)
            
            # Get list of tickers from all_companies (assuming insertion order is preserved from Sector sheet)
            # We need to be sure about the order. 
            # The all_companies dict was populated from Sector sheet iteration.
            # Python 3.7+ dicts preserve insertion order.
            tickers_list = list(all_companies.keys())
            
            num_pairs = df_prices.shape[1] // 2
            print(f"  Found {num_pairs} price columns pairs. Companies: {len(tickers_list)}")
            
            if num_pairs != len(tickers_list):
                print("  WARNING: Mismatch between price columns and companies count. Mapping might be wrong!")
            
            # Iterate pairs
            for i in range(min(num_pairs, len(tickers_list))):
                ticker = tickers_list[i]
                col_date = i * 2
                col_price = i * 2 + 1
                
                # Data starts at row 2 (0-indexed)
                # Row 0: Name, Row 1: "Date"/"Close"
                company_data = df_prices.iloc[2:, [col_date, col_price]]
                company_data.columns = ['Date', 'Price']
                
                for _, row in company_data.iterrows():
                    date_val = row['Date']
                    price_val = row['Price']
                    
                    if pd.isna(date_val) or pd.isna(price_val): continue
                    
                    # Convert Excel Serial Date
                    try:
                        if isinstance(date_val, (int, float)):
                            dt = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=date_val)
                            date_str = dt.strftime("%Y-%m-%d")
                        else:
                            # Maybe it's already a datetime or string
                            date_str = str(date_val)
                            # Basic validation/parsing could go here
                            
                        share_prices_data.append((ticker, date_str, price_val))
                    except Exception as e:
                        # print(f"Error parsing date {date_val}: {e}")
                        pass
                        
            print(f"  Extracted {len(share_prices_data)} price records.")
            
        except Exception as e:
            print(f"Error reading Share Prices: {e}")

    # 3. Ingest into DB
    print(f"Starting DB ingestion for {len(all_companies)} companies...")
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Run Migrations
                migration_dir = 'db_migrations'
                if os.path.exists(migration_dir):
                    print("Running migrations...")
                    migration_files = sorted([f for f in os.listdir(migration_dir) if f.endswith('.sql')])
                    for sql_file in migration_files:
                        print(f"Executing {sql_file}...")
                        with open(os.path.join(migration_dir, sql_file), 'r') as f:
                            try:
                                cur.execute(f.read())
                            except psycopg2.errors.DuplicateObject:
                                conn.rollback() # Ignore if constraint already exists
                                continue
                            except psycopg2.errors.DuplicateTable:
                                conn.rollback()
                                continue
                            except Exception as e:
                                # Check if it's just "relation already exists"
                                conn.rollback()
                                print(f"  Warning: Migration {sql_file} failed (likely already applied): {e}")
                    conn.commit()
                
                # Upsert Companies
                print("Upserting companies...")
                company_id_map = {} # Ticker -> UUID
                
                for ticker, info in all_companies.items():
                    cur.execute("""
                        INSERT INTO companies (ticker, sector, industry)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (ticker) DO UPDATE 
                        SET sector = COALESCE(EXCLUDED.sector, companies.sector), 
                            industry = COALESCE(EXCLUDED.industry, companies.industry)
                        RETURNING id;
                    """, (ticker, info['sector'], info['industry']))
                    company_id_map[ticker] = cur.fetchone()[0]
                
                # Upsert Metrics (Batching would be better, but simple loop for now)
                print("Upserting financial metrics...")
                metrics_count = 0
                
                for ticker, years_data in historical_data.items():
                    company_id = company_id_map.get(ticker)
                    if not company_id: continue
                    
                    for year, metrics in years_data.items():
                        # Dynamic Upsert
                        cols = ['company_id', 'period'] + list(metrics.keys())
                        vals = [company_id, year] + list(metrics.values())
                        
                        placeholders = ", ".join(['%s'] * len(cols))
                        col_names = ", ".join(cols)
                        
                        # Build SET clause for ON CONFLICT UPDATE
                        update_clause = ", ".join([f"{k} = EXCLUDED.{k}" for k in metrics.keys()])
                        
                        query = f"""
                            INSERT INTO financial_metrics ({col_names})
                            VALUES ({placeholders})
                            ON CONFLICT (company_id, period) DO UPDATE
                            SET {update_clause};
                        """
                        
                        cur.execute(query, vals)
                        metrics_count += 1
                        
                conn.commit()
                
                # Refresh Materialized View (CONCURRENTLY to allow reads during refresh)
                print("Refreshing Materialized View...")
                cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY company_latest_metrics;")
                conn.commit()
                
                # Upsert Share Prices
                if share_prices_data:
                    print(f"Upserting {len(share_prices_data)} share price records (this may take a while)...")
                    # Use execute_batch for performance if possible, but we'll stick to loop for now or simple batch
                    # Actually, let's do a simple batch insert using executemany
                    
                    # Prepare data: (company_id, date, price)
                    prices_to_insert = []
                    for ticker, date_str, price in share_prices_data:
                        cid = company_id_map.get(ticker)
                        if cid:
                            prices_to_insert.append((cid, date_str, price))
                            
                    # Chunk it
                    chunk_size = 1000
                    for i in range(0, len(prices_to_insert), chunk_size):
                        chunk = prices_to_insert[i:i+chunk_size]
                        args_str = ','.join(cur.mogrify("(%s, %s, %s)", x).decode('utf-8') for x in chunk)
                        cur.execute(f"""
                            INSERT INTO share_prices (company_id, date, price) 
                            VALUES {args_str}
                            ON CONFLICT (company_id, date) DO UPDATE SET price = EXCLUDED.price
                        """)
                        conn.commit()
                        print(f"  Inserted chunk {i // chunk_size + 1}/{(len(prices_to_insert) - 1) // chunk_size + 1}")
                
                print(f"Ingestion completed. Processed {metrics_count} metric records and {len(share_prices_data)} price records.")
                print("Note: Stock returns are calculated dynamically via database functions (no pre-calculation needed)")

    except Exception as e:
        print(f"Error during DB transaction: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.ingest_data <path_to_excel>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    ingest_data(file_path)
