/** Per-browser conveniences. Storage can be missing or throw; nothing depends on it. */
export function remember(key: string, value: string): void {
  try {
    window.localStorage.setItem(`evg:${key}`, value);
  } catch {
    // private mode or blocked storage: forget it
  }
}

export function recall(key: string): string | null {
  try {
    return window.localStorage.getItem(`evg:${key}`);
  } catch {
    return null;
  }
}
