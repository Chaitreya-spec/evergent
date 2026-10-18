import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";

export function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return { client, ...render(<QueryClientProvider client={client}><TooltipProvider>{ui}</TooltipProvider></QueryClientProvider>) };
}

type Route = (init: RequestInit | undefined, url: URL) => unknown;

/** A fetch that answers /api/v1 paths from a table, recording every call. */
export function mockApi(routes: Record<string, Route | unknown>) {
  const calls: { method: string; path: string; body: unknown; headers: Headers }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
    calls.push({ method, path, body, headers: new Headers(init?.headers) });
    const key = `${method} ${path}`;
    const route = key in routes ? routes[key] : routes[path];
    if (route === undefined) return new Response(JSON.stringify({ message: `unmocked ${key}` }), { status: 404 });
    const value = typeof route === "function" ? (route as Route)(init, url) : route;
    return new Response(JSON.stringify(value), { status: 200, headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}
