// Activity the server stores as a calendar day (episode progress, status
// changes, rewatches) is recorded on the device's own day, the same zone
// Statistics reads with (#473). The app's own /api requests carry that zone as
// X-Timezone; the server ignores an unknown zone and then keeps its own day.
import { viewerTimeZone } from "./mediaStats";

export const TIME_ZONE_HEADER = "X-Timezone";

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

// Only this app's own API: a same-origin path or an absolute URL on this origin.
function isOwnApi(url: string, origin: string): boolean {
  if (url.startsWith("/api/")) return true;
  try {
    const parsed = new URL(url);
    return parsed.origin === origin && parsed.pathname.startsWith("/api/");
  } catch {
    return false;
  }
}

export function installViewerTimeZoneHeader(
  target: Pick<Window, "fetch" | "location"> = window,
  zone: () => string = viewerTimeZone,
): void {
  const real = target.fetch.bind(target);
  target.fetch = (input, init) => {
    if (!isOwnApi(requestUrl(input), target.location.origin)) {
      return real(input, init);
    }
    const headers = new Headers(
      init?.headers ?? (input instanceof Request ? input.headers : undefined),
    );
    if (!headers.has(TIME_ZONE_HEADER)) headers.set(TIME_ZONE_HEADER, zone());
    return real(input, { ...init, headers });
  };
}
