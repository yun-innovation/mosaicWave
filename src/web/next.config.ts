import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  // next dev binds as localhost; the browser often opens http://127.0.0.1:3000
  allowedDevOrigins: ["127.0.0.1"],
};

if (process.env.NODE_ENV !== "production") {
  // 4GB matches a Takeout zip split; leftover 4K videos beside the zips are larger.
  // Same cap as MAX_PUSH_BYTES. Large uploads also skip this rewrite (api.ts → :8000).
  nextConfig.experimental = {
    middlewareClientMaxBodySize: "256gb",
  };
  nextConfig.rewrites = async () => [
    {
      source: "/api/:path*",
      destination: "http://127.0.0.1:8000/api/:path*",
    },
  ];
}

export default nextConfig;
