import type { Metadata } from "next";
import { Suspense } from "react";
import { WorkflowsPage } from "@/components/pages/WorkflowsPage";

export const metadata: Metadata = { title: "Execution history" };

export default function Page() {
  return (
    <Suspense>
      <WorkflowsPage />
    </Suspense>
  );
}
