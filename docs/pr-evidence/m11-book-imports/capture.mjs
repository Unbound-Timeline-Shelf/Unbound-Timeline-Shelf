// Screenshots of the book list import for the M11 PR. Docker (and so the
// backend and Postgres) was unavailable, so the real frontend (Vite dev server)
// runs against a stubbed /api. The preview rows and the import counts in
// stub-answers.json were produced by the real backend parser and matcher
// (make_preview.py) on goodreads-synthetic.csv; only which row is already in
// the library and the provider list are stated. Every book here is synthetic.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

const [pluginsRoot, outDir, origin] = process.argv.slice(2);
const { chromium } = createRequire(path.resolve(pluginsRoot, "package.json"))(
  "playwright",
);
const answers = JSON.parse(
  await readFile(path.join(outDir, "stub-answers.json"), "utf-8"),
);
const csv = path.join(outDir, "goodreads-synthetic.csv");

const user = {
  id: "u1",
  username: "reader",
  display_name: "Sample Reader",
  email: "reader@example.test",
  is_admin: false,
  steamgriddb_api_key: null,
};
const requests = [];
function respond(route, body, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}
async function api(route) {
  const request = route.request();
  const p = new URL(request.url()).pathname;
  if (p.startsWith("/api/import/books")) requests.push(`${request.method()} ${p}`);
  if (p === "/api/setup/status")
    return respond(route, {
      setup_required: false,
      startup_mode: "normal",
      startup_ui_enabled: false,
    });
  if (p === "/api/auth/me") return respond(route, user);
  if (p === "/api/preferences")
    return respond(route, {
      ui_theme: "system",
      date_format: "dmy",
      date_format_chosen: true,
      date_format_offered: true,
      ui_welcome_completed: true,
      anilist_import_username: "",
    });
  if (p === "/api/export/backup-status")
    return respond(route, {
      enabled: true,
      interval_hours: 24,
      backups_kept: 7,
      backup_count: 2,
      last_backup_at: 1767225600,
    });
  if (p === "/api/import/books/preview") return respond(route, answers.preview);
  if (p === "/api/import/books") return respond(route, answers.result);
  return respond(route, { detail: "Not available in this capture" }, 404);
}

const browser = await chromium.launch({ headless: true });
const shots = [];
const errors = [];
try {
  for (const [device, viewport] of [
    ["desktop", { width: 1440, height: 1000 }],
    ["mobile", { width: 390, height: 844 }],
  ]) {
    for (const scheme of ["light", "dark"]) {
      const context = await browser.newContext({
        viewport,
        colorScheme: scheme,
        deviceScaleFactor: 1,
      });
      const page = await context.newPage();
      page.on("pageerror", (e) => errors.push(`${device}-${scheme}: ${e}`));
      await page.route("**/api/**", api);
      const tile = page.locator(".book-import");
      const capture = async (name) => {
        await page.waitForTimeout(500);
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth + 1,
        );
        assert(!overflow, `${name} ${device} ${scheme}: horizontal overflow`);
        await tile.scrollIntoViewIfNeeded();
        const file = `${name}-${device}-${scheme}.png`;
        await tile.screenshot({ path: path.join(outDir, file) });
        shots.push(file);
      };
      await page.goto(`${origin}/settings/preferences/export?tab=import`, {
        waitUntil: "domcontentloaded",
      });
      await tile.waitFor();
      // ISBN list: the paste box
      await tile.getByRole("button", { name: "ISBN list" }).click();
      await tile
        .locator("#book-isbn-list")
        .fill("9781111111113\n978-2-222-22222-4, 0-000-00001-9\n# to read next");
      await capture("book-import-isbn-list");
      // Goodreads: choose the file, review, import
      await tile.getByRole("button", { name: "Goodreads" }).click();
      await tile.locator('input[type="file"]').setInputFiles(csv);
      await tile.locator('[data-testid="book-import-preview"]').waitFor();
      const text = await tile.innerText();
      assert.match(text, /read 14\/03\/2021/);
      assert.doesNotMatch(text, /2021-03-14/);
      await capture("book-import-preview");
      await tile.getByRole("button", { name: /^Import \d+ books?$/ }).click();
      await tile.locator('[data-testid="book-import-result"]').waitFor();
      await capture("book-import-result");
      await context.close();
    }
  }
} finally {
  await browser.close();
}
await writeFile(
  path.join(outDir, "capture-report.json"),
  JSON.stringify(
    {
      stubbed_api: true,
      synthetic_data: true,
      answers_from: "make_preview.py (real parser and matcher)",
      screenshots: shots,
      page_errors: errors,
      import_requests: [...new Set(requests)],
    },
    null,
    2,
  ) + "\n",
);
console.log(JSON.stringify({ shots, errors }, null, 2));
