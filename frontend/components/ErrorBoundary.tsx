'use client';

import React, { Component, ErrorInfo, ReactNode } from 'react';
import { Box, Button, Typography, Paper } from '@mui/material';
import ErrorIcon from '@mui/icons-material/Error';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('ErrorBoundary caught an error:', error, errorInfo);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <Paper
          sx={{
            p: 4,
            textAlign: 'center',
            maxWidth: 600,
            mx: 'auto',
            mt: 4
          }}
        >
          <ErrorIcon sx={{ fontSize: 64, color: 'error.main', mb: 2 }} />
          <Typography variant="h5" gutterBottom>
            Something went wrong
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            {this.state.error?.message || 'An unexpected error occurred'}
          </Typography>
          <Button variant="contained" onClick={this.handleReset}>
            Try Again
          </Button>
        </Paper>
      );
    }

    return this.props.children;
  }
}

