import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import type { Game } from "../types/game";

// #528 review: an uncertain play log (the server may have recorded it, but the
// response was lost) must be retried with the same request id, so the server's
// idempotency check returns the first play instead of recording a second one.
const logPlay = vi.fn();
const setPlaytimeSeconds = vi.fn();
let ids = 0;

vi.mock("../services/gameIdentity", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/gameIdentity")>()),
  logPlay: (...args: unknown[]) => logPlay(...args),
  newPlayRequestId: () => `req-${++ids}`,
}));
vi.mock("../services/games", () => ({
  setPlaytimeSeconds: (...args: unknown[]) => setPlaytimeSeconds(...args),
}));

const { useGamePlayLog } = await import("../composables/useGamePlayLog");

function game(id = "g1", minutes = 60): Game {
  return {
    id,
    lastPlayedAt: null,
    platforms: [{ playtimeMinutes: minutes }],
  } as unknown as Game;
}

const recorded = { playedAt: 1_700_000_000, lastPlayedAt: 1_700_000_000 };
const requestIds = () =>
  logPlay.mock.calls.map(
    ([, options]) => (options as { requestId: string }).requestId,
  );

describe("useGamePlayLog retries", () => {
  beforeEach(() => {
    logPlay.mockReset();
    setPlaytimeSeconds.mockReset();
    ids = 0;
  });

  it("reuses the request id after a committed play whose response was lost", async () => {
    logPlay
      .mockRejectedValueOnce(new Error("Network error"))
      .mockResolvedValueOnce(recorded);
    const log = useGamePlayLog(ref(game()));

    await log.logPlayNow();
    expect(log.playLogError.value).toBe("Network error");
    await log.logPlayNow();

    expect(log.playLogError.value).toBeNull();
    expect(requestIds()).toEqual(["req-1", "req-1"]);
  });

  it("retries only the playtime update when the play was recorded", async () => {
    logPlay.mockResolvedValue(recorded);
    setPlaytimeSeconds
      .mockRejectedValueOnce(new Error("Playtime update failed"))
      .mockResolvedValueOnce({ platforms: [{ playtimeMinutes: 90 }] });
    const current = ref(game());
    const log = useGamePlayLog(current);

    await log.logPlaytime(30);
    expect(log.playLogError.value).toBe("Playtime update failed");
    await log.logPlaytime(30);

    expect(logPlay).toHaveBeenCalledTimes(1);
    expect(setPlaytimeSeconds.mock.calls).toEqual([
      ["g1", 90 * 60],
      ["g1", 90 * 60],
    ]);
    expect(current.value?.platforms[0].playtimeMinutes).toBe(90);
    expect(log.playLogMessage.value).toMatch(/^Logged 30 min played on /);
  });

  it("uses a new request id once a play is logged", async () => {
    logPlay.mockResolvedValue(recorded);
    const log = useGamePlayLog(ref(game()));

    await log.logPlayNow();
    await log.logPlayNow();

    expect(requestIds()).toEqual(["req-1", "req-2"]);
  });

  it("treats a different date or game as a new play", async () => {
    logPlay.mockRejectedValue(new Error("Network error"));
    const current = ref(game());
    const log = useGamePlayLog(current);

    await log.logPlayNow();
    log.playedOn.value = "2024-02-29";
    await log.logPlayNow();
    current.value = game("g2");
    await log.logPlayNow();

    expect(requestIds()).toEqual(["req-1", "req-2", "req-3"]);
  });
});
