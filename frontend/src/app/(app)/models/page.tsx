import type { Metadata } from "next";
import { ModelsPage } from "@/components/pages/ModelsPage";

export const metadata: Metadata = { title: "Models & providers" };

export default function Page() {
  return <ModelsPage />;
}
