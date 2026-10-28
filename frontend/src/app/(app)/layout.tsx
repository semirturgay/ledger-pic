"use client";

import type { ReactNode } from "react";

import { AppShell } from "@/components/AppShell";
import { useRequireAuth } from "@/lib/auth";

/** Signed-in pages. The shell stays mounted across navigation, so finished-receipt notices are never missed. */
export default function SignedInLayout({ children }: { children: ReactNode }) {
  const ready = useRequireAuth();
  if (!ready) return null;
  return <AppShell>{children}</AppShell>;
}
