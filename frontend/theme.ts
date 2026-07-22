import { createTheme } from '@mui/material/styles';

const theme = createTheme({
  cssVariables: true,
  palette: {
    mode: 'dark',
    background: {
      default: '#0b0e11',
      paper: '#12161b'
    },
    text: {
      primary: '#e6eef5',
      secondary: '#8ea2b2'
    },
    primary: {
      main: '#4fc3f7'
    },
    secondary: {
      main: '#2ecc71'
    },
    divider: 'rgba(255,255,255,0.08)'
  },
  shape: {
    borderRadius: 12
  },
  typography: {
    fontFamily: [
      'Inter',
      'ui-sans-serif',
      'system-ui',
      '-apple-system',
      'Segoe UI',
      'Roboto',
      'Helvetica Neue',
      'Arial',
      'Noto Sans',
      'Apple Color Emoji',
      'Segoe UI Emoji'
    ].join(','),
    h6: { fontWeight: 600 }
  },
  components: {
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: 'none'
        }
      }
    },
    MuiCard: {
      defaultProps: {
        elevation: 0
      }
    },
    MuiButton: {
      defaultProps: {
        variant: 'contained'
      }
    }
  }
});

export default theme;

