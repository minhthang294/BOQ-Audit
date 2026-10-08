import type { Metadata } from "next";
import { Barlow, Barlow_Condensed } from "next/font/google";
import "./globals.css";

const bodyFont = Barlow({ subsets: ["latin", "vietnamese"], weight: ["400", "500", "600", "700"], variable: "--font-body", display: "swap" });
const displayFont = Barlow_Condensed({ subsets: ["latin", "vietnamese"], weight: ["600", "700", "800"], variable: "--font-display", display: "swap" });

export const metadata: Metadata = { title: "BOQ Audit Portal", description: "Cổng tra soát hồ sơ BOQ" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="vi"><body className={`${bodyFont.variable} ${displayFont.variable}`}>{children}</body></html>;
}
