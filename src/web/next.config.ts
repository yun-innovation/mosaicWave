import { networkInterfaces } from "node:os";

import type { NextConfig } from "next";

const isProd = process.env.NODE_ENV === "production";
// Where the debug API listens (scripts/start*.ps1|sh set it through MOSAICWAVE_DEV_HOST).
const devApiHost = (() => {
  const host = (process.env.MOSAICWAVE_DEV_HOST ?? "").trim();
  return host && host !== "0.0.0.0" && host !== "::" ? host : "127.0.0.1";
})();

// Dev pages opened from another device (hot reload) must name this machine's address. With the dev host
// 0.0.0.0 that is every IPv4 address the machine has; with one address, just that one.
const devOrigins = (() => {
  const raw = (process.env.MOSAICWAVE_DEV_HOST ?? "").trim();
  const found = new Set(["127.0.0.1"]);
  if (raw === "0.0.0.0" || raw === "::") {
    for (const list of Object.values(networkInterfaces())) {
      for (const net of list ?? []) {
        if (net.family === "IPv4" && !net.internal) found.add(net.address);
      }
    }
  } else if (raw) {
    found.add(raw);
  }
  return [...found];
})();

const nextConfig: NextConfig = {
  // Static export only for production builds. `next dev` keeps rewrites working
  // without the "rewrites will not work with output: export" warning.
  ...(isProd ? { output: "export" as const } : {}),
  images: { unoptimized: true },
  // next dev listens where scripts/start* put it (MOSAICWAVE_DEV_HOST; it follows the API's saved address, default 127.0.0.1).
  allowedDevOrigins: devOrigins,
};

if (!isProd) {
  // 4GB matches a Takeout zip split; leftover 4K videos beside the zips are larger.
  // Same cap as MAX_PUSH_BYTES. Large uploads also skip this rewrite (api.ts → :8000).
  // Next 16: proxyClientMaxBodySize (middlewareClientMaxBodySize is deprecated).
  nextConfig.experimental = {
    proxyClientMaxBodySize: "256gb",
  };
  nextConfig.rewrites = async () => [
    {
      source: "/api/:path*",
      destination: `http://${devApiHost}:8000/api/:path*`,
    },
  ];
}

export default nextConfig;
