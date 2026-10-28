import type { NextConfig } from "next";

const dev = process.env.NODE_ENV !== "production";
const apiOrigin = new URL(process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").origin;
const turnstile = "https://challenges.cloudflare.com";

// The JWT lives in localStorage, so the policy's job is to keep foreign script and framing out.
// Next's bootstrap scripts are inline (no nonce setup), hence 'unsafe-inline' for scripts; dev
// additionally needs eval and the hot-reload websocket.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline' ${turnstile}${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data:",
  "font-src 'self' data:",
  `connect-src 'self' ${apiOrigin} ${turnstile}${dev ? " ws: wss:" : ""}`,
  `frame-src ${turnstile}`,
  "worker-src 'self'",
  "manifest-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // A self-contained server in .next/standalone, for the Docker image (Vercel ignores this).
  output: "standalone",
  agentRules: false,
  // Lets phones on the same Wi-Fi load the dev server (e.g. http://192.168.x.x:3000).
  allowedDevOrigins: process.env.DEV_LAN_HOST ? [process.env.DEV_LAN_HOST] : [],
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(self), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
