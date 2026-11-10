import { computed } from "vue";
import type { RouteLocationNormalizedLoaded, Router } from "vue-router";

// How the game page sits in the browser history (UX-AUDIT-APP A2, A23).
export function useGameDetailHistory(
  route: RouteLocationNormalizedLoaded,
  router: Router,
) {
  // Back only when the previous history entry is a page of this app; a game
  // opened from another site, a bookmark or a fresh tab goes to the library
  // instead of leaving the app.
  function goBackToLibrary() {
    const back = router.options.history.state.back;
    if (typeof back === "string" && back.startsWith("/")) router.back();
    else void router.push("/games");
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
