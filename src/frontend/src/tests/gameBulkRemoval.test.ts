import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { reactive } from "vue";
import type { Game } from "../types/game";

// #462: removing several games reports what the server did and the library
// follows the persisted result.

const routing = vi.hoisted(() => ({
  route: {} as Record<string, unknown>,
  replace: vi.fn(),
  push: vi.fn(),
}));
vi.mock("vue-router", async (original) => ({
  ...(await original<typeof import("vue-router")>()),
  useRoute: () => routing.route,
  useRouter: () => ({ replace: routing.replace, push: routing.push }),
}));
vi.mock("vue", async (original) => ({
  ...(await original<typeof import("vue")>()),
  onMounted: vi.fn(),
  onUnmounted: vi.fn(),
  onActivated: vi.fn(),
  onDeactivated: vi.fn(),
}));
vi.mock("../utils/useKeptAlive", () => ({ useKeptAlive: vi.fn() }));
vi.mock("@tanstack/vue-virtual", async () => {
  const { ref } = await import("vue");
  return { useWindowVirtualizer: () => ref({}) };
});

function game(id: string): Game {
  return {
    id,
    title: `Title ${id}`,
    status: "backlog",
    tags: [],
    favorite: false,
    platforms: [],
    collections: [],
    achievements: [],
  } as unknown as Game;
}

beforeEach(() => {
  const data = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => data.set(key, value),
  });
  vi.stubGlobal("window", {
    innerWidth: 1440,
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
  });
  vi.stubGlobal("document", { documentElement: { clientWidth: 1440 } });
  routing.route = reactive({
    path: "/games",
    fullPath: "/games",
    hash: "",
    query: {},
  });
  routing.replace.mockResolvedValue(undefined);
});
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  vi.resetModules();
});

function respond(body: unknown, ok = true) {
  return {
    ok,
    status: ok ? 200 : 503,
    statusText: ok ? "OK" : "Service Unavailable",
    headers: new Headers(),
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

describe("bulkDeleteGames", () => {
  it("sends deduplicated, bounded batches and keeps going after a failed one", async () => {
    const ids = Array.from({ length: 205 }, (_, i) => `g${i}`);
    const batches: string[][] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, options: RequestInit) => {
        const { game_ids } = JSON.parse(String(options.body)) as {
          game_ids: string[];
        };
        batches.push(game_ids);
        if (batches.length === 2) return respond({ detail: "busy" }, false);
        return respond({
          deleted: game_ids.slice(1),
          not_found: game_ids.slice(0, 1),
          already_deleted: [],
          failed: [],
        });
      }),
    );
    const { bulkDeleteGames } = await import("../services/gameBulkActions");
    const outcome = await bulkDeleteGames([...ids, "g0", "g1"]);
    expect(batches.map((batch) => batch.length)).toEqual([100, 100, 5]);
    expect(outcome.notFound).toEqual(["g0", "g200"]);
    expect(outcome.deleted).toHaveLength(99 + 4);
    // the request that failed outright counts every game in it as not removed
    expect(outcome.failed.map((failure) => failure.id)).toEqual(
      ids.slice(100, 200),
    );
  });

  it("forgets removed games so no cached snapshot shows them again", async () => {
    const { rememberGameMutation, peekGame, gameLibraryRevision } =
      await import("../services/gameCache");
    rememberGameMutation(game("a"));
    rememberGameMutation(game("b"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        respond({
          deleted: ["a"],
          not_found: [],
          already_deleted: [],
          failed: [{ id: "b", detail: "storage is busy" }],
        }),
      ),
    );
    const before = gameLibraryRevision();
    const { bulkDeleteGames } = await import("../services/gameBulkActions");
    await bulkDeleteGames(["a", "b"]);
    expect(peekGame("a")).toBeUndefined();
    expect(peekGame("b")?.id).toBe("b");
    expect(gameLibraryRevision()).toBeGreaterThan(before);
  });
});

describe("Games selection removal", () => {
  it("keeps only failed games selected, reports the outcome and reloads", async () => {
    const remaining = [game("b")];
    vi.doMock("../services/gameBulkActions", async (original) => ({
      ...(await original<typeof import("../services/gameBulkActions")>()),
      bulkDeleteGames: vi.fn(async () => ({
        deleted: ["a"],
        notFound: ["gone"],
        alreadyDeleted: [],
        failed: [{ id: "b", detail: "storage is busy" }],
      })),
    }));
    vi.doMock("../services/games", async (original) => ({
      ...(await original<typeof import("../services/games")>()),
      fetchGamesForViewport: vi.fn(async () => ({
        games: remaining,
        total: remaining.length,
        statusCounts: null,
      })),
      peekAllGames: vi.fn(() => null),
      fetchAchievementsSummary: vi.fn(async () => null),
    }));
    const { useGameLibrary } = await import("../composables/useGameLibrary");
    const library = useGameLibrary();
    library.games.value = [game("a"), game("b")];
    library.selectMode.value = true;
    library.selectedIds.value = new Set(["a", "b", "gone"]);

    library.openBulkDelete();
    expect(library.confirmingBulkDelete.value).toBe(true);
    await library.confirmBulkDelete();

    expect(library.confirmingBulkDelete.value).toBe(false);
    expect([...library.selectedIds.value]).toEqual(["b"]);
    expect(library.bulkDeleteMessage.value).toBe(
      "Moved 1 game to trash; 1 no longer in your library; 1 could not be removed and stay selected",
    );
    expect(library.bulkDeleteFailures.value).toEqual([
      { id: "b", title: "Title b", detail: "storage is busy" },
    ]);
    // the list now shows what the server holds
    expect(library.games.value.map((g) => g.id)).toEqual(["b"]);
  });

  it("does nothing without a selection", async () => {
    const { useGameLibrary } = await import("../composables/useGameLibrary");
    const library = useGameLibrary();
    library.openBulkDelete();
    expect(library.confirmingBulkDelete.value).toBe(false);
  });
});
