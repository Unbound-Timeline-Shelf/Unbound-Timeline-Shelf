// What to say when a page cannot load its data (UX-AUDIT-APP A18). A browser
// network failure ("Failed to fetch", "NetworkError…", "Load failed") is not
// something the person can act on as written; the server's own messages are
// kept, since they say what went wrong.
const NETWORK_FAILURES = [
  /failed to fetch/i,
  /networkerror/i,
  /load failed/i,
  /network request failed/i,
  /fetch failed/i,
  /the internet connection appears to be offline/i,
];

export const SERVER_UNREACHABLE =
  "Couldn't reach the server. Your library is safe; try again in a moment.";

export function isNetworkFailure(message: string | null | undefined): boolean {
  return Boolean(message) && NETWORK_FAILURES.some((re) => re.test(message!));
}

export function friendlyLoadError(message: string | null | undefined): string {
  if (!message || isNetworkFailure(message)) return SERVER_UNREACHABLE;
  return message;
}
