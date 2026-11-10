// @vitest-environment happy-dom
// UX-AUDIT-APP A13, A18, A22, A24 and A25: a Home that is not empty by
// default, honest library counts and wording when the server cannot be
// reached, a welcome that can wait, tips remembered per account, Delete away
// from the editor's everyday actions and the plugin manager's actions in one
// place.
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ queue: vi.fn() }));
vi.mock("../services/preferences", async (original) => ({
  ...(await original<typeof import("../services/preferences")>()),
  queuePreferences: api.queue,
}));

import AppearanceWelcome from "../components/AppearanceWelcome.vue";
import appSource from "../App.vue?raw";
import gameFormSource from "../components/GameFormModal.vue?raw";
import gameLibrarySource from "../views/GameLibrary.vue?raw";
import homeSource from "../views/HomeHub.vue?raw";
import pluginManagerSource from "../components/settings/PluginManagerSection.vue?raw";
import { DEFAULT_PREFERENCES } from "../services/preferences";
import {
  EMPTY_LIBRARY_HOME_WIDGETS,
  SUGGESTED_HOME_WIDGETS,
  homeWidgetIds,
  showsSuggestedHome,
} from "../services/homeWidgets";
import { currentUser } from "../state/auth";
import {
  preferences,
  preferencesError,
  preferencesLoaded,
  welcomeHiddenFor,
} from "../state/preferences";
import { BULK_EDIT_TIP, dismissTip, tipVisible } from "../state/tips";
import {
  SERVER_UNREACHABLE,
  friendlyLoadError,
  isNetworkFailure,
} from "../utils/loadErrors";
import type { CurrentUser } from "../services/auth";

beforeEach(() => {
  currentUser.value = { id: "u1", username: "someone" } as CurrentUser;
  preferences.value = { ...DEFAULT_PREFERENCES };
  preferencesLoaded.value = true;
  preferencesError.value = null;
  welcomeHiddenFor.value = null;
  api.queue.mockReset();
  localStorage.clear();
});
afterEach(() => {
  currentUser.value = null;
  document.body.innerHTML = "";
});

describe("Home defaults (A22)", () => {
  const library = (size: number, loaded = true) => ({ loaded, size });

  it("suggests widgets for a library with games until the person chooses", () => {
    const prefs = { home_widgets: [], home_widgets_customized: false };
    expect(homeWidgetIds(prefs, library(135))).toEqual(SUGGESTED_HOME_WIDGETS);
    expect(SUGGESTED_HOME_WIDGETS).toContain("continue-playing");
    expect(SUGGESTED_HOME_WIDGETS).toContain("recently-added");
    expect(homeWidgetIds(prefs, library(0))).toEqual(
      EMPTY_LIBRARY_HOME_WIDGETS,
    );
    // still deciding while the library loads
    expect(homeWidgetIds(prefs, library(0, false))).toBeNull();
    expect(showsSuggestedHome(prefs)).toBe(true);
  });

  it("keeps the person's own choice, including an empty Home", () => {
    expect(
      homeWidgetIds(
        { home_widgets: ["backlog"], home_widgets_customized: false },
        library(10),
      ),
    ).toEqual(["backlog"]);
    const quiet = { home_widgets: [], home_widgets_customized: true };
    expect(homeWidgetIds(quiet, library(10))).toEqual([]);
    expect(showsSuggestedHome(quiet)).toBe(false);
  });

  it("saves a Customize Home choice as the person's own and drops the external-link arrows", () => {
    expect(homeSource).toContain("home_widgets_customized: true");
    expect(homeSource).not.toContain("↗");
    // coming back to the tab refreshes stale Home data on its own
    expect(homeSource).toContain('addEventListener("visibilitychange"');
  });
});

describe("library load errors (A18)", () => {
  it("turns a browser network failure into words, keeping server messages", () => {
    for (const raw of [
      "Failed to fetch",
      "NetworkError when attempting to fetch resource.",
      "Load failed",
    ]) {
      expect(isNetworkFailure(raw)).toBe(true);
      expect(friendlyLoadError(raw)).toBe(SERVER_UNREACHABLE);
    }
    expect(SERVER_UNREACHABLE).toMatch(/Your library is safe/);
    expect(friendlyLoadError("You do not have access to this library.")).toBe(
      "You do not have access to this library.",
    );
    expect(friendlyLoadError(null)).toBe(SERVER_UNREACHABLE);
  });

  it("shows no count while the library is unknown instead of 0 games", () => {
    expect(gameLibrarySource).toContain(
      '<template v-if="countUnknown">— games',
    );
    expect(gameLibrarySource).toContain("{{ statusCountLabel(s) }}");
    expect(gameLibrarySource).not.toMatch(/\{\{\s*error\s*\}\}/);
  });
});

