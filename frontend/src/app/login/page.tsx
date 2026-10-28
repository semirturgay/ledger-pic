"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, ScanLine, ShieldCheck, SquarePen } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Logo } from "@/components/Logo";
import { Turnstile, TURNSTILE_SITE_KEY } from "@/components/Turnstile";
import { api } from "@/lib/api";
import { getDeviceToken, setDeviceToken, setToken } from "@/lib/auth";

const FEATURES = [
  { Icon: ScanLine, text: "AI reads merchant, items, tax and total from a photo" },
  { Icon: SquarePen, text: "You review and correct. Your version is the truth" },
  { Icon: ShieldCheck, text: "Totals computed from confirmed receipts only" },
];

export default function LoginPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [triedSubmit, setTriedSubmit] = useState(false);
  const [captchaToken, setCaptchaToken] = useState<string | null>(null);
  const [captchaReset, setCaptchaReset] = useState(0);
  const needsCaptcha = TURNSTILE_SITE_KEY !== "" && captchaToken === null;

  const registering = mode === "register";
  const passwordsMatch = password === confirmPassword;
  // Flag a mismatch once the user has typed as much as the password, or tried to submit.
  const showMismatch =
    registering && !passwordsMatch && (triedSubmit || confirmPassword.length >= password.length);

  const submit = useMutation({
    mutationFn: async () =>
      mode === "login"
        ? api.login(email, password, captchaToken, await getDeviceToken(email))
        : api.register(email, password, captchaToken),
    onSuccess: async ({ access_token, device_token }) => {
      void queryClient.cancelQueries();
      queryClient.clear();
      await setDeviceToken(email, device_token);
      await setToken(access_token); // the previous account's offline data is gone first
      router.replace("/");
    },
    // The token was spent on this attempt; the next one needs a new check.
    onError: () => setCaptchaReset((n) => n + 1),
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    setTriedSubmit(true);
    if ((registering && !passwordsMatch) || needsCaptcha) return;
    submit.mutate();
  }

  function switchTo(next: "login" | "register") {
    submit.reset();
    setConfirmPassword("");
    setTriedSubmit(false);
    setMode(next);
  }

  return (
    <div className="auth">
      <aside className="auth-hero">
        <Logo />
        <div>
          <h1 className="auth-headline">Snap a receipt. Get a ledger.</h1>
          <p className="auth-lede">
            LedgerPic turns receipt photos into clean, structured expenses, with you in control of every number.
          </p>
          <ul className="auth-features">
            {FEATURES.map(({ Icon, text }) => (
              <li key={text}>
                <span className="feature-icon">
                  <Icon size={18} />
                </span>
                {text}
              </li>
            ))}
          </ul>
        </div>
        <p style={{ color: "rgba(255,255,255,0.7)", fontSize: 13 }}>© {new Date().getFullYear()} LedgerPic</p>
      </aside>

      <section className="auth-panel">
        <form className="auth-card" onSubmit={onSubmit}>
          <div className="mobile-only-brand">
            <Logo />
          </div>
          <h2 className="page-title" style={{ marginTop: 28 }}>
            {mode === "login" ? "Welcome back" : "Create your ledger"}
          </h2>
          <p className="page-subtitle">
            {mode === "login" ? "Log in to see your receipts and totals." : "It takes ten seconds. No card needed."}
          </p>

          <div className="segmented" role="group" aria-label="Choose log in or register">
            <button type="button" aria-pressed={mode === "login"} onClick={() => switchTo("login")}>
              Log in
            </button>
            <button type="button" aria-pressed={mode === "register"} onClick={() => switchTo("register")}>
              Register
            </button>
          </div>

          <div className="stack" style={{ gap: 14 }}>
            <label className="field">
              <span className="field-label">Email</span>
              <input
                className="input"
                type="email"
                required
                autoComplete="email"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </label>
            <label className="field">
              <span className="field-label">Password</span>
              <input
                className="input"
                type="password"
                required
                minLength={mode === "register" ? 8 : 1}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                placeholder={mode === "register" ? "At least 8 characters" : "Your password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {registering && (
              <label className="field">
                <span className="field-label">Confirm password</span>
                <input
                  className={`input${showMismatch ? " invalid" : ""}`}
                  type="password"
                  required
                  autoComplete="new-password"
                  placeholder="Type it again"
                  aria-invalid={showMismatch}
                  aria-describedby="confirm-hint"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                />
                {showMismatch && (
                  <span id="confirm-hint" className="field-error">
                    <CircleAlert size={13} /> Passwords don&apos;t match
                  </span>
                )}
                {registering && confirmPassword && passwordsMatch && (
                  <span id="confirm-hint" className="field-ok">
                    <CircleCheck size={13} /> Passwords match
                  </span>
                )}
              </label>
            )}

            {TURNSTILE_SITE_KEY && <Turnstile onToken={setCaptchaToken} resetKey={captchaReset} />}

            {submit.isError && (
              <div className="notice error">
                <CircleAlert size={16} />
                {submit.error.message}
              </div>
            )}

            <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={submit.isPending || needsCaptcha}>
              {submit.isPending ? "One moment…" : mode === "login" ? "Log in" : "Create account"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
