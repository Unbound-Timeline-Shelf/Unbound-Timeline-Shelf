// Resolve a test proxy request without allowing the incoming target to change its backend.
export function testProxyTarget(requestTarget, backendUrl) {
  try {
    const base = new URL(backendUrl);
    if (base.protocol !== "http:" || base.username || base.password ||
        typeof requestTarget !== "string" || !requestTarget.startsWith("/") ||
        requestTarget.startsWith("//")) return null;
    const target = new URL(requestTarget, base);
    if (target.origin !== base.origin || target.username || target.password || target.hash) return null;
    // Authority is taken only from trusted configuration, never from the request.
    const hostname = base.hostname.startsWith("[") ? base.hostname.slice(1, -1) : base.hostname;
    return { hostname, port: base.port, path: target.pathname + target.search };
  } catch {
    return null;
  }
}
