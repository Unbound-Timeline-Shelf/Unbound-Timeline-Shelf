// Acceptance checks for UX-AUDIT-APP §3.6 (U4 part 1): your place survives a
// reload, Back walks back through the sections and tabs you opened, and a
// dialog with unsaved edits asks before Escape, Back or a reload discard
// them. Clicks that a measurement depends on go through element.click() so
// Playwright's scroll-into-view cannot move the page first (critic C5).
import { expect, test, type Locator, type Page } from "@playwright/test";

const TOLERANCE = 50;

async function settle(page: Page, ms = 400) {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page.waitForTimeout(ms);
}
const scrollY = (page: Page) => page.evaluate(() => window.scrollY);
const press = (target: Locator) =>
  target.evaluate((element) => (element as HTMLElement).click());
const visible = (page: Page, name: string) =>
  page.getByRole("button", { name, exact: true }).filter({ visible: true });

/** Scrolls the window to `y`, letting a paged list load until it can. */
async function scrollWindowTo(page: Page, y: number): Promise<number> {
  for (let attempt = 0; attempt < 40; attempt++) {
    const reached = await page.evaluate((target) => {
      window.scrollTo(0, target);
      return Math.abs(window.scrollY - target) < 2;
    }, y);
    if (reached) break;
    await page.evaluate(() =>
      window.scrollTo(0, document.documentElement.scrollHeight),
    );
    await page.waitForTimeout(300);
  }
  await page.evaluate((target) => window.scrollTo(0, target), y);
  await page.waitForTimeout(500);
  return scrollY(page);
}

async function expectScrollRestored(page: Page, y: number) {
  await expect
    .poll(async () => Math.abs((await scrollY(page)) - y), { timeout: 10_000 })
    .toBeLessThanOrEqual(TOLERANCE);
}

async function firstGameId(page: Page): Promise<string> {
  const response = await page.request.get("/api/game/list?limit=1");
  const [game] = (await response.json()) as { id: string }[];
  return game.id;
}

test.describe("reload keeps your place (A3, A5)", () => {
  test("in the games library", async ({ page }) => {
    await page.goto("/games");
    await settle(page, 800);
    const y = await scrollWindowTo(page, 2500);
    expect(y).toBeGreaterThan(2400);
    await page.reload();
    await settle(page);
    await expectScrollRestored(page, y);
  });

  test("on a game page", async ({ page }) => {
    await page.goto(`/games/${await firstGameId(page)}`);
    await settle(page, 800);
    const max = await page.evaluate(
      () => document.documentElement.scrollHeight - window.innerHeight,
    );
    const y = await scrollWindowTo(page, Math.min(600, max - 10));
    expect(y).toBeGreaterThan(100);
    await page.reload();
    await settle(page);
    await expectScrollRestored(page, y);
  });

  for (const section of ["appearance", "library", "shortcuts"]) {
    test(`in settings › ${section}`, async ({ page }) => {
      await page.goto(`/settings?section=${section}`);
      await settle(page, 800);
      const max = await page.evaluate(
        () => document.documentElement.scrollHeight - window.innerHeight,
      );
      test.skip(max < 300, "the section is too short to scroll");
      const y = await scrollWindowTo(page, Math.min(1500, max - 10));
      await page.reload();
      await settle(page);
      await expectScrollRestored(page, y);
    });
  }

  test("after Back and Forward to a game page (A5)", async ({ page }) => {
    await page.goto("/games");
    await settle(page);
    await page.goto(`/games/${await firstGameId(page)}`);
    await settle(page, 800);
    const y = await scrollWindowTo(page, 500);
    await page.goBack();
    await settle(page);
    await page.goForward();
    await settle(page);
    await expectScrollRestored(page, y);
  });
});

