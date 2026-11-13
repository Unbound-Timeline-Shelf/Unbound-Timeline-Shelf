// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { RouteLocationNormalized } from "vue-router";
import {
  forgetKeptScroll,
  rememberScroll,
  useScrollReady,
} from "../state/scrollMemory";

vi.mock("vue-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("vue-router")>();
  return { ...actual, createWebHistory: actual.createMemoryHistory };
});
import router from "../router";

const scroll = router.options.scrollBehavior!;
const route = (path: string): RouteLocationNormalized => {
  const resolved = router.resolve(path);
  return { ...resolved, name: resolved.name ?? undefined };
};
function documentHeight(height: number) {
  Object.defineProperty(document.documentElement, "scrollHeight", {
    configurable: true,
    get: () => height,
  });
}
beforeEach(() => {
  forgetKeptScroll();
  documentHeight(10_000);
});
afterEach(() => vi.useRealTimers());

it.each(["/games", "/movies", "/tv", "/anime"])(
  "preserves the viewport when filtering %s without a document reload",
  async (path) => {
    expect(
      await scroll(
        route(`${path}?tags=Puzzle&genres=Drama`),
        route(path),
        null,
      ),
    ).toBe(false);
    expect(await scroll(route(path), route(`${path}?tags=Puzzle`), null)).toBe(
      false,
    );
  },
);
it("preserves scroll when changing sort or other query options on the same page", async () => {
  expect(
    await scroll(
      route("/games?sort=rating&filters=1"),
      route("/games?filters=1"),
      null,
    ),
  ).toBe(false);
});
it("uses the browser history position before query-only preservation", async () => {
  const saved = { left: 0, top: 720 };
  expect(
    await scroll(route("/games?tags=Puzzle"), route("/games"), saved),
  ).toEqual(saved);
});
it("starts a different page at the top", async () => {
  expect(
    await scroll(route("/movies"), route("/games?tags=Puzzle"), null),
  ).toEqual({ top: 0 });
});
it("keeps existing fragment navigation behavior", async () => {
  expect(await scroll(route("/games#details"), route("/games"), null)).toEqual({
    top: 0,
  });
});

// UX-AUDIT-APP A10: a settings section is a new place, a tab inside it is not
it("starts a new settings section at the top", async () => {
  expect(
    await scroll(
      route("/settings?section=dev-tools"),
      route("/settings?section=users"),
      null,
    ),
  ).toEqual({ top: 0 });
  expect(
    await scroll(
      route("/settings?area=account"),
      route("/settings?area=preferences"),
      null,
    ),
  ).toEqual({ top: 0 });
});
it("keeps the viewport when only a settings tab or plugin view changes", async () => {
  expect(
    await scroll(
      route("/settings?section=library&tab=duplicates"),
      route("/settings?section=library"),
      null,
    ),
  ).toBe(false);
  expect(
    await scroll(
      route("/settings?section=plugins&view=discover"),
      route("/settings?section=plugins"),
      null,
    ),
  ).toBe(false);
});
it("keeps the viewport when a game tab or the editor step changes", async () => {
  const id = "00000000-0000-4000-8000-000000000001";
  expect(
    await scroll(route(`/games/${id}?tab=Stats`), route(`/games/${id}`), null),
  ).toBe(false);
  expect(
    await scroll(
      route(`/games/${id}?edit=basics`),
      route(`/games/${id}`),
      null,
    ),
  ).toBe(false);
});

// UX-AUDIT-APP A3/A5: a saved position waits for the page, not the route
it("restores a saved position only once the view is ready", async () => {
  let ready = false;
  useScrollReady(() => ready);
  const result = scroll(route("/games"), route("/"), { left: 0, top: 2500 });
  let settled = false;
  void Promise.resolve(result).then(() => (settled = true));
  await new Promise((resolve) => setTimeout(resolve, 120));
  expect(settled).toBe(false);
  ready = true;
  expect(await result).toEqual({ left: 0, top: 2500 });
});

// UX-AUDIT-APP A4: a kept-alive library returns to where it was left,
// whether you come back by Back or by a link
it("returns a kept-alive library to where it was left through a link", async () => {
  rememberScroll(route("/games?filters=0"), { left: 0, top: 2500 });
  expect(await scroll(route("/games"), route("/"), null)).toEqual({
    left: 0,
    top: 2500,
  });
  // other pages are not kept alive and start at the top
  rememberScroll(route("/upload"), { left: 0, top: 900 });
  expect(await scroll(route("/upload"), route("/"), null)).toEqual({ top: 0 });
});
it("does not carry a library position over to a different filter", async () => {
  rememberScroll(route("/games?status=playing"), { left: 0, top: 2500 });
  expect(await scroll(route("/games"), route("/"), null)).toEqual({ top: 0 });
});
