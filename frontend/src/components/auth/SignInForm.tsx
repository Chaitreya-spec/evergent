"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Notice, Spinner } from "@/components/ui/feedback";
import { Field, Input } from "@/components/ui/form";
import { AuthCard } from "./AuthCard";

/** Email and password against the backend's own accounts. */
export function SignInForm({ next, initialMode = "login" }: { next: string; initialMode?: "login" | "register" }) {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">(initialMode);
  const [error, setError] = useState<string | null>(null);
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
