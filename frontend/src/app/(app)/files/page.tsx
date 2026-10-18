import type { Metadata } from "next";
import { FilesPage } from "@/components/pages/FilesPage";

export const metadata: Metadata = { title: "Files & artifacts" };

export default function Page() {
  return <FilesPage />;
}
