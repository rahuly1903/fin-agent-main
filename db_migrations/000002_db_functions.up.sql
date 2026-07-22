-- ============================================================================
-- DYNAMIC METRIC CALCULATION FUNCTIONS
-- ============================================================================
-- These functions calculate metrics on-demand based on time periods
-- No need to store pre-calculated values for fixed periods
-- More flexible, always up-to-date, storage-efficient

-- ============================================================================
-- 1. STOCK RETURN FUNCTIONS
-- ============================================================================

-- Calculate stock return for any time period
CREATE OR REPLACE FUNCTION calculate_stock_return(
    p_company_id UUID,
    p_period INTERVAL,
    p_reference_date DATE DEFAULT CURRENT_DATE
)
RETURNS NUMERIC AS $$
DECLARE
    v_latest_price NUMERIC;
    v_start_price NUMERIC;
    v_return NUMERIC;
BEGIN
    -- Get latest price on or before reference date
    SELECT price INTO v_latest_price
    FROM share_prices
    WHERE company_id = p_company_id 
      AND date <= p_reference_date
    ORDER BY date DESC
    LIMIT 1;
    
    -- Get price at start of period
    SELECT price INTO v_start_price
    FROM share_prices
    WHERE company_id = p_company_id 
      AND date <= p_reference_date - p_period
    ORDER BY date DESC
    LIMIT 1;
    
    -- Calculate return percentage
    IF v_start_price IS NULL OR v_start_price = 0 THEN
        RETURN NULL;
    END IF;
    
    v_return := ((v_latest_price / v_start_price) - 1) * 100;
    RETURN ROUND(v_return, 2);
END;
$$ LANGUAGE plpgsql STABLE;

-- Batch function to get returns for all companies
CREATE OR REPLACE FUNCTION get_stock_returns(
    p_period INTERVAL,
    p_reference_date DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    company_id UUID,
    ticker VARCHAR(50),
    return_pct NUMERIC
) AS $$
BEGIN
    RETURN QUERY
    WITH price_points AS (
        SELECT DISTINCT ON (sp.company_id)
            sp.company_id,
            first_value(sp.price) OVER (PARTITION BY sp.company_id ORDER BY sp.date DESC) AS latest_price,
            first_value(sp.price) OVER (PARTITION BY sp.company_id ORDER BY ABS(EXTRACT(EPOCH FROM (sp.date - (p_reference_date - p_period)))) ASC) AS start_price
        FROM share_prices sp
        WHERE sp.date <= p_reference_date
    )
    SELECT 
        pp.company_id,
        c.ticker,
        ROUND((((pp.latest_price / NULLIF(pp.start_price, 0)) - 1) * 100)::numeric, 2) AS return_pct
    FROM price_points pp
    JOIN companies c ON pp.company_id = c.id
    WHERE pp.start_price IS NOT NULL AND pp.start_price > 0;
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================================================
-- 2. CAGR CALCULATION FUNCTIONS
-- ============================================================================

-- Calculate CAGR for any metric over any period
CREATE OR REPLACE FUNCTION calculate_cagr(
    p_company_id UUID,
    p_metric_name VARCHAR(50),
    p_years INTEGER,
    p_end_year INTEGER DEFAULT EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER
)
RETURNS NUMERIC AS $$
DECLARE
    v_end_value NUMERIC;
    v_start_value NUMERIC;
    v_cagr NUMERIC;
BEGIN
    -- Get ending value
    EXECUTE format('SELECT %I FROM financial_metrics WHERE company_id = $1 AND period = $2', p_metric_name)
    INTO v_end_value
    USING p_company_id, p_end_year;
    
    -- Get starting value
    EXECUTE format('SELECT %I FROM financial_metrics WHERE company_id = $1 AND period = $2', p_metric_name)
    INTO v_start_value
    USING p_company_id, p_end_year - p_years;
    
    -- Calculate CAGR
    IF v_start_value IS NULL OR v_start_value <= 0 THEN
        RETURN NULL;
    END IF;
    
    v_cagr := (POWER(v_end_value / v_start_value, 1.0 / p_years) - 1) * 100;
    RETURN ROUND(v_cagr, 2);
END;
$$ LANGUAGE plpgsql STABLE;

