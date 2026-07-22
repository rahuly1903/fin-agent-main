'use client';

import React from 'react';
import { Box, Typography, Paper } from '@mui/material';
import DataUpload from '@/components/DataUpload';

export default function UploadPage() {
    return (
        <Box sx={{ maxWidth: 800, mx: 'auto' }}>
            <Typography variant="h4" gutterBottom sx={{ fontWeight: 600, mb: 3 }}>
                Data Management
            </Typography>

            <Paper
                elevation={0}
                sx={{
                    p: 3,
                    borderRadius: 2,
                    border: '1px solid rgba(255,255,255,0.08)',
                    backgroundColor: 'rgba(255,255,255,0.02)'
                }}
            >
                <Typography variant="body1" color="text.secondary" paragraph>
                    Upload Excel files containing Exit Multiple Analysis data to update the database.
                    The uploaded data will replace all existing data.
                </Typography>

                <DataUpload />
            </Paper>
        </Box>
    );
}
