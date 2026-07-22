'use client';

import React, { useState, useEffect, useRef } from 'react';
import {
    Box,
    Typography,
    Autocomplete,
    TextField,
    Button,
    Paper,
    CircularProgress,
    Stack,
    Alert,
    Chip,
    Divider,
    IconButton,
    Tooltip,
    LinearProgress
} from '@mui/material';
import {
    Science as ResearchIcon,
    Download as DownloadIcon,
    Psychology as ThinkingIcon,
    CheckCircle as CompleteIcon,
    Error as ErrorIcon,
    PlayArrow as StartIcon
} from '@mui/icons-material';
import { Company, ResearchEvent, fetchCompanies, streamResearch, downloadResearchPdf } from '@/lib/api';
import { renderMarkdown } from '@/lib/markdown';

type ResearchStatus = 'idle' | 'loading' | 'researching' | 'complete' | 'error';

type ThinkingStep = {
    id: number;
    thought: string;
    timestamp: Date;
};

export default function ResearchPage() {
    const [companies, setCompanies] = useState<Company[]>([]);
    const [selectedCompany, setSelectedCompany] = useState<Company | null>(null);
    const [loadingCompanies, setLoadingCompanies] = useState(true);
    const [status, setStatus] = useState<ResearchStatus>('idle');
    const [thinkingSteps, setThinkingSteps] = useState<ThinkingStep[]>([]);
    const [reportContent, setReportContent] = useState<string>('');
    const [streamingContent, setStreamingContent] = useState<string>('');
    const [analystName, setAnalystName] = useState<string>('');
    const [error, setError] = useState<string | null>(null);
    const [downloadingPdf, setDownloadingPdf] = useState(false);

    const abortControllerRef = useRef<AbortController | null>(null);
    const reportRef = useRef<HTMLDivElement>(null);
    const stepIdRef = useRef(0);
    const streamingContentRef = useRef<string>('');

    // Load companies on mount
    useEffect(() => {
        const loadCompanies = async () => {
            try {
                const data = await fetchCompanies();
                setCompanies(data);
            } catch (err) {
                console.error('Failed to load companies:', err);
                setError('Failed to load companies. Please refresh the page.');
            } finally {
                setLoadingCompanies(false);
            }
        };
        loadCompanies();
    }, []);

    // Auto-scroll report as content streams
    useEffect(() => {
        if (reportRef.current && streamingContent) {
            reportRef.current.scrollTop = reportRef.current.scrollHeight;
        }
    }, [streamingContent]);

    const handleStartResearch = async () => {
        if (!selectedCompany) return;

        // Reset state
        setStatus('researching');
        setThinkingSteps([]);
        setReportContent('');
        setStreamingContent('');
        streamingContentRef.current = '';
        setError(null);
        stepIdRef.current = 0;

        // Create abort controller for cancellation
        abortControllerRef.current = new AbortController();

        try {
            for await (const event of streamResearch(
                selectedCompany.id,
                analystName || undefined,
                abortControllerRef.current.signal
            )) {
                if (event.type === 'start') {
                    setThinkingSteps(prev => [
                        ...prev,
                        {
                            id: stepIdRef.current++,
                            thought: 'Starting deep research...',
                            timestamp: new Date()
                        }
                    ]);
                } else if (event.type === 'thinking') {
                    const data = event.data as { thought?: string };
                    if (data.thought) {
                        setThinkingSteps(prev => [
                            ...prev,
                            {
                                id: stepIdRef.current++,
                                thought: data.thought!,
                                timestamp: new Date()
                            }
                        ]);
                    }
                } else if (event.type === 'content') {
                    const data = event.data as { text?: string };
                    if (data.text) {
                        streamingContentRef.current += data.text;
                        setStreamingContent(streamingContentRef.current);
                    }
                } else if (event.type === 'complete') {
                    const data = event.data as { report?: string };
                    // Use ref to get the latest streaming content (avoid stale closure)
                    setReportContent(data.report || streamingContentRef.current);
                    setStatus('complete');
                } else if (event.type === 'error') {
                    const errorMsg = typeof event.data === 'string' ? event.data : 'Research failed';
                    setError(errorMsg);
                    setStatus('error');
                }
            }
        } catch (err) {
            if ((err as Error).name === 'AbortError') {
                setStatus('idle');
            } else {
                setError((err as Error).message);
                setStatus('error');
            }
        }
    };

    const handleCancelResearch = () => {
        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
        }
        setStatus('idle');
    };

    const handleDownloadPdf = async () => {
        const content = reportContent || streamingContent;
        if (!content || !selectedCompany) return;

        setDownloadingPdf(true);
        try {
            const title = `Initiating Coverage - ${selectedCompany.name || selectedCompany.ticker}`;
            const blob = await downloadResearchPdf(content, title);

            // Create download link
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${(selectedCompany.name || selectedCompany.ticker).replace(/[^a-zA-Z0-9]/g, '_')}_Research.pdf`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        } catch (err) {
            setError((err as Error).message);
        } finally {
            setDownloadingPdf(false);
        }
    };

    const displayContent = reportContent || streamingContent;

    return (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3, maxWidth: 1400, mx: 'auto' }}>
            {/* Header */}
            <Box>
                <Typography variant="h4" sx={{ fontWeight: 700, mb: 1, display: 'flex', alignItems: 'center', gap: 1 }}>
                    <ResearchIcon sx={{ fontSize: 36, color: 'primary.main' }} />
                    Research Assistant
                </Typography>
                <Typography variant="body1" color="text.secondary">
                    Generate comprehensive Initiating Coverage reports using AI-powered deep research
                </Typography>
            </Box>

            {/* Company Selection Panel */}
            <Paper className="panel" sx={{ p: 3 }}>
                <Typography variant="h6" sx={{ mb: 2 }}>Research Configuration</Typography>

                <Stack spacing={2}>
                    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems="flex-start">
                        <Autocomplete
                            sx={{ minWidth: 350, flexGrow: 1 }}
                            options={companies}
                            loading={loadingCompanies}
                            value={selectedCompany}
                            onChange={(_, value) => setSelectedCompany(value)}
                            isOptionEqualToValue={(option, value) => option.id === value.id}
                            getOptionLabel={(option) =>
                                option.name ? `${option.name} (${option.ticker})` : option.ticker
                            }
                            renderOption={(props, option) => {
                                const { key, ...otherProps } = props as { key: string } & React.HTMLAttributes<HTMLLIElement>;
                                return (
                                    <Box component="li" key={key} {...otherProps} role="option">
                                        <Box>
                                            <Typography variant="body1">{option.name || option.ticker}</Typography>
                                            <Typography variant="caption" color="text.secondary">{option.ticker}</Typography>
                                        </Box>
                                    </Box>
                                );
                            }}
                            renderInput={(params) => (
                                <TextField
                                    {...params}
                                    label="Select Company"
                                    variant="outlined"
                                    aria-label="Select company for research"
                                    InputProps={{
                                        ...params.InputProps,
                                        endAdornment: (
                                            <>
                                                {loadingCompanies ? <CircularProgress color="inherit" size={20} aria-label="Loading companies" /> : null}
                                                {params.InputProps.endAdornment}
                                            </>
                                        )
                                    }}
                                />
                            )}
                            disabled={status === 'researching'}
                            aria-label="Company selection"
                        />

                        <TextField
                            label="Analyst Name (Optional)"
                            variant="outlined"
                            value={analystName}
                            onChange={(e) => setAnalystName(e.target.value)}
                            placeholder="Your Name / Team"
                            sx={{ minWidth: 250 }}
                            disabled={status === 'researching'}
                            aria-label="Analyst name input"
                        />

                        <Button
                            variant="contained"
                            size="large"
                            startIcon={status === 'researching' ? <CircularProgress size={20} color="inherit" aria-label="Processing" /> : <StartIcon />}
                            onClick={status === 'researching' ? handleCancelResearch : handleStartResearch}
                            disabled={!selectedCompany || status === 'loading'}
                            sx={{ minWidth: 180, height: 56 }}
                            color={status === 'researching' ? 'error' : 'primary'}
                            aria-label={status === 'researching' ? 'Cancel research' : 'Generate research report'}
                        >
                            {status === 'researching' ? 'Cancel' : 'Generate Report'}
                        </Button>
                    </Stack>
                </Stack>
            </Paper>

            {/* Error Alert */}
            {error && (
                <Alert severity="error" onClose={() => setError(null)} role="alert">
                    {error}
                </Alert>
            )}

            {/* Research Progress & Results */}
            {(status === 'researching' || status === 'complete' || displayContent) && (
                <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '300px 1fr' }, gap: 2 }}>
                    {/* Thinking Steps Panel */}
                    <Paper className="panel" sx={{ p: 2, maxHeight: 600, overflow: 'auto' }} role="region" aria-label="Research progress">
                        <Typography variant="h6" sx={{ mb: 2, display: 'flex', alignItems: 'center', gap: 1 }} component="h3">
                            <ThinkingIcon fontSize="small" aria-hidden="true" />
                            Research Progress
                        </Typography>

                        {status === 'researching' && (
                            <LinearProgress sx={{ mb: 2 }} />
                        )}

                        <Stack spacing={1.5}>
                            {thinkingSteps.map((step) => (
                                <Box
                                    key={step.id}
                                    sx={{
                                        p: 1.5,
                                        borderRadius: 1,
                                        bgcolor: 'background.default',
                                        borderLeft: '3px solid',
                                        borderColor: 'primary.main'
                                    }}
                                >
                                    <Typography variant="body2" sx={{ lineHeight: 1.5 }}>
                                        {step.thought}
                                    </Typography>
                                    <Typography variant="caption" color="text.secondary">
                                        {step.timestamp.toLocaleTimeString()}
                                    </Typography>
                                </Box>
                            ))}

                            {thinkingSteps.length === 0 && status === 'researching' && (
                                <Typography variant="body2" color="text.secondary">
                                    Initializing research agent...
                                </Typography>
                            )}
                        </Stack>
                    </Paper>

                    {/* Report Content Panel */}
                    <Paper className="panel" sx={{ p: 3 }} role="region" aria-label="Research report">
                        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                            <Typography variant="h6" sx={{ display: 'flex', alignItems: 'center', gap: 1 }} component="h3">
                                {status === 'complete' ? (
                                    <CompleteIcon color="success" aria-label="Complete" />
                                ) : status === 'error' ? (
                                    <ErrorIcon color="error" aria-label="Error" />
                                ) : (
                                    <ResearchIcon color="primary" aria-label="In progress" />
                                )}
                                Research Report
                                {selectedCompany && (
                                    <Chip
                                        label={selectedCompany.name || selectedCompany.ticker}
                                        size="small"
                                        color="primary"
                                        variant="outlined"
                                        aria-label={`Company: ${selectedCompany.name || selectedCompany.ticker}`}
                                    />
                                )}
                            </Typography>

                            {displayContent && (
                                <Tooltip title="Download as PDF">
                                    <IconButton
                                        onClick={handleDownloadPdf}
                                        disabled={downloadingPdf || status === 'researching'}
                                        color="primary"
                                        aria-label="Download report as PDF"
                                    >
                                        {downloadingPdf ? <CircularProgress size={24} aria-label="Downloading" /> : <DownloadIcon />}
                                    </IconButton>
                                </Tooltip>
                            )}
                        </Box>

                        <Divider sx={{ mb: 2 }} />

                        <Box
                            ref={reportRef}
                            sx={{
                                maxHeight: 'calc(100vh - 400px)',
                                minHeight: 400,
                                overflow: 'auto',
                                '& h1, & h2, & h3': { color: 'primary.main', mt: 3, mb: 1 },
                                '& h1': { fontSize: '1.75rem' },
                                '& h2': { fontSize: '1.4rem', borderBottom: '1px solid', borderColor: 'divider', pb: 1 },
                                '& h3': { fontSize: '1.15rem' },
                                '& p': { mb: 2, lineHeight: 1.7 },
                                '& ul, & ol': { mb: 2, pl: 3 },
                                '& li': { mb: 0.5 },
                                '& blockquote': {
                                    borderLeft: '3px solid',
                                    borderColor: 'primary.main',
                                    pl: 2,
                                    ml: 0,
                                    color: 'text.secondary',
                                    fontStyle: 'italic'
                                }
                            }}
                        >
                            {displayContent ? (
                                <Typography
                                    variant="body1"
                                    component="div"
                                    sx={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit' }}
                                    dangerouslySetInnerHTML={{
                                        __html: renderMarkdown(displayContent)
                                    }}
                                />
                            ) : (
                                <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 300, color: 'text.secondary' }}>
                                    <ResearchIcon sx={{ fontSize: 64, mb: 2, opacity: 0.3 }} />
                                    <Typography>Generating report...</Typography>
                                </Box>
                            )}
                        </Box>
                    </Paper>
                </Box>
            )}

            {/* Empty State */}
            {status === 'idle' && !displayContent && (
                <Paper
                    className="panel"
                    sx={{
                        p: 6,
                        textAlign: 'center',
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        gap: 2
                    }}
                >
                    <ResearchIcon sx={{ fontSize: 80, color: 'primary.main', opacity: 0.5 }} />
                    <Typography variant="h6" color="text.secondary">
                        Select a company to generate an Initiating Coverage report
                    </Typography>
                    <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 600 }}>
                        The report will include comprehensive analysis of the business model, industry dynamics,
                        competitive environment, and investment considerations using AI-powered deep research.
                    </Typography>
                </Paper>
            )}
        </Box>
    );
}
