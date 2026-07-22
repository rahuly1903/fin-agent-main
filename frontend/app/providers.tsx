'use client';

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ThemeProvider, CssBaseline, AppBar, Toolbar, Typography, Box, Container, Button } from '@mui/material';
import { FilterAlt as ScreenerIcon, Science as ResearchIcon, CloudUpload as UploadIcon } from '@mui/icons-material';
import theme from '@/theme';
import { ErrorBoundary } from '@/components/ErrorBoundary';

export default function Providers(props: { children: React.ReactNode }) {
  const pathname = usePathname();

  const navItems = [
    { label: 'Screener', href: '/', icon: <ScreenerIcon fontSize="small" /> },
    { label: 'Research Assistant', href: '/research', icon: <ResearchIcon fontSize="small" /> },
    { label: 'Data Upload', href: '/upload', icon: <UploadIcon fontSize="small" /> }
  ];

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <AppBar position="sticky" color="transparent" elevation={0} sx={{ borderBottom: '1px solid rgba(255,255,255,0.08)', backdropFilter: 'blur(6px)' }}>
        <Toolbar sx={{ display: 'flex', justifyContent: 'space-between' }}>
          <Typography variant="h6" component="div" sx={{ fontWeight: 600 }}>
            Equity Screener
          </Typography>
          <Box sx={{ display: 'flex', gap: 1 }}>
            {navItems.map((item) => (
              <Button
                key={item.href}
                component={Link}
                href={item.href}
                startIcon={item.icon}
                variant={pathname === item.href ? 'contained' : 'text'}
                color={pathname === item.href ? 'primary' : 'inherit'}
                sx={{
                  textTransform: 'none',
                  fontWeight: pathname === item.href ? 600 : 400
                }}
              >
                {item.label}
              </Button>
            ))}
          </Box>
        </Toolbar>
      </AppBar>
      <Container maxWidth="xl" sx={{ py: 2 }}>
        <ErrorBoundary>
          {props.children}
        </ErrorBoundary>
      </Container>
    </ThemeProvider>
  );
}