test.describe("Back walks back through what you opened (A6, A9, A11, A16)", () => {
  test("settings sections", async ({ page }) => {
    await page.goto("/");
    await settle(page);
    await page.goto("/settings");
    await settle(page);
    const steps = ["Appearance & interface", "Library", "Keyboard Shortcuts"];
    const urls = [page.url()];
    for (const label of steps) {
      await press(visible(page, label).first());
      await settle(page, 300);
      urls.push(page.url());
    }
    expect(new Set(urls).size).toBe(steps.length + 1);
    for (let back = steps.length - 1; back >= 0; back--) {
      await page.goBack();
      await settle(page, 300);
      expect(page.url()).toBe(urls[back]);
    }
  });

  test("a settings section's tabs", async ({ page }) => {
    await page.goto("/settings?section=library");
    await settle(page);
    const tabs = page.getByRole("tab");
    for (const label of ["Duplicates", "Trash"]) {
      await press(tabs.filter({ hasText: label }));
      await settle(page, 200);
    }
    await expect(tabs.filter({ hasText: "Trash" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await page.goBack();
    await expect(tabs.filter({ hasText: "Duplicates" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await page.goBack();
    await expect(tabs.filter({ hasText: "Library" }).first()).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  test("game page tabs, kept across a reload", async ({ page }) => {
    await page.goto(`/games/${await firstGameId(page)}`);
    await settle(page, 800);
    const tab = (name: string) =>
      page.locator(".tabbar .tab-btn", { hasText: new RegExp(`^${name}$`) });
    const active = page.locator(".tabbar .tab-btn.active");
    const opened = ["Notes", "Stats", "Overview"];
    for (const name of opened) {
      await press(tab(name));
      await settle(page, 200);
      await expect(active).toHaveText(name);
    }
    await press(tab("Stats"));
    await page.reload();
    await settle(page, 800);
    await expect(active).toHaveText("Stats");
    for (const name of [...opened].reverse()) {
      await page.goBack();
      await settle(page, 200);
      await expect(active).toHaveText(name);
    }
  });

  test("the game page's back arrow skips its tabs (A23)", async ({ page }) => {
    await page.goto("/games?sort=rating");
    await settle(page);
    const library = page.url();
    await page.goto(`/games/${await firstGameId(page)}`);
    await settle(page, 800);
    for (const name of ["Notes", "Stats"]) {
      await press(
        page.locator(".tabbar .tab-btn", { hasText: new RegExp(`^${name}$`) }),
      );
      await settle(page, 200);
    }
    await press(page.locator(".back-spot").first());
    await settle(page);
    expect(page.url()).toBe(library);
  });

  test("plugin manager views", async ({ page }) => {
    await page.goto("/settings?section=plugins");
    await settle(page, 800);
    const views = page.locator("nav.manager-tabs button");
    for (const name of ["Discover", "Updates Available"]) {
      await press(views.filter({ hasText: name }));
      await settle(page, 200);
    }
    expect(new URL(page.url()).searchParams.get("view")).toBe("updates");
    await page.goBack();
    await expect(views.filter({ hasText: "Discover" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await page.reload();
    await settle(page, 800);
    await expect(views.filter({ hasText: "Discover" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await page.goBack();
    await expect(views.filter({ hasText: "Installed" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  test("library status tabs and search", async ({ page }) => {
    await page.goto("/");
    await settle(page);
    await page.goto("/games");
    await settle(page, 800);
    await press(page.locator(".status-tab", { hasText: /^\s*playing/i }));
    await settle(page, 300);
    expect(new URL(page.url()).searchParams.get("status")).toBe("playing");
    const search = page.getByRole("textbox", { name: "Search games" });
    await search.fill("Amber");
    await page.waitForTimeout(1200);
    await search.fill("Quiet");
    await page.waitForTimeout(1200);
    await page.goBack();
    await settle(page, 300);
    expect(new URL(page.url()).searchParams.get("q")).toBe("Amber");
    await page.goBack();
    await settle(page, 300);
    expect(new URL(page.url()).searchParams.get("q")).toBeNull();
    expect(new URL(page.url()).searchParams.get("status")).toBe("playing");
    await page.goBack();
    await settle(page, 300);
    expect(new URL(page.url()).searchParams.get("status")).toBeNull();
  });
});

test.describe("a dirty dialog asks first (A1, A2)", () => {
  async function openDirtyEditor(page: Page) {
    await page.goto("/games");
    await settle(page);
    await page.goto(`/games/${await firstGameId(page)}`);
    await settle(page, 800);
    await page
      .getByRole("button", { name: /Edit$/ })
      .filter({ visible: true })
      .first()
      .click();
    const editor = page.locator("dialog.game-editor[open]");
    await expect(editor).toBeVisible();
    expect(new URL(page.url()).searchParams.get("edit")).toBe("basics");
    const title = editor.locator('input[type="text"]').first();
    const typed = `${await title.inputValue()} (edited)`;
    await title.fill(typed);
    return { editor, title, typed };
  }
  const question = (page: Page) =>
    page.getByRole("dialog", { name: "Discard changes?" });

  test("on Escape @phone", async ({ page }) => {
    const { editor, title, typed } = await openDirtyEditor(page);
    await page.keyboard.press("Escape");
    await expect(question(page)).toBeVisible();
    await question(page).getByRole("button", { name: "Keep editing" }).click();
    await expect(editor).toBeVisible();
    await expect(title).toHaveValue(typed);

    await title.focus();
    await page.keyboard.press("Escape");
    await question(page)
      .getByRole("button", { name: "Discard changes" })
      .click();
    await expect(editor).toBeHidden();
    expect(new URL(page.url()).searchParams.get("edit")).toBeNull();
  });

  test("on Back, which closes the dialog rather than the page @phone", async ({
    page,
  }) => {
    const { editor, title, typed } = await openDirtyEditor(page);
    const gamePage = page.url().split("?")[0];
    await page.goBack();
    await expect(question(page)).toBeVisible();
    await question(page).getByRole("button", { name: "Keep editing" }).click();
    await expect(editor).toBeVisible();
    await expect(title).toHaveValue(typed);
    expect(new URL(page.url()).searchParams.get("edit")).toBe("basics");

    await page.goBack();
    await question(page)
      .getByRole("button", { name: "Discard changes" })
      .click();
    await expect(editor).toBeHidden();
    // Back closed the editor and stayed on the game page
    expect(page.url()).toBe(gamePage);
  });

  test("on reload", async ({ page }) => {
    const { editor, title, typed } = await openDirtyEditor(page);
    let asked = "";
    page.once("dialog", async (dialog) => {
      asked = dialog.type();
      await dialog.dismiss();
    });
    // the cancelled reload never finishes loading, so do not wait for it
    await page.reload({ timeout: 3000 }).catch(() => undefined);
    await expect.poll(() => asked).toBe("beforeunload");
    await expect(editor).toBeVisible();
    await expect(title).toHaveValue(typed);
  });

  test("but a clean dialog closes on Escape and Back without asking", async ({
    page,
  }) => {
    await page.goto(`/games/${await firstGameId(page)}`);
    await settle(page, 800);
    const edit = page
      .getByRole("button", { name: /Edit$/ })
      .filter({ visible: true })
      .first();
    await edit.click();
    const editor = page.locator("dialog.game-editor[open]");
    await expect(editor).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(editor).toBeHidden();
    await expect(question(page)).toHaveCount(0);
    await edit.click();
    await expect(editor).toBeVisible();
    await page.goBack();
    await expect(editor).toBeHidden();
    await expect(question(page)).toHaveCount(0);
  });
});
