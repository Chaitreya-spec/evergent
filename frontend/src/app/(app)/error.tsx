"use client";

import { RotateCcw, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function WorkspaceError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div role="alert" className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
      <TriangleAlert className="size-6 text-bad" />
      <h1 className="text-sm font-semibold">This page hit an error</h1>
      <p className="max-w-md text-sm text-muted">{error.message || "Something went wrong while rendering."}</p>
      {error.digest && <p className="font-mono text-[11px] text-subtle">Reference {error.digest}</p>}
      <Button onClick={reset}>
        <RotateCcw /> Try again
      </Button>
    </div>
  );
}
