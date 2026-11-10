import { computed, watch } from "vue";
import type {
  RouteLocationNormalized,
  RouteLocationNormalizedLoaded,
  Router,
} from "vue-router";

// The library address last shown, with its status, sort and filters, so the
// game page's back arrow can return to it when there is no in-app page to go
// back to (UX-AUDIT-APP A23). Kept for the tab, so a reload still has it.
const LIBRARY_KEY = "uts.lastLibrary";
let lastLibrary: string | null = null;

export function rememberLibraryRoute(to: RouteLocationNormalized) {
  if (to.path !== "/games") return;
  lastLibrary = to.fullPath;
  try {
    window.sessionStorage.setItem(LIBRARY_KEY, to.fullPath);
  } catch {
    // storage blocked: the in-memory copy still serves this page load
  }
}

export function lastLibraryLocation(): string {
  if (lastLibrary) return lastLibrary;
  try {
    const saved = window.sessionStorage.getItem(LIBRARY_KEY);
    if (saved && saved.startsWith("/games") && !saved.startsWith("//"))
      return saved;
  } catch {
    // storage blocked
  }
  return "/games";
}

// Where the page's run of history entries began: every entry from `position`
// on has this page's path (tab, `?edit=` and other same-page steps).
interface Landing {
  path: string;
  position: number;
  back: unknown;
}

/**
 * How the back arrow leaves the game page: the number of history steps to the
 * in-app page before it, skipping the page's own tab and editor steps, or
 * null when there is none (opened from another site, a bookmark, a fresh tab
 * or a reload that landed partway through the page's own steps).
 */
export function stepsToPreviousPage(
  landing: Landing | null,
  position: unknown,
  pathOf: (fullPath: string) => string,
): number | null {
  if (!landing || typeof position !== "number") return null;
  const back = landing.back;
  if (typeof back !== "string" || !back.startsWith("/")) return null;
  if (pathOf(back) === landing.path) return null;
  return position - landing.position + 1;
}

// How the game page sits in the browser history (UX-AUDIT-APP A2, A23).
export function useGameDetailHistory(
  route: RouteLocationNormalizedLoaded,
  router: Router,
) {
  let landing: Landing | null = null;
  const noteEntry = () => {
    const state = router.options.history.state;
    const position = state.position;
    if (typeof position !== "number") {
      landing = null;
      return;
    }
    // a new page (or another game in the same view) starts a new run; Back
    // to an earlier step of the same run moves its start back
    if (!landing || landing.path !== route.path || position < landing.position)
      landing = { path: route.path, position, back: state.back };
  };
  watch(() => route.fullPath, noteEntry, { immediate: true });

  // The arrow goes to the page before this one, past the tabs and editor
  // steps taken here, so it never just switches tabs. With no in-app page
  // to go back to, it opens the library as last shown instead of leaving
  // the app.
  function goBackToLibrary() {
    const steps = stepsToPreviousPage(
      landing,
      router.options.history.state.position,
      (fullPath) => router.resolve(fullPath).path,
    );
    if (steps !== null) router.go(-steps);
    else void router.push(lastLibraryLocation());
  }

  // The editor is a history step (`?edit=basics`), so Back and the Android
  // back gesture close it instead of leaving the page, and a reload reopens
  // it. Its unsaved-edit guard asks before either.
  function closeEditor() {
    if (route.query.edit === undefined) return;
    const query = { ...route.query };
    delete query.edit;
    const location = { path: route.path, query, hash: route.hash };
    // the step this page added is undone; one it arrived with is replaced
    if (router.options.history.state.back === router.resolve(location).fullPath)
      router.back();
    else void router.replace(location);
  }
  const showEditModal = computed<boolean>({
    get: () => route.query.edit !== undefined,
    set(open) {
      if (!open) return closeEditor();
      if (route.query.edit !== undefined) return;
      void router.push({
        path: route.path,
        query: { ...route.query, edit: "basics" },
        hash: route.hash,
      });
    },
  });

  // Choosing a tab adds a history step and writes `?tab=`, leaving the page's
  // default tab out of the address, so Back returns to the previous tab and
  // a reload keeps it.
  function writeTab(tab: string, defaultTab: string) {
    const query = { ...route.query };
    if (tab === defaultTab) delete query.tab;
    else query.tab = tab;
    void router.push({ path: route.path, query, hash: route.hash });
  }

  return { goBackToLibrary, showEditModal, writeTab };
}
