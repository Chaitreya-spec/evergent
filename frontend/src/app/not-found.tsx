import Link from "next/link";
import { Mark } from "@/components/shell/Wordmark";

export default function NotFound() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-4 px-4 text-center">
      <Mark className="size-8" />
      <h1 className="text-base font-semibold">Page not found</h1>
      <p className="text-sm text-muted">There is nothing at this address.</p>
      <Link href="/" className="text-sm text-accent hover:underline">
        Back to the workspace
      </Link>
    </main>
  );
}
