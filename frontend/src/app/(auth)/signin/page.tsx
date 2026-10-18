import type { Metadata } from "next";
import { SignInForm } from "@/components/auth/SignInForm";
import { safeNext } from "@/lib/safe-next";

export const metadata: Metadata = { title: "Sign in" };

export default async function SignInPage(props: PageProps<"/signin">) {
  const params = await props.searchParams;
  return (
    <SignInForm next={safeNext(params.next)} initialMode={params.mode === "register" ? "register" : "login"} />
  );
}
