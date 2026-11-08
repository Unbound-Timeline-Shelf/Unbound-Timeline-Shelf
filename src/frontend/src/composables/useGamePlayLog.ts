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
export function useGamePlayLog(game: Ref<Game | null>) {
  const loggingPlaytime = ref(false);
  const playLogMessage = ref<string | null>(null);
  const playLogError = ref<string | null>(null);
  const playedOn = ref("");

  function playLogTime(): number | undefined {
    if (!playedOn.value) return undefined;
    const chosen = playedOnDate(playedOn.value);
    if (chosen === null) return undefined;
    // today's date means "now"; local noon of today may still be ahead
    return Math.min(chosen, Math.floor(Date.now() / 1000));
  }

  async function recordPlay(minutes = 0) {
    if (!game.value || loggingPlaytime.value) return;
    const id = game.value.id;
    const playedAt = playLogTime();
    loggingPlaytime.value = true;
    playLogMessage.value = playLogError.value = null;
    try {
      const logged = await logPlay(id, {
        playedAt,
        requestId: newPlayRequestId(),
      });
      if (game.value?.id !== id) return;
      if (logged.lastPlayedAt !== null)
        game.value.lastPlayedAt = new Date(
          logged.lastPlayedAt * 1000,
        ).toISOString();
      if (minutes > 0) {
        const currentSeconds = game.value.platforms.reduce(
          (sum, p) => sum + p.playtimeMinutes * 60,
          0,
        );
        const updated = await setPlaytimeSeconds(
          id,
          currentSeconds + minutes * 60,
        );
        if (game.value?.id !== id) return;
        game.value.platforms = updated.platforms;
      }
      const day = new Date(logged.playedAt * 1000).toLocaleDateString();
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
