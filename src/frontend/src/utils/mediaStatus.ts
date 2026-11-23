// The single MAL-style 5-value status set every media surface shows to
// the user (List/Shelf/Board tabs, status dropdowns on the create/edit
// forms and detail pages). The real backend enum still has 8 values
// (wishlist/watchlist/backlog/in progress/watched/favorite/rewatch/
// dropped) since `favorite` and `rewatch` distinctions are useful to keep
// on the row itself — but nothing in the UI should ever present all 8 as
// separate choices. Reads collapse into these 5 buckets; writes expand
// back to one canonical real value per bucket.
export const STATUS_BUCKETS = [
  { key: "watching", label: "Watching" },
  { key: "completed", label: "Completed" },
  { key: "hold", label: "On Hold" },
  { key: "dropped", label: "Dropped" },
  { key: "plan", label: "Plan to Watch" },
] as const;

export type StatusBucketKey = (typeof STATUS_BUCKETS)[number]["key"];

// Books (#265) keep the same five tabs, read for reading: Wishlist and
// Backlog are both still to read, and On hold is its own status. Every
// helper below takes the kind; without one it is the movie/TV/anime set.
export type MediaKind = "movie" | "tv" | "anime" | "book";

export const BOOK_STATUS_BUCKETS: readonly {
  key: StatusBucketKey;
  label: string;
}[] = [
  { key: "watching", label: "Reading" },
  { key: "completed", label: "Read" },
  { key: "hold", label: "On Hold" },
  { key: "dropped", label: "Dropped" },
  { key: "plan", label: "Plan to Read" },
];

const BOOK_REAL_TO_BUCKET: Record<string, StatusBucketKey> = {
  wishlist: "plan",
  backlog: "plan",
  "in progress": "watching",
  "on hold": "hold",
  read: "completed",
  dropped: "dropped",
};

const BOOK_BUCKET_TO_REAL: Record<StatusBucketKey, string> = {
  plan: "wishlist",
  hold: "on hold",
  watching: "in progress",
  completed: "read",
  dropped: "dropped",
};

export function statusBuckets(
  kind?: MediaKind,
): readonly { key: StatusBucketKey; label: string }[] {
  return kind === "book" ? BOOK_STATUS_BUCKETS : STATUS_BUCKETS;
}

function realToBucket(kind?: MediaKind): Record<string, StatusBucketKey> {
  return kind === "book" ? BOOK_REAL_TO_BUCKET : REAL_TO_BUCKET;
}

const REAL_TO_BUCKET: Record<string, StatusBucketKey> = {
  wishlist: "plan",
  watchlist: "plan",
  backlog: "hold",
  "in progress": "watching",
  watched: "completed",
  favorite: "completed",
  rewatch: "watching",
  dropped: "dropped",
};

const BUCKET_TO_REAL: Record<StatusBucketKey, string> = {
  plan: "wishlist",
  hold: "backlog",
  watching: "in progress",
  completed: "watched",
  dropped: "dropped",
};

export function statusBucket(
  realStatus: string,
  kind?: MediaKind,
): StatusBucketKey {
  return realToBucket(kind)[realStatus] ?? "plan";
}

export function bucketToReal(bucket: string, kind?: MediaKind): string {
  const map = kind === "book" ? BOOK_BUCKET_TO_REAL : BUCKET_TO_REAL;
  return map[bucket as StatusBucketKey] ?? bucket;
}

// The real status to save when a form shows only the five buckets: an
// unchanged bucket keeps the stored value, so Favorite and Rewatch are not
// silently rewritten to Watched / In progress.
export function resolveStatus(
  bucket: string,
  currentReal?: string,
  kind?: MediaKind,
): string {
  if (currentReal && statusBucket(currentReal, kind) === bucket)
    return currentReal;
  return bucketToReal(bucket, kind);
}

export function statusBucketLabel(
  realStatus: string,
  kind?: MediaKind,
): string {
  const bucket = statusBucket(realStatus, kind);
  const buckets = statusBuckets(kind);
  return (
    buckets.find((s) => s.key === bucket)?.label ??
    buckets[buckets.length - 1]!.label
  );
}

export function mediaBucketCounts(
  realCounts: Record<string, number>,
  kind?: MediaKind,
): Record<string, number> {
  const counts: Record<string, number> = {
    all: 0,
    ...Object.fromEntries(STATUS_BUCKETS.map(({ key }) => [key, 0])),
  };
  for (const [status, count] of Object.entries(realCounts)) {
    const bucket =
      realToBucket(kind)[status.toLowerCase().replaceAll("_", " ")];
    if (!bucket || !Number.isFinite(count) || count < 0) continue;
    counts[bucket] += count;
    counts.all += count;
  }
  return counts;
}
