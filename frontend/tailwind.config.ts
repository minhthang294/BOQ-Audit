import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  theme: { extend: { colors: { ink: "#17212b", brand: "#164e63", paper: "#f4f6f5", line: "#d8dfdc" } } },
  plugins: [],
} satisfies Config;

