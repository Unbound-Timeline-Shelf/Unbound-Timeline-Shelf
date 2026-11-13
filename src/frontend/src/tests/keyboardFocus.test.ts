// @vitest-environment happy-dom
// UX-AUDIT-APP A14, A15 and critic C1–C3: cards are links, groups of tabs
// and toggles are one Tab stop with arrow keys, and focus follows a
// navigation to the new page's heading.
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, nextTick } from "vue";
import { createMemoryHistory, createRouter, RouterView } from "vue-router";

vi.mock("../services/games", () => ({
  setFavorite: vi.fn(() => new Promise(() => {})),
  setStatus: vi.fn(),
}));

import GameCard from "../components/GameCard.vue";
import SegmentedTabs from "../components/SegmentedTabs.vue";
import { mockGames } from "../data/mockGames";
import type { Game } from "../types/game";
import { rovingTabIndex, rovingTarget } from "../utils/rovingFocus";
import {
  installRouteFocus,
  routeAnnouncement,
  viewHeading,
} from "../state/routeFocus";
import gameDetailSource from "../views/GameDetail.vue?raw";
import gameLibrarySource from "../views/GameLibrary.vue?raw";

const wrappers: VueWrapper[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  document.body.innerHTML = "";
});

function memoryRouter() {
  const page = (title: string) =>
    defineComponent({ render: () => h("h1", title) });
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: page("Home") },
      { path: "/games", component: page("Games") },
      { path: "/games/:id", component: page("A game") },
    ],
  });
}

describe("roving focus", () => {
  it("moves with arrows, wraps at the ends and jumps with Home and End", () => {
    expect(rovingTarget("ArrowRight", 0, 3)).toBe(1);
    expect(rovingTarget("ArrowRight", 2, 3)).toBe(0);
    expect(rovingTarget("ArrowLeft", 0, 3)).toBe(2);
    expect(rovingTarget("ArrowDown", 1, 3)).toBe(2);
    expect(rovingTarget("Home", 2, 3)).toBe(0);
    expect(rovingTarget("End", 0, 3)).toBe(2);
    expect(rovingTarget("Enter", 0, 3)).toBeNull();
    expect(rovingTarget("ArrowRight", 0, 0)).toBeNull();
  });

  it("makes only the selected item, or the first, a Tab stop", () => {
    expect([0, 1, 2].map((i) => rovingTabIndex(i, 1))).toEqual([-1, 0, -1]);
    expect([0, 1, 2].map((i) => rovingTabIndex(i, -1))).toEqual([0, -1, -1]);
  });

  it("turns a segmented button group into one Tab stop with arrow keys", async () => {
    const wrapper = mount(SegmentedTabs, {
      attachTo: document.body,
      props: {
        ariaLabel: "View",
        modelValue: "shelves",
        options: [
          { value: "list", label: "List" },
          { value: "shelves", label: "Shelves" },
          { value: "preview", label: "Preview" },
        ],
      },
    });
    wrappers.push(wrapper);
    const group = wrapper.get(".seg");
    expect(group.attributes("role")).toBe("toolbar");
    const buttons = wrapper.findAll("button");
    expect(buttons.map((b) => b.attributes("tabindex"))).toEqual([
      "-1",
      "0",
      "-1",
    ]);
    // toggles keep their pressed state for assistive technology
    expect(buttons[1]!.attributes("aria-pressed")).toBe("true");
    (buttons[1]!.element as HTMLElement).focus();
    await buttons[1]!.trigger("keydown", { key: "ArrowRight" });
    expect(document.activeElement).toBe(buttons[2]!.element);
    // moving focus never chooses: Enter or a click does
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
  });

  it("leaves link groups as ordinary links", () => {
    const wrapper = mount(SegmentedTabs, {
      props: {
        modelValue: "games",
        options: [
          { value: "games", label: "Games", to: "/games" },
          { value: "collections", label: "Collections", to: "/c" },
        ],
      },
      global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } },
    });
    wrappers.push(wrapper);
    expect(wrapper.get(".seg").attributes("role")).toBe("group");
    expect(
      wrapper.findAll("a").every((a) => a.attributes("tabindex") === undefined),
    ).toBe(true);
  });

  it("gives the library status filter and the game page tabs tab semantics", () => {
    for (const source of [gameLibrarySource, gameDetailSource]) {
      expect(source).toContain('role="tablist"');
      expect(source).toContain('role="tab"');
      expect(source).toMatch(/:aria-selected=/);
      expect(source).toContain("onRovingKeydown($event)");
    }
    expect(gameLibrarySource).toContain('aria-label="Card size"');
  });
});

