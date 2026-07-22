import type { Metadata } from 'next';
import './globals.css';
import Providers from './providers';

export const metadata: Metadata = {
  title: 'Equity Screener & IRR Engine',
  description: 'AI-Powered Equity Screener & Research Assistant - Filters UI'
};

export default function RootLayout(props: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>{props.children}</Providers>
      </body>
    </html>
  );
}

