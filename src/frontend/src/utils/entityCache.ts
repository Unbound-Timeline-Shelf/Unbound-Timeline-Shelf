// A keep-what-we-last-saw cache for library entities (movies, TV shows,
// anime). Every response that carries an entity is remembered here, so
// opening a title's page can draw it immediately from what the library
// list (or its last visit) already fetched, then quietly refresh it,
// instead of blanking to a loading state on every open.
//
// The caches are shared by every page but belong to one signed-in account
// (#468). When the account changes, resetEntityCaches() empties all of them
// and starts a new epoch; a response to a request started in an earlier
// epoch is still returned to its caller but never stored, so a late answer
// for the previous account cannot reappear as the next account's content.
export interface EntityCache<T extends { id: string }> {
  put(item: T): T;
  peek(id: string): T | undefined;
  remove(id: string): void;
  all(): T[];
  /** true once a full list has been loaded, so all() is complete */
  listLoaded(): boolean;
  markListLoaded(): void;
}

let epoch = 0;
// the epoch of the request whose response is being stored, while atEpoch runs
let writingEpoch: number | null = null;
const resetHandlers = new Set<() => void>();

/** The current account epoch; capture it before a request is sent. */
export function entityCacheEpoch(): number {
  return epoch;
}

/** Whether a request started at `started` still belongs to the current account. */
export function isCurrentEpoch(started: number): boolean {
  return started === epoch;
}

/**
 * Run `store` (which maps a response into the caches) on behalf of a request
 * started at `started`: when the account has changed since, nothing it
 * puts into a cache is kept.
 */
export function atEpoch<T>(started: number, store: () => T): T {
  const previous = writingEpoch;
  writingEpoch = started;
  try {
    return store();
  } finally {
    writingEpoch = previous;
  }
}

function writable(): boolean {
  return (writingEpoch ?? epoch) === epoch;
}

/** Also clear `reset` (another per-account cache) whenever the caches reset. */
export function onEntityCacheReset(reset: () => void): void {
  resetHandlers.add(reset);
}

/** The signed-in account changed (or signed out): forget every snapshot. */
export function resetEntityCaches(): void {
  epoch++;
  for (const reset of resetHandlers) reset();
}

export function createEntityCache<T extends { id: string }>(): EntityCache<T> {
  const map = new Map<string, T>();
  let loaded = false;
  onEntityCacheReset(() => {
    map.clear();
    loaded = false;
  });
  return {
    put(item) {
      if (writable()) map.set(item.id, item);
      return item;
    },
    peek: (id) => map.get(id),
    remove: (id) => void map.delete(id),
    all: () => [...map.values()],
    listLoaded: () => loaded,
    markListLoaded: () => {
      if (writable()) loaded = true;
    },
  };
}
