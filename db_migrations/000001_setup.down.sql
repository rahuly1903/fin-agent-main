-- Rollback: Drop all tables and extensions
-- WARNING: This will destroy all data!

DROP TRIGGER IF EXISTS ticker_change_trigger ON companies;
DROP FUNCTION IF EXISTS log_ticker_change();

DROP MATERIALIZED VIEW IF EXISTS company_latest_metrics;

DROP TABLE IF EXISTS ticker_changes;
DROP TABLE IF EXISTS cagr_data;
DROP TABLE IF EXISTS stock_returns;
DROP TABLE IF EXISTS share_prices;
DROP TABLE IF EXISTS financial_metrics;
DROP TABLE IF EXISTS companies;

DROP EXTENSION IF EXISTS "uuid-ossp";
