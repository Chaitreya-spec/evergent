/**
 * Parse a timestamp from the backend. A stamp with no zone is UTC — the
 * backend stores UTC — and must not be read as the browser's local time.
 */
export function parseTime(iso: string | null | undefined): number {
  if (!iso) return Number.NaN;
  const zoned = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : `${iso}Z`;
  return Date.parse(zoned);
}
