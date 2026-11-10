// Acceptance checks for UX-AUDIT-APP §3.6 steps 4–7 (U4 part 2): cards are
// links and the first library card is at most 15 Tab stops away, settings
// sections are paths with an Import & sync hub, the phone game page shows its
// tab bar above the bottom navigation, and Home, error states and the
// first-visit welcome are polished.
import { expect, test, type Page } from "@playwright/test";

const MAX_TAB_STOPS = 15;

async function settle(page: Page, ms = 400) {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page.waitForTimeout(ms);
}
const inFirstCard = (page: Page) =>
  page.evaluate(() =>
    Boolean(document.activeElement?.closest(".game-card-wrap")),
  );
const focusedText = (page: Page) =>
  page.evaluate(() => {
    const active = document.activeElement as HTMLElement | null;
    return (
      active?.getAttribute("aria-label") ||
      active?.textContent ||
      ""
    ).trim();
  });

/** Tab presses from the current focus until a library card has focus. */
async function tabsToFirstCard(page: Page, limit = 60): Promise<number> {
  for (let presses = 1; presses <= limit; presses++) {
    await page.keyboard.press("Tab");
    if (await inFirstCard(page)) return presses;
  }
  return Infinity;
}

async function firstGameId(page: Page): Promise<string> {
  const response = await page.request.get("/api/game/list?limit=1");
  const [game] = (await response.json()) as { id: string }[];
  return game.id;
}

test.describe("keyboard: card links and Tab stops (A14, A15, C1)", () => {
  test("the skip link lands on the page heading, then at most 15 Tab stops to the first card", async ({
    page,
  }) => {
    await page.goto("/games");
    await settle(page, 800);
    await page.keyboard.press("Tab");
    expect(await focusedText(page)).toBe("Skip to content");
    await page.keyboard.press("Enter");
    await expect(page.locator("#main-content h1")).toBeFocused();
    const presses = 1 + (await tabsToFirstCard(page));
    expect(presses).toBeLessThanOrEqual(MAX_TAB_STOPS);
  });

  test("opening the library from the sidebar puts focus on its heading, at most 15 Tab stops from the first card", async ({
    page,
  }) => {
    await page.goto("/");
    await settle(page, 800);
    await page
      .getByRole("navigation", { name: "Library and tools" })
      .getByRole("link", { name: "Games", exact: true })
      .focus();
    await page.keyboard.press("Enter");
    await settle(page, 800);
    await expect(page.locator("#main-content h1")).toHaveText("Games");
    await expect(page.locator("#main-content h1")).toBeFocused();
    expect(await tabsToFirstCard(page)).toBeLessThanOrEqual(MAX_TAB_STOPS);
  });

  test("the card's first stop is a link to the game, so Enter opens it and changes nothing", async ({
    page,
  }) => {
    await page.goto("/games");
    await settle(page, 800);
    const card = page.locator(".game-card-wrap").first();
    const link = card.locator("a.cover-link");
    const href = await link.getAttribute("href");
    expect(href).toMatch(/^\/games\/[0-9a-f-]+$/);
    const favoriteBefore = await card
      .locator(".favorite-button")
      .getAttribute("aria-pressed");
    await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");
    await expect(page.locator("#main-content h1")).toBeFocused();
    expect(await tabsToFirstCard(page)).toBeLessThan(Infinity);
    await expect(link).toBeFocused();
    await page.keyboard.press("Enter");
    await page.waitForURL(`**${href}`);
    await page.goBack();
    await settle(page);
    expect(
      await page
        .locator(".game-card-wrap")
        .first()
        .locator(".favorite-button")
        .getAttribute("aria-pressed"),
    ).toBe(favoriteBefore);
  });

  test("Ctrl-click on a card opens the game in a new tab", async ({
    page,
    context,
  }) => {
    await page.goto("/games");
    await settle(page, 800);
    const link = page.locator(".game-card-wrap a.cover-link").first();
    const href = await link.getAttribute("href");
    const [opened] = await Promise.all([
      context.waitForEvent("page"),
      link.click({ modifiers: ["Control"] }),
    ]);
    await opened.waitForLoadState();
    expect(new URL(opened.url()).pathname).toBe(href);
    expect(new URL(page.url()).pathname).toBe("/games");
    await opened.close();
  });

  test("status filters are one Tab stop; arrows move and Enter chooses", async ({
    page,
  }) => {
    await page.goto("/games");
    await settle(page, 800);
    const tabs = page.getByRole("tablist", { name: "Status" }).getByRole("tab");
    await expect(tabs.first()).toHaveAttribute("aria-selected", "true");
    expect(
      await tabs.evaluateAll(
        (items) => items.filter((item) => item.tabIndex === 0).length,
      ),
    ).toBe(1);
    await tabs.first().focus();
    await page.keyboard.press("ArrowRight");
    await expect(tabs.nth(1)).toBeFocused();
    await expect(tabs.first()).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("Enter");
    await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true");
    expect(new URL(page.url()).searchParams.get("status")).toBe("playing");
  });

  test("game page tabs are a tablist with the open tab selected (C3)", async ({
    page,
  }) => {
    await page.goto(`/games/${await firstGameId(page)}`);
    await settle(page, 800);
    const tablist = page.getByRole("tablist", { name: "Game page sections" });
    await expect(tablist.getByRole("tab", { selected: true })).toHaveText(
      "Overview",
    );
    await tablist.getByRole("tab", { name: "Overview" }).focus();
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Enter");
    await expect(tablist.getByRole("tab", { selected: true })).not.toHaveText(
      "Overview",
    );
  });
});

