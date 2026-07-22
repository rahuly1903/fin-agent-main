"""
Data ingestion service for Exit Multiple Analysis files.
Refactored from scripts/ingest_data.py for API use.
"""
import os
import datetime
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import pandas as pd
import psycopg2

# Support both package import and direct script usage
try:
    from database import get_db_connection
    from upload import clean_ticker, get_year_columns, ValidationResult
except ImportError:
    from .database import get_db_connection
    from .upload import clean_ticker, get_year_columns, ValidationResult


@dataclass
class IngestionStats:
    """Statistics from data ingestion."""
    companies_processed: int = 0
    metrics_inserted: int = 0
    prices_inserted: int = 0
    errors: List[str] = field(default_factory=list)


def ingest_excel_file(
    file_path: str,
    replace_existing: bool = True
) -> IngestionStats:
    """
    Ingest Exit Multiple Analysis Excel file into database.
    
    Args:
        file_path: Path to Excel file
        replace_existing: If True, clear existing data before inserting
        
    Returns:
        IngestionStats with counts and any errors
    """
    stats = IngestionStats()
    
    try:
        xl = pd.ExcelFile(file_path)
    except Exception as e:
        stats.errors.append(f"Failed to open Excel file: {str(e)}")
        return stats
    
    all_companies: Dict[str, Dict[str, Any]] = {}
    historical_data: Dict[str, Dict[int, Dict[str, Any]]] = {}
    share_prices_data: List[Tuple[str, str, float]] = []
    
    # 1. Read Sector sheet for company info
    if 'Sector' in xl.sheet_names:
        try:
            df_sector = pd.read_excel(xl, sheet_name='Sector', header=None)
            for _, row in df_sector.iterrows():
                ticker = clean_ticker(row.iloc[0])
                if not ticker:
                    continue
                    
                all_companies[ticker] = {
                    'sector': row.iloc[3] if len(row) > 3 and pd.notna(row.iloc[3]) else None,
                    'industry': row.iloc[4] if len(row) > 4 and pd.notna(row.iloc[4]) else None
                }
        except Exception as e:
            stats.errors.append(f"Error reading Sector sheet: {str(e)}")
    
    # 2. Read financial metrics
    metrics_map = {
        'EPS': 'eps',
        'ROE': 'roe',
        'ROIC': 'roic',
        'FCFPS': 'fcf',
    }
    
    for sheet_name, metric_field in metrics_map.items():
        if sheet_name in xl.sheet_names:
            try:
                df_metric = pd.read_excel(xl, sheet_name=sheet_name)
                year_cols = get_year_columns(df_metric.columns)
                
                if not year_cols:
                    continue
                    
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
                        
                        if year not in historical_data[ticker]:
                            historical_data[ticker][year] = {}
                        
                        historical_data[ticker][year][metric_field] = val
                        
            except Exception as e:
                stats.errors.append(f"Error reading {sheet_name}: {str(e)}")
    
    # 3. Read PE ratio (prefer FTM over LTM)
    pe_sheet = None
    if 'FTM PE GAAP' in xl.sheet_names:
        pe_sheet = 'FTM PE GAAP'
    elif 'LTM PE GAAP' in xl.sheet_names:
        pe_sheet = 'LTM PE GAAP'
    
    if pe_sheet:
        try:
            df_metric = pd.read_excel(xl, sheet_name=pe_sheet)
            year_cols = get_year_columns(df_metric.columns)
            
            if year_cols:
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
            stats.errors.append(f"Error reading {pe_sheet}: {str(e)}")
    
    # 4. Read share prices
    if 'Monthly Share prices' in xl.sheet_names:
        try:
            df_prices = pd.read_excel(xl, sheet_name='Monthly Share prices', header=None)
            tickers_list = list(all_companies.keys())
            num_pairs = df_prices.shape[1] // 2
            
            for i in range(min(num_pairs, len(tickers_list))):
                ticker = tickers_list[i]
                col_date = i * 2
                col_price = i * 2 + 1
                
                company_data = df_prices.iloc[2:, [col_date, col_price]]
                company_data.columns = ['Date', 'Price']
                
                for _, row in company_data.iterrows():
                    date_val = row['Date']
                    price_val = row['Price']
                    
                    if pd.isna(date_val) or pd.isna(price_val):
                        continue
                    
                    try:
                        if isinstance(date_val, (int, float)):
                            dt = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=date_val)
                            date_str = dt.strftime("%Y-%m-%d")
                        elif isinstance(date_val, datetime.datetime):
                            date_str = date_val.strftime("%Y-%m-%d")
                        else:
                            date_str = str(date_val)[:10]
                        
                        share_prices_data.append((ticker, date_str, float(price_val)))
                    except Exception:
                        pass
                        
        except Exception as e:
            stats.errors.append(f"Error reading Share Prices: {str(e)}")
    
    # 5. Insert into database using a SINGLE ATOMIC TRANSACTION
    # If anything fails, the entire operation is rolled back - no partial data loss
    conn = None
    try:
        conn = get_db_connection().__enter__()
        cur = conn.cursor()
        
        try:
            # Clear existing data if replacing (within same transaction)
            if replace_existing:
                cur.execute("DELETE FROM share_prices;")
                cur.execute("DELETE FROM financial_metrics;")
                cur.execute("DELETE FROM stock_returns;")
                cur.execute("DELETE FROM cagr_data;")
                cur.execute("DELETE FROM companies;")
                # NO COMMIT HERE - stays in same transaction
            
            # Upsert companies
            company_id_map: Dict[str, str] = {}
            
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
            
            stats.companies_processed = len(company_id_map)
            
            # Insert metrics
            for ticker, years_data in historical_data.items():
                company_id = company_id_map.get(ticker)
                if not company_id:
                    continue
                
                for year, metrics in years_data.items():
                    cols = ['company_id', 'period'] + list(metrics.keys())
                    vals = [company_id, year] + list(metrics.values())
                    
                    placeholders = ", ".join(['%s'] * len(cols))
                    col_names = ", ".join(cols)
                    update_clause = ", ".join([f"{k} = EXCLUDED.{k}" for k in metrics.keys()])
                    
                    query = f"""
                        INSERT INTO financial_metrics ({col_names})
                        VALUES ({placeholders})
                        ON CONFLICT (company_id, period) DO UPDATE
                        SET {update_clause};
                    """
                    
                    cur.execute(query, vals)
                    stats.metrics_inserted += 1
            
            # Insert share prices in chunks (still within same transaction)
            if share_prices_data:
                prices_to_insert = []
                for ticker, date_str, price in share_prices_data:
                    cid = company_id_map.get(ticker)
                    if cid:
                        prices_to_insert.append((cid, date_str, price))
                
                chunk_size = 1000
                for i in range(0, len(prices_to_insert), chunk_size):
                    chunk = prices_to_insert[i:i+chunk_size]
                    args_str = ','.join(
                        cur.mogrify("(%s, %s, %s)", x).decode('utf-8') 
                        for x in chunk
                    )
                    cur.execute(f"""
                        INSERT INTO share_prices (company_id, date, price) 
                        VALUES {args_str}
                        ON CONFLICT (company_id, date) DO UPDATE SET price = EXCLUDED.price
                    """)
                
                stats.prices_inserted = len(prices_to_insert)
            
            # ALL operations succeeded - COMMIT the entire transaction
            conn.commit()
            
            # Refresh materialized view (separate transaction, non-critical)
            try:
                cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY company_latest_metrics;")
                conn.commit()
            except Exception:
                # View might not exist yet or be empty - not a failure
                conn.rollback()
                
        except Exception as e:
            # ANY failure during ingestion - ROLLBACK everything
            # The database remains in its original state
            conn.rollback()
            stats.errors.append(f"Database error (transaction rolled back): {str(e)}")
        finally:
            cur.close()
            
    except Exception as e:
        stats.errors.append(f"Database connection error: {str(e)}")
    finally:
        if conn:
            try:
                get_db_connection().__exit__(None, None, None)
            except Exception:
                pass
    
    return stats
