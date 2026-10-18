import { Spinner } from "@/components/ui/feedback";

export default function Loading() {
  return (
    <div className="flex h-full items-center justify-center">
      <Spinner className="size-5" />
    </div>
  );
}
