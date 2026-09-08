export type NumericRange = {
  min?: number | null;
  max?: number | null;
};

export type CountryFilter = {
  selection: string[];
  excludeMode: boolean;
};

export type FilterState = {
  // Market & Geography
  marketCap: NumericRange; // in USD Bn
  country: CountryFilter;

  // Historical CAGRs (Past 3y) - percentage minimums
  salesCagr3yMin?: number | null;
  ebitCagr3yMin?: number | null;
  patCagr3yMin?: number | null;
  epsCagr3yMin?: number | null;
  fcfCagr3yMin?: number | null;

  // Forward CAGRs (Next 3y) - percentage minimums
  salesCagrFwd3yMin?: number | null;
  ebitCagrFwd3yMin?: number | null;
  patCagrFwd3yMin?: number | null;
  epsCagrFwd3yMin?: number | null;
  fcfCagrFwd3yMin?: number | null;

  // Profitability & Returns
  roeMin?: number | null;
  roicMin?: number | null;
  ebitMarginMin?: number | null;

  // Valuation Metrics - maximums
  peMax?: number | null;
  priceToFcfMax?: number | null;
  evToEbitdaMax?: number | null;

  // Capital Structure
  debtToEbitdaMax?: number | null;

  // Sector / Industry
  sectors: string[];
  industries: string[];

  // Performance History
  stockReturns5yMin?: number | null;
  stockReturns10yMin?: number | null;

  // Model-Driven Outputs
  assignedExitMultipleMin?: number | null;
  irrMin?: number | null;
  irrMax?: number | null;
};

export const defaultFilterState: FilterState = {
  marketCap: {},
  country: { selection: [], excludeMode: false },
  salesCagr3yMin: null,
  ebitCagr3yMin: null,
  patCagr3yMin: null,
  epsCagr3yMin: null,
  fcfCagr3yMin: null,
  salesCagrFwd3yMin: null,
  ebitCagrFwd3yMin: null,
  patCagrFwd3yMin: null,
  epsCagrFwd3yMin: null,
  fcfCagrFwd3yMin: null,
  roeMin: null,
  roicMin: null,
  ebitMarginMin: null,
  peMax: null,
  priceToFcfMax: null,
  evToEbitdaMax: null,
  debtToEbitdaMax: null,
  sectors: [],
  industries: [],
  stockReturns5yMin: null,
  stockReturns10yMin: null,
  assignedExitMultipleMin: null,
  irrMin: null,
  irrMax: null
};