describe("game cards are links (A14, C1, C2)", () => {
  async function card(props: Record<string, unknown> = {}) {
    const router = memoryRouter();
    await router.push("/games");
    const game: Game = { ...mockGames[0]!, id: "g1", title: "Amber Harbor" };
    const wrapper = mount(GameCard, {
      attachTo: document.body,
      props: { game, ...props },
      global: { plugins: [router], stubs: { Teleport: true } },
    });
    wrappers.push(wrapper);
    return { wrapper, router };
  }

  it("makes the cover the card's first stop, a real link to the game", async () => {
    const { wrapper } = await card();
    const focusable = wrapper
      .findAll("a, button")
      .filter((el) => el.attributes("tabindex") !== "-1");
    expect(focusable[0]!.element.tagName).toBe("A");
    expect(focusable[0]!.attributes("href")).toBe("/games/g1");
    expect(focusable[0]!.attributes("aria-label")).toBe("Amber Harbor");
    // the title links to the same place without a second Tab stop
    const title = wrapper.get(".title a");
    expect(title.attributes("href")).toBe("/games/g1");
    expect(title.attributes("tabindex")).toBe("-1");
  });

  it("opens the game from the cover link", async () => {
    const { wrapper, router } = await card();
    await wrapper.get(".cover-link").trigger("click", { button: 0 });
    await flushPromises();
    expect(router.currentRoute.value.path).toBe("/games/g1");
  });

  it("selects instead of navigating in select mode", async () => {
    const { wrapper, router } = await card({ selectMode: true });
    await wrapper.get(".cover-link").trigger("click", { button: 0 });
    await flushPromises();
    expect(router.currentRoute.value.path).toBe("/games");
    expect(wrapper.emitted("toggle-select")).toHaveLength(1);
  });

  it("names each action with the game and keeps focus on Favorite while it saves", async () => {
    const { wrapper } = await card();
    const favorite = wrapper.get(".favorite-button");
    expect(favorite.attributes("aria-label")).toBe("Favorite Amber Harbor");
    expect(wrapper.get(".edit-button").attributes("aria-label")).toBe(
      "Edit Amber Harbor",
    );
    expect(wrapper.get(".collection-button").attributes("aria-label")).toBe(
      "Add Amber Harbor to a collection",
    );
    (favorite.element as HTMLElement).focus();
    await favorite.trigger("click");
    await nextTick();
    // a disabled button would drop focus to <body>
    expect(favorite.attributes("disabled")).toBeUndefined();
    expect(favorite.attributes("aria-disabled")).toBe("true");
    expect(document.activeElement).toBe(favorite.element);
  });
});

describe("focus follows navigation (A15)", () => {
  async function app() {
    const router = memoryRouter();
    installRouteFocus(router);
    const Shell = defineComponent({
      render: () =>
        h("div", [
          h("a", { href: "/games", id: "nav-link" }, "Games"),
          h("div", { id: "main-content", tabindex: "-1" }, [
            h("input", { id: "search" }),
            h(RouterView),
          ]),
        ]),
    });
    await router.push("/");
    const wrapper = mount(Shell, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    wrappers.push(wrapper);
    await router.isReady();
    // happy-dom lays nothing out, so every element counts as visible here
    vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([
      {},
    ] as unknown as DOMRectList);
    return { router };
  }
  async function settle() {
    await flushPromises();
    for (let i = 0; i < 4; i++) {
      await new Promise((resolve) => requestAnimationFrame(resolve));
      await flushPromises();
    }
  }

  it("moves focus from the navigation to the new page's heading and announces it", async () => {
    const { router } = await app();
    document.getElementById("nav-link")!.focus();
    await router.push("/games");
    await settle();
    const heading = viewHeading();
    expect(heading?.textContent).toBe("Games");
    expect(document.activeElement).toBe(heading);
    expect(heading?.getAttribute("tabindex")).toBe("-1");
    expect(routeAnnouncement.value).toBe("Games");
  });

  it("leaves focus alone for a query change on the same page", async () => {
    const { router } = await app();
    await router.push("/games");
    await settle();
    const search = document.getElementById("search") as HTMLInputElement;
    search.focus();
    await router.push("/games?q=amber");
    await settle();
    expect(document.activeElement).toBe(search);
  });

  it("does not take focus from a control the person chose on the page", async () => {
    const { router } = await app();
    const search = document.getElementById("search") as HTMLInputElement;
    search.focus();
    await router.push("/games/g1");
    await settle();
    expect(document.activeElement).toBe(search);
  });
});
