// Screenshots of book search and "Add this edition" for the M10 PR. Docker
// (and so the backend, Postgres and the plugin runtime) was unavailable, so the
// real frontend (Vite dev server) runs against a stubbed /api: one invented
// book provider answering the metadata session endpoints. Every book here is
// synthetic.
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
const PROVIDER = "official.metadata-example.books";
const edition = (id, over) => ({ id, book_id: "b1", isbn_13: null, isbn_10: null, other_identifiers: {},
  format: "paperback", publisher: null, publish_date: null, publish_date_precision: null, page_count: null,
  language: null, cover_url: null, ownership: "none", acquired_at: null, note: null,
  created_at: now, updated_at: now, ...over });
const book = (id, title, over = {}) => ({ id, user_id: "u1", title, sort_title: title, subtitle: null,
  authors: [], description: null, first_published: null, first_published_precision: null, language: null,
  genres: [], tags: [], series_name: null, series_position: null, cover_url: null, provider_ids: {},
  status: "WISHLIST", priority: null, favorite: false, note: null, rating_overall: null, personal_rank: null,
  active_edition_id: null, locked_fields: [], editions: [], reads: [], created_at: now, updated_at: now, ...over });

const saved = book("b1", "The Lantern Archive", {
  subtitle: "A Tale of Tides", authors: ["Mira Holt"], first_published: "1987-01-01",
  first_published_precision: "year", language: "en", provider_ids: { example: "EX-W1" },
  editions: [edition("e1", { isbn_13: "9780000000019", format: "hardcover", publisher: "Gullwing Press",
    publish_date: "1987-03-01", publish_date_precision: "month", page_count: 320, ownership: "owned" })],
});

const providerBook = {
  authors: [{ name: "Mira Holt", role: "author" }, { name: "Ines Ko", role: "illustrator" }],
  subtitle: "A Tale of Tides", isbn_13: "9780000000002", series_name: "Tidewater", series_position: "2",
  original_language: "en", first_published: { date: "1987", precision: "year" },
  editions: [
    { key: "ED1", isbn_13: "9780000000002", format: "paperback", publisher: "Harbor Books",
      publish_date: { date: "2005-08", precision: "month" }, page_count: 352, language: "en" },
    { key: "ED2", isbn_13: "9780000000019", format: "hardcover", publisher: "Gullwing Press",
      publish_date: { date: "1987-03", precision: "month" }, page_count: 320, language: "en" },
    { key: "ED3", format: "audiobook", publisher: "Low Tide Audio",
      publish_date: { date: "2019-11-04", precision: "day" }, language: "en" },
  ],
};
const candidate = (id, title, over = {}) => ({ id, rank: 0, external_id: id, title, year: null,
  provider: PROVIDER, provider_name: "Example Books", providers: [PROVIDER], provider_ids: {},
  media_type: "book", alternate_titles: [], authors: [], isbn_13: null, metadata: {}, assets: [], ...over });
const lantern = candidate("c1", "The Lantern Archive", { year: 1987, authors: ["Mira Holt"],
  isbn_13: "9780000000002", provider_ids: { example: "EX-W1" } });
// the provider reports its primary ISBN even for a title search; only an ISBN search adds that edition
const lanternFull = { ...lantern, assets: [{ kind: "poster", url: "https://covers.example.test/lantern.png" }],
  metadata: { title: "The Lantern Archive",
  description: "An invented novel used only to show the page: a lighthouse keeper catalogues the letters that wash ashore.",
  genres: ["Fantasy", "Mystery"], book: providerBook } };
const others = [
  candidate("c2", "Lanterns of the Salt Road", { year: 2011, authors: ["Ada Wren"], rank: 1 }),
  candidate("c3", "The Lantern Keeper's Almanac", { year: 1994, authors: ["Jon Pell"], rank: 2 }),
];

