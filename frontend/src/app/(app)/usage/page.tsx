import type { Metadata } from "next";
import { UsagePage } from "@/components/pages/UsagePage";

export const metadata: Metadata = { title: "Usage & cost" };

export default function Page() {
  return <UsagePage />;
}
