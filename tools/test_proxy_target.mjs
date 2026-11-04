// Resolve a test proxy request without allowing the incoming target to change its backend.
export function testProxyTarget(requestTarget, backendUrl) {
  try {
    const base = new URL(backendUrl);
    if (base.protocol !== "http:" || base.username || base.password ||
        typeof requestTarget !== "string" || !requestTarget.startsWith("/") ||
        requestTarget.startsWith("//")) return null;
    const target = new URL(requestTarget, base);
    if (target.origin !== base.origin || target.username || target.password || target.hash) return null;
    return target;
  } catch {
    return null;
  }
}
