import React from 'react';
import Section from './Section';
import { Box, Button, Divider, FormControlLabel, Grid, Switch, TextField, Typography, Autocomplete } from '@mui/material';
import { useFilters } from '@/providers/FiltersProvider';
import { COUNTRY_OPTIONS, SECTOR_OPTIONS, INDUSTRY_OPTIONS } from '@/lib/options';
import MinMaxField from './MinMaxField';
import NumericField from './NumericField';

export default function FilterPanel() {
  const { filters, setFilters, reset } = useFilters();

  return (
    <Box className="panel" sx={{ p: 2 }} role="region" aria-label="Filter Panel">
      <Typography variant="h6" sx={{ mb: 2 }} component="h2">Filters</Typography>

      <Section title="Market & Geography" defaultExpanded>
        <Grid container spacing={2}>
          <Grid item xs={12}>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>Market Cap (USD Bn)</Typography>
            <MinMaxField
              range={filters.marketCap}
              onChange={(range) => setFilters((f) => ({ ...f, marketCap: range }))}
              adornment="B"
              step={0.1}
            />
          </Grid>
          <Grid item xs={12}>
            <FormControlLabel
              control={<Switch checked={filters.country.excludeMode} onChange={(_, c) => setFilters((f) => ({ ...f, country: { ...f.country, excludeMode: c } }))} />}
              label={filters.country.excludeMode ? 'Exclude selected countries' : 'Include only selected countries'}
            />
            <Autocomplete
              multiple
              options={COUNTRY_OPTIONS}
              value={filters.country.selection}
              onChange={(_, value) => setFilters((f) => ({ ...f, country: { ...f.country, selection: value } }))}
              renderInput={(params) => <TextField {...params} label="Countries" size="small" aria-label="Select countries" />}
              sx={{ mt: 1 }}
              aria-label="Country filter"
            />
          </Grid>
        </Grid>
      </Section>

      <Section title="Historical CAGRs (Past 3 Years)">
        <Grid container spacing={1.5}>
          <Grid item xs={6}><NumericField label="Sales CAGR >" value={filters.salesCagr3yMin} onChange={(v) => setFilters((f) => ({ ...f, salesCagr3yMin: v }))} adornment="%" step={0.5} /></Grid>
          <Grid item xs={6}><NumericField label="EBIT CAGR >" value={filters.ebitCagr3yMin} onChange={(v) => setFilters((f) => ({ ...f, ebitCagr3yMin: v }))} adornment="%" step={0.5} /></Grid>
          <Grid item xs={6}><NumericField label="PAT CAGR >" value={filters.patCagr3yMin} onChange={(v) => setFilters((f) => ({ ...f, patCagr3yMin: v }))} adornment="%" step={0.5} /></Grid>
          <Grid item xs={6}><NumericField label="EPS CAGR >" value={filters.epsCagr3yMin} onChange={(v) => setFilters((f) => ({ ...f, epsCagr3yMin: v }))} adornment="%" step={0.5} /></Grid>
          <Grid item xs={6}><NumericField label="FCF CAGR >" value={filters.fcfCagr3yMin} onChange={(v) => setFilters((f) => ({ ...f, fcfCagr3yMin: v }))} adornment="%" step={0.5} /></Grid>
        </Grid>
      </Section>

      <Section title="Forward CAGRs (Next 3 Years)">
        <Grid container spacing={1.5}>
          <Grid item xs={6}><NumericField label="Sales CAGR >" value={filters.salesCagrFwd3yMin} onChange={(v) => setFilters((f) => ({ ...f, salesCagrFwd3yMin: v }))} adornment="%" step={0.5} /></Grid>
          <Grid item xs={6}><NumericField label="EBIT CAGR >" value={filters.ebitCagrFwd3yMin} onChange={(v) => setFilters((f) => ({ ...f, ebitCagrFwd3yMin: v }))} adornment="%" step={0.5} /></Grid>
          <Grid item xs={6}><NumericField label="PAT CAGR >" value={filters.patCagrFwd3yMin} onChange={(v) => setFilters((f) => ({ ...f, patCagrFwd3yMin: v }))} adornment="%" step={0.5} /></Grid>
          <Grid item xs={6}><NumericField label="EPS CAGR >" value={filters.epsCagrFwd3yMin} onChange={(v) => setFilters((f) => ({ ...f, epsCagrFwd3yMin: v }))} adornment="%" step={0.5} /></Grid>
          <Grid item xs={6}><NumericField label="FCF CAGR >" value={filters.fcfCagrFwd3yMin} onChange={(v) => setFilters((f) => ({ ...f, fcfCagrFwd3yMin: v }))} adornment="%" step={0.5} /></Grid>
        </Grid>
      </Section>

      <Section title="Profitability & Returns">
        <Grid container spacing={1.5}>
          <Grid item xs={4}><NumericField label="ROE >" value={filters.roeMin} onChange={(v) => setFilters((f) => ({ ...f, roeMin: v }))} adornment="%" step={0.5} /></Grid>
          <Grid item xs={4}><NumericField label="ROIC >" value={filters.roicMin} onChange={(v) => setFilters((f) => ({ ...f, roicMin: v }))} adornment="%" step={0.5} /></Grid>
          <Grid item xs={4}><NumericField label="EBIT Margin >" value={filters.ebitMarginMin} onChange={(v) => setFilters((f) => ({ ...f, ebitMarginMin: v }))} adornment="%" step={0.5} /></Grid>
        </Grid>
      </Section>

      <Section title="Valuation Metrics">
        <Grid container spacing={1.5}>
          <Grid item xs={4}><NumericField label="P/E <" value={filters.peMax} onChange={(v) => setFilters((f) => ({ ...f, peMax: v }))} adornment="x" step={0.1} /></Grid>
          <Grid item xs={4}><NumericField label="Price/FCF <" value={filters.priceToFcfMax} onChange={(v) => setFilters((f) => ({ ...f, priceToFcfMax: v }))} adornment="x" step={0.1} /></Grid>
          <Grid item xs={4}><NumericField label="EV/EBITDA <" value={filters.evToEbitdaMax} onChange={(v) => setFilters((f) => ({ ...f, evToEbitdaMax: v }))} adornment="x" step={0.1} /></Grid>
        </Grid>
      </Section>

      <Section title="Capital Structure">
        <Grid container spacing={1.5}>
          <Grid item xs={6}><NumericField label="Debt/EBITDA <" value={filters.debtToEbitdaMax} onChange={(v) => setFilters((f) => ({ ...f, debtToEbitdaMax: v }))} adornment="x" step={0.1} /></Grid>
        </Grid>
      </Section>

      <Section title="Sector / Industry">
        <Grid container spacing={1.5}>
          <Grid item xs={12}>
            <Autocomplete
              multiple
              options={SECTOR_OPTIONS}
              value={filters.sectors}
              onChange={(_, value) => setFilters((f) => ({ ...f, sectors: value }))}
              renderInput={(params) => <TextField {...params} label="Sector(s)" size="small" aria-label="Select sectors" />}
              aria-label="Sector filter"
            />
          </Grid>
          <Grid item xs={12}>
            <Autocomplete
              multiple
              options={INDUSTRY_OPTIONS}
              value={filters.industries}
              onChange={(_, value) => setFilters((f) => ({ ...f, industries: value }))}
              renderInput={(params) => <TextField {...params} label="Industry(ies)" size="small" aria-label="Select industries" />}
              aria-label="Industry filter"
            />
          </Grid>
        </Grid>
      </Section>

      <Section title="Performance History">
        <Grid container spacing={1.5}>
          <Grid item xs={6}><NumericField label="Last 5Y Returns >" value={filters.stockReturns5yMin} onChange={(v) => setFilters((f) => ({ ...f, stockReturns5yMin: v }))} adornment="%" step={0.5} /></Grid>
          <Grid item xs={6}><NumericField label="Last 10Y Returns >" value={filters.stockReturns10yMin} onChange={(v) => setFilters((f) => ({ ...f, stockReturns10yMin: v }))} adornment="%" step={0.5} /></Grid>
        </Grid>
      </Section>

      <Section title="Model-Driven Outputs">
        <Grid container spacing={1.5}>
          <Grid item xs={6}><NumericField label="Assigned Exit Multiple >" value={filters.assignedExitMultipleMin} onChange={(v) => setFilters((f) => ({ ...f, assignedExitMultipleMin: v }))} adornment="x" step={0.1} /></Grid>
          <Grid item xs={6}><NumericField label="IRR >" value={filters.irrMin} onChange={(v) => setFilters((f) => ({ ...f, irrMin: v }))} adornment="%" step={0.1} /></Grid>
          <Grid item xs={6}><NumericField label="IRR ≤" value={filters.irrMax} onChange={(v) => setFilters((f) => ({ ...f, irrMax: v }))} adornment="%" step={0.1} /></Grid>
        </Grid>
      </Section>

      <Divider sx={{ my: 2 }} />
      <Grid container spacing={1.5}>
        <Grid item xs={12}>
          <Button 
            fullWidth 
            color="secondary" 
            onClick={reset}
            aria-label="Clear all filters"
          >
            Clear All Filters
          </Button>
        </Grid>
      </Grid>
    </Box>
  );
}

