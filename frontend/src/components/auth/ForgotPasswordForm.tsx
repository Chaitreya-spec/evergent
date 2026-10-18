"use client";

import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Notice, Spinner } from "@/components/ui/feedback";
import { Field, Input } from "@/components/ui/form";
import { api, errorText } from "@/lib/api/client";
import type { Accepted } from "@/lib/api/types";
import { AuthCard } from "./AuthCard";

export function ForgotPasswordForm() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<Accepted | null>(null);

  async function submit(form: FormData) {
    setBusy(true);
    setError(null);
    try {
      setAnswer(
        await api<Accepted>("/auth/password-reset/request", {
          method: "POST",
          json: { email: form.get("email") },
          redirectOn401: false,
        }),
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard
      title="Reset your password"
      description="We will email a link that sets a new one."
      footer={<Link href="/signin" className="hover:text-fg">Back to sign in</Link>}
    >
      {answer ? (
        answer.email_delivery ? (
          <Notice title="Check your email">{answer.message}</Notice>
        ) : (
          <Notice tone="warn" title="Email is not set up on this server">
            No message can be sent, so no reset link exists. An administrator needs to configure SMTP
            (see the deployment guide), or reset the password for you.
          </Notice>
        )
      ) : (
        <form action={submit} className="space-y-4">
          <Field label="Email">
            {(p) => <Input {...p} name="email" type="email" required autoComplete="email" />}
          </Field>
          {error && <Notice tone="bad">{error}</Notice>}
          <Button type="submit" variant="primary" size="lg" className="w-full" disabled={busy}>
            {busy && <Spinner className="text-accent-fg" />}
            Send reset link
          </Button>
        </form>
      )}
    </AuthCard>
  );
}
