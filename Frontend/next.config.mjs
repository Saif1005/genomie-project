/** @type {import('next').NextConfig} */

// Backend URL seen from the Next.js server (internal Docker network).
// Frozen at build time (routes-manifest): http://backend:8000 in docker-compose.local.yml.
const backendUrl = (process.env.BACKEND_INTERNAL_URL || 'http://localhost:8000').replace(/\/$/, '');

const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  experimental: {
    // Multi-GB FASTQ uploads through the proxy: Next.js default = 30 s
    proxyTimeout: 3_600_000,
  },
  async rewrites() {
    // Le navigateur appelle /api/v1/… et /health sur le port 3000 ;
    // Next.js proxies to the backend → same origin, no CORS, no hard-coded IP.
    return [
      { source: '/api/v1/:path*', destination: `${backendUrl}/api/v1/:path*` },
      { source: '/health', destination: `${backendUrl}/health` },
    ];
  },
};

export default nextConfig;
