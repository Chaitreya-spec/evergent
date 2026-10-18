"use client";

import * as RM from "@radix-ui/react-dropdown-menu";
import { cn } from "@/lib/cn";

export const Menu = RM.Root;
export const MenuTrigger = RM.Trigger;

export function MenuContent({ className, ...props }: React.ComponentProps<typeof RM.Content>) {
  return (
    <RM.Portal>
      <RM.Content
        sideOffset={6}
        className={cn(
          "z-50 min-w-48 rounded-md border border-line-strong bg-panel-2 p-1 shadow-2xl shadow-black/50",
          className,
        )}
        {...props}
      />
    </RM.Portal>
  );
}

export function MenuItem({ className, ...props }: React.ComponentProps<typeof RM.Item>) {
  return (
    <RM.Item
      className={cn(
        "flex cursor-default select-none items-center gap-2 rounded px-2 py-1.5 text-sm text-fg outline-none data-[disabled]:opacity-40 data-[highlighted]:bg-panel-3 [&_svg]:size-4 [&_svg]:text-subtle",
        className,
      )}
      {...props}
    />
  );
}

export function MenuLabel({ className, ...props }: React.ComponentProps<typeof RM.Label>) {
  return <RM.Label className={cn("px-2 py-1.5 text-xs text-subtle", className)} {...props} />;
}

export const MenuSeparator = () => <RM.Separator className="my-1 h-px bg-line" />;
