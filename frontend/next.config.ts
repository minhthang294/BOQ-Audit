import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: process.cwd(),
  // Type safety is enforced by the separate `npm run typecheck` CI step.
  typescript: { ignoreBuildErrors: true },
  experimental: { useTypeScriptCli: false, cpus: 1 },
};
export default nextConfig;
