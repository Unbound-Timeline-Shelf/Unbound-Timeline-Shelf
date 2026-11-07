import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// #468: the shared game, movie and anime snapshots belong to the signed-in
// account. Signing out, or another account signing in in the same tab, must
// never show the previous account's titles, and a late answer to a request
// the previous account started must not repopulate the next account's caches.

import { currentUser } from "../state/auth";
import {
  fetchGame,
  fetchGames,
  peekAllGames,
  peekGame,
  setFavorite,
  type BackendGame,
} from "../services/games";
import {
  fetchMovies,
  getMovie,
  peekAllMovies,
  peekMovie,
  movieLibraryRevision,
} from "../services/movies";
import { fetchAnimeRelations, peekAnimeRelations } from "../services/anime";
import type { CurrentUser } from "../services/auth";

function account(id: string): CurrentUser {
  return { id, username: id } as unknown as CurrentUser;
}

function rawGame(id: string, title = id): BackendGame {
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

function rawMovie(id: string) {
  return {
    id,
    title: id,
    status: "PLANNED",
    created_at: 0,
    updated_at: 0,
    tags: [],
  };
}

function ok(body: unknown) {
  return {
    ok: true,
    status: 200,
    headers: new Headers(),
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

// A fetch whose answers the test releases one at a time, like a slow server.
function slowServer() {
  const pending: { url: string; release: (body: unknown) => void }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (url: string) =>
        new Promise((resolve) =>
          pending.push({ url, release: (body) => resolve(ok(body)) }),
        ),
    ),
  );
  return pending;
}

function serve(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ok(body)),
  );
}

beforeEach(() => {
  currentUser.value = account("alice");
});
afterEach(() => {
  vi.unstubAllGlobals();
  currentUser.value = null;
});

describe("shared entity caches across accounts", () => {
  it("forgets the previous account's games when another account signs in", async () => {
    serve([rawGame("alice-game")]);
    await fetchGames();
    expect(peekAllGames()?.map((g) => g.id)).toEqual(["alice-game"]);

    currentUser.value = account("bob");

    expect(peekAllGames()).toBeNull();
    expect(peekGame("alice-game")).toBeUndefined();
  });

  it("shows nothing of the previous account when the next account's fetch fails", async () => {
    serve([rawGame("alice-game")]);
    await fetchGames();
    currentUser.value = null; // sign out
    currentUser.value = account("bob");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 503, json: async () => ({}) })),
    );
    await expect(fetchGame("alice-game")).rejects.toThrow();
    expect(peekAllGames()).toBeNull();
    expect(peekGame("alice-game")).toBeUndefined();
  });

  it("never stores a late list or detail answer started before the account changed", async () => {
    const pending = slowServer();
    const list = fetchGames();
    const detail = fetchGame("alice-detail");
    await vi.waitFor(() => expect(pending).toHaveLength(2));

    currentUser.value = account("bob");
    pending[0]?.release([rawGame("alice-late")]);
    pending[1]?.release(rawGame("alice-detail"));

    // the callers still get their answer...
    expect((await list).map((g) => g.id)).toEqual(["alice-late"]);
    expect((await detail)?.id).toBe("alice-detail");
    // ...but bob's caches stay empty and incomplete
    expect(peekAllGames()).toBeNull();
    expect(peekGame("alice-late")).toBeUndefined();
    expect(peekGame("alice-detail")).toBeUndefined();

    // bob's own reads are cached as usual
    serve([rawGame("bob-game")]);
    await fetchGames();
    expect(peekAllGames()?.map((g) => g.id)).toEqual(["bob-game"]);
  });

  it("never stores a save answered after sign-out", async () => {
    const pending = slowServer();
    const save = setFavorite("alice-saved", true);
    await vi.waitFor(() => expect(pending).toHaveLength(1));
    currentUser.value = null;
    pending[0]?.release(rawGame("alice-saved"));
    await save;
    expect(peekGame("alice-saved")).toBeUndefined();
  });

  it("applies the same rule to movies and anime relations", async () => {
    serve({
      items: [rawMovie("alice-movie")],
      total: 1,
      offset: 0,
      limit: 100,
    });
    await fetchMovies();
    expect(peekAllMovies()?.map((m) => m.id)).toEqual(["alice-movie"]);
    const revision = movieLibraryRevision();

    const pending = slowServer();
    const list = fetchMovies();
    const one = getMovie("alice-other");
    const relations = fetchAnimeRelations("alice-anime");
    await vi.waitFor(() => expect(pending).toHaveLength(3));

    currentUser.value = account("bob");
    // views keyed on the library revision know to reload for bob
    expect(movieLibraryRevision()).toBeGreaterThan(revision);
    expect(peekAllMovies()).toBeNull();
    expect(peekMovie("alice-movie")).toBeUndefined();

    pending[0]?.release({
      items: [rawMovie("alice-late")],
      total: 1,
      offset: 0,
      limit: 100,
    });
    pending[1]?.release(rawMovie("alice-other"));
    pending[2]?.release({ chain: [], branches: [], configured: true });
    await Promise.all([list, one, relations]);

    expect(peekAllMovies()).toBeNull();
    expect(peekMovie("alice-late")).toBeUndefined();
    expect(peekMovie("alice-other")).toBeUndefined();
    expect(peekAnimeRelations("alice-anime")).toBeUndefined();
  });

  it("keeps same-account snapshots across ordinary navigation", async () => {
    serve([rawGame("kept")]);
    await fetchGames();
    // the session poll re-reading the same account changes nothing
    currentUser.value = account("alice");
    expect(peekGame("kept")?.id).toBe("kept");
    expect(peekAllGames()).not.toBeNull();
  });
});
