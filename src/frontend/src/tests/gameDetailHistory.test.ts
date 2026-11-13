// @vitest-environment happy-dom
// UX-AUDIT-APP A23: the game page's back arrow goes to the page before it,
// past the tab and editor steps taken on the page, and falls back to the
// library as last shown when there is no in-app page to go back to.
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import {
  createRouter,
  createWebHistory,
  RouterView,
  useRoute,
  useRouter,
} from "vue-router";
import { afterEach, expect, it } from "vitest";
import {
  lastLibraryLocation,
  rememberLibraryRoute,
  stepsToPreviousPage,
  useGameDetailHistory,
} from "../composables/useGameDetailHistory";

let api: ReturnType<typeof useGameDetailHistory>;
const GamePage = defineComponent({
  setup() {
    api = useGameDetailHistory(useRoute(), useRouter());
    return () => h("p", "game");
  },
});
const Page = { render: () => h("p") };

let wrapper: ReturnType<typeof mount> | null = null;
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
});

async function settle(router: ReturnType<typeof createRouter>) {
  // web history Back/go resolve on popstate
  await new Promise((resolve) => setTimeout(resolve, 20));
  await flushPromises();
  return router.currentRoute.value.fullPath;
}

async function start(entries: string[], landing = "/") {
  window.history.replaceState(null, "", landing);
  const router = createRouter({
    history: createWebHistory(),
    routes: [
      { path: "/games", component: Page },
      { path: "/games/:id", component: GamePage },
      { path: "/collections", component: Page },
      { path: "/", component: Page },
    ],
  });
  router.afterEach((to) => rememberLibraryRoute(to));
  await router.push(landing);
  for (const entry of entries) await router.push(entry);
  wrapper = mount(defineComponent({ setup: () => () => h(RouterView) }), {
    global: { plugins: [router] },
  });
  await flushPromises();
  return router;
}

it("skips the page's own tab steps instead of switching tabs", async () => {
  const router = await start(["/games?status=playing", "/games/g1"]);
  api.writeTab("Notes", "Overview");
  await flushPromises();
  api.writeTab("Stats", "Overview");
  await flushPromises();
  expect(router.currentRoute.value.fullPath).toBe("/games/g1?tab=Stats");
  api.goBackToLibrary();
  expect(await settle(router)).toBe("/games?status=playing");
});

it("returns to the in-app page it came from, not only the library", async () => {
  const router = await start(["/collections", "/games/g1"]);
  api.writeTab("Notes", "Overview");
  await flushPromises();
  api.goBackToLibrary();
  expect(await settle(router)).toBe("/collections");
});

it("still skips the run after browser Back to an earlier tab", async () => {
  const router = await start(["/collections", "/games/g1"]);
  api.writeTab("Notes", "Overview");
  await flushPromises();
  api.writeTab("Stats", "Overview");
  await flushPromises();
  router.back();
  expect(await settle(router)).toBe("/games/g1?tab=Notes");
  api.goBackToLibrary();
  expect(await settle(router)).toBe("/collections");
});

it("opens the library as last shown when there is no page before", async () => {
  rememberLibraryRoute({
    path: "/games",
    fullPath: "/games?status=beaten&sort=rating",
  } as never);
  // a game opened in a fresh tab: no previous in-app entry
  const router = await start([], "/games/g1");
  api.writeTab("Notes", "Overview");
  await flushPromises();
  api.goBackToLibrary();
  expect(await settle(router)).toBe("/games?status=beaten&sort=rating");
});

it("works out the steps from where the page's run began", () => {
  const pathOf = (full: string) => full.split("?")[0];
  const landing = { path: "/games/g1", position: 3, back: "/games?q=x" };
  expect(stepsToPreviousPage(landing, 3, pathOf)).toBe(1);
  expect(stepsToPreviousPage(landing, 5, pathOf)).toBe(3);
  // reload that landed partway through the page's own steps
  expect(
    stepsToPreviousPage({ ...landing, back: "/games/g1?tab=Notes" }, 5, pathOf),
  ).toBeNull();
  expect(stepsToPreviousPage({ ...landing, back: null }, 5, pathOf)).toBe(null);
  expect(stepsToPreviousPage(landing, undefined, pathOf)).toBeNull();
  expect(stepsToPreviousPage(null, 3, pathOf)).toBeNull();
});

it("remembers only library addresses", () => {
  rememberLibraryRoute({ path: "/games", fullPath: "/games?tag=RPG" } as never);
  rememberLibraryRoute({ path: "/settings", fullPath: "/settings" } as never);
  expect(lastLibraryLocation()).toBe("/games?tag=RPG");
});
