import type { Metadata } from "next";
import { SignInForm } from "@/components/auth/SignInForm";
import { googleClientId } from "@/lib/api/server";
import { safeNext } from "@/lib/safe-next";

export const metadata: Metadata = { title: "Sign in" };

export default async function SignInPage(props: PageProps<"/signin">) {
  const params = await props.searchParams;
  const error = typeof params.error === "string" ? params.error.slice(0, 300) : null;
  return (
    <SignInForm
      next={safeNext(params.next)}
      initialMode={params.mode === "register" ? "register" : "login"}
      google={(await googleClientId()) !== null}
      initialError={error}
    />
  );
}
