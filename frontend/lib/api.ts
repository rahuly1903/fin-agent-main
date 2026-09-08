import type { FilterState } from '@/types/filters';

export type CompanyIrr = {
  ticker: string;
  name?: string | null;
  sector?: string | null;
  country?: string | null;
  market_cap?: number | null;
  current_price?: number | null;
  irr: number; // percent
  exit_pe?: number | null;
  exit_pe_confidence?: number | null; // 0..1
  exit_pe_notes?: string | null;
  exit_pe_breakdown?: Array<{ label?: string; value?: any }> | null;
  eps?: number | null;
  eps_year?: number | null;
  pe_avg_10y?: number | null;
  pe_avg_10y_years_total?: number | null;
  pe_avg_10y_years_used?: number | null;
};

export type TopIrrResponse = {
  companies: CompanyIrr[];
  analysis?: string | null;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || '';
const DEFAULT_TOP_IRR_LIMIT = Number(process.env.NEXT_PUBLIC_TOP_IRR_LIMIT || 25);

export async function fetchTopIrr(params: {
  limit?: number;
  horizonYears?: number;
  filters: FilterState;
  signal?: AbortSignal;
}): Promise<TopIrrResponse> {
  const limit = params.limit ?? DEFAULT_TOP_IRR_LIMIT;
  const horizon = params.horizonYears ?? 3;
  const url = `${API_BASE}/api/top-irr`;
  const body = {
    limit,
    horizon_years: horizon,
    filters: {
      marketCap: params.filters.marketCap,
      country: params.filters.country,
      sectors: params.filters.sectors,
      industries: params.filters.industries,
      salesCagr3yMin: params.filters.salesCagr3yMin,
      epsCagr3yMin: params.filters.epsCagr3yMin,
      fcfCagr3yMin: params.filters.fcfCagr3yMin,
      roeMin: params.filters.roeMin,
      roicMin: params.filters.roicMin,
      ebitMarginMin: params.filters.ebitMarginMin,
      peMax: params.filters.peMax,
      priceToFcfMax: params.filters.priceToFcfMax,
      evToEbitdaMax: params.filters.evToEbitdaMax,
      debtToEbitdaMax: params.filters.debtToEbitdaMax,
      stockReturns5yMin: params.filters.stockReturns5yMin,
      stockReturns10yMin: params.filters.stockReturns10yMin,
      assignedExitMultipleMin: params.filters.assignedExitMultipleMin,
      irrMin: params.filters.irrMin,
      irrMax: params.filters.irrMax
    }
  };
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    cache: 'no-store',
    signal: params.signal,
    body: JSON.stringify(body)
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}


// =============================================================================
// Research Assistant API
// =============================================================================

export type Company = {
  id: string;
  ticker: string;
  name: string | null;
};

export type ResearchEvent = {
  type: 'start' | 'thinking' | 'content' | 'complete' | 'error';
  data: {
    interaction_id?: string;
    thought?: string;
    text?: string;
    report?: string;
  } | string;
};

/**
 * Fetch list of companies for dropdown selector
 */
export async function fetchCompanies(signal?: AbortSignal): Promise<Company[]> {
  const res = await fetch(`${API_BASE}/api/companies`, {
    method: 'GET',
    cache: 'no-store',
    signal
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

/**
 * Stream research events from the deep research API
 * Uses Server-Sent Events for real-time updates
 */
export async function* streamResearch(
  companyId: string,
  analystName?: string,
  signal?: AbortSignal
): AsyncGenerator<ResearchEvent, void, unknown> {
  const res = await fetch(`${API_BASE}/api/research/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ company_id: companyId, analyst_name: analystName || null }),
    signal
  });

  if (!res.ok) {
    throw new Error(`API error ${res.status}`);
  }

  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error('No response body');
  }

  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const event: ResearchEvent = JSON.parse(line.slice(6));
            yield event;
          } catch {
            // Skip malformed events
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/**
 * Generate and download PDF from markdown content
 */
export async function downloadResearchPdf(
  content: string,
  title: string
): Promise<Blob> {
  const res = await fetch(`${API_BASE}/api/research/pdf`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, title })
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ error: 'PDF generation failed' }));
    throw new Error(error.error || 'PDF generation failed');
  }

  return res.blob();
}

// =============================================================================
// Data Upload API
// =============================================================================

export type UploadResponse = {
  success: boolean;
  message: string;
  errors?: string[];
  warnings?: string[];
  companies_inserted?: number;
  metrics_inserted?: number;
  prices_inserted?: number;
  year_range?: number[];
  sheets_found?: string[];
  company_count?: number;
};

/**
 * Upload Exit Multiple Analysis Excel file to replace database data
 */
export async function uploadDataFile(
  file: File,
  onProgress?: (percent: number) => void
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${API_BASE}/api/upload-data`, {
    method: 'POST',
    body: formData
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Upload failed' }));
    throw new Error(error.detail || 'Upload failed');
  }

  return res.json();
}
