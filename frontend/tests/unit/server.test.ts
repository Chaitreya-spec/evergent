import type { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const incoming = (forwardedFor?: string) =>
  ({ headers: new Headers(forwardedFor ? { "x-forwarded-for": forwardedFor } : {}) }) as NextRequest;

// The secret is read once, when the module loads, as on the server.
async function withSecret(secret: string) {
  vi.stubEnv("FRONTEND_PROXY_SECRET", secret);
  vi.resetModules();
  return (await import("@/lib/api/server")).clientHeaders;
}

afterEach(() => vi.unstubAllEnvs());

describe("clientHeaders", () => {
  it("passes the client on as X-Forwarded-For when no secret is set", async () => {
    const clientHeaders = await withSecret("");
    expect(clientHeaders(incoming("203.0.113.9"))).toEqual({ "x-forwarded-for": "203.0.113.9" });
  });

  it("vouches for the client with the secret when one is set", async () => {
    const clientHeaders = await withSecret("s".repeat(44));
    expect(clientHeaders(incoming("203.0.113.9"))).toEqual({
      "x-forwarded-for": "203.0.113.9",
      "x-evergent-proxy-secret": "s".repeat(44),
      "x-evergent-client-ip": "203.0.113.9",
    });
  });

  it("takes the hop the platform appended, not one the browser claimed", async () => {
    const clientHeaders = await withSecret("s".repeat(44));
    expect(clientHeaders(incoming("6.6.6.6, 203.0.113.9"))["x-evergent-client-ip"]).toBe("203.0.113.9");
  });

  it("sends nothing, not even the secret, when there is no client address", async () => {
    const clientHeaders = await withSecret("s".repeat(44));
    expect(clientHeaders(incoming())).toEqual({});
  });
});
