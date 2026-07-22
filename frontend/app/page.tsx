'use client';

import React from 'react';
import { Box, Grid } from '@mui/material';
import { FiltersProvider } from '@/providers/FiltersProvider';
import { TopIrrProvider } from '@/providers/TopIrrProvider';
import FilterPanel from '@/components/filters/FilterPanel';
import ActiveFiltersBar from '@/components/ActiveFiltersBar';
import ResultsTable from '@/components/ResultsTable';

export default function Page() {
  return (
    <FiltersProvider>
      <TopIrrProvider>
        <Box className="container">
          <Box>
            <FilterPanel />
          </Box>
          <Box sx={{ display: 'grid', gap: 1 }}>
            <ActiveFiltersBar />
            <ResultsTable />
          </Box>
        </Box>
      </TopIrrProvider>
    </FiltersProvider>
  );
}
