"use client";

import * as RT from "@radix-ui/react-tabs";
import { cn } from "@/lib/cn";

export const Tabs = RT.Root;
export const TabsContent = RT.Content;

export function TabsList({ className, ...props }: React.ComponentProps<typeof RT.List>) {
  return <RT.List className={cn("flex items-center gap-0.5", className)} {...props} />;
}

export function TabsTrigger({ className, ...props }: React.ComponentProps<typeof RT.Trigger>) {
  return (
    <RT.Trigger
      className={cn(
        "inline-flex h-7 items-center gap-1.5 whitespace-nowrap rounded px-2.5 text-xs font-medium text-subtle transition-colors hover:text-fg data-[state=active]:bg-panel-3 data-[state=active]:text-fg [&_svg]:size-3.5",
        className,
      )}
      {...props}
    />
  );
}
