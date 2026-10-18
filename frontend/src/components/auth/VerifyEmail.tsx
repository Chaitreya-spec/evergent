"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Notice, Spinner } from "@/components/ui/feedback";
import { api, ApiError, errorText } from "@/lib/api/client";
import { AuthCard } from "./AuthCard";

export function VerifyEmail({ token }: { token: string }) {
  const [state, setState] = useState<"working" | "done" | { error: string }>(token ? "working" : { error: "This page needs the link from the confirmation email." });
  const started = useRef(false);

  useEffect(() => {
    if (!token || started.current) return;
    started.current = true;
    api("/auth/verify-email/confirm", { method: "POST", json: { token }, redirectOn401: false })
      .then(() => setState("done"))
      .catch((e) =>
        setState({
          error: e instanceof ApiError && e.status === 401 ? "This link has expired or was already used." : errorText(e),
        }),
      );
  }, [token]);

  return (
    <AuthCard title="Confirm your email" footer={<Link href="/" className="hover:text-fg">Go to the workspace</Link>}>
      {state === "working" ? (
        <div className="flex items-center gap-2 text-sm text-muted">
          <Spinner /> Confirming…
        </div>
      ) : state === "done" ? (
        <Notice title="Email confirmed">Thanks — your address is verified.</Notice>
      ) : (
        <Notice tone="bad">{state.error}</Notice>
      )}
    </AuthCard>
  );
}
