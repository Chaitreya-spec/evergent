"use client";

import { useId } from "react";
import { cn } from "@/lib/cn";

export type Segment<T extends string> = { value: T; label: string; hint?: string };

/** A row of radio buttons styled as one control. Native radios: arrow keys work. */
export function Segmented<T extends string>({
  legend,
  value,
  onChange,
  options,
  className,
}: {
  legend: string;
  value: T;
  onChange: (value: T) => void;
  options: Segment<T>[];
  className?: string;
}) {
  const name = useId();
  return (
    <fieldset className={cn("min-w-0", className)}>
      <legend className="mb-1.5 text-xs font-medium text-muted">{legend}</legend>
      <div className="flex flex-wrap gap-1 rounded-md border border-line bg-panel-2 p-0.5">
        {options.map((option) => (
          <label
            key={option.value}
            title={option.hint}
            className={cn(
              "relative flex h-7 flex-1 cursor-pointer items-center justify-center whitespace-nowrap rounded px-2.5 text-xs font-medium text-subtle transition-colors hover:text-fg has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent",
              value === option.value && "bg-panel-3 text-fg",
            )}
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
              className="sr-only"
            />
            {option.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
