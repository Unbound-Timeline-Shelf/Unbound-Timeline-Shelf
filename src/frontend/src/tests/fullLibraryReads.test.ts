import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchGames, peekGame, setFavorite } from "../services/games";
import type { BackendGame } from "../services/games";
import { resetEntityCaches } from "../utils/entityCache";

// #469: callers asking for the whole library while an identical read is still
// in flight share that read; anything that changed the library or the account
// since it started gets a fresh one, and finished or failed reads are never
// reused.
function raw(id: string, title: string): BackendGame {
  return {
    id,
    title,
    sort_title: title.toLowerCase(),
    source: "Steam",
    platform: "PC",
    status: "BACKLOG",
    playtime_seconds: 0,
    created_at: 0,
    updated_at: 0,
    last_played_at: null,
    stale_since: null,
    completion_date: null,
    purchase_date: null,
    purchase_price: null,
    purchase_price_currency_code: null,
    physical_condition: null,
    rating_overall: null,
    rating_story: null,
    rating_gameplay: null,
    rating_soundtrack: null,
    time_to_beat_hours: null,
    tags: [],
    features: [],
    collections: [],
    links: [],
  } as unknown as BackendGame;
}

const listCalls = (fetch: ReturnType<typeof vi.fn>) =>
  fetch.mock.calls.filter(([url]) => String(url).startsWith("/api/game/list"))
    .length;

function deferredList() {
  const pending: ((games: BackendGame[]) => void)[] = [];
  const fetch = vi.fn((url: string) => {
    if (!String(url).startsWith("/api/game/list"))
      return Promise.resolve(
        new Response(JSON.stringify({ ...raw("a", "Alpha"), favorite: true })),
      );
    return new Promise<Response>((resolve) => {
      pending.push((games) =>
        resolve(new Response(JSON.stringify(games), { status: 200 })),
      );
    });
  });
  vi.stubGlobal("fetch", fetch);
  return { fetch, pending };
}

afterEach(() => {
  vi.unstubAllGlobals();
  resetEntityCaches();
});

describe("complete library reads (#469)", () => {
  it("shares one in-flight read between concurrent callers, each with its own array", async () => {
    const { fetch, pending } = deferredList();
    const first = fetchGames();
    const second = fetchGames();
    await Promise.resolve();
    expect(listCalls(fetch)).toBe(1);
    pending[0]?.([raw("a", "Alpha"), raw("b", "Beta")]);
    const [one, two] = await Promise.all([first, second]);
    expect(one.map((game) => game.id)).toEqual(["a", "b"]);
    expect(two).toEqual(one);
    expect(two).not.toBe(one);
    one.reverse();
    expect(two.map((game) => game.id)).toEqual(["a", "b"]);
  });

  it("never reuses a finished read", async () => {
    const { fetch, pending } = deferredList();
    const first = fetchGames();
    await Promise.resolve();
    pending[0]?.([raw("a", "Alpha")]);
    await first;
    const again = fetchGames();
    await Promise.resolve();
    expect(listCalls(fetch)).toBe(2);
    pending[1]?.([raw("a", "Alpha"), raw("c", "Gamma")]);
    expect((await again).map((game) => game.id)).toEqual(["a", "c"]);
  });

  it("starts a fresh read after a change made while one was in flight", async () => {
    const { fetch, pending } = deferredList();
    const before = fetchGames();
    await Promise.resolve();
    await setFavorite("a", true);
    const after = fetchGames();
    await Promise.resolve();
    expect(listCalls(fetch)).toBe(2);
    pending[0]?.([raw("a", "Alpha")]);
    pending[1]?.([{ ...raw("a", "Alpha"), favorite: true } as BackendGame]);
    expect((await after)[0]?.favorite).toBe(true);
    // The older read still answers its own caller without undoing the save.
    await before;
    expect(peekGame("a")?.favorite).toBe(true);
  });

  it("starts a fresh read for another account and never shares across accounts", async () => {
    const { fetch, pending } = deferredList();
    const previous = fetchGames();
    await Promise.resolve();
    resetEntityCaches();
    const next = fetchGames();
    await Promise.resolve();
    expect(listCalls(fetch)).toBe(2);
    pending[1]?.([raw("mine", "Mine")]);
    pending[0]?.([raw("theirs", "Theirs")]);
    expect((await next).map((game) => game.id)).toEqual(["mine"]);
    await previous;
    expect(peekGame("theirs")).toBeUndefined();
  });

  it("does not remember a failed read, so a retry asks again", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response("nope", { status: 500 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify([raw("a", "Alpha")]), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetch);
    const failed = fetchGames();
    const joined = fetchGames();
    await expect(failed).rejects.toThrow();
    await expect(joined).rejects.toThrow();
    expect((await fetchGames()).map((game) => game.id)).toEqual(["a"]);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
