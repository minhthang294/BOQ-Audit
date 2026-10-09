import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

const bodyFont = localFont({ src: [{ path: "./fonts/FiraSans-Regular.ttf", weight: "400" }, { path: "./fonts/FiraSans-SemiBold.ttf", weight: "600" }], variable: "--font-body", display: "swap" });
const displayFont = localFont({ src: [{ path: "./fonts/FiraSansCondensed-Bold.ttf", weight: "700" }, { path: "./fonts/FiraSansCondensed-ExtraBold.ttf", weight: "800" }], variable: "--font-display", display: "swap" });

export const metadata: Metadata = { title: "BOQ Audit Portal", description: "Cổng tra soát hồ sơ BOQ" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="vi"><body className={`${bodyFont.variable} ${displayFont.variable}`}>{children}</body></html>;
}
