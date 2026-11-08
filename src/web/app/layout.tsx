import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import { THEME_BOOT_SCRIPT } from "./theme";

export const metadata: Metadata = {
  title: "mosaicWave",
  description: "Personal photo library",
  icons: {
    apple: "/apple-touch-icon.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
