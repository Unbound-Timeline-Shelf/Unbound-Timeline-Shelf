import { createRouter, createWebHistory, START_LOCATION } from "vue-router";
import {
  currentUser,
  authChecked,
  authCheckFailed,
  ensureAuthChecked,
} from "../state/auth";
import {
  cancelScrollRestore,
  changesDestination,
  installScrollPersistence,
  keptScroll,
  rememberScroll,
  restoreWhenReady,
  scrollKey,
  takeUnloadedScroll,
} from "../state/scrollMemory";
import { legacySettingsRedirect } from "../state/settingsRegistry";
import { installRouteFocus } from "../state/routeFocus";
import { appearanceLoaded, loadAppearanceSettings } from "../state/appearance";
import { fetchSetupStatus } from "../services/setup";
import {
  classifySetupStatus,
  consumeReturnPath,
  rememberReturnPath,
  safeReturnPath,
  setStartupState,
} from "../state/startup";

declare module "vue-router" {
  interface RouteMeta {
    // Shown on the browser tab as "<title> · Archive". Left unset, the
    // tab just falls back to "Archive".
    title?: string;
    // A kept-alive page that returns to where you left it, even through an
    // ordinary link rather than Back (UX-AUDIT-APP A4).
    keepsScroll?: boolean;
    // Query keys that pick a different place on the same path (a settings
    // section), so changing one starts at the top like a new page (A10).
    destinationQuery?: string[];
  }
}

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior(to, from, savedPosition) {
    // Back, Forward and reload: the entry's saved position, applied once the
    // view has loaded enough to show it rather than clamped to the top.
    if (savedPosition) return restoreWhenReady(savedPosition);
    if (from === START_LOCATION) {
      const unloaded = takeUnloadedScroll(to);
      if (unloaded) return restoreWhenReady(unloaded);
    }
    if (to.path === from.path && to.hash === from.hash) {
      // A settings section is a new place; a filter, sort or tab is not, so
      // those leave the viewport where the person is (A7, A10).
      return changesDestination(to, from) ? { top: 0 } : false;
    }
    const kept = keptScroll(to);
    if (kept && !to.hash) return restoreWhenReady(kept);
    return { top: 0 };
  },
  routes: [
    {
      path: "/integration-access/:requestId",
      name: "integration-access",
      meta: { title: "Integration access" },
      component: () => import("../views/IntegrationAccess.vue"),
    },
    {
      path: "/accept-invite",
      name: "accept-invite",
      meta: { title: "Accept invitation" },
      component: () => import("../views/AcceptInvite.vue"),
    },
    {
      path: "/reset-password",
      name: "password-reset",
      meta: { title: "Password recovery" },
      component: () => import("../views/PasswordReset.vue"),
    },
    // Preserve bookmarks and navigation shortcuts when retired native
    // features move into their official plugin. Its page explains installation
    // when the optional plugin is not enabled.
    { path: "/cards", redirect: "/plugins/official.collectors-archive/cards" },
    { path: "/sets", redirect: "/plugins/official.collectors-archive/sets" },
    {
      path: "/bounties",
      redirect: "/plugins/official.collectors-archive/bounties",
    },
    {
      path: "/cards/:cardId",
      redirect: (to) => ({
        path: "/plugins/official.collectors-archive/card-detail",
        query: { record_id: String(to.params.cardId) },
      }),
    },
    {
      path: "/sets/:setId",
      redirect: (to) => ({
        path: "/plugins/official.collectors-archive/set-detail",
        query: { record_id: String(to.params.setId) },
      }),
    },
    {
      path: "/",
      name: "home",
      meta: { title: "Home", keepsScroll: true },
      component: () => import("../views/HomeHub.vue"),
    },
    {
      path: "/games",
      name: "library",
      meta: { title: "Games", keepsScroll: true },
      component: () => import("../views/GameLibrary.vue"),
    },
    {
      path: "/games/collections",
      name: "collections",
      meta: { title: "Collections" },
      component: () => import("../views/Collections.vue"),
    },
    {
      path: "/games/collections/:name",
      name: "collection-detail",
      meta: { title: "Collection" },
      component: () => import("../views/CollectionDetail.vue"),
    },
    { path: "/collections", redirect: "/games/collections" },
    {
      path: "/collections/:name",
      redirect: (to) =>
        `/games/collections/${encodeURIComponent(String(to.params.name))}`,
    },
    {
      path: "/upload",
      name: "upload",
      meta: { title: "Upload" },
      component: () => import("../views/Upload.vue"),
    },
    { path: "/inbox", redirect: "/upload" },
    {
      path: "/games/:id",
      name: "game-detail",
      meta: { title: "Game" },
      component: () => import("../views/GameDetail.vue"),
    },
    {
      path: "/media/search",
      name: "media-search",
      meta: { title: "Add media" },
      component: () => import("../views/MediaSearch.vue"),
    },
    {
      path: "/movies",
      name: "movie-library",
      meta: { title: "Movies", keepsScroll: true },
      component: () => import("../views/MovieLibrary.vue"),
    },
    {
      path: "/movies/:id",
      name: "movie-detail",
      meta: { title: "Movie" },
      component: () => import("../views/MovieDetail.vue"),
    },
    {
      path: "/tv",
      name: "tv-show-library",
      meta: { title: "TV Shows", keepsScroll: true },
      component: () => import("../views/TVShowLibrary.vue"),
    },
    {
      path: "/tv/:id",
      name: "tv-show-detail",
      meta: { title: "TV Show" },
      component: () => import("../views/TVShowDetail.vue"),
    },
    {
      path: "/anime",
      name: "anime-library",
      meta: { title: "Anime", keepsScroll: true },
      component: () => import("../views/AnimeLibrary.vue"),
    },
    {
      path: "/anime/:id",
      name: "anime-detail",
      meta: { title: "Anime" },
      component: () => import("../views/AnimeDetail.vue"),
    },
    {
      path: "/books",
      name: "book-library",
      meta: { title: "Books", keepsScroll: true },
      component: () => import("../views/BookLibrary.vue"),
    },
    {
      path: "/books/:id",
      name: "book-detail",
      meta: { title: "Book" },
      component: () => import("../views/BookDetail.vue"),
    },
    {
      path: "/calendar",
      name: "calendar",
      meta: { title: "Calendar", keepsScroll: true },
      component: () => import("../views/Calendar.vue"),
    },
    {
      path: "/statistics",
      name: "statistics",
      meta: { title: "Statistics", keepsScroll: true },
      component: () => import("../views/Statistics.vue"),
    },
    {
      path: "/notifications",
      name: "notifications",
      meta: { title: "Notifications", keepsScroll: true },
      component: () => import("../views/Notifications.vue"),
    },
    {
      path: "/notifications/stories/:id",
      name: "notification-story",
      meta: { title: "Notification" },
      component: () => import("../views/NotificationStory.vue"),
    },
    {
      path: "/notifications/live/:token",
      name: "notification-live",
      meta: { title: "Notification" },
      component: () => import("../views/NotificationStory.vue"),
    },
    {
      path: "/media/collections",
      name: "media-lists",
      meta: { title: "Collections", keepsScroll: true },
      component: () => import("../views/MediaLists.vue"),
    },
    {
      path: "/media/collections/:id",
      name: "media-list-detail",
      meta: { title: "Collection" },
      component: () => import("../views/MediaListDetail.vue"),
    },
    { path: "/lists", redirect: "/media/collections" },
    {
      path: "/lists/:id",
      redirect: (to) =>
        `/media/collections/${encodeURIComponent(String(to.params.id))}`,
    },
    // History merged into the Calendar page as a second tab
    { path: "/history", redirect: "/calendar" },
    {
      path: "/login",
      name: "login",
      meta: { title: "Sign in" },
      component: () => import("../views/Login.vue"),
    },
    {
      path: "/login/oidcstart",
      name: "oidc-start",
      meta: { title: "Sign in" },
      component: () => import("../views/OidcStart.vue"),
    },
    {
      path: "/login/local",
      name: "local-login",
      meta: { title: "Sign in" },
      component: () => import("../views/Login.vue"),
    },
    {
      path: "/login/:provider",
      name: "oidc-provider-start",
      meta: { title: "Sign in" },
      component: () => import("../views/OidcProviderStart.vue"),
    },
    {
      path: "/setup",
      name: "setup",
      meta: { title: "Setup" },
      component: () => import("../views/Setup.vue"),
    },
    { path: "/profile", redirect: "/settings" },
    { path: "/sessions", redirect: "/settings/account/sessions" },
    {
      path: "/admin/sessions",
      redirect: "/settings/administration/admin-sessions",
    },
    {
      path: "/plugins/:pluginId",
      name: "plugin-host",
      component: () => import("../views/PluginHost.vue"),
    },
    {
      path: "/plugins/:pluginId/:pluginPath(.*)*",
      name: "plugin-route",
      component: () => import("../views/PluginHost.vue"),
    },
    {
      // /settings, /settings/<area>, /settings/<area>/<section> (§3.4); old
      // ?section= addresses are moved here by legacySettingsRedirect below
      path: "/settings/:area(preferences|account|administration)?/:section?",
      name: "settings",
      meta: { title: "Settings", destinationQuery: ["area", "section"] },
      component: () => import("../views/Settings.vue"),
    },
    {
      path: "/games/:gameId/achievements/:achievementId",
      name: "achievement-detail",
      meta: { title: "Achievement" },
      component: () => import("../views/AchievementDetail.vue"),
    },
    // last, so it only catches addresses no other route claims
    {
      path: "/:pathMatch(.*)*",
      name: "not-found",
      meta: { title: "Page not found" },
      component: () => import("../views/NotFound.vue"),
    },
  ],
});

