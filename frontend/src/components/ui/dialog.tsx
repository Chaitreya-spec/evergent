"use client";

import * as RD from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export const Dialog = RD.Root;
export const DialogTrigger = RD.Trigger;
export const DialogClose = RD.Close;

export function DialogContent({
  title,
  description,
  children,
  className,
  wide,
}: {
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  className?: string;
  wide?: boolean;
}) {
  return (
    <RD.Portal>
      <RD.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-[2px]" />
      <RD.Content
        className={cn(
          "fixed left-1/2 top-1/2 z-50 flex max-h-[min(90dvh,56rem)] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg border border-line-strong bg-panel shadow-2xl shadow-black/60 focus:outline-none",
          wide ? "max-w-4xl" : "max-w-lg",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <RD.Title className="text-sm font-semibold text-fg">{title}</RD.Title>
            {description ? (
              <RD.Description className="mt-1 text-xs text-muted">{description}</RD.Description>
            ) : (
              <RD.Description className="sr-only">{typeof title === "string" ? title : "Dialog"}</RD.Description>
            )}
          </div>
          <RD.Close className="rounded p-1 text-subtle hover:bg-panel-3 hover:text-fg" aria-label="Close">
            <X className="size-4" />
          </RD.Close>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </RD.Content>
    </RD.Portal>
  );
}