test.describe("settings are paths, with an Import & sync hub (A19, A20)", () => {
  test("old ?section= links land on the section's path", async ({ page }) => {
    await page.goto("/settings?section=appearance");
    await settle(page);
    expect(new URL(page.url()).pathname).toBe(
      "/settings/preferences/appearance",
    );
    await expect(page.locator("#main-content h1")).toHaveText(
      "Appearance & interface",
    );
    await page.goto("/settings?section=library&tab=trash");
    await settle(page);
    const url = new URL(page.url());
    expect(url.pathname).toBe("/settings/preferences/library");
    expect(url.searchParams.get("tab")).toBe("trash");
  });

  test("sections open as links and Back walks back through them", async ({
    page,
  }) => {
    await page.goto("/settings/preferences");
    await settle(page);
    const landing = page.locator(".settings-links a", {
      hasText: "Import & sync",
    });
    await expect(landing).toHaveAttribute(
      "href",
      "/settings/preferences/import-sync",
    );
    await landing.click();
    await settle(page);
    await expect(page.locator("#main-content h1")).toHaveText("Import & sync");
    const hub = page.locator(".import-sync");
    for (const link of [
      "Open connections",
      "Import a list",
      "Restore and back up",
      "Open Upload",
    ])
      await expect(
        hub.getByRole("link", { name: new RegExp(link) }),
      ).toBeVisible();
    await hub.getByRole("link", { name: /Open connections/ }).click();
    await settle(page);
    expect(new URL(page.url()).pathname).toBe("/settings/account/connections");
    await page.goBack();
    await settle(page);
    expect(new URL(page.url()).pathname).toBe(
      "/settings/preferences/import-sync",
    );
    await page.goBack();
    await settle(page);
    expect(new URL(page.url()).pathname).toBe("/settings/preferences");
  });

  test("the same name never appears in two settings areas", async ({
    page,
  }) => {
    await page.goto("/settings/administration/users");
    await settle(page);
    for (const name of [
      "My notifications",
      "My background tasks",
      "Notification delivery",
      "Server background tasks",
    ]) {
      await page.locator(".settings-search input").fill(name);
      await expect(page.getByRole("option").first()).toContainText(name);
    }
  });
});

