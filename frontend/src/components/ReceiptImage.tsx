"use client";

import { ImageOff, ScanLine } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";

/**
 * The image endpoint requires the Bearer token, and <img src> can't send headers,
 * so fetch it as a blob and show it through a short-lived object URL.
 */
export function ReceiptImage({ id, scanning }: { id: string; scanning: boolean }) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let objectUrl: string | undefined;
    let cancelled = false;
    api
      .getReceiptImage(id)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [id]);

  return (
    <div className="image-frame">
      {error && (
        <div className="empty">
          <div className="empty-icon">
            <ImageOff size={22} />
          </div>
          Couldn&apos;t load the image: {error}
        </div>
      )}
      {!error && !src && <div className="skeleton" style={{ position: "absolute", inset: 0, borderRadius: 0 }} />}
      {/* eslint-disable-next-line @next/next/no-img-element -- a blob: URL of an authenticated fetch; next/image can't load it */}
      {src && <img src={src} alt="Uploaded receipt" />}
      {scanning && (
        <div className="scan-overlay">
          <div className="scan-line" />
          <span className="scan-badge">
            <ScanLine size={15} /> Reading your receipt…
          </span>
        </div>
      )}
    </div>
  );
}