const requests = [];
let coverPng = null; // drawn once in the browser: an invented cover, served by the stubbed artwork preview
let selectedOnce = false;
function respond(route, body, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}
function stream(route, events) {
  const after = Number(new URL(route.request().url()).searchParams.get("after") ?? 0);
  const body = events.filter(e => e.id > after)
    .map(e => `id: ${e.id}\nevent: ${e.event}\ndata: ${JSON.stringify(e)}\n\n`).join("");
  return route.fulfill({ status: 200, contentType: "text/event-stream", body: `retry: 60000\n\n${body}` });
}
async function api(route) {
  const request = route.request();
  const url = new URL(request.url());
  const p = url.pathname;
  if (p.startsWith("/api/metadata")) requests.push(`${request.method()} ${p} ${request.postData() ?? ""}`.trim());
  if (p === "/api/setup/status") return respond(route, { setup_required: false, startup_mode: "normal", startup_ui_enabled: false });
  if (p === "/api/auth/me") return respond(route, user);
  if (p === "/api/preferences") return respond(route, { ui_theme: "system", date_format: "dmy", date_format_chosen: true,
    date_format_offered: true, ui_welcome_completed: true, metadata_automatic_search_enabled: false });
  if (p === "/api/metadata/providers") return respond(route, { providers: [{ provider_id: PROVIDER,
    plugin_id: "official.metadata-example", name: "Example Books", media_types: ["book"], state: "healthy",
    checked_at: now, failure: null, configured_fields: {}, configuration: [],
    operations: { search: "search", metadata: "metadata", media: null, health: null } }] });
  if (p === "/api/metadata/sessions" && request.method() === "POST")
    return respond(route, { id: "s1", query: "lantern", media_type: "book", state: "running", results: [], last_event_id: 0 });
  if (p === "/api/metadata/sessions/s1/events") return stream(route, [
    { id: 1, event: "result_added", session_id: "s1", result: lantern },
    { id: 2, event: "result_added", session_id: "s1", result: others[0] },
    { id: 3, event: "result_added", session_id: "s1", result: others[1] },
    { id: 4, event: "search_completed", session_id: "s1" },
    // sent once the person picks a result (the selection stream starts after event 4)
    ...(selectedOnce ? [{ id: 5, event: "result_updated", session_id: "s1", result: lanternFull },
      { id: 6, event: "selection_enrichment_completed", session_id: "s1" }] : []),
  ]);
  if (p === "/api/metadata/sessions/s1/artwork")
    return route.fulfill({ status: 200, contentType: "image/png", body: coverPng });
  if (p === "/api/metadata/sessions/s1/selection") {
    selectedOnce = true;
    return respond(route, lanternFull);
  }
  if (p === "/api/metadata/selection")
    return respond(route, { id: "s2", query: "The Lantern Archive", media_type: "book", state: "running",
      results: [lanternFull], last_event_id: 0 });
  if (p === "/api/metadata/sessions/s2/events") return stream(route, [
    { id: 1, event: "result_updated", session_id: "s2", result: lanternFull },
    { id: 2, event: "selection_enrichment_completed", session_id: "s2" },
  ]);
  if (p.startsWith("/api/metadata/sessions/") && request.method() === "DELETE") return respond(route, {});
  if (p === "/api/book/list") return respond(route, { items: [saved], total: 1, offset: 0, limit: 50,
    status_counts: { WISHLIST: 1 }, score_ranks: {} });
  if (p === "/api/book/get/b1") return respond(route, saved);
  return respond(route, { detail: "Not available in this capture" }, 404);
}