-- Batch CAGR for all companies
CREATE OR REPLACE FUNCTION get_metric_cagr(
    p_metric_name VARCHAR(50),
    p_years INTEGER,
    p_end_year INTEGER DEFAULT EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER
)
RETURNS TABLE (
    company_id UUID,
    ticker VARCHAR(50),
    cagr_pct NUMERIC
) AS $$
BEGIN
    RETURN QUERY
    EXECUTE format('
        WITH metric_data AS (
            SELECT 
                company_id,
                MAX(CASE WHEN period = $1 THEN %I END) AS end_value,
                MAX(CASE WHEN period = $2 THEN %I END) AS start_value
            FROM financial_metrics
            WHERE period IN ($1, $2)
            GROUP BY company_id
        )
        SELECT 
            md.company_id,
            c.ticker,
            ROUND(((POWER(md.end_value / NULLIF(md.start_value, 0), 1.0 / $3) - 1) * 100)::NUMERIC, 2) AS cagr_pct
        FROM metric_data md
        JOIN companies c ON md.company_id = c.id
        WHERE md.start_value IS NOT NULL AND md.start_value > 0
    ', p_metric_name, p_metric_name)
    USING p_end_year, p_end_year - p_years, p_years;
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================================================
-- 3. PRICE STATISTICS FUNCTIONS
-- ============================================================================

-- Get 52-week high/low and current position
CREATE OR REPLACE FUNCTION get_price_statistics(
    p_company_id UUID,
    p_reference_date DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    current_price NUMERIC,
    week_52_high NUMERIC,
    week_52_low NUMERIC,
    pct_off_high NUMERIC,
    pct_above_low NUMERIC
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        (SELECT price FROM share_prices 
         WHERE company_id = p_company_id AND date <= p_reference_date 
         ORDER BY date DESC LIMIT 1) AS current_price,
        MAX(price) AS week_52_high,
        MIN(price) AS week_52_low,
        ROUND((((SELECT price FROM share_prices 
                WHERE company_id = p_company_id AND date <= p_reference_date 
                ORDER BY date DESC LIMIT 1) / MAX(price) - 1) * 100)::numeric, 2) AS pct_off_high,
        ROUND((((SELECT price FROM share_prices 
                WHERE company_id = p_company_id AND date <= p_reference_date 
                ORDER BY date DESC LIMIT 1) / MIN(price) - 1) * 100)::numeric, 2) AS pct_above_low
    FROM share_prices
    WHERE company_id = p_company_id 
      AND date BETWEEN p_reference_date - INTERVAL '52 weeks' AND p_reference_date;
END;
$$ LANGUAGE plpgsql STABLE;

-- Calculate price volatility (standard deviation of returns)
CREATE OR REPLACE FUNCTION calculate_volatility(
    p_company_id UUID,
    p_period INTERVAL DEFAULT INTERVAL '1 year',
    p_reference_date DATE DEFAULT CURRENT_DATE
)
RETURNS NUMERIC AS $$
BEGIN
    RETURN (
        SELECT ROUND(STDDEV((price - LAG(price) OVER (ORDER BY date)) / LAG(price) OVER (ORDER BY date) * 100)::numeric, 2)
        FROM share_prices
        WHERE company_id = p_company_id
          AND date BETWEEN p_reference_date - p_period AND p_reference_date
    );
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================================================
-- 4. YEAR-OVER-YEAR GROWTH FUNCTIONS
-- ============================================================================

-- YoY growth for any metric
CREATE OR REPLACE FUNCTION calculate_yoy_growth(
    p_company_id UUID,
    p_metric_name VARCHAR(50),
    p_year INTEGER
)
RETURNS NUMERIC AS $$
DECLARE
    v_current_value NUMERIC;
    v_prior_value NUMERIC;
    v_growth NUMERIC;
BEGIN
    EXECUTE format('SELECT %I FROM financial_metrics WHERE company_id = $1 AND period = $2', p_metric_name)
    INTO v_current_value
    USING p_company_id, p_year;
    
    EXECUTE format('SELECT %I FROM financial_metrics WHERE company_id = $1 AND period = $2', p_metric_name)
    INTO v_prior_value
    USING p_company_id, p_year - 1;
    
    IF v_prior_value IS NULL OR v_prior_value = 0 THEN
        RETURN NULL;
    END IF;
    
    v_growth := ((v_current_value / v_prior_value) - 1) * 100;
    RETURN ROUND(v_growth, 2);
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================================================
-- 5. SECTOR COMPARISON FUNCTIONS
-- ============================================================================

-- Get metric percentile within sector
CREATE OR REPLACE FUNCTION get_sector_percentile(
    p_company_id UUID,
    p_metric_name VARCHAR(50),
    p_year INTEGER DEFAULT EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER
)
RETURNS NUMERIC AS $$
DECLARE
    v_sector VARCHAR(100);
    v_percentile NUMERIC;
BEGIN
    -- Get company sector
    SELECT sector INTO v_sector FROM companies WHERE id = p_company_id;
    
    -- Calculate percentile
    EXECUTE format('
        SELECT ROUND((PERCENT_RANK() OVER (ORDER BY %I) * 100)::numeric, 1)
        FROM financial_metrics fm
        JOIN companies c ON fm.company_id = c.id
        WHERE c.sector = $1 AND fm.period = $2 AND fm.company_id = $3
    ', p_metric_name)
    INTO v_percentile
    USING v_sector, p_year, p_company_id;
    
    RETURN v_percentile;
END;
$$ LANGUAGE plpgsql STABLE;

-- Get sector median/average for comparison
CREATE OR REPLACE FUNCTION get_sector_stats(
    p_sector VARCHAR(100),
    p_metric_name VARCHAR(50),
    p_year INTEGER DEFAULT EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER
)
RETURNS TABLE (
    metric_median NUMERIC,
    metric_avg NUMERIC,
    metric_stddev NUMERIC,
    company_count INTEGER
) AS $$
BEGIN
    RETURN QUERY
    EXECUTE format('
        SELECT 
            (PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY %I))::numeric AS metric_median,
            (AVG(%I))::numeric AS metric_avg,
            (STDDEV(%I))::numeric AS metric_stddev,
            COUNT(*)::INTEGER AS company_count
        FROM financial_metrics fm
        JOIN companies c ON fm.company_id = c.id
        WHERE c.sector = $1 AND fm.period = $2
    ', p_metric_name, p_metric_name, p_metric_name)
    USING p_sector, p_year;
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================================================
-- 6. COMPOSITE SCORE FUNCTIONS
-- ============================================================================

-- Quality Score (ROE + ROIC + Margin - Debt)
CREATE OR REPLACE FUNCTION calculate_quality_score(
    p_company_id UUID,
    p_year INTEGER DEFAULT EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER
)
RETURNS NUMERIC AS $$
BEGIN
    RETURN (
        /*
          Outlier-robust quality score (0..100).
          - Saturates ROE at 40% and ROIC at 30% (anything higher gets full points).
          - Saturates EBIT margin at 30% (anything higher gets full points).
          - Debt/EBITDA: full points at 0x, zero points at >=5x.
          - Handles common unit mismatch: if a metric is stored as a decimal (0.20), treat it as 20%;
            if stored as a percent (20), use it as-is. (Heuristic: abs(x) <= 2 => decimal.)
        */
        SELECT ROUND((
            -- ROE component (0..25), saturates at 40%
            COALESCE(
                LEAST(
                    GREATEST(
                        (CASE WHEN ABS(roe) <= 2 THEN roe * 100 ELSE roe END),
                        0
                    ),
                    40
                ) / 40.0,
                0
            ) * 25
            +
            -- ROIC component (0..25), saturates at 30%
            COALESCE(
                LEAST(
                    GREATEST(
                        (CASE WHEN ABS(roic) <= 2 THEN roic * 100 ELSE roic END),
                        0
                    ),
                    30
                ) / 30.0,
                0
            ) * 25
            +
            -- EBIT margin component (0..25), saturates at 30%
            COALESCE(
                LEAST(
                    GREATEST(
                        (CASE WHEN ABS(ebit_margin) <= 2 THEN ebit_margin ELSE ebit_margin / 100 END),
                        0
                    ),
                    0.30
                ) / 0.30,
                0
            ) * 25
            +
            -- Low debt component (0..25), saturates at 0..5x
            COALESCE(
                (1 - LEAST(GREATEST(debt_ebitda, 0) / 5.0, 1)),
                0
            ) * 25
        )::numeric, 2) AS quality_score
        FROM financial_metrics
        WHERE company_id = p_company_id AND period = p_year
    );
END;
$$ LANGUAGE plpgsql STABLE;

-- Growth Score (EPS CAGR + FCF CAGR)
CREATE OR REPLACE FUNCTION calculate_growth_score(
    p_company_id UUID,
    p_years INTEGER DEFAULT 3,
    p_end_year INTEGER DEFAULT EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER
)
RETURNS NUMERIC AS $$
DECLARE
    v_eps_cagr NUMERIC;
    v_fcf_cagr NUMERIC;
BEGIN
    v_eps_cagr := calculate_cagr(p_company_id, 'eps', p_years, p_end_year);
    v_fcf_cagr := calculate_cagr(p_company_id, 'fcf', p_years, p_end_year);
    
    RETURN ROUND(
        COALESCE(LEAST(v_eps_cagr / 20.0, 1), 0) * 50 +  -- EPS growth (cap at 20%)
        COALESCE(LEAST(v_fcf_cagr / 20.0, 1), 0) * 50,   -- FCF growth
        2
    );
END;
$$ LANGUAGE plpgsql STABLE;

-- Value Score (Low PE + Low P/FCF + High FCF Yield)
CREATE OR REPLACE FUNCTION calculate_value_score(
    p_company_id UUID,
    p_year INTEGER DEFAULT EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER
)
RETURNS NUMERIC AS $$
BEGIN
    RETURN (
        SELECT ROUND((
            COALESCE((1 - LEAST(pe_ratio / 30.0, 1)), 0) * 40 +  -- Low PE is good
            COALESCE((1 - LEAST(p_fcf_ratio / 25.0, 1)), 0) * 40 + -- Low P/FCF is good
            COALESCE(LEAST((1.0 / p_fcf_ratio) / 0.1, 1), 0) * 20  -- High FCF yield is good
        )::numeric, 2) AS value_score
        FROM financial_metrics
        WHERE company_id = p_company_id AND period = p_year
    );
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================================================
-- EXAMPLE USAGE
-- ============================================================================

-- Average trailing P/E over the last 10 completed years.
-- Example: in 2026, compute over 2016-2025 (skip 2026 as in-progress).
CREATE OR REPLACE FUNCTION calculate_pe_avg_10y(
    p_company_id UUID,
    p_reference_year INTEGER DEFAULT (EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER - 1)
)
RETURNS NUMERIC AS $$
BEGIN
    RETURN (
        SELECT ROUND(AVG(fm.pe_ratio)::numeric, 2)
        FROM financial_metrics fm
        WHERE fm.company_id = p_company_id
          AND fm.pe_ratio IS NOT NULL
          AND fm.period BETWEEN (p_reference_year - 9) AND p_reference_year
    );
END;
$$ LANGUAGE plpgsql STABLE;

-- 1. Get 3-month return for AAPL
-- SELECT calculate_stock_return(
--     (SELECT id FROM companies WHERE ticker = 'AAPL UW Equity'),
--     INTERVAL '3 months'
-- );

-- 2. Get all companies with 5-year returns
-- SELECT * FROM get_stock_returns(INTERVAL '5 years');

-- 3. Get 3-year EPS CAGR for all companies
-- SELECT * FROM get_metric_cagr('eps', 3, 2023);

-- 4. Get YoY EPS growth for AAPL in 2023
-- SELECT calculate_yoy_growth(
--     (SELECT id FROM companies WHERE ticker = 'AAPL UW Equity'),
--     'eps',
--     2023
-- );

-- 5. Get AAPL's ROE percentile in Technology sector
-- SELECT get_sector_percentile(
--     (SELECT id FROM companies WHERE ticker = 'AAPL UW Equity'),
--     'roe',
--     2023
-- );

-- 6. Multi-metric query
-- SELECT 
--     c.ticker,
--     c.name,
--     calculate_stock_return(c.id, INTERVAL '1 year') AS return_1y,
--     calculate_cagr(c.id, 'eps', 3, 2023) AS eps_cagr_3y,
--     calculate_quality_score(c.id, 2023) AS quality_score,
--     get_sector_percentile(c.id, 'roe', 2023) AS roe_percentile
-- FROM companies c
-- WHERE c.sector = 'Technology'
-- ORDER BY quality_score DESC
-- LIMIT 20;
