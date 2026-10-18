"use client";

import * as RD from "@radix-ui/react-dialog";
import { Menu as MenuIcon } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { Suspense, useEffect, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Mark } from "./Wordmark";
import { Sidebar } from "./Sidebar";

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [drawer, setDrawer] = useState(false);

  // Global shortcut: N starts a new task from anywhere.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || isTyping(event.target)) return;
      if (event.key === "n" || event.key === "N") {
        event.preventDefault();
        if (pathname === "/") document.getElementById("task-goal")?.focus();
        else router.push("/?focus=1");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pathname, router]);

  return (
    <div className="flex h-dvh overflow-hidden">
      <a
        href="#main"
        className="sr-only z-50 rounded bg-accent px-3 py-2 text-accent-fg focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Skip to content
      </a>
      <aside className="hidden w-60 shrink-0 border-r border-line lg:block">
        <Suspense>
          <Sidebar />
        </Suspense>
      </aside>

      <RD.Root open={drawer} onOpenChange={setDrawer}>
        <RD.Portal>
          <RD.Overlay className="fixed inset-0 z-40 bg-black/60 lg:hidden" />
          <RD.Content className="fixed inset-y-0 left-0 z-50 w-72 max-w-[85vw] border-r border-line focus:outline-none lg:hidden">
            <RD.Title className="sr-only">Navigation</RD.Title>
            <RD.Description className="sr-only">Workspace navigation</RD.Description>
            <Suspense>
              <Sidebar onNavigate={() => setDrawer(false)} />
            </Suspense>
          </RD.Content>
        </RD.Portal>
      </RD.Root>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-panel px-3 lg:hidden">
          <Button variant="ghost" size="icon" aria-label="Open navigation" onClick={() => setDrawer(true)}>
            <MenuIcon />
          </Button>
          <Mark className="size-5" />
          <span className="text-sm font-semibold">Evergent</span>
        </div>
        <main id="main" className="min-h-0 flex-1 overflow-hidden">
          {children}
        </main>
      </div>
    </div>
  );
}
