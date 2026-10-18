import type { Metadata } from "next";
import { WorkflowWorkspace } from "@/components/workspace/WorkflowWorkspace";

export const metadata: Metadata = { title: "Workflow" };

export default async function WorkflowPage(props: PageProps<"/workflows/[id]">) {
  const { id } = await props.params;
  return <WorkflowWorkspace key={id} id={id} />;
}
