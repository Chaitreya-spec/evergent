"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Notice, Spinner } from "@/components/ui/feedback";
import { Field, Input } from "@/components/ui/form";
import { AuthCard } from "./AuthCard";

/** Email and password against the backend's own accounts, or Google. */
export function SignInForm({
  next,
  initialMode = "login",
  google = false,
  initialError = null,
}: {
  next: string;
  initialMode?: "login" | "register";
  /** Offer "Continue with Google": the backend has it set up. */
  google?: boolean;
  initialError?: string | null;
}) {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">(initialMode);
  const [error, setError] = useState<string | null>(initialError);
  const [busy, setBusy] = useState(false);
  const creating = mode === "register";

  async function submit(form: FormData) {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode,
          email: form.get("email"),
          password: form.get("password"),
          full_name: form.get("full_name") ?? undefined,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setError(body?.message ?? "Could not sign in.");
        return;
      }
      router.replace(next);
      router.refresh();
    } catch {
      setError("This app's server is not reachable.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard
      title={creating ? "Create your account" : "Sign in"}
      description={creating ? "Your workspace, projects and keys stay private to you." : "Welcome back to the engineering room."}
      footer={
        <>
          {creating ? "Already have an account?" : "New here?"}{" "}
          <button
            type="button"
            className="font-medium text-fg hover:underline"
            onClick={() => {
              setMode(creating ? "login" : "register");
              setError(null);
            }}
          >
            {creating ? "Sign in" : "Create an account"}
          </button>
        </>
      }
    >
      {google && (
        <>
          <Button asChild size="lg" className="w-full">
            <a href={`/api/auth/google?next=${encodeURIComponent(next)}`}>
              <GoogleMark />
              Continue with Google
            </a>
          </Button>
          <div className="my-5 flex items-center gap-3 text-xs text-subtle">
            <span className="h-px flex-1 bg-line" />
            or with email
            <span className="h-px flex-1 bg-line" />
          </div>
        </>
      )}
      <form action={submit} className="space-y-4">
        {creating && (
          <Field label="Name" hint="Optional.">
            {(p) => <Input {...p} name="full_name" autoComplete="name" maxLength={200} />}
          </Field>
        )}
        <Field label="Email">
          {(p) => <Input {...p} name="email" type="email" required autoComplete="email" placeholder="you@company.com" />}
        </Field>
        <Field
          label={
            <span className="flex items-center justify-between">
              Password
              {!creating && (
                <Link href="/forgot-password" className="font-normal text-subtle hover:text-fg">
                  Forgot it?
                </Link>
              )}
            </span>
          }
          hint={creating ? "At least 10 characters." : undefined}
        >
          {(p) => (
            <Input
              {...p}
              name="password"
              type="password"
              required
              minLength={creating ? 10 : undefined}
              autoComplete={creating ? "new-password" : "current-password"}
            />
          )}
        </Field>
        {error && <Notice tone="bad">{error}</Notice>}
        <Button type="submit" variant="primary" size="lg" className="w-full" disabled={busy}>
          {busy && <Spinner className="text-accent-fg" />}
          {creating ? "Create account" : "Sign in"}
        </Button>
      </form>
    </AuthCard>
  );
}

function GoogleMark() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#4285F4" d="M23.5 12.27c0-.85-.08-1.67-.22-2.45H12v4.64h6.45a5.52 5.52 0 0 1-2.4 3.62v3h3.88c2.27-2.09 3.57-5.17 3.57-8.81z" />
      <path fill="#34A853" d="M12 24c3.24 0 5.96-1.07 7.94-2.9l-3.88-3.02c-1.07.72-2.45 1.15-4.06 1.15-3.12 0-5.77-2.11-6.71-4.95H1.28v3.11A12 12 0 0 0 12 24z" />
      <path fill="#FBBC05" d="M5.29 14.28A7.2 7.2 0 0 1 4.91 12c0-.79.14-1.56.38-2.28V6.61H1.28A12 12 0 0 0 0 12c0 1.94.46 3.77 1.28 5.39l4.01-3.11z" />
      <path fill="#EA4335" d="M12 4.77c1.76 0 3.34.61 4.59 1.8l3.44-3.44A11.97 11.97 0 0 0 12 0 12 12 0 0 0 1.28 6.61l4.01 3.11C6.23 6.88 8.88 4.77 12 4.77z" />
    </svg>
  );
}