test.describe("phone layout (A12, A13) @phone", () => {
  test.skip(
    ({ viewport }) => (viewport?.width ?? 1440) > 760,
    "phone layout only",
  );

  test("the game page's tab bar is on the first screen, above the bottom navigation @phone", async ({
    page,
  }) => {
    await page.goto(`/games/${await firstGameId(page)}`);
    await settle(page, 800);
    const bar = await page
      .getByRole("tablist", { name: "Game page sections" })
      .boundingBox();
    const nav = await page.locator("nav.mobile-tabs").boundingBox();
    expect(bar).not.toBeNull();
    expect(nav).not.toBeNull();
    expect(bar!.y).toBeGreaterThanOrEqual(0);
    expect(bar!.y + bar!.height).toBeLessThanOrEqual(nav!.y);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
  });

  test("the library needs one row of switchers above its heading @phone", async ({
    page,
  }) => {
    await page.goto("/games");
    await settle(page, 800);
    const switchers = await page
      .locator(".media-topbar")
      .first()
      .evaluate((bar) => {
        const tops = [...bar.querySelectorAll(".seg")].map(
          (seg) => seg.getBoundingClientRect().top,
        );
        return new Set(tops.map(Math.round)).size;
      });
    expect(switchers).toBe(1);
    await expect(
      page.getByRole("link", { name: "Collections" }).first(),
    ).toBeVisible();
  });

  test("More in the bottom navigation opens the menu with Calendar and Upload @phone", async ({
    page,
  }) => {
    await page.goto("/games");
    await settle(page);
    await page
      .locator("nav.mobile-tabs")
      .getByRole("button", { name: "More" })
      .click();
    const menu = page.locator("#app-navigation");
    await expect(menu.getByRole("link", { name: "Calendar" })).toBeVisible();
    await expect(menu.getByRole("link", { name: "Upload" })).toBeVisible();
  });

  test("the editor is a full-screen sheet with Delete kept in Advanced @phone", async ({
    page,
  }) => {
    await page.goto(`/games/${await firstGameId(page)}`);
    await settle(page, 800);
    await page
      .getByRole("button", { name: /Edit$/ })
      .filter({ visible: true })
      .first()
      .click();
    const editor = page.locator("dialog.game-editor[open]");
    await expect(editor).toBeVisible();
    const box = await editor.boundingBox();
    const viewport = page.viewportSize()!;
    expect(Math.round(box!.width)).toBe(viewport.width);
    await expect(
      editor
        .locator(".modal-footer")
        .getByRole("button", { name: "Delete game" }),
    ).toHaveCount(0);
    await editor.getByRole("tab", { name: "Advanced" }).click();
    await expect(
      editor.getByRole("button", { name: "Delete game" }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
  });
});

test.describe("Home, errors and the first visit (A18, A22, A24, A25)", () => {
  test("a library that cannot load says so without claiming 0 games", async ({
    page,
  }) => {
    await page.route(/\/api\/game\/list/, (route) => route.abort("failed"));
    await page.goto("/games");
    await settle(page, 1200);
    const head = page.locator(".page-head .sub");
    await expect(head).toHaveText(/—/);
    await expect(head).not.toHaveText(/\b0 games/);
    await expect(page.getByRole("alert").first()).toContainText(
      "Couldn't reach the server. Your library is safe",
    );
    await expect(page.getByRole("alert").first()).not.toContainText(
      "Failed to fetch",
    );
    await expect(
      page.getByRole("button", { name: "Retry" }).first(),
    ).toBeVisible();
  });

  test("Home shows suggested widgets for a library with games and plain internal links", async ({
    page,
  }) => {
    const prefs = await (await page.request.get("/api/preferences")).json();
    test.skip(
      prefs.home_widgets.length > 0 || prefs.home_widgets_customized,
      "this account already chose its Home",
    );
    await page.goto("/");
    await settle(page, 800);
    await expect(
      page.getByRole("region", { name: "Continue playing" }),
    ).toBeVisible();
    await expect(
      page.getByRole("region", { name: "Recently added" }),
    ).toBeVisible();
    await expect(page.getByText("Make yourself at home.")).toHaveCount(0);
    await expect(page.locator(".home-shortcuts")).not.toContainText("↗");
  });

  test("the first-visit welcome can wait until later", async ({ page }) => {
    await page.request.patch("/api/preferences", {
      data: { ui_welcome_completed: false },
    });
    try {
      await page.goto("/");
      const welcome = page.getByRole("dialog", {
        name: "Make yourself at home",
      });
      await expect(welcome).toBeVisible();
      await welcome.getByRole("button", { name: "Later" }).click();
      await expect(welcome).toBeHidden();
      await page.goto("/games");
      await settle(page);
      await page.goto("/");
      await expect(welcome).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(welcome).toBeHidden();
    } finally {
      await page.request.patch("/api/preferences", {
        data: { ui_welcome_completed: true },
      });
    }
  });

  test("the plugin manager's actions sit under the page heading on every view", async ({
    page,
  }) => {
    await page.goto("/settings/administration/plugins");
    await settle(page, 800);
    const actions = page.locator(".manager-actions");
    for (const view of ["Installed", "Updates Available", "Discover"]) {
      await page.locator("nav.manager-tabs button", { hasText: view }).click();
      for (const name of [
        "Install a plugin",
        "Install package or URL",
        "Check for updates",
      ])
        await expect(actions.getByRole("button", { name })).toBeVisible();
    }
    await expect(
      page.locator(".plugin-manager h2", { hasText: /^Plugins$/ }),
    ).toHaveCount(0);
  });
});
