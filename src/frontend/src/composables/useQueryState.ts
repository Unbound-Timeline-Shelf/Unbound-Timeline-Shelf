// The one navigation rule (UX-AUDIT-APP §3.1): a tab, section or view that
// is a place you can be lives in the address, and choosing it adds a history
// step, so Back walks back through what you looked at and a reload or a
// shared link opens the same view. Without a router (an isolated component
// test) the value simply lives in the component.
import { computed, inject, ref, type WritableComputedRef } from "vue";
import { routeLocationKey, routerKey } from "vue-router";

export interface QueryStateOptions<T extends string> {
  values: readonly T[];
  // the value shown when the address names none (or an unknown one)
  fallback: () => T;
  // leave the fallback out of the address instead of writing it
  omitFallback?: boolean;
}

export function useQueryState<T extends string>(
  key: string,
  options: QueryStateOptions<T>,
): WritableComputedRef<T> {
  const router = inject(routerKey, null);
  const route = inject(routeLocationKey, null);
  const local = ref(options.fallback()) as { value: T };
  const known = (value: unknown): value is T =>
    typeof value === "string" && options.values.includes(value as T);
  return computed<T>({
    get() {
      if (!router || !route) return local.value;
      const value = route.query[key];
      return known(value) ? value : options.fallback();
    },
    set(next) {
      if (!router || !route) {
        local.value = next;
        return;
      }
      const current = route.query[key];
      const query = { ...route.query };
      if (options.omitFallback && next === options.fallback())
        delete query[key];
      else query[key] = next;
      if ((known(current) ? current : undefined) === query[key]) return;
      void router.push({ path: route.path, query, hash: route.hash });
    },
  });
}
