"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Logo } from "@/components/Logo";
import { api } from "@/lib/api";
import { clearToken } from "@/lib/auth";
import { initials } from "@/lib/format";
import { queryKeys } from "@/lib/queryKeys";

export function NavBar() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: queryKeys.me, queryFn: api.me, staleTime: Infinity });

  async function logout() {
    void queryClient.cancelQueries();
    queryClient.clear(); // never show one user's cached receipts to the next
    await clearToken();
    router.replace("/login");
  }

  return (
    <header className="nav">
      <div className="container nav-inner">
        <Link href="/" aria-label="LedgerPic home">
          <Logo />
        </Link>
        <div className="nav-user">
          {me.data && (
            <>
              <span className="avatar" style={{ background: "var(--brand)" }} aria-hidden="true">
                {initials(me.data.email)}
              </span>
              <span className="hide-sm">{me.data.email}</span>
            </>
          )}
          <button className="btn btn-ghost btn-sm" onClick={logout}>
            <LogOut size={16} /> <span className="hide-sm">Log out</span>
          </button>
        </div>
      </div>
    </header>
  );
}
