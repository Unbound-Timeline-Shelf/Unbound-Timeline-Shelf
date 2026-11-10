// @vitest-environment happy-dom
// UX-AUDIT-APP A19 / §3.3: one entry per destination. While the pinned
// desktop sidebar already lists a folder's pages (Games › All games,
// Collections; Media › Movies, TV shows, …), the top bar does not repeat
// them as a second switcher. A phone, the icon rail or a folder the person
// closed keeps the switcher, because then nothing else on screen offers it.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createSSRApp, nextTick } from "vue";
import { renderToString } from "vue/server-renderer";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import GameTopBar from "../components/GameTopBar.vue";
import MediaTopBar from "../components/MediaTopBar.vue";
import SidebarNav from "../components/SidebarNav.vue";
import { navigationViewport, sidebarMode } from "../state/sidebarMode";
import {
  expandedNavigationFolders,
  sidebarListsFolder,
} from "../state/navigationFolders";

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/:pathMatch(.*)*", component: { render: () => null } }],
  });
}

async function render(component: object, props: Record<string, unknown>) {
  const router = makeRouter();
  await router.push("/games");
  const app = createSSRApp(component, props);
  app.use(router);
  return renderToString(app);
}

describe("top bar switchers and the sidebar (A19)", () => {
  beforeEach(() => {
    navigationViewport.value = "desktop";
    sidebarMode.value = "auto";
    expandedNavigationFolders.value = new Set();
  });
  afterEach(() => {
    navigationViewport.value = "desktop";
    sidebarMode.value = "auto";
    expandedNavigationFolders.value = new Set();
  });

  it("drops Games / Collections from the top bar while the pinned sidebar lists them", async () => {
    expandedNavigationFolders.value = new Set(["games"]);
    expect(sidebarListsFolder("games")).toBe(true);
    const html = await render(GameTopBar, { active: "games" });
    expect(html).not.toContain('aria-label="Games area"');
    // the bar itself (page actions, account chip) stays
    expect(html).toContain("media-topbar");
  });

  it("keeps the switcher when the Games folder is closed", async () => {
    expandedNavigationFolders.value = new Set(["media"]);
    const html = await render(GameTopBar, { active: "games" });
    expect(html).toContain('aria-label="Games area"');
    expect(html).toContain('href="/games/collections"');
  });

  it.each([
    ["the icon rail", "rail", "desktop"],
    ["the overlay menu", "overlay", "desktop"],
    ["a tablet's rail", "auto", "tablet"],
    ["a phone", "auto", "phone"],
  ] as const)(
    "keeps the switcher with %s, where the sidebar does not show the pages",
    async (_label, mode, viewport) => {
      sidebarMode.value = mode;
      navigationViewport.value = viewport;
      expandedNavigationFolders.value = new Set(["games", "media"]);
      expect(sidebarListsFolder("games")).toBe(false);
      const html = await render(GameTopBar, { active: "games" });
      expect(html).toContain('aria-label="Games area"');
    },
  );

  it("treats the Media folder the same way", async () => {
    expandedNavigationFolders.value = new Set(["media"]);
    let html = await render(MediaTopBar, { active: "movie" });
    expect(html).not.toContain('aria-label="Library"');
    expandedNavigationFolders.value = new Set(["games"]);
    html = await render(MediaTopBar, { active: "movie" });
    expect(html).toContain('aria-label="Library"');
  });

  it("follows the sidebar's own chevron", async () => {
    // the sidebar asks the server for plugin entries; none here
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("[]", { status: 200 })),
    );
    const router = makeRouter();
    await router.push("/games");
    await router.isReady();
    const wrapper = mount(SidebarNav, {
      global: { plugins: [router] },
      attachTo: document.body,
    });
    await nextTick();
    // on a games page the Games folder starts open
    expect(expandedNavigationFolders.value.has("games")).toBe(true);
    await wrapper.get('button[aria-label="Games pages"]').trigger("click");
    expect(expandedNavigationFolders.value.has("games")).toBe(false);
    expect(sidebarListsFolder("games")).toBe(false);
    await wrapper.get('button[aria-label="Games pages"]').trigger("click");
    expect(sidebarListsFolder("games")).toBe(true);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });
});
