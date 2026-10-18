"use client";

import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Notice, Spinner } from "@/components/ui/feedback";
import { Field, Input } from "@/components/ui/form";
import { api, ApiError, errorText } from "@/lib/api/client";
import { AuthCard } from "./AuthCard";

export function ResetPasswordForm({ token }: { token: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(form: FormData) {
    const password = String(form.get("password") ?? "");
    if (password !== form.get("confirm")) {
      setError("The two passwords differ.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api("/auth/password-reset/confirm", {
        method: "POST",
        json: { token, new_password: password },
        redirectOn401: false,
      });
      setDone(true);
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 401
          ? "This link has expired or was already used. Ask for a new one."
          : errorText(e),
      );
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <AuthCard title="Reset your password">
        <Notice tone="bad">This page needs the link from the reset email.</Notice>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Choose a new password" footer={<Link href="/signin" className="hover:text-fg">Back to sign in</Link>}>
      {done ? (
        <Notice title="Password changed">
          Every existing session was signed out. <Link href="/signin" className="text-fg underline">Sign in</Link> with the new password.
        </Notice>
      ) : (
        <form action={submit} className="space-y-4">
          <Field label="New password" hint="At least 10 characters.">
            {(p) => <Input {...p} name="password" type="password" required minLength={10} autoComplete="new-password" />}
          </Field>
          <Field label="Repeat it">
            {(p) => <Input {...p} name="confirm" type="password" required minLength={10} autoComplete="new-password" />}
          </Field>
          {error && <Notice tone="bad">{error}</Notice>}
          <Button type="submit" variant="primary" size="lg" className="w-full" disabled={busy}>
            {busy && <Spinner className="text-accent-fg" />}
            Set password
          </Button>
        </form>
      )}
    </AuthCard>
  );
}
