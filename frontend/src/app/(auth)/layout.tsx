import { Wordmark } from "@/components/shell/Wordmark";

export default function AuthLayout({ children }: LayoutProps<"/">) {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center px-4 py-10">
      <Wordmark className="mb-8" />
      <div className="w-full max-w-sm">{children}</div>
    </main>
  );
}