const browser = await chromium.launch({ headless: true });
{
  const page = await browser.newPage();
  const dataUrl = await page.evaluate(() => {
    const canvas = Object.assign(document.createElement("canvas"), { width: 240, height: 360 });
    const g = canvas.getContext("2d");
    const sky = g.createLinearGradient(0, 0, 0, 360);
    sky.addColorStop(0, "#1d3557");
    sky.addColorStop(1, "#457b9d");
    g.fillStyle = sky;
    g.fillRect(0, 0, 240, 360);
    g.fillStyle = "#f1c453";
    g.beginPath();
    g.arc(120, 150, 34, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = "#f1faee";
    g.font = "bold 22px serif";
    g.textAlign = "center";
    g.fillText("The Lantern", 120, 270);
    g.fillText("Archive", 120, 298);
    return canvas.toDataURL("image/png");
  });
  coverPng = Buffer.from(dataUrl.split(",")[1], "base64");
  await page.close();
}
const shots = [];
const errors = [];
try {
  for (const [device, viewport] of [["desktop", { width: 1440, height: 1000 }], ["mobile", { width: 390, height: 844 }]]) {
    for (const scheme of ["light", "dark"]) {
      selectedOnce = false;
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
      // the add form: search a provider, then the chosen result
      await page.goto(`${origin}/books`, { waitUntil: "domcontentloaded" });
      await page.getByText("The Lantern Archive").first().waitFor();
      // on a phone the add button sits behind "Controls"
      if (!(await page.locator("button.add-btn").isVisible()))
        await page.getByRole("button", { name: "Controls" }).click();
      await page.locator("button.add-btn").click();
      const dialog = page.getByRole("dialog");
      await dialog.waitFor();
      await dialog.getByPlaceholder("Search by title, author or ISBN").fill("lantern");
      await dialog.getByRole("button", { name: "Search", exact: true }).click();
      await dialog.locator(".metadata-result").first().waitFor();
      await capture("book-search-results", false);
      await dialog.locator(".metadata-result").first().click();
      await dialog.locator(".found-book").waitFor();
      await dialog.getByText("Gathering metadata and artwork…").waitFor({ state: "hidden" });
      await dialog.locator(".found-book img").waitFor();
      assert.match(await dialog.locator(".found-book img").getAttribute("src"), /^blob:/);
      // a title search: the provider's ISBN adds no edition
      assert.equal(await dialog.getByText("Also adds the edition you searched for").count(), 0);
      await capture("book-search-found", false);
      await page.keyboard.press("Escape");
      // an ISBN search: that edition is added with the work
      selectedOnce = false;
      await page.goto(`${origin}/books`, { waitUntil: "domcontentloaded" });
      await page.getByText("The Lantern Archive").first().waitFor();
      if (!(await page.locator("button.add-btn").isVisible()))
        await page.getByRole("button", { name: "Controls" }).click();
      await page.locator("button.add-btn").click();
      await dialog.waitFor();
      await dialog.getByPlaceholder("Search by title, author or ISBN").fill("978-0-00-000000-2");
      await dialog.getByRole("button", { name: "Search", exact: true }).click();
      await dialog.locator(".metadata-result").first().waitFor();
      await dialog.locator(".metadata-result", { hasText: "The Lantern Archive" }).click();
      await dialog.locator(".found-book").waitFor();
      await dialog.getByText("Gathering metadata and artwork…").waitFor({ state: "hidden" });
      await dialog.locator(".found-book img").waitFor();
      await dialog.getByText("Also adds the edition you searched for").waitFor();
      await capture("book-search-isbn-found", false);
      await page.keyboard.press("Escape");
      // the book page: the provider's editions, one already held
      await page.goto(`${origin}/books/b1`, { waitUntil: "domcontentloaded" });
      await page.getByRole("heading", { name: "The Lantern Archive" }).first().waitFor();
      await page.getByRole("button", { name: "Find editions" }).click();
      await page.getByRole("button", { name: "Add this edition" }).first().waitFor();
      await page.evaluate(() => document.activeElement instanceof HTMLElement && document.activeElement.blur());
      if (device === "mobile") {
        // a viewport shot: in a full-page phone capture the fixed bars land mid-page
        await page.locator(".provider-editions").evaluate(el => el.scrollIntoView({ block: "start" }));
        await capture("book-provider-editions", false);
      } else {
        await capture("book-provider-editions");
      }
      await context.close();
    }
  }
} finally {
  await browser.close();
}
await writeFile(path.join(outDir, "capture-report.json"), JSON.stringify({ stubbed_api: true,
  synthetic_data: true, screenshots: shots, page_errors: errors, metadata_requests: [...new Set(requests)] }, null, 2));
console.log(JSON.stringify({ shots, errors }, null, 2));
