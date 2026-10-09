import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  theme: { extend: { colors: { ink: "var(--ink)", brand: { DEFAULT: "var(--brand)", strong: "var(--brand-strong)", soft: "var(--brand-soft)" }, signal: { DEFAULT: "var(--signal)", strong: "var(--signal-strong)", soft: "var(--signal-soft)" }, paper: "var(--canvas)", line: "var(--line)", night: "var(--night)", "night-muted": "var(--night-muted)" } } },
  plugins: [],
} satisfies Config;