describe("first-visit welcome and tips (A24)", () => {
  const Modal = defineComponent({
    props: ["title", "description", "dismissible", "size"],
    emits: ["close"],
    setup(props, { slots, emit }) {
      return () =>
        h(
          "div",
          { class: "modal", "data-dismissible": String(props.dismissible) },
          [
            h("button", { class: "close", onClick: () => emit("close") }, "×"),
            slots.default?.(),
            slots.footer?.(),
          ],
        );
    },
  });
  function welcome() {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
    return mount(AppearanceWelcome, {
      global: { stubs: { UiModal: Modal } },
    });
  }

  it("can be closed or put off without saving anything", async () => {
    const wrapper = welcome();
    expect(wrapper.get(".modal").attributes("data-dismissible")).toBe("true");
    const later = wrapper
      .findAll("button")
      .find((button) => button.text() === "Later")!;
    await later.trigger("click");
    expect(welcomeHiddenFor.value).toBe("u1");
    expect(api.queue).not.toHaveBeenCalled();
    welcomeHiddenFor.value = null;
    await wrapper.get(".close").trigger("click");
    expect(welcomeHiddenFor.value).toBe("u1");
    wrapper.unmount();
    // App hides it, and the date offer behind it, for the rest of the visit
    expect(
      appSource.match(/welcomeHiddenFor !== currentUser\.id/g),
    ).toHaveLength(2);
  });

  it("remembers a dismissed tip on the account, not the browser", async () => {
    api.queue.mockImplementation(async (changes) => ({
      prefs: { ...preferences.value, ...changes },
      latest: true,
    }));
    const visible = tipVisible(BULK_EDIT_TIP);
    expect(visible.value).toBe(true);
    await dismissTip(BULK_EDIT_TIP);
    expect(visible.value).toBe(false);
    expect(api.queue).toHaveBeenCalledWith({ dismissed_tips: [BULK_EDIT_TIP] });
    expect(localStorage.getItem("seenBulkEditHint")).toBeNull();
  });

  it("waits for the account's preferences and carries an old browser dismissal over", async () => {
    api.queue.mockImplementation(async (changes) => ({
      prefs: { ...preferences.value, ...changes },
      latest: true,
    }));
    preferencesLoaded.value = false;
    const visible = tipVisible(BULK_EDIT_TIP);
    expect(visible.value).toBe(false);
    localStorage.setItem("seenBulkEditHint", "true");
    preferencesLoaded.value = true;
    expect(visible.value).toBe(false);
    await flushPromises();
    expect(api.queue).toHaveBeenCalledWith({ dismissed_tips: [BULK_EDIT_TIP] });
  });
});

describe("actions in their place (A13, A25)", () => {
  it("keeps Delete game out of the editor footer, in Advanced", () => {
    const footer = gameFormSource.slice(
      gameFormSource.indexOf('<template #actions="{ formId }">'),
    );
    expect(footer).not.toContain("Delete game");
    const advanced = gameFormSource.slice(
      gameFormSource.indexOf("activeTab === 'Advanced'"),
      gameFormSource.indexOf('<template #actions="{ formId }">'),
    );
    expect(advanced).toContain("Delete game");
  });

  it("gives the plugin manager one action row under the page heading and no second Plugins heading", () => {
    const template = pluginManagerSource.slice(
      pluginManagerSource.indexOf("<template>"),
    );
    expect(template).not.toContain("<h2>Plugins</h2>");
    const head = template.slice(0, template.indexOf('class="manager-tabs"'));
    for (const action of [
      "Install a plugin",
      "Install package or URL",
      "Check for updates",
    ])
      expect(head).toContain(action);
    // and nothing hides one of them on a particular view
    expect(head).not.toContain("v-if=\"view !== 'Discover'\"");
    // platform health and manager settings follow the list
    expect(head).not.toContain("Plugin Manager settings");
  });
});
