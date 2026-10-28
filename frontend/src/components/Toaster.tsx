"use client";

import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import Link from "next/link";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { useIsClient } from "@/lib/useIsClient";

type ToastInput = {
  tone: "ok" | "error" | "info";
  title: string;
  body?: string;
  action?: { label: string; href: string };
};
type Toast = ToastInput & { id: number };

const ICONS = { ok: CircleCheck, error: CircleAlert, info: Info };
const VISIBLE_MS = 9000;
// Notices with something to act on (e.g. "Review") stay longer.
const ACTIONABLE_VISIBLE_MS = 30000;
const MAX_TOASTS = 4;

const ToastContext = createContext<(toast: ToastInput) => void>(() => {});

export const useToast = () => useContext(ToastContext);

let nextId = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  // Toasts are browser-only: render them after hydration so server and client HTML always match.
  const mounted = useIsClient();

  const dismiss = useCallback(
    (id: number) => setToasts((all) => all.filter((t) => t.id !== id)),
    [],
  );

  const push = useCallback(
    (toast: ToastInput) => {
      const id = nextId++;
      setToasts((all) => [...all.slice(-(MAX_TOASTS - 1)), { ...toast, id }]);
      window.setTimeout(() => dismiss(id), toast.action ? ACTIONABLE_VISIBLE_MS : VISIBLE_MS);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={push}>
      {children}
      {mounted &&
        createPortal(
          <div className="toaster" role="status" aria-live="polite">
            {toasts.map((toast) => {
              const Icon = ICONS[toast.tone];
              return (
                <div key={toast.id} className={`toast ${toast.tone}`}>
                  <span className="toast-icon">
                    <Icon size={18} />
                  </span>
                  <div className="toast-content">
                    <div className="toast-title">{toast.title}</div>
                    {toast.body && (
                      <div className="toast-body">{toast.body}</div>
                    )}
                    {toast.action && (
                      <Link
                        className="btn btn-sm btn-primary toast-action"
                        href={toast.action.href}
                        onClick={() => dismiss(toast.id)}
                      >
                        {toast.action.label}
                      </Link>
                    )}
                  </div>
                  <button
                    className="btn btn-ghost btn-sm btn-icon"
                    aria-label="Dismiss"
                    onClick={() => dismiss(toast.id)}
                  >
                    <X size={15} />
                  </button>
                </div>
              );
            })}
          </div>,
          document.body,
        )}
    </ToastContext.Provider>
  );
}
