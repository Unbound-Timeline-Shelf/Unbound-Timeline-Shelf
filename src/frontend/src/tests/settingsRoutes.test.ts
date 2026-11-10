// @vitest-environment happy-dom
// UX-AUDIT-APP §3.4, A19 and A20: one path per settings section, old
// ?section= links land on the same place, names are not repeated across
// areas and an Import & sync hub lists every way into the library.
import { afterEach, describe, expect, it } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { currentUser } from "../state/auth";
import type { CurrentUser } from "../services/auth";
import {
  buildSettingsGroups,
  legacySettingsRedirect,
  searchSettings,
  settingsAreaOf,
  settingsLocation,
  settingsSearchIndex,
} from "../state/settingsRegistry";
import routerSource from "../router/index.ts?raw";
import sidebarSource from "../components/SidebarNav.vue?raw";

afterEach(() => {
  currentUser.value = null;
});
function signIn(isAdmin: boolean) {
  currentUser.value = {
    id: isAdmin ? "admin" : "member",
    username: "someone",
    is_admin: isAdmin,
  } as CurrentUser;
}

describe("settings sections are paths", () => {
  it("builds /settings/<area>/<section> with the tab as a query", () => {
    signIn(true);
    expect(settingsLocation("appearance")).toEqual({
      path: "/settings/preferences/appearance",
      query: {},
    });
    expect(
      settingsLocation("plugins", { query: { view: "discover" } }),
    ).toEqual({
      path: "/settings/administration/plugins",
      query: { view: "discover" },
    });
    expect(settingsLocation("library", { tab: "trash" })).toEqual({
      path: "/settings/preferences/library",
      query: { tab: "trash" },
    });
    // an old id that became a tab opens that tab of its merged section
    expect(settingsLocation("media-trash")).toEqual({
      path: "/settings/preferences/library",
      query: { tab: "trash" },
    });
    expect(settingsLocation("profile").path).toBe("/settings/account/profile");
  });

  it("knows a built-in section's area before the account or plugins load", () => {
    currentUser.value = null;
    expect(settingsAreaOf("plugins")).toBe("administration");
    expect(settingsAreaOf("connections")).toBe("account");
    expect(settingsAreaOf("example.plugin-settings")).toBeUndefined();
  });

  it("moves every old ?section= link to its path, keeping its other parameters", () => {
    signIn(true);
    expect(
      legacySettingsRedirect({
        path: "/settings",
        query: { section: "plugins", view: "updates" },
      }),
    ).toEqual({
      path: "/settings/administration/plugins",
      query: { view: "updates" },
    });
    expect(
      legacySettingsRedirect({
        path: "/settings",
        query: { section: "library", tab: "duplicates" },
      }),
    ).toEqual({
      path: "/settings/preferences/library",
      query: { tab: "duplicates" },
    });
    // the retired "admin" section with its old tab names
    expect(
      legacySettingsRedirect({
        path: "/settings",
        query: { section: "admin", tab: "sso" },
      }),
    ).toEqual({ path: "/settings/administration/oidc", query: {} });
    expect(
      legacySettingsRedirect({
        path: "/settings",
        query: { area: "account", host: "1" },
      }),
    ).toEqual({ path: "/settings/account", query: { host: "1" } });
    expect(
      legacySettingsRedirect({
        path: "/settings",
        query: { section: "upload" },
      }),
    ).toEqual({ path: "/upload", query: {} });
    // already a path, or nothing to move
    expect(
      legacySettingsRedirect({ path: "/settings/account/profile", query: {} }),
    ).toBeNull();
    expect(legacySettingsRedirect({ path: "/settings", query: {} })).toBeNull();
    expect(
      legacySettingsRedirect({ path: "/games", query: { section: "x" } }),
    ).toBeNull();
  });

  it("matches the router's settings pattern", async () => {
    const pattern = routerSource.match(/path: "(\/settings\/:area[^"]+)"/)?.[1];
    expect(pattern).toBeTruthy();
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: pattern!, name: "settings", component: { render: () => null } },
      ],
    });
    for (const [path, params] of [
      ["/settings", { area: "", section: "" }],
      ["/settings/account", { area: "account", section: "" }],
      [
        "/settings/administration/plugins",
        { area: "administration", section: "plugins" },
      ],
    ] as const) {
      const resolved = router.resolve(path);
      expect(resolved.name).toBe("settings");
      expect(resolved.params.area ?? "").toBe(params.area);
      expect(resolved.params.section ?? "").toBe(params.section);
    }
    expect(routerSource).toContain("legacySettingsRedirect(to)");
  });

  it("points the sidebar's Preferences, Administration and account at their landings (A19)", () => {
    expect(sidebarSource).toContain('to="/settings/preferences"');
    expect(sidebarSource).toContain('to="/settings/administration"');
    expect(sidebarSource).toContain('to="/settings/account"');
    expect(sidebarSource).not.toContain("section=appearance");
  });

  it("links inside the app use settings paths, not the legacy ?section= form", () => {
    // Every in-app link goes straight to its path, so a click costs no
    // redirect hop. Only the redirect itself and the command palette's
    // fallback for a plugin section whose area is not known yet keep the
    // old form.
    const sources = import.meta.glob(
      ["../**/*.{vue,ts}", "!../tests/**", "!../**/*.test.ts"],
      { query: "?raw", import: "default", eager: true },
    ) as Record<string, string>;
    const allowed = new Set([
      "../state/settingsRegistry.ts",
      "../components/CommandPalette.vue",
    ]);
    const offenders = Object.entries(sources)
      .filter(([file]) => !allowed.has(file))
      .filter(([, text]) => /\/settings\?(section|area)=/.test(text))
      .map(([file]) => file);
    expect(offenders).toEqual([]);
  });
});

describe("settings names and the Import & sync hub (A20)", () => {
  it("never shows the same section name in two areas", () => {
    const groups = buildSettingsGroups(true, [], []);
    const labels = groups.flatMap((group) =>
      group.sections.map((section) => section.label),
    );
    expect(new Set(labels).size).toBe(labels.length);
    const groupLabels = new Set(groups.map((group) => group.label));
    expect(labels.filter((label) => groupLabels.has(label))).toEqual([]);
  });

  it("lists Import & sync first under Library & sources and finds it by source name", () => {
    signIn(false);
    const library = buildSettingsGroups(false, [], []).find(
      (group) => group.label === "Library & sources",
    );
    expect(library?.sections[0]).toEqual({
      id: "import-sync",
      label: "Import & sync",
    });
    for (const term of ["steam", "anilist", "playstation", "upload"])
      expect(searchSettings(settingsSearchIndex.value, term)[0]?.section).toBe(
        "import-sync",
      );
  });

  it("still places plugin sections that name the old Library group", () => {
    const groups = buildSettingsGroups(
      false,
      [
        {
          pluginId: "example.plugin",
          contributionId: "example.library-extra",
          label: "Extra",
          group: "Library",
          area: "preferences",
          folders: [],
        } as never,
      ],
      [],
    );
    expect(
      groups
        .find((group) => group.label === "Library & sources")
        ?.sections.map((section) => section.id),
    ).toContain("example.library-extra");
    expect(groups.some((group) => group.label === "Library")).toBe(false);
  });
});
