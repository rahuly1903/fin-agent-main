import React from 'react';
import { Box, Typography, Skeleton, Stack, Alert } from '@mui/material';
import { useTopIrr } from '@/providers/TopIrrProvider';

export default function AnalysisPanel() {
  const { data, loading, error } = useTopIrr();

  return (
    <Box className="panel" sx={{ p: 2 }} role="region" aria-label="AI Trend Analysis">
      <Typography variant="h6" sx={{ mb: 1 }} component="h2">AI Trend Analysis</Typography>
      
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} role="alert">
          {error}
        </Alert>
      )}
      
      {loading && (
        <Stack spacing={1}>
          <Skeleton variant="text" width="100%" height={20} />
          <Skeleton variant="text" width="100%" height={20} />
          <Skeleton variant="text" width="90%" height={20} />
          <Skeleton variant="text" width="100%" height={20} />
          <Skeleton variant="text" width="85%" height={20} />
        </Stack>
      )}
      
      {!loading && !error && (
        <Typography 
          variant="body2" 
          sx={{ whiteSpace: 'pre-wrap' }}
          role="article"
          aria-label="Analysis content"
        >
          {data?.analysis || (
            <Box sx={{ py: 2, textAlign: 'center', color: 'text.secondary' }}>
              <Typography variant="body2">
                No analysis available. Adjust your filters to generate analysis.
              </Typography>
            </Box>
          )}
        </Typography>
      )}
    </Box>
  );
}

