// Route-level scroll restoration (UX-AUDIT-APP §3.2, A3-A5, A10).
//
// vue-router already keeps a per-history-entry position: in memory for
// Back/Forward and in `history.state.scroll` across a reload (written on
// `beforeunload`; `pagehide` is added here for mobile browsers that skip it).
// What went wrong was the timing: the saved position was applied the moment
// the route changed, before the asynchronous view had any height, so the
// browser clamped it to 0. Here a restore waits until every view on screen
// says it is ready and the document is tall enough, asking paged lists to
// load more pages on the way, and gives up if the person scrolls first.
//
// Kept-alive views (the libraries, Home, Calendar...) also remember where you
// left them for an ordinary link back, keyed on the normalised address so
// "/games" and "/games?filters=0" are the same place (A4).
import {
  getCurrentInstance,
  onActivated,
  onDeactivated,
  onUnmounted,
} from "vue";
import type { LocationQuery, RouteLocationNormalized } from "vue-router";

export interface ScrollTarget {
  left: number;
  top: number;
}

interface Readiness {
  ready: () => boolean;
  grow?: () => unknown;
}

const readiness = new Set<Readiness>();

/**
 * Declare when this view's content is in place, so a scroll restore waits for
 * it. `grow` is asked to load more (another page of a list) while the document
 * is still shorter than the position being restored.
 */
export function useScrollReady(
  ready: () => boolean,
  grow?: () => unknown,
): void {
  const entry: Readiness = { ready, grow };
  readiness.add(entry);
  if (!getCurrentInstance()) return;
  onActivated(() => readiness.add(entry));
  onDeactivated(() => readiness.delete(entry));
  onUnmounted(() => readiness.delete(entry));
}

export function viewsReady(): boolean {
  for (const entry of readiness) if (!entry.ready()) return false;
  return true;
}

const RESTORE_TIMEOUT_MS = 4000;
const POLL_MS = 40;
// keys that only change how a page looks, never where you are
const PRESENTATION_KEYS = new Set(["filters"]);
let restoreGeneration = 0;

function maxScrollY(): number {
  return Math.max(
    0,
    document.documentElement.scrollHeight - window.innerHeight,
  );
}

/** Stop any restore still waiting, e.g. because another navigation started. */
export function cancelScrollRestore(): void {
  ++restoreGeneration;
}

/**
 * Resolves true once the views are ready and the document can scroll to
 * `top`, false if the person scrolled, typed or navigated first. After the
 * timeout it resolves true anyway, so the browser lands as close as it can.
 */
export function waitForScrollTarget(
  top: number,
  timeout = RESTORE_TIMEOUT_MS,
): Promise<boolean> {
  const generation = ++restoreGeneration;
  const started = Date.now();
  return new Promise((resolve) => {
    let growing: Promise<unknown> | null = null;
    let interrupted = false;
    const interrupt = () => {
      interrupted = true;
    };
    const events = ["wheel", "touchmove", "keydown"] as const;
    for (const name of events)
      window.addEventListener(name, interrupt, { passive: true });
    const finish = (value: boolean) => {
      for (const name of events) window.removeEventListener(name, interrupt);
      resolve(value);
    };
    const check = () => {
      if (interrupted || generation !== restoreGeneration) return finish(false);
      const ready = viewsReady();
      if (ready && maxScrollY() >= top - 1) return finish(true);
      if (Date.now() - started >= timeout) return finish(true);
      if (ready && !growing) {
        for (const entry of readiness) {
          const result = entry.grow?.();
          if (result instanceof Promise) {
            growing = result.finally(() => {
              growing = null;
            });
            break;
          }
        }
      }
      window.setTimeout(check, POLL_MS);
    };
    check();
  });
}

/** Restores `target` once the page can show it, or false when interrupted. */
export async function restoreWhenReady(
  target: ScrollTarget,
): Promise<ScrollTarget | false> {
  if (!(await waitForScrollTarget(target.top))) return false;
  return { left: target.left, top: target.top };
}

/** The address without presentation-only parameters, with sorted keys. */
export function scrollKey(path: string, query: LocationQuery): string {
  const entries = Object.entries(query)
    .filter(([key, value]) => !PRESENTATION_KEYS.has(key) && value != null)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => `${key}=${JSON.stringify(value)}`);
  return entries.length ? `${path}?${entries.join("&")}` : path;
}

const keptPositions = new Map<string, ScrollTarget>();

/** Called before every navigation with the page being left. */
export function rememberScroll(
  from: RouteLocationNormalized,
  position: ScrollTarget,
): void {
  if (!from.meta.keepsScroll) return;
  keptPositions.set(scrollKey(from.path, from.query), position);
}

/** Where a kept-alive page was left, for an ordinary link back to it. */
export function keptScroll(
  to: RouteLocationNormalized,
): ScrollTarget | undefined {
  if (!to.meta.keepsScroll) return undefined;
  const saved = keptPositions.get(scrollKey(to.path, to.query));
  return saved && saved.top > 0 ? saved : undefined;
}

export function forgetKeptScroll(): void {
  keptPositions.clear();
}

/** Did this same-page change move to another place (a settings section)? */
export function changesDestination(
  to: RouteLocationNormalized,
  from: RouteLocationNormalized,
): boolean {
  const keys = to.meta.destinationQuery ?? [];
  return keys.some(
    (key) => JSON.stringify(to.query[key]) !== JSON.stringify(from.query[key]),
  );
}

const RELOAD_KEY = "scrollMemory.unload";
let persistenceInstalled = false;
/**
 * vue-router keeps a position for a reload in `history.state`, but any
 * `router.replace` while the page starts (normalising an address) writes that
 * state again without it. So the position of the page being left is also
 * kept here, in this tab's session storage, when the page is hidden or
 * unloaded, and offered to the first navigation after a reload or a return
 * through the browser's Back/Forward.
 */
export function installScrollPersistence(currentKey: () => string): void {
  if (
    persistenceInstalled ||
    typeof window === "undefined" ||
    typeof window.addEventListener !== "function"
  )
    return;
  persistenceInstalled = true;
  const save = () => {
    try {
      window.sessionStorage.setItem(
        RELOAD_KEY,
        JSON.stringify({
          key: currentKey(),
          left: window.scrollX,
          top: window.scrollY,
        }),
      );
    } catch {
      // without storage a reload simply starts at the top, as before
    }
  };
  window.addEventListener("pagehide", save);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") save();
  });
}

function documentWasReloaded(): boolean {
  try {
    const [entry] = performance.getEntriesByType(
      "navigation",
    ) as PerformanceNavigationTiming[];
    return entry?.type === "reload" || entry?.type === "back_forward";
  } catch {
    return false;
  }
}

/** The position saved when this same address was unloaded, used once. */
export function takeUnloadedScroll(
  to: RouteLocationNormalized,
): ScrollTarget | undefined {
  let saved: { key?: string; left?: number; top?: number } | null;
  try {
    saved = JSON.parse(window.sessionStorage.getItem(RELOAD_KEY) ?? "null");
    window.sessionStorage.removeItem(RELOAD_KEY);
  } catch {
    return undefined;
  }
  if (
    !saved ||
    !documentWasReloaded() ||
    saved.key !== scrollKey(to.path, to.query) ||
    typeof saved.top !== "number" ||
    saved.top <= 0
  )
    return undefined;
  return { left: saved.left ?? 0, top: saved.top };
}
