'use client';

import React, { useState, useCallback, useRef } from 'react';
import {
    Box,
    Button,
    Typography,
    Paper,
    LinearProgress,
    Alert,
    AlertTitle,
    Chip,
    Stack,
    IconButton,
    Collapse
} from '@mui/material';
import CloudUploadIcon from '@mui/icons-material/CloudUpload';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import { uploadDataFile, UploadResponse } from '@/lib/api';

type UploadStatus = 'idle' | 'uploading' | 'success' | 'error';

export default function DataUpload() {
    const [status, setStatus] = useState<UploadStatus>('idle');
    const [result, setResult] = useState<UploadResponse | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [dragActive, setDragActive] = useState(false);
    const [showDetails, setShowDetails] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleFile = useCallback(async (file: File) => {
        // Validate file extension client-side
        const validExtensions = ['.xlsx', '.xls'];
        const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
        if (!validExtensions.includes(ext)) {
            setError('Invalid file type. Please upload an Excel file (.xlsx or .xls)');
            setStatus('error');
            return;
        }

        setStatus('uploading');
        setError(null);
        setResult(null);

        try {
            const response = await uploadDataFile(file);
            setResult(response);
            setStatus(response.success ? 'success' : 'error');
            if (!response.success) {
                setError(response.message);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Upload failed');
            setStatus('error');
        }
    }, []);

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setDragActive(false);

        const file = e.dataTransfer.files[0];
        if (file) handleFile(file);
    }, [handleFile]);

    const handleDragOver = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setDragActive(true);
    }, []);

    const handleDragLeave = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setDragActive(false);
    }, []);

    const handleClick = () => {
        fileInputRef.current?.click();
    };

    const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (file) handleFile(file);
    };

    const resetUpload = () => {
        setStatus('idle');
        setResult(null);
        setError(null);
        if (fileInputRef.current) fileInputRef.current.value = '';
    };

    return (
        <Paper
            elevation={2}
            sx={{
                p: 3,
                mb: 3,
                border: dragActive ? '2px dashed #1976d2' : '2px dashed transparent',
                bgcolor: dragActive ? 'action.hover' : 'background.paper',
                transition: 'all 0.2s ease'
            }}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
        >
            <input
                ref={fileInputRef}
                type="file"
                accept=".xlsx,.xls"
                onChange={handleFileInput}
                style={{ display: 'none' }}
            />

            {status === 'idle' && (
                <Box textAlign="center" py={2}>
                    <CloudUploadIcon sx={{ fontSize: 48, color: 'primary.main', mb: 1 }} />
                    <Typography variant="h6" gutterBottom>
                        Upload Exit Multiple Analysis
                    </Typography>
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                        Drag and drop your Excel file here, or click to browse
                    </Typography>
                    <Button variant="contained" onClick={handleClick} startIcon={<CloudUploadIcon />}>
                        Select File
                    </Button>
                    <Typography variant="caption" display="block" sx={{ mt: 1, color: 'text.disabled' }}>
                        Accepts .xlsx or .xls files
                    </Typography>
                </Box>
            )}

            {status === 'uploading' && (
                <Box textAlign="center" py={2}>
                    <Typography variant="body1" sx={{ mb: 2 }}>
                        Uploading and processing...
                    </Typography>
                    <LinearProgress sx={{ maxWidth: 300, mx: 'auto' }} />
                    <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                        This may take a minute for large files
                    </Typography>
                </Box>
            )}

            {status === 'success' && result && (
                <Box>
                    <Alert
                        severity="success"
                        icon={<CheckCircleIcon />}
                        action={
                            <Button color="inherit" size="small" onClick={resetUpload}>
                                Upload Another
                            </Button>
                        }
                    >
                        <AlertTitle>{result.message}</AlertTitle>
                        <Stack direction="row" spacing={1} sx={{ mt: 1, flexWrap: 'wrap', gap: 1 }}>
                            <Chip size="small" label={`${result.companies_inserted} companies`} color="primary" />
                            <Chip size="small" label={`${result.metrics_inserted} metrics`} color="secondary" />
                            <Chip size="small" label={`${result.prices_inserted} prices`} />
                            {result.year_range && (
                                <Chip size="small" label={`Years: ${result.year_range[0]}-${result.year_range[1]}`} variant="outlined" />
                            )}
                        </Stack>
                    </Alert>

                    {result.warnings && result.warnings.length > 0 && (
                        <Alert severity="warning" sx={{ mt: 1 }}>
                            {result.warnings.map((w, i) => <div key={i}>{w}</div>)}
                        </Alert>
                    )}
                </Box>
            )}

            {status === 'error' && (
                <Box>
                    <Alert
                        severity="error"
                        icon={<ErrorIcon />}
                        action={
                            <Button color="inherit" size="small" onClick={resetUpload}>
                                Try Again
                            </Button>
                        }
                    >
                        <AlertTitle>Upload Failed</AlertTitle>
                        {error}
                    </Alert>

                    {result?.errors && result.errors.length > 0 && (
                        <Box sx={{ mt: 1 }}>
                            <Button
                                size="small"
                                onClick={() => setShowDetails(!showDetails)}
                                endIcon={showDetails ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                            >
                                {showDetails ? 'Hide' : 'Show'} Details
                            </Button>
                            <Collapse in={showDetails}>
                                <Alert severity="error" sx={{ mt: 1 }}>
                                    <ul style={{ margin: 0, paddingLeft: 20 }}>
                                        {result.errors.map((e, i) => <li key={i}>{e}</li>)}
                                    </ul>
                                </Alert>
                            </Collapse>
                        </Box>
                    )}

                    {result?.sheets_found && (
                        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                            Sheets found: {result.sheets_found.join(', ')}
                        </Typography>
                    )}
                </Box>
            )}
        </Paper>
    );
}
