import type { NextConfig } from "next";

const isProd = process.env.NODE_ENV === "production";
// Where the debug API listens (scripts/start*.ps1|sh set it through MOSAICWAVE_DEV_HOST).
const devApiHost = (() => {
  const host = (process.env.MOSAICWAVE_DEV_HOST ?? "").trim();
  return host && host !== "0.0.0.0" && host !== "::" ? host : "127.0.0.1";
})();

const nextConfig: NextConfig = {
  // Static export only for production builds. `next dev` keeps rewrites working
  // without the "rewrites will not work with output: export" warning.
  ...(isProd ? { output: "export" as const } : {}),
  images: { unoptimized: true },
  // next dev is bound to 127.0.0.1 only (package.json), so it is never offered on a VPN or LAN address; the browser often opens http://127.0.0.1:3000
  allowedDevOrigins: ["127.0.0.1"],
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
