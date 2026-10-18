import type { Metadata } from "next";
import { VerifyEmail } from "@/components/auth/VerifyEmail";

export const metadata: Metadata = { title: "Confirm email", referrer: "no-referrer" };

export default async function VerifyEmailPage(props: PageProps<"/verify-email">) {
  const { token } = await props.searchParams;
  return <VerifyEmail token={typeof token === "string" ? token : ""} />;
}
