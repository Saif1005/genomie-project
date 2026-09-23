/** @type {import('next').NextConfig} */

// URL du backend vue depuis le serveur Next.js (réseau Docker interne).
// Figée au build (routes-manifest) : http://backend:8000 dans docker-compose.local.yml.
const backendUrl = (process.env.BACKEND_INTERNAL_URL || 'http://localhost:8000').replace(/\/$/, '');

const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  experimental: {
    // Upload FASTQ de plusieurs Go via le proxy : défaut Next.js = 30 s
    proxyTimeout: 3_600_000,
  },
  async rewrites() {
    // Le navigateur appelle /api/v1/… et /health sur le port 3000 ;
    // Next.js relaie vers le backend → même origine, pas de CORS, pas d'IP en dur.
    return [
      { source: '/api/v1/:path*', destination: `${backendUrl}/api/v1/:path*` },
      { source: '/health', destination: `${backendUrl}/health` },
    ];
  },
};

export default nextConfig;
