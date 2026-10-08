// Persists the currently-active job id across page reloads. The backend's
// SSE stream replays history on reconnect, so refreshing the tab never
// loses progress as long as uvicorn keeps running.

const KEY = "cninfo:active-job-id";

export function loadActiveJobId(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function saveActiveJobId(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(KEY, id);
    else window.localStorage.removeItem(KEY);
  } catch {
    // ignore quota / private mode failures
  }
}
