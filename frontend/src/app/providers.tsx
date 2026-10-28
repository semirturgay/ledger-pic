"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { ToastProvider } from "@/components/Toaster";
import { ApiError } from "@/lib/api";
import { registerServiceWorker } from "@/lib/serviceWorker";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 10_000,
            // Always try the request, even offline: the service worker answers with the saved copy.
            networkMode: "offlineFirst",
            // Retry network blips and 5xx; a 4xx won't change by asking again.
            retry: (failureCount, error) =>
              failureCount < 2 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
          },
        },
      }),
  );
  useEffect(registerServiceWorker, []);
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  );
}
