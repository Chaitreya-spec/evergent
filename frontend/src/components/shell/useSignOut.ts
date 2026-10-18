"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

export function useSignOut() {
  const router = useRouter();
  const queries = useQueryClient();
  return async (everywhere = false) => {
    try {
      const response = await fetch(`/api/session${everywhere ? "?everywhere=true" : ""}`, { method: "DELETE" });
      if (response.status === 202) {
        const body = await response.json().catch(() => null);
        toast.warning(body?.message ?? "Signed out of this browser only.");
      }
    } catch {
      toast.error("Could not reach this app's server to sign out.");
      return;
    }
    queries.clear();
    router.replace("/signin");
    router.refresh();
  };
}
