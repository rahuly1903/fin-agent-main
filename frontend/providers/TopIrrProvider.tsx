'use client';

import React from 'react';
import type { TopIrrResponse } from '@/lib/api';
import { fetchTopIrr } from '@/lib/api';
import { useFilters } from '@/providers/FiltersProvider';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';

const DEFAULT_TOP_IRR_LIMIT = Number(process.env.NEXT_PUBLIC_TOP_IRR_LIMIT || 25);
const TOP_IRR_MAX_RETRIES = Number(process.env.NEXT_PUBLIC_TOP_IRR_MAX_RETRIES || 3);

type TopIrrContextValue = {
  data: TopIrrResponse | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
};

const TopIrrContext = React.createContext<TopIrrContextValue | undefined>(undefined);

export function TopIrrProvider(props: { children: React.ReactNode }) {
  const { filters } = useFilters();
  const debouncedFilters = useDebouncedValue(filters, 500);

  const [data, setData] = React.useState<TopIrrResponse | null>(null);
  const [loading, setLoading] = React.useState<boolean>(true);
  const [error, setError] = React.useState<string | null>(null);

  const [refreshNonce, setRefreshNonce] = React.useState(0);
  const refresh = React.useCallback(() => setRefreshNonce((n) => n + 1), []);

  React.useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    const isRetryable = (msg: string) =>
      msg.includes('Failed to fetch') ||
      msg.includes('NetworkError') ||
      msg.includes('Load failed') ||
      msg.includes('ECONNRESET');

    (async () => {
      const delays = [1500, 3000, 6000, 12000, 20000];
      const maxRetries = Math.max(0, Math.min(TOP_IRR_MAX_RETRIES, delays.length));

      for (let attempt = 0; attempt <= maxRetries; attempt++) {
        try {
          const res = await fetchTopIrr({
            limit: DEFAULT_TOP_IRR_LIMIT,
            horizonYears: 3,
            filters: debouncedFilters,
            signal: controller.signal
          });
          if (controller.signal.aborted) return;
          setData(res);
          setLoading(false);
          return;
        } catch (e: any) {
          if (controller.signal.aborted) return;
          const msg = String(e?.message || 'Failed to load');
          const canRetry = attempt < maxRetries && isRetryable(msg);
          if (!canRetry) {
            setError(msg);
            setLoading(false);
            return;
          }
          await sleep(delays[attempt]);
          if (controller.signal.aborted) return;
        }
      }
    })();

    return () => {
      controller.abort();
    };
  }, [debouncedFilters, refreshNonce]);

  const value = React.useMemo(
    () => ({ data, loading, error, refresh }),
    [data, loading, error, refresh]
  );

  return <TopIrrContext.Provider value={value}>{props.children}</TopIrrContext.Provider>;
}

export function useTopIrr() {
  const ctx = React.useContext(TopIrrContext);
  if (!ctx) throw new Error('useTopIrr must be used within TopIrrProvider');
  return ctx;
}


