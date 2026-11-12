import { ref, watch, type Ref } from "vue";

// A record from a response is a new object with new arrays and dates, so a
// field counts as changed only when its value differs, not its identity.
function same(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || !a || !b) return false;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}

interface Queue<T> {
  tail: Promise<void>;
  // The value of each field as the server last confirmed it.
  confirmed: T;
  // The newest value asked for, per field with a quick save in flight.
  wanted: Map<keyof T, unknown>;
  pending: number;
  // Bumped per field when another writer (the full editor, a progress
  // action) replaces the record with a different value for that field.
  replaced: Map<keyof T, number>;
  fields: Set<keyof T>;
  errors: Map<keyof T, string>;
}

/**
 * Serialize quick edits per title, sending only the changed fields. Independent
 * editors may replace the record while a request is pending. A replacement is
 * reconciled per field: a field the replacement changed is that writer's, so an
 * older quick-save answer for it is neither applied nor reported; a field it
 * left as the server last confirmed it still belongs to the pending quick save,
 * whose answer (success or failure) is applied when it arrives.
 */
export function useMediaQuickSave<T extends { id: string }>(
  record: Ref<T | null>,
  persist: (id: string, changes: Partial<T>) => Promise<T>,
) {
  const saveError = ref<string | null>(null);
  const queues = new Map<string, Queue<T>>();
  watch(
    record,
    (value, previous) => {
      if (value?.id !== previous?.id) saveError.value = null;
      const queue = value && queues.get(value.id);
      if (!queue || value === previous) return;
      for (const key of Object.keys(value) as (keyof T)[]) {
        if (same(value[key], queue.confirmed[key])) continue;
        queue.confirmed[key] = value[key];
        queue.replaced.set(key, (queue.replaced.get(key) ?? 0) + 1);
        queue.wanted.delete(key);
        queue.errors.delete(key);
      }
      // Fields the replacement did not touch keep showing their pending change.
      for (const [key, wanted] of queue.wanted)
        value[key] = wanted as T[keyof T];
    },
    { flush: "sync" },
  );

  async function quickSave(
    change: (target: T) => void,
    failure: string,
  ): Promise<boolean> {
    const target = record.value;
    if (!target) return false;
    const id = target.id;
    let queue = queues.get(id);
    if (!queue) {
      queue = {
        tail: Promise.resolve(),
        confirmed: { ...target },
        wanted: new Map(),
        pending: 0,
        replaced: new Map(),
        fields: new Set(),
        errors: new Map(),
      };
      queues.set(id, queue);
    }
    const current = queue;
    const next = { ...target };
    change(next);
    const fields = (Object.keys(next) as (keyof T)[]).filter(
      (key) => !Object.is(next[key], target[key]),
    );
    const changes: Partial<T> = {};
    for (const key of fields) {
      changes[key] = next[key];
      target[key] = next[key];
      current.fields.add(key);
      current.wanted.set(key, next[key]);
    }
    current.pending += 1;
    saveError.value = null;
    let saved = false;
    current.tail = current.tail.then(async () => {
      // Only the fields no other writer has replaced since this request was sent.
      const sentAt = new Map(
        fields.map((key) => [key, current.replaced.get(key) ?? 0]),
      );
      const own = () =>
        fields.filter(
          (key) => (current.replaced.get(key) ?? 0) === sentAt.get(key),
        );
      try {
        const result = await persist(id, changes);
        for (const key of own()) {
          current.confirmed[key] = result[key];
          current.errors.delete(key);
        }
        saved = true;
      } catch {
        for (const key of own()) current.errors.set(key, failure);
      }
    });
    await current.tail;
    current.pending -= 1;
    if (current.pending === 0) {
      queues.delete(id);
      if (record.value?.id === id) {
        for (const key of current.fields)
          record.value[key] = current.confirmed[key];
        saveError.value = [...current.errors.values()].at(-1) ?? null;
      }
    }
    return saved;
  }

  return { saveError, quickSave };
}
