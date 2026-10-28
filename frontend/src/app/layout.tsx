import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import type { ReactNode } from "react";

import { Providers } from "./providers";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });

export const metadata: Metadata = {
  title: "LedgerPic",
  description: "Snap a receipt, let AI draft the expense, confirm it, and watch your totals add up.",
  // iOS home-screen app (Android uses app/manifest.ts).
  appleWebApp: { capable: true, title: "LedgerPic", statusBarStyle: "black-translucent" },
};

export const viewport: Viewport = {
  themeColor: "#0b8063",
  // Draw under the notch / status bar when installed; globals.css pads with the safe-area insets.
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
