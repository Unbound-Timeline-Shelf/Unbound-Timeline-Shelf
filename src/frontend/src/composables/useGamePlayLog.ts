import { formatInstant } from "../utils/dates";
import { ref, type Ref } from "vue";
import { setPlaytimeSeconds } from "../services/games";
import {
  logPlay,
  newPlayRequestId,
  playedOnDate,
} from "../services/gameIdentity";
import type { Game } from "../types/game";

// Logging play (#467). "Log play" is activity: it records a play and moves last
// played forward. Adding a just-played session also adds its minutes to the
// total. Editing the total elsewhere is a correction and never counts as
// playing today.
//
// A failed attempt may still have been recorded (the response was lost), so a
// retry of the same play (same game, date and minutes) reuses its request id
// and the server returns the first play instead of recording another. Once the
// play is recorded, a retry only repeats the playtime update, to the same
// total.
type PendingPlay = {
  key: string;
  requestId: string;
  logged?: { playedAt: number; lastPlayedAt: number | null };
  playtimeSeconds?: number;
};

export function useGamePlayLog(game: Ref<Game | null>) {
  const loggingPlaytime = ref(false);
  const playLogMessage = ref<string | null>(null);
  const playLogError = ref<string | null>(null);
  const playedOn = ref("");
  let pending: PendingPlay | null = null;

  function playLogTime(): number | undefined {
    if (!playedOn.value) return undefined;
    const chosen = playedOnDate(playedOn.value);
    if (chosen === null) return undefined;
    // today's date means "now"; local noon of today may still be ahead
    return Math.min(chosen, Math.floor(Date.now() / 1000));
  }

  function finish(attempt: PendingPlay) {
    if (pending === attempt) pending = null;
  }

  async function recordPlay(minutes = 0) {
    if (!game.value || loggingPlaytime.value) return;
    const id = game.value.id;
    const playedAt = playLogTime();
    const key = JSON.stringify([id, playedOn.value, minutes]);
    if (pending?.key !== key) pending = { key, requestId: newPlayRequestId() };
    const attempt = pending;
    loggingPlaytime.value = true;
    playLogMessage.value = playLogError.value = null;
    try {
      const logged =
        attempt.logged ??
        (await logPlay(id, { playedAt, requestId: attempt.requestId }));
      attempt.logged = logged;
      // the play is recorded; leaving the game ends this attempt as before
      if (game.value?.id !== id) return finish(attempt);
      if (logged.lastPlayedAt !== null)
        game.value.lastPlayedAt = new Date(
          logged.lastPlayedAt * 1000,
        ).toISOString();
      if (minutes > 0) {
        // the total is fixed once, so a retried update cannot add twice
        attempt.playtimeSeconds ??=
          game.value.platforms.reduce(
            (sum, p) => sum + p.playtimeMinutes * 60,
            0,
          ) +
          minutes * 60;
        const updated = await setPlaytimeSeconds(id, attempt.playtimeSeconds);
        if (game.value?.id !== id) return finish(attempt);
        game.value.platforms = updated.platforms;
      }
      finish(attempt);
      const day = formatInstant(logged.playedAt, { time: false });
      playLogMessage.value =
        minutes > 0
          ? `Logged ${minutes} min played on ${day}.`
          : `Logged a play on ${day}.`;
      playedOn.value = "";
    } catch (err) {
      if (game.value?.id === id)
        playLogError.value =
          err instanceof Error ? err.message : "Could not log this play.";
    } finally {
      loggingPlaytime.value = false;
    }
  }

  return {
    loggingPlaytime,
    playLogMessage,
    playLogError,
    playedOn,
    logPlaytime: (minutes: number) => recordPlay(minutes),
    logPlayNow: () => recordPlay(0),
  };
}
