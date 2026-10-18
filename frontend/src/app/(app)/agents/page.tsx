import type { Metadata } from "next";
import { AgentsPage } from "@/components/pages/AgentsPage";

export const metadata: Metadata = { title: "Agent registry" };

export default function Page() {
  return <AgentsPage />;
}
