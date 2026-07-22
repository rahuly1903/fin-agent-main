-- Financial AI Agent - Consolidated Database Setup
-- This file represents the current production schema after all migrations
-- Run this on a fresh database to set up the complete schema

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- CORE TABLES
-- ============================================================================

-- Companies Table
CREATE TABLE IF NOT EXISTS companies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticker VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(255),
    country VARCHAR(100),
    sector VARCHAR(100),
    industry VARCHAR(100),
    market_cap DOUBLE PRECISION,
    currency VARCHAR(10)
);

-- Financial Metrics Table (time-series data)
CREATE TABLE IF NOT EXISTS financial_metrics (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    period INTEGER,  -- Year (e.g., 2025)
    sales DOUBLE PRECISION,
    ebitda DOUBLE PRECISION,
    ebit DOUBLE PRECISION,
    pat DOUBLE PRECISION,
    eps DOUBLE PRECISION,
    fcf DOUBLE PRECISION,
    roe DOUBLE PRECISION,
    roic DOUBLE PRECISION,
    pe_ratio DOUBLE PRECISION,
    p_fcf_ratio DOUBLE PRECISION,
    ev_ebitda DOUBLE PRECISION,
    debt_ebitda DOUBLE PRECISION,
    net_debt_ebitda DOUBLE PRECISION,
    ebit_margin DOUBLE PRECISION,
    assigned_exit_multiple DOUBLE PRECISION,
    irr DOUBLE PRECISION,
    CONSTRAINT uq_financial_metrics_company_period UNIQUE (company_id, period)
);

-- Share Prices Table (monthly historical prices)
CREATE TABLE IF NOT EXISTS share_prices (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    price DOUBLE PRECISION,
    CONSTRAINT uq_share_prices_company_date UNIQUE (company_id, date)
);

-- Stock Returns Table
CREATE TABLE IF NOT EXISTS stock_returns (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    last_5_years_return DOUBLE PRECISION,
    last_10_years_return DOUBLE PRECISION,
    CONSTRAINT uq_stock_returns_company UNIQUE (company_id)
);

-- CAGR Data Table
CREATE TABLE IF NOT EXISTS cagr_data (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    period_type VARCHAR(50), -- 'last_3_years', 'next_3_years'
    sales_cagr DOUBLE PRECISION,
    ebit_cagr DOUBLE PRECISION,
    pat_cagr DOUBLE PRECISION,
    eps_cagr DOUBLE PRECISION,
    fcf_cagr DOUBLE PRECISION
);

-- Ticker Changes Audit Table
CREATE TABLE IF NOT EXISTS ticker_changes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    old_ticker VARCHAR(50) NOT NULL,
    new_ticker VARCHAR(50) NOT NULL,
    change_date DATE NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    notes TEXT
);

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Companies indexes
CREATE INDEX IF NOT EXISTS idx_companies_ticker ON companies(ticker);
CREATE INDEX IF NOT EXISTS idx_companies_market_cap ON companies(market_cap);
CREATE INDEX IF NOT EXISTS idx_companies_country ON companies(country);
CREATE INDEX IF NOT EXISTS idx_companies_sector ON companies(sector);

-- Financial Metrics indexes
CREATE INDEX IF NOT EXISTS idx_financial_metrics_company_id ON financial_metrics(company_id);
CREATE INDEX IF NOT EXISTS idx_financial_metrics_period ON financial_metrics(period);
CREATE INDEX IF NOT EXISTS idx_financial_metrics_roe ON financial_metrics(roe);
CREATE INDEX IF NOT EXISTS idx_financial_metrics_pe_ratio ON financial_metrics(pe_ratio);
CREATE INDEX IF NOT EXISTS idx_financial_metrics_irr ON financial_metrics(irr);

-- Share Prices indexes
CREATE INDEX IF NOT EXISTS idx_share_prices_company_id ON share_prices(company_id);
CREATE INDEX IF NOT EXISTS idx_share_prices_date ON share_prices(date);

-- Stock Returns indexes
CREATE INDEX IF NOT EXISTS idx_stock_returns_company_id ON stock_returns(company_id);

-- CAGR Data indexes
CREATE INDEX IF NOT EXISTS idx_cagr_data_company_id ON cagr_data(company_id);

-- Ticker Changes indexes
CREATE INDEX IF NOT EXISTS idx_ticker_changes_company_id ON ticker_changes(company_id);
CREATE INDEX IF NOT EXISTS idx_ticker_changes_change_date ON ticker_changes(change_date);

-- ============================================================================
-- MATERIALIZED VIEW (Dashboard Optimization)
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS company_latest_metrics AS
SELECT DISTINCT ON (c.id)
    c.id AS company_id,
    c.ticker,
    c.name,
    c.sector,
    c.industry,
    c.country,
    c.market_cap,
    fm.period,
    fm.sales,
    fm.roe,
    fm.pe_ratio,
    fm.irr,
    fm.ev_ebitda
FROM companies c
JOIN financial_metrics fm ON c.id = fm.company_id
ORDER BY c.id, fm.period DESC;

-- UNIQUE index enables CONCURRENT refresh
CREATE UNIQUE INDEX IF NOT EXISTS idx_mat_view_company_id ON company_latest_metrics(company_id);
CREATE INDEX IF NOT EXISTS idx_mat_view_market_cap ON company_latest_metrics(market_cap);
CREATE INDEX IF NOT EXISTS idx_mat_view_roe ON company_latest_metrics(roe);
CREATE INDEX IF NOT EXISTS idx_mat_view_sector ON company_latest_metrics(sector);

-- ============================================================================
-- TRIGGERS (Audit Trail)
-- ============================================================================

-- Trigger function to log ticker changes
CREATE OR REPLACE FUNCTION log_ticker_change()
RETURNS TRIGGER AS $$
BEGIN
    -- Only log if ticker actually changed
    IF OLD.ticker IS DISTINCT FROM NEW.ticker THEN
        INSERT INTO ticker_changes (company_id, old_ticker, new_ticker, change_date, notes)
        VALUES (
            NEW.id, 
            OLD.ticker, 
            NEW.ticker, 
            CURRENT_DATE,
            'Automatic update via trigger'
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attach trigger to companies table
DROP TRIGGER IF EXISTS ticker_change_trigger ON companies;
CREATE TRIGGER ticker_change_trigger
    AFTER UPDATE ON companies
    FOR EACH ROW
    EXECUTE FUNCTION log_ticker_change();

-- ============================================================================
-- NOTES
-- ============================================================================
-- This schema represents:
-- - 503 companies from S&P 500 + selected stocks
-- - 9,715+ financial metric records (2010-2029)
-- - 88,831+ share price records (monthly historical)
-- - Normalized schema (3NF)
-- - Full audit trail for ticker changes
-- - Performance-optimized indexes
-- - Zero-downtime materialized view refresh (CONCURRENT)
--
-- To refresh the materialized view after data changes:
-- REFRESH MATERIALIZED VIEW CONCURRENTLY company_latest_metrics;
