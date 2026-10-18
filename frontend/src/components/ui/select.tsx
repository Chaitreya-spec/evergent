"use client";

import * as RS from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/cn";

export type Option = { value: string; label: string; hint?: string; disabled?: boolean };

/** A themed, keyboard-accessible single select. */
export function Select({
  value,
  onValueChange,
  options,
  placeholder = "Select…",
  id,
  className,
  "aria-label": ariaLabel,
  disabled,
}: {
  value: string | undefined;
  onValueChange: (value: string) => void;
  options: Option[];
  placeholder?: string;
  id?: string;
  className?: string;
  "aria-label"?: string;
  disabled?: boolean;
}) {
  return (
    <RS.Root value={value} onValueChange={onValueChange} disabled={disabled}>
      <RS.Trigger
        id={id}
        aria-label={ariaLabel}
        className={cn(
          "inline-flex h-9 w-full items-center justify-between gap-2 rounded-md border border-line bg-panel-2 px-3 text-left text-sm text-fg hover:border-line-strong focus:border-accent focus:outline-none data-[placeholder]:text-subtle disabled:opacity-50",
          className,
        )}
      >
        <RS.Value placeholder={placeholder} />
        <RS.Icon>
          <ChevronDown className="size-4 text-subtle" />
        </RS.Icon>
      </RS.Trigger>
      <RS.Portal>
        <RS.Content
          position="popper"
          sideOffset={4}
          className="z-50 max-h-[min(24rem,var(--radix-select-content-available-height))] min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-md border border-line-strong bg-panel-2 shadow-2xl shadow-black/50"
        >
          <RS.Viewport className="p-1">
            {options.map((option) => (
              <RS.Item
                key={option.value}
                value={option.value}
                disabled={option.disabled}
                className="relative flex cursor-default select-none items-start gap-2 rounded px-2 py-1.5 pr-7 text-sm text-fg outline-none data-[disabled]:opacity-40 data-[highlighted]:bg-panel-3"
              >
                <div className="min-w-0">
                  <RS.ItemText>{option.label}</RS.ItemText>
                  {option.hint && <div className="truncate text-xs text-subtle">{option.hint}</div>}
                </div>
                <RS.ItemIndicator className="absolute right-2 top-2">
                  <Check className="size-3.5 text-accent" />
                </RS.ItemIndicator>
              </RS.Item>
            ))}
          </RS.Viewport>
        </RS.Content>
      </RS.Portal>
    </RS.Root>
  );
}
