import { request, type FullConfig } from "@playwright/test";
import { mkdirSync } from "node:fs";

// A synthetic account and library: the first-run admin is created when the
// host is fresh, then enough games are added for the library to page and
// scroll. Values come from the environment so a lab can choose its own.
export const E2E_USER = process.env.E2E_USERNAME ?? "e2e-navigation";
const E2E_PASSWORD =
  process.env.E2E_PASSWORD ?? "E2e-navigation-synthetic-pass1!";
const MIN_GAMES = 120;

export default async function globalSetup(config: FullConfig) {
  const baseURL = config.projects[0].use.baseURL!;
  const api = await request.newContext({ baseURL });
  const status = await (await api.get("/api/setup/status")).json();
  if (status.setup_required) {
    const created = await api.post("/api/setup", {
      data: {
        username: E2E_USER,
        email: `${E2E_USER}@example.invalid`,
        password: E2E_PASSWORD,
      },
    });
    if (!created.ok()) throw new Error(`setup ${created.status()}`);
  }
  const login = await api.post("/api/auth/login", {
    data: { username_or_email: E2E_USER, password: E2E_PASSWORD },
  });
  if (!login.ok()) throw new Error(`login ${login.status()}`);
  // the first-visit appearance welcome would cover every page
  const prefs = await api.patch("/api/preferences", {
    data: { ui_welcome_completed: true, date_format_offered: true },
  });
  if (!prefs.ok()) throw new Error(`preferences ${prefs.status()}`);

  const page = await api.get("/api/game/list?limit=1");
  const total = Number(page.headers()["x-total-count"] ?? 0);
  const adjectives = ["Amber", "Quiet", "Velvet", "Copper", "Paper", "Glass"];
  const nouns = ["Harbor", "Lantern", "Orchard", "Signal", "Summit"];
  for (let n = total; n < MIN_GAMES; n++) {
    const title = `${adjectives[n % 6]} ${nouns[n % 5]} ${n + 1}`;
    const created = await api.post("/api/game/create", {
      data: {
        title,
        folder_location: `e2e_navigation_${n + 1}`,
        description:
          "Synthetic record for the navigation acceptance checks. ".repeat(
            1 + (n % 9),
          ),
        status: ["playing", "backlog", "beaten", "wishlist"][n % 4],
      },
    });
    if (!created.ok()) throw new Error(`seed ${created.status()}`);
  }
  mkdirSync("test-results", { recursive: true });
  await api.storageState({ path: "test-results/.auth.json" });
  await api.dispose();
}
