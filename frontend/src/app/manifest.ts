import type { MetadataRoute } from "next";

/** Web app manifest: lets phones install LedgerPic to the home screen and open it like an app. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "LedgerPic",
    short_name: "LedgerPic",
    description: "Snap a receipt, let AI draft the expense, confirm it, and watch your totals add up.",
    id: "/",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#0a0f0d",
    theme_color: "#0b8063",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
