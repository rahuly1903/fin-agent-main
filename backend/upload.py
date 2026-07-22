"""
Excel file upload and validation module for Exit Multiple Analysis files.
"""
import os
import tempfile
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
import pandas as pd
from fastapi import UploadFile, HTTPException


# Required sheets for validation
REQUIRED_SHEETS = ["Sector"]
METRIC_SHEETS = ["EPS", "ROE", "ROIC", "FCFPS"]
PE_SHEETS = ["LTM PE GAAP", "FTM PE GAAP"]  # At least one required
OPTIONAL_SHEETS = ["Monthly Share prices", "Summary"]

# Validation thresholds
MIN_COMPANIES = 5
MIN_YEAR = 2000
MAX_YEAR = 2050


@dataclass
class ValidationResult:
    """Result of Excel file validation."""
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    sheets_found: List[str] = field(default_factory=list)
    company_count: int = 0
    year_range: Optional[Tuple[int, int]] = None


@dataclass
class UploadResult:
    """Result of file upload and ingestion."""
    success: bool
    message: str
    validation: ValidationResult
    companies_inserted: int = 0
    metrics_inserted: int = 0
    prices_inserted: int = 0


def validate_file_extension(filename: str) -> bool:
    """Check if file has valid Excel extension."""
    valid_extensions = ['.xlsx', '.xls']
    ext = os.path.splitext(filename)[1].lower()
    return ext in valid_extensions


def get_year_columns(columns: List) -> List[int]:
    """Extract valid year columns from dataframe columns."""
    years = []
    for col in columns:
        try:
            year = int(col)
            if MIN_YEAR <= year <= MAX_YEAR:
                years.append(year)
        except (ValueError, TypeError):
            pass
    return sorted(years)


def validate_excel_structure(file_path: str) -> ValidationResult:
    """
    Validate Excel file structure for Exit Multiple Analysis format.
    
    Checks:
    - Required sheets exist
    - At least one PE sheet exists
    - Sector sheet has data
    - Ticker column present
    - Valid year columns in metric sheets
    """
    result = ValidationResult(valid=True)
    
    try:
        xl = pd.ExcelFile(file_path)
        result.sheets_found = xl.sheet_names
    except Exception as e:
        result.valid = False
        result.errors.append(f"Failed to open Excel file: {str(e)}")
        return result
    
    # Check required sheets
    for sheet in REQUIRED_SHEETS:
        if sheet not in xl.sheet_names:
            result.valid = False
            result.errors.append(f"Missing required sheet: '{sheet}'")
    
    # Check at least one PE sheet exists
    pe_found = [s for s in PE_SHEETS if s in xl.sheet_names]
    if not pe_found:
        result.warnings.append(f"No PE sheet found. Expected one of: {PE_SHEETS}")
    
    # Check metric sheets
    metrics_found = [s for s in METRIC_SHEETS if s in xl.sheet_names]
    if not metrics_found:
        result.valid = False
        result.errors.append(f"No metric sheets found. Expected at least one of: {METRIC_SHEETS}")
    
    # Validate Sector sheet content
    if "Sector" in xl.sheet_names:
        try:
            df_sector = pd.read_excel(xl, sheet_name="Sector", header=None)
            
            # Count non-empty rows (excluding headers)
            ticker_col = df_sector.iloc[:, 0]
            valid_tickers = ticker_col.dropna()
            # Filter out header rows
            valid_tickers = valid_tickers[~valid_tickers.astype(str).str.lower().isin(['ticker', 'id', 'symbol'])]
            
            result.company_count = len(valid_tickers)
            
            if result.company_count < MIN_COMPANIES:
                result.valid = False
                result.errors.append(f"Too few companies: {result.company_count} (minimum: {MIN_COMPANIES})")
                
        except Exception as e:
            result.valid = False
            result.errors.append(f"Error reading Sector sheet: {str(e)}")
    
    # Check year columns in first available metric sheet
    for sheet in metrics_found:
        try:
            df_metric = pd.read_excel(xl, sheet_name=sheet, nrows=1)
            years = get_year_columns(df_metric.columns)
            if years:
                result.year_range = (min(years), max(years))
                break
        except Exception:
            pass
    
    if result.year_range is None:
        result.warnings.append("Could not determine year range from metric sheets")
    
    return result


async def save_upload_to_temp(file: UploadFile) -> str:
    """Save uploaded file to temporary location."""
    # Create temp file with correct extension
    suffix = os.path.splitext(file.filename)[1] if file.filename else '.xlsx'
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        return tmp.name


def clean_ticker(ticker) -> Optional[str]:
    """Clean and validate ticker symbol."""
    if pd.isna(ticker):
        return None
    ticker_str = str(ticker).strip()
    if ticker_str.lower() in ['ticker', 'id', 'symbol', '']:
        return None
    return ticker_str
