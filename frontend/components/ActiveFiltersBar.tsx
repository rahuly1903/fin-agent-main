import React from 'react';
import { Box, Chip, Stack, Tooltip, Typography } from '@mui/material';
import { useFilters } from '@/providers/FiltersProvider';
import { FilterState } from '@/types/filters';

function buildChips(filters: FilterState, handleDelete: (key: string) => void) {
  const chips: { key: string; label: string; onDelete: () => void }[] = [];

  const push = (key: string, label: string) => chips.push({ key, label, onDelete: () => handleDelete(key) });

  if (filters.marketCap.min != null || filters.marketCap.max != null) {
    const min = filters.marketCap.min != null ? `${filters.marketCap.min} B` : '';
    const max = filters.marketCap.max != null ? `${filters.marketCap.max} B` : '';
    push('marketCap', `Market Cap ${min && '≥ ' + min}${min && max ? ' • ' : ''}${max && '≤ ' + max}`);
  }

  if (filters.country.selection.length > 0) {
    const mode = filters.country.excludeMode ? '≠' : '=';
    push('country', `Country ${mode} ${filters.country.selection.join(', ')}`);
  }

  const pct = (v?: number | null) => (v != null ? `${v}%` : '');
  const val = (v?: number | null) => (v != null ? `${v}x` : '');

  const numericMins: Array<[keyof FilterState, string]> = [
    ['salesCagr3yMin', 'Sales CAGR (3y) >'],
    ['ebitCagr3yMin', 'EBIT CAGR (3y) >'],
    ['patCagr3yMin', 'PAT CAGR (3y) >'],
    ['epsCagr3yMin', 'EPS CAGR (3y) >'],
    ['fcfCagr3yMin', 'FCF CAGR (3y) >'],
    ['salesCagrFwd3yMin', 'Sales CAGR (Fwd 3y) >'],
    ['ebitCagrFwd3yMin', 'EBIT CAGR (Fwd 3y) >'],
    ['patCagrFwd3yMin', 'PAT CAGR (Fwd 3y) >'],
    ['epsCagrFwd3yMin', 'EPS CAGR (Fwd 3y) >'],
    ['fcfCagrFwd3yMin', 'FCF CAGR (Fwd 3y) >'],
    ['roeMin', 'ROE >'],
    ['roicMin', 'ROIC >'],
    ['ebitMarginMin', 'EBIT Margin >'],
    ['stockReturns5yMin', '5Y Returns >'],
    ['stockReturns10yMin', '10Y Returns >'],
    ['assignedExitMultipleMin', 'Assigned Exit Multiple >'],
    ['irrMin', 'IRR >']
  ];
  numericMins.forEach(([k, label]) => {
    const value = filters[k] as number | null | undefined;
    if (value != null) {
      const suffix = ['assignedExitMultipleMin'].includes(k as string) ? val(value) : pct(value);
      push(k as string, `${label} ${suffix}`);
    }
  });

  const numericMaxes: Array<[keyof FilterState, string, 'x' | '%']> = [
    ['peMax', 'P/E <', 'x'],
    ['priceToFcfMax', 'Price/FCF <', 'x'],
    ['evToEbitdaMax', 'EV/EBITDA <', 'x'],
    ['debtToEbitdaMax', 'Debt/EBITDA <', 'x'],
    ['irrMax', 'IRR ≤', '%']
  ];
  numericMaxes.forEach(([k, label, suffix]) => {
    const value = filters[k] as number | null | undefined;
    if (value != null) {
      push(k as string, `${label} ${value}${suffix}`);
    }
  });

  if (filters.sectors.length > 0) {
    push('sectors', `Sectors: ${filters.sectors.join(', ')}`);
  }
  if (filters.industries.length > 0) {
    push('industries', `Industries: ${filters.industries.join(', ')}`);
  }

  return chips;
}

export default function ActiveFiltersBar() {
  const { filters, setFilters, reset } = useFilters();

  const handleDelete = React.useCallback((key: string) => {
    setFilters((prev) => {
      const draft = { ...prev };
      switch (key) {
        case 'marketCap':
          draft.marketCap = {};
          break;
        case 'country':
          draft.country = { selection: [], excludeMode: false };
          break;
        case 'sectors':
          draft.sectors = [];
          break;
        case 'industries':
          draft.industries = [];
          break;
        default:
          // clear numeric single value keys
          // @ts-expect-error dynamic index
          draft[key] = null;
      }
      return draft;
    });
  }, [setFilters]);

  const chips = React.useMemo(() => buildChips(filters, handleDelete), [filters, handleDelete]);

  if (chips.length === 0) {
    return (
      <Box sx={{ px: 2, py: 1 }}>
        <Typography variant="body2" color="text.secondary">No active filters</Typography>
      </Box>
    );
  }

  return (
    <Box 
      role="region" 
      aria-label="Active filters"
      sx={{ px: 2, py: 1 }}
    >
      <Stack 
        direction="row" 
        gap={1} 
        flexWrap="wrap"
        role="list"
        aria-label="Active filter chips"
      >
        {chips.map((c) => (
          <Tooltip title={c.label} key={c.key}>
            <Chip
              label={c.label}
              color="primary"
              variant="outlined"
              onDelete={() => handleDelete(c.key)}
              onClick={() => handleDelete(c.key)}
              sx={{ borderColor: 'primary.main', cursor: 'pointer' }}
              role="listitem"
              aria-label={`Remove filter: ${c.label}`}
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  handleDelete(c.key);
                }
              }}
            />
          </Tooltip>
        ))}
        <Chip 
          label="Clear All" 
          onClick={reset} 
          color="secondary" 
          variant="filled"
          role="button"
          aria-label="Clear all active filters"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              reset();
            }
          }}
        />
      </Stack>
    </Box>
  );
}

