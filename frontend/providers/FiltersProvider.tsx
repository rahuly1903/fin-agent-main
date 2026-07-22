import React from 'react';
import { FilterState, defaultFilterState } from '@/types/filters';

type FiltersContextValue = {
  filters: FilterState;
  setFilters: React.Dispatch<React.SetStateAction<FilterState>>;
  reset: () => void;
};

const FiltersContext = React.createContext<FiltersContextValue | undefined>(undefined);

export function FiltersProvider(props: { children: React.ReactNode }) {
  const [filters, setFilters] = React.useState<FilterState>(defaultFilterState);
  const reset = React.useCallback(() => setFilters(defaultFilterState), []);

  const value = React.useMemo(() => ({ filters, setFilters, reset }), [filters, reset]);
  return <FiltersContext.Provider value={value}>{props.children}</FiltersContext.Provider>;
}

export function useFilters() {
  const ctx = React.useContext(FiltersContext);
  if (!ctx) throw new Error('useFilters must be used within FiltersProvider');
  return ctx;
}

