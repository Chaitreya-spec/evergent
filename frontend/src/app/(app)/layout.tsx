import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/shell/AppShell";
import { hasSession } from "@/lib/api/server";

export default async function WorkspaceLayout({ children }: LayoutProps<"/">) {
  // Backstop for the proxy's check, in case a matcher change ever skips it.
  if (!hasSession(await cookies())) redirect("/signin");
  return <AppShell>{children}</AppShell>;
}
