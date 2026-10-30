/** @type {import('next').NextConfig} */
// Static export: `next build` emits a plain `out/` dir (no Node server). All data is fetched
// client-side from /api/* (proxied to the in-cluster api gateway by nginx), so the export stays
// fully static and air-gap portable.
// The dev server (2026-10-22) proxies the live plant: /api/live/plant to the plant service and
// /api/live/engine to the engine service (live/README.md). VISR_PLANT and VISR_ENGINE move them.
// A static export has no rewrites: there nginx proxies the same paths (nginx.preview.conf).
const dev = process.env.NODE_ENV !== "production";
const plant = process.env.VISR_PLANT || "http://127.0.0.1:9110";
const engine = process.env.VISR_ENGINE || "http://127.0.0.1:9111";

const nextConfig = {
  ...(dev ? {} : { output: "export" }),
  images: { unoptimized: true },
  ...(dev && {
    async rewrites() {
      return [
        { source: "/api/live/plant/:path*", destination: `${plant}/:path*` },
        { source: "/api/live/engine/:path*", destination: `${engine}/:path*` },
      ];
    },
  }),
};
export default nextConfig;
