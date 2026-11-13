// Screenshots of the Books pages for the M9 PR. Docker (and so the backend
// and Postgres) was unavailable, so the real frontend (Vite dev server) runs
// against a stubbed /api with invented books. Every response here is synthetic.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

const [pluginsRoot, outDir, origin] = process.argv.slice(2);
const { chromium } = createRequire(path.resolve(pluginsRoot, "package.json"))("playwright");
await mkdir(outDir, { recursive: true });

const now = 1767225600;
const user = { id: "u1", username: "reader", display_name: "Sample Reader", email: "reader@example.test",
  is_admin: false, steamgriddb_api_key: null };
const edition = (id, over) => ({ id, book_id: "b1", isbn_13: null, isbn_10: null, other_identifiers: {},
  format: "paperback", publisher: null, publish_date: null, publish_date_precision: null, page_count: null,
  language: null, cover_url: null, ownership: "none", acquired_at: null, note: null,
  created_at: now, updated_at: now, ...over });
const read = (id, over) => ({ id, book_id: "b1", edition_id: null, started_on: null, finished_on: null,
  progress_unit: "page", progress_value: null, outcome: "reading", created_at: now, updated_at: now, ...over });
const book = (id, title, over = {}) => ({ id, user_id: "u1", title, sort_title: title, subtitle: null,
  authors: [], description: null, first_published: null, first_published_precision: null, language: null,
  genres: [], tags: [], series_name: null, series_position: null, cover_url: null, provider_ids: {},
  status: "WISHLIST", priority: null, favorite: false, note: null, rating_overall: null, personal_rank: null,
  active_edition_id: null, locked_fields: [], editions: [], reads: [], created_at: now, updated_at: now, ...over });

const main = book("b1", "The Lantern Archive", {
  subtitle: "A Tale of Tides", authors: ["Mira Holt", "Tobias Fenn"],
  description: "An invented novel used only to show the page: a lighthouse keeper catalogues the letters that wash ashore.",
  first_published: "1987-01-01", first_published_precision: "year", language: "en",
  genres: ["Fantasy", "Mystery"], tags: ["reread"], series_name: "Tidewater", series_position: "2",
  status: "IN_PROGRESS", favorite: true, rating_overall: "8.50", active_edition_id: "e2",
  editions: [
    edition("e1", { isbn_13: "9780000000002", isbn_10: "0000000000", format: "hardcover", publisher: "Gullwing Press",
      publish_date: "1987-03-01", publish_date_precision: "month", page_count: 320, language: "en", ownership: "owned",
      acquired_at: "2019-08-14" }),
    edition("e2", { format: "ebook", publisher: "Gullwing Digital", publish_date: "2015-01-01",
      publish_date_precision: "year", page_count: 352, ownership: "owned", acquired_at: "2024-11-05" }),
  ],
  reads: [
    read("r1", { edition_id: "e1", started_on: "2020-01-04", finished_on: "2020-02-11", progress_value: "320",
      outcome: "finished" }),
    read("r2", { edition_id: "e2", started_on: "2026-01-02", progress_value: "141" }),
  ],
});
const books = [
  main,
  book("b2", "Salt and Cinder", { authors: ["Ada Wren"], status: "READ", rating_overall: "9.00",
    first_published: "2003-05-01", first_published_precision: "month" }),
  book("b3", "A Field Guide to Quiet Rooms", { authors: ["Jon Pell"], status: "WISHLIST" }),
  book("b4", "Northbound", { authors: ["Ines Ko"], status: "ON_HOLD", first_published: "2011-01-01",
    first_published_precision: "year", reads: [read("r9", { progress_unit: "percent", progress_value: "40" })] }),
  book("b5", "The Clockmaker's Daughter of Vell", { authors: ["R. Amari"], status: "BACKLOG" }),
];

function respond(route, body, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}
async function api(route) {
  const url = new URL(route.request().url());
  const p = url.pathname;
  if (p === "/api/setup/status") return respond(route, { setup_required: false, startup_mode: "normal", startup_ui_enabled: false });
  if (p === "/api/auth/me") return respond(route, user);
  if (p === "/api/preferences") return respond(route, { ui_theme: "system", date_format: "dmy", date_format_chosen: true,
    date_format_offered: true, ui_welcome_completed: true });
  if (p === "/api/book/list") {
    const bucket = url.searchParams.get("status_bucket");
    const items = bucket ? [] : books;
    return respond(route, { items, total: items.length, offset: 0, limit: 50,
      status_counts: { IN_PROGRESS: 1, READ: 1, WISHLIST: 1, ON_HOLD: 1, BACKLOG: 1 }, score_ranks: {} });
  }
  if (p.startsWith("/api/book/get/")) {
    const found = books.find(b => b.id === p.split("/").pop());
    return found ? respond(route, found) : respond(route, { detail: "Not found" }, 404);
  }
  if (p === "/api/book/b3") return respond(route, books[2]);
  return respond(route, { detail: "Not available in this capture" }, 404);
}

const browser = await chromium.launch({ headless: true });
const shots = [];
const errors = [];
try {
  for (const [device, viewport] of [["desktop", { width: 1440, height: 1000 }], ["mobile", { width: 390, height: 844 }]]) {
    for (const scheme of ["light", "dark"]) {
      const context = await browser.newContext({ viewport, colorScheme: scheme, deviceScaleFactor: 1 });
      const page = await context.newPage();
      page.on("pageerror", e => errors.push(`${device}-${scheme}: ${e}`));
      await page.route("**/api/**", api);
      const capture = async (name, fullPage = true) => {
        await page.waitForTimeout(700);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
        assert(!overflow, `${name} ${device} ${scheme}: horizontal overflow`);
        const file = `${name}-${device}-${scheme}.png`;
        await page.screenshot({ path: path.join(outDir, file), fullPage });
        shots.push(file);
      };
      await page.goto(`${origin}/books`, { waitUntil: "domcontentloaded" });
      await page.getByText("The Lantern Archive").first().waitFor();
      await capture("books-library");
      await page.goto(`${origin}/books/b1`, { waitUntil: "domcontentloaded" });
      await page.getByRole("heading", { name: "The Lantern Archive" }).waitFor();
      await capture("book-detail");
      await page.locator("[aria-labelledby=reads-heading] li").first().getByRole("button", { name: "Edit" }).click();
      await page.getByRole("dialog").waitFor();
      await capture("book-read-dialog", false);
      await page.keyboard.press("Escape");
      await page.goto(`${origin}/books/b3`, { waitUntil: "domcontentloaded" });
      await page.getByRole("heading", { name: "A Field Guide to Quiet Rooms" }).waitFor();
      await capture("book-detail-empty");
      await context.close();
    }
  }
} finally {
  await browser.close();
}
await writeFile(path.join(outDir, "capture-report.json"), JSON.stringify({ stubbed_api: true,
  synthetic_data: true, screenshots: shots, page_errors: errors }, null, 2));
console.log(JSON.stringify({ shots, errors }, null, 2));
