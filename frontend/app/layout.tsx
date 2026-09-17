import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "BOQ Audit Portal", description: "Cổng tra soát hồ sơ BOQ" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="vi"><body>{children}</body></html>;
}

