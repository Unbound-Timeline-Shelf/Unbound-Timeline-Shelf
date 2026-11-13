/**
 * Episode stills come from metadata providers and plugins. The browser loads
 * them through the host's image cache, which refuses private addresses, never
 * from the stored address itself (#398).
 */
export function episodeStillUrl(
  kind: "tv" | "anime",
  episodeId: string,
  stored: string | null | undefined,
): string | null {
  if (!stored) return null;
  return `/api/media-image/${kind}/episodes/${encodeURIComponent(episodeId)}/still`;
}
