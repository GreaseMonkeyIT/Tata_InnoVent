/** @type {import('next').NextConfig} */
// Static export: `next build` emits a plain `out/` dir (no Node server). All data is fetched
// client-side from /api/* (proxied to the in-cluster api gateway by nginx), so the export stays
// fully static and air-gap portable.
const nextConfig = {
  output: "export",
  images: { unoptimized: true },
};
export default nextConfig;
