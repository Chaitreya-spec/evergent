/**
 * Browser-side calls to the backend. Everything goes through this app's
 * /api/v1 proxy, which attaches the session — the page never sees a token.
 */

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Options = Omit<RequestInit, "body"> & {
  json?: unknown;
  form?: FormData;
  /** Off for pages that expect a 401 (an expired reset link, say). */
  redirectOn401?: boolean;
};

function messageFrom(body: unknown, status: number): string {
  const b = body as {
    message?: string;
    details?: { errors?: { loc?: unknown[]; msg?: string }[] };
  } | null;
  const first = b?.details?.errors?.[0];
  if (first?.msg) {
    const field = first.loc?.at(-1);
    return typeof field === "string" ? `${field}: ${first.msg}` : first.msg;
  }
  if (b?.message) return b.message;
  if (status === 502) return "The backend is not reachable.";
  return `Request failed (${status})`;
}

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const { json, form, headers, redirectOn401 = true, ...rest } = options;
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...rest,
      cache: "no-store",
      headers:
        json === undefined ? headers : { "Content-Type": "application/json", ...headers },
      body: form ?? (json === undefined ? undefined : JSON.stringify(json)),
    });
  } catch {
    throw new ApiError("This app's server is not reachable.", 0);
  }

  if (response.status === 401 && redirectOn401) {
    // The session is over (the proxy has already cleared it).
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.replace(`/signin?next=${next}`);
    throw new ApiError("Signed out", 401);
  }

  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(messageFrom(body, response.status), response.status);
  return body as T;
}

export const errorText = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

/** A path with query parameters, skipping empty ones. Arrays repeat the key. */
export function withQuery(
  path: string,
  params: Record<string, string | number | boolean | string[] | null | undefined>,
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => search.append(key, v));
    else search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}
