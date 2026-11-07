// @vitest-environment happy-dom
// The game page's panels that read a game's sources share one request when
// they load together, and a later load asks the server again.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchIdentitySources } from "../services/gameIdentity";

const body = { observations: [], relationships: [] };

describe("fetchIdentitySources", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shares one request between callers in the same task", async () => {
    const [a, b, c] = await Promise.all([
      fetchIdentitySources("g1"),
      fetchIdentitySources("g1", new AbortController().signal),
      fetchIdentitySources("g1"),
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(a).toEqual(body);
    // each caller gets its own copy
    expect(a).not.toBe(b);
    expect(b).not.toBe(c);
  });

  it("asks again for a later load and for another game", async () => {
    await fetchIdentitySources("g1");
    await fetchIdentitySources("g1");
    await Promise.all([fetchIdentitySources("g1"), fetchIdentitySources("g2")]);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("lets one caller stop waiting without failing the others", async () => {
    const stop = new AbortController();
    const first = fetchIdentitySources("g1", stop.signal);
    const second = fetchIdentitySources("g1");
    stop.abort();
    await expect(first).rejects.toThrow();
    await expect(second).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("passes a failed request to every caller", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "nope" }), { status: 500 }),
    );
    const results = await Promise.allSettled([
      fetchIdentitySources("g1"),
      fetchIdentitySources("g1"),
    ]);
    expect(results.map((r) => r.status)).toEqual(["rejected", "rejected"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
