import { afterEach, describe, expect, it, vi } from "vitest";
import {
  logPlay,
  newPlayRequestId,
  playedOnDate,
} from "../services/gameIdentity";

// #467: logging a play is activity, sent to its own endpoint; a playtime
// correction never goes through it.
describe("logPlay", () => {
  afterEach(() => vi.unstubAllGlobals());

  function stubFetch(body: unknown, ok = true) {
    const fetchMock = vi.fn().mockResolvedValue({
      ok,
      status: ok ? 201 : 422,
      statusText: ok ? "Created" : "Unprocessable",
      json: async () => body,
      text: async () => JSON.stringify(body),
      headers: new Headers({ "content-type": "application/json" }),
      clone() {
        return this;
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("posts now with a retry-safe request id and returns the new last played", async () => {
    const fetchMock = stubFetch({
      event: { played_at: 1_700_000_000 },
      last_played_at: 1_700_000_000,
    });
    const result = await logPlay("g1", { requestId: "req-12345678" });
    expect(result).toEqual({
      playedAt: 1_700_000_000,
      lastPlayedAt: 1_700_000_000,
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/game-identity/games/g1/plays");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ request_id: "req-12345678" });
  });

  it("sends an earlier time and keeps a newer last played from the server", async () => {
    const fetchMock = stubFetch({
      event: { played_at: 1_600_000_000 },
      last_played_at: 1_700_000_000,
    });
    const result = await logPlay("g1", { playedAt: 1_600_000_000 });
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.played_at).toBe(1_600_000_000);
    expect(body.request_id).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
    expect(result.lastPlayedAt).toBe(1_700_000_000);
  });

  it("surfaces a refused log", async () => {
    stubFetch({ detail: "The play time cannot be in the future." }, false);
    await expect(logPlay("g1", { playedAt: 9_999_999_999 })).rejects.toThrow();
  });
});

describe("play log helpers", () => {
  it("makes request ids the server accepts, different each time", () => {
    const first = newPlayRequestId();
    expect(first).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
    expect(newPlayRequestId()).not.toBe(first);
  });

  it("places a date-only choice at local noon so no timezone moves its day", () => {
    const seconds = playedOnDate("2024-02-29");
    expect(seconds).not.toBeNull();
    const when = new Date((seconds as number) * 1000);
    expect([when.getFullYear(), when.getMonth(), when.getDate()]).toEqual([
      2024, 1, 29,
    ]);
    expect(when.getHours()).toBe(12);
    expect(playedOnDate("")).toBeNull();
    expect(playedOnDate("29/02/2024")).toBeNull();
  });
});