installScrollPersistence(() =>
  scrollKey(router.currentRoute.value.path, router.currentRoute.value.query),
);

installRouteFocus(router);

let setupState: "unknown" | "required" | "complete" = "unknown";

export async function retryStartup() {
  setupState = "unknown";
  authChecked.value = false;
  setStartupState("checking");
  const target = router.resolve(
    window.location.pathname + window.location.search + window.location.hash,
  );
  return router.replace({
    path: target.path,
    query: target.query,
    hash: target.hash,
    force: true,
  });
}

function loginRedirect(toPath: string) {
  const returnPath = rememberReturnPath(toPath);
  return returnPath
    ? { path: "/login", query: { return_to: returnPath } }
    : { path: "/login" };
}

function setupRedirect(toPath: string) {
  const returnPath = rememberReturnPath(toPath);
  return returnPath
    ? { path: "/setup", query: { return_to: returnPath } }
    : { path: "/setup" };
}

router.beforeEach(async (to, from) => {
  cancelScrollRestore();
  if (typeof window !== "undefined")
    rememberScroll(from, { left: window.scrollX, top: window.scrollY });

  // Settings sections moved from ?section= to paths: links, bookmarks and
  // notifications that still use the old form land on the same section.
  const movedSettings = legacySettingsRedirect(to);
  if (movedSettings) return { ...movedSettings, hash: to.hash };

  // Start fetching this route's code while the startup checks are in flight.
  // Vue Router normally resolves lazy components only after beforeEach guards
  // finish, which serialized a static asset RTT behind setup/auth RTTs on a
  // cold deep link. The same import is then reused by the router below.
  const routeComponent = to.matched.at(-1)?.components?.default;
  if (typeof routeComponent === "function") {
    const lazyRouteLoader = routeComponent as unknown as () => Promise<unknown>;
    void Promise.resolve(lazyRouteLoader()).catch(() => {
      // Let Vue Router surface the import error through its normal navigation
      // handling after the guard completes.
    });
  }

  const entryPage =
    to.path === "/reset-password" ||
    to.path === "/accept-invite" ||
    to.path === "/setup" ||
    to.path === "/login" ||
    to.path.startsWith("/login/");
  const destination = entryPage
    ? (safeReturnPath(to.query.return_to) ?? to.fullPath)
    : to.fullPath;

  // Entry pages are never return destinations. Clean old bookmarks as well as
  // newly generated redirects without discarding OIDC errors or setup options.
  if (
    entryPage &&
    "return_to" in to.query &&
    !safeReturnPath(to.query.return_to)
  ) {
    const query = { ...to.query };
    delete query.return_to;
    return { path: to.path, query, hash: to.hash, replace: true };
  }

  if (setupState === "unknown") {
    setStartupState("checking");
    try {
      const publicAuthEntry =
        to.name === "oidc-start" ||
        to.name === "oidc-provider-start" ||
        to.name === "password-reset" ||
        to.name === "accept-invite";
      // Setup status and the current session are independent reads. Start
      // both together on the first ordinary route so high RTT does not charge
      // two full network round trips before the page can render.
      const [status] = await Promise.all([
        fetchSetupStatus(),
        ...(publicAuthEntry ? [] : [ensureAuthChecked()]),
      ]);
      const state = classifySetupStatus(status);
      setupState = state === "setup-required" ? "required" : "complete";
      if (state === "setup-required") {
        setStartupState("setup-required");
      }
    } catch (err) {
      setStartupState(
        "unavailable",
        err instanceof Error ? err.message : "Unable to reach the backend.",
      );
      return false;
    }
  }

  if (setupState === "required" && to.path !== "/setup") {
    setStartupState("setup-required");
    try {
      const status = await fetchSetupStatus();
      if (!status.setup_required) {
        setupState = "complete";
      }
    } catch (err) {
      setStartupState(
        "unavailable",
        err instanceof Error ? err.message : "Unable to reach the backend.",
      );
      return false;
    }
  }

  if (setupState === "required") {
    if (to.path !== "/setup") return setupRedirect(destination);
    return;
  }

  if (to.path === "/setup") {
    try {
      const status = await fetchSetupStatus();
      if (status.setup_required) {
        setStartupState("setup-required");
        return;
      }
      setupState = "complete";
      const returnPath = safeReturnPath(to.query.return_to);
      await ensureAuthChecked();
      if (currentUser.value) {
        setStartupState("ready");
        return returnPath ?? "/";
      }
      setStartupState("auth-required");
      return returnPath
        ? { path: "/login", query: { return_to: returnPath } }
        : "/login";
    } catch (err) {
      setStartupState(
        "unavailable",
        err instanceof Error ? err.message : "Unable to reach the backend.",
      );
      return false;
    }
  }

  // This public route deliberately bypasses the normal auth redirect so a
  // bookmark or reverse-proxy login entrypoint can start OIDC immediately.
  if (
    to.name === "oidc-start" ||
    to.name === "oidc-provider-start" ||
    to.name === "password-reset" ||
    to.name === "accept-invite"
  ) {
    setStartupState("auth-required");
    return;
  }

  await ensureAuthChecked();
  if (authCheckFailed.value) {
    setStartupState(
      "unavailable",
      "Unable to reach the backend while checking authentication.",
    );
    return false;
  }

  const loginPage = to.name === "login" || to.name === "local-login";
  if (!loginPage && !currentUser.value) {
    setStartupState("auth-required");
    return loginRedirect(to.fullPath);
  }

  if (loginPage && currentUser.value) {
    setStartupState("ready");
    return consumeReturnPath(to.query.return_to) ?? "/";
  }

  if (currentUser.value) {
    setStartupState("ready");
    // Appearance options only affect optional card decorations. Fetch them in
    // the background so a settings round trip cannot delay the first route.
    if (!appearanceLoaded.value) void loadAppearanceSettings();
  } else {
    setStartupState("auth-required");
  }
});

export default router;
