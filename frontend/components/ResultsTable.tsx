import React from 'react';
import {
  Box,
  Collapse,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
  Skeleton,
  Stack,
  Alert,
  IconButton
} from '@mui/material';
import { useTopIrr } from '@/providers/TopIrrProvider';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp';

export default function ResultsTable() {
  const { data, loading, error } = useTopIrr();
  const [openTicker, setOpenTicker] = React.useState<string | null>(null);
  const defaultLimit = Number(process.env.NEXT_PUBLIC_TOP_IRR_LIMIT || 25);

  const epsYear =
    (data?.companies || []).length > 0
      ? (() => {
        const years = (data?.companies || [])
          .map((c) => c.eps_year)
          .filter((y): y is number => typeof y === 'number');
        if (years.length === 0) return undefined;
        const first = years[0];
        return years.every((y) => y === first) ? first : undefined;
      })()
      : undefined;

  return (
    <Box className="panel" sx={{ p: 2 }} role="region" aria-label="Top AI-Selected Picks">
      <Typography variant="h6" sx={{ mb: 2 }} component="h2">
        Top {(data?.companies?.length ?? defaultLimit)} AI-Selected Picks
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} role="alert">
          {error}
        </Alert>
      )}

      {loading && (
        <Box sx={{ mb: 4 }}>
          <Alert
            severity="info"
            variant="outlined"
            sx={{
              mb: 3,
              py: 2,
              borderColor: 'primary.main',
              bgcolor: 'rgba(25, 118, 210, 0.05)',
              '& .MuiAlert-icon': { fontSize: '2rem' }
            }}
          >
            <Typography variant="h6" gutterBottom color="primary">
              AI Deep-Dive in Progress
            </Typography>
            <Typography variant="body1" sx={{ maxWidth: '800px' }}>
              Our AI analysts are currently researching hundreds of companies to generate qualitative justifications and IRR predictions.
              <strong> This extensive process typically takes around 30 minutes. </strong>
              Please keep this tab open—the analysis will appear here automatically once completed.
            </Typography>
          </Alert>

          <Table size="small" aria-label="Loading results">
            <TableHead>
              <TableRow>
                <TableCell />
                <TableCell>Ticker</TableCell>
                <TableCell>Sector</TableCell>
                <TableCell align="right">Market Cap</TableCell>
                <TableCell align="right">Current Price</TableCell>
                <TableCell align="right">IRR (3Y Annualized %)</TableCell>
                <TableCell align="right">Exit P/E</TableCell>
                <TableCell align="right">EPS (3Y){epsYear ? ` (${epsYear})` : ''}</TableCell>
                <TableCell align="right">P/E Avg (10Y)</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {Array.from({ length: Math.min(defaultLimit, 15) }, (_, i) => i + 1).map((i) => (
                <TableRow key={i}>
                  <TableCell><Skeleton variant="circular" width={24} height={24} /></TableCell>
                  <TableCell><Skeleton variant="text" width={60} /></TableCell>
                  <TableCell><Skeleton variant="text" width={100} /></TableCell>
                  <TableCell align="right"><Skeleton variant="text" width={80} /></TableCell>
                  <TableCell align="right"><Skeleton variant="text" width={80} /></TableCell>
                  <TableCell align="right"><Skeleton variant="text" width={60} /></TableCell>
                  <TableCell align="right"><Skeleton variant="text" width={60} /></TableCell>
                  <TableCell align="right"><Skeleton variant="text" width={60} /></TableCell>
                  <TableCell align="right"><Skeleton variant="text" width={60} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      )}

      {!loading && !error && (
        <Table size="small" aria-label="Top AI-Selected Picks Results">
          <TableHead>
            <TableRow>
              <TableCell />
              <TableCell>Ticker</TableCell>
              <TableCell>Sector</TableCell>
              <TableCell align="right">Market Cap</TableCell>
              <TableCell align="right">Current Price</TableCell>
              <TableCell align="right">IRR (3Y Annualized %)</TableCell>
              <TableCell align="right">Exit P/E</TableCell>
              <TableCell align="right">EPS (3Y){epsYear ? ` (${epsYear})` : ''}</TableCell>
              <TableCell align="right">P/E Avg (10Y)</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(data?.companies || []).map((row) => {
              const isOpen = openTicker === row.ticker;
              const hasDetails =
                (Array.isArray(row.exit_pe_breakdown) && row.exit_pe_breakdown.length > 0) ||
                !!row.exit_pe_notes ||
                typeof row.exit_pe_confidence === 'number';
              return (
                <React.Fragment key={row.ticker}>
                  <TableRow hover tabIndex={0} role="row">
                    <TableCell padding="checkbox">
                      <IconButton
                        size="small"
                        aria-label={isOpen ? `Collapse ${row.ticker}` : `Expand ${row.ticker}`}
                        onClick={() => setOpenTicker((t) => (t === row.ticker ? null : row.ticker))}
                        disabled={!hasDetails}
                      >
                        {isOpen ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}
                      </IconButton>
                    </TableCell>
                    <TableCell>{row.ticker}</TableCell>
                    <TableCell>{row.sector || '—'}</TableCell>
                    <TableCell align="right">{row.market_cap != null ? row.market_cap.toLocaleString() : '—'}</TableCell>
                    <TableCell align="right">{row.current_price != null ? row.current_price.toFixed(2) : '—'}</TableCell>
                    <TableCell align="right">{row.irr.toFixed(2)}</TableCell>
                    <TableCell align="right">{row.exit_pe != null ? row.exit_pe.toFixed(1) : '—'}</TableCell>
                    <TableCell align="right">{row.eps != null ? row.eps.toFixed(2) : '—'}</TableCell>
                    <TableCell align="right">
                      {(() => {
                        const total = row.pe_avg_10y_years_total;
                        const used = row.pe_avg_10y_years_used;
                        if (typeof total === 'number' && total === 0) return '—';
                        if (typeof total === 'number' && total > 0 && typeof used === 'number' && used === 0) return 'NM';
                        if (row.pe_avg_10y != null) return row.pe_avg_10y.toFixed(1);
                        return '—';
                      })()}
                    </TableCell>
                  </TableRow>

                  <TableRow>
                    <TableCell style={{ paddingBottom: 0, paddingTop: 0 }} colSpan={9}>
                      <Collapse in={isOpen} timeout="auto" unmountOnExit>
                        <Box sx={{ p: 2, bgcolor: 'rgba(255,255,255,0.03)', borderRadius: 2, my: 1 }}>
                          <Typography variant="subtitle1" sx={{ mb: 1 }}>
                            Exit P/E Calculation Breakdown
                          </Typography>

                          <Table size="small" aria-label={`Exit P/E breakdown for ${row.ticker}`}>
                            <TableBody>
                              {(row.exit_pe_breakdown || []).map((it, idx) => (
                                <TableRow key={idx}>
                                  <TableCell sx={{ borderBottom: 'none', color: 'text.secondary', width: '60%' }}>
                                    {it?.label ?? '—'}
                                  </TableCell>
                                  <TableCell sx={{ borderBottom: 'none' }} align="right">
                                    {typeof it?.value === 'number' ? it.value.toFixed(2) : (it?.value ?? '—')}
                                  </TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>

                          {typeof row.exit_pe_confidence === 'number' && (
                            <Typography variant="body2" sx={{ mt: 2 }}>
                              <strong>AI Confidence:</strong> {Math.round(row.exit_pe_confidence * 100)}%
                            </Typography>
                          )}
                          {row.exit_pe_notes && (
                            <Typography variant="body2" sx={{ mt: 1, whiteSpace: 'pre-wrap' }}>
                              <strong>AI Notes:</strong> {row.exit_pe_notes}
                            </Typography>
                          )}
                        </Box>
                      </Collapse>
                    </TableCell>
                  </TableRow>
                </React.Fragment>
              );
            })}
            {(data?.companies || []).length === 0 && (
              <TableRow>
                <TableCell colSpan={8}>
                  <Stack spacing={2} alignItems="center" sx={{ py: 4 }}>
                    <Typography variant="body1" color="text.secondary">
                      No results found
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Try adjusting your filters to see more results
                    </Typography>
                  </Stack>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      )}
    </Box>
  );
}

