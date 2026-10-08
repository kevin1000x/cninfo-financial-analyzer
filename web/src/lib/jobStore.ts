// Persists the currently-active job id across page reloads. The backend's
// SSE stream replays history on reconnect, so refreshing the tab never
// loses progress as long as uvicorn keeps running.

const KEY = "cninfo:active-job-id";
const storageKey = (owner: string) => owner === "legacy" ? KEY : `${KEY}:${owner}`;

export function loadActiveJobId(owner = "legacy"): string | null {
  try {
    return window.localStorage.getItem(storageKey(owner));
  } catch {
    return null;
  }
}

export function saveActiveJobId(id: string | null, owner = "legacy"): void {
  try {
    if (id) window.localStorage.setItem(storageKey(owner), id);
    else window.localStorage.removeItem(storageKey(owner));
  } catch {
    // ignore quota / private mode failures
  }
}
