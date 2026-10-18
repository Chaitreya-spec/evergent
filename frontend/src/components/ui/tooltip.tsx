"use client";

import * as RTip from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

export const TooltipProvider = RTip.Provider;

export function Tooltip({ content, children, side = "top" }: { content: ReactNode; children: ReactNode; side?: "top" | "right" | "bottom" | "left" }) {
  return (
    <RTip.Root delayDuration={300}>
      <RTip.Trigger asChild>{children}</RTip.Trigger>
      <RTip.Portal>
        <RTip.Content
          side={side}
          sideOffset={6}
          className="z-50 max-w-xs rounded border border-line-strong bg-panel-3 px-2 py-1 text-xs text-fg shadow-lg"
        >
          {content}
        </RTip.Content>
      </RTip.Portal>
    </RTip.Root>
  );
}
