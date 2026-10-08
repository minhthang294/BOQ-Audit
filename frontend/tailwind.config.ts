import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  theme: { extend: { colors: { ink: "var(--ink)", brand: "var(--brand)", paper: "var(--canvas)", line: "var(--line)" } } },
  plugins: [],
} satisfies Config;
