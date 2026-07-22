/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone', // Required for Docker deployment
  experimental: {
    typedRoutes: true
  }
};

export default nextConfig;

