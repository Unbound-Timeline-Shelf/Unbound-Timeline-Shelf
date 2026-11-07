import { createEntityCache, onEntityCacheReset } from "../utils/entityCache";
import type { Game, GameRelationshipType } from "../types/game";

// Shared snapshots let detail pages render immediately and keep older list
// responses from overwriting a local mutation that finished while they loaded.
export const gameCache = createEntityCache<Game>();
let mutationVersion = 0;
const gameMutationVersions = new Map<string, number>();
// The revision at which the current account's snapshots began (#468): a
// response to a request started before it belongs to the previous account.
let accountRevision = 0;
onEntityCacheReset(() => {
  gameMutationVersions.clear();
  accountRevision = ++mutationVersion;
});
/** Whether a request started at revision `started` is for the current account. */
export const isCurrentAccountRequest = (started: number): boolean =>
  started >= accountRevision;
export const gameLibraryRevision = (): number => mutationVersion;
export const peekGame = gameCache.peek;
export const peekAllGames = (): Game[] | null =>
  gameCache.listLoaded() ? gameCache.all() : null;

export function rememberGameRelationship(
  id: string,
  parentGameId: string | null,
  relationshipType: GameRelationshipType | null,
): void {
  const game = gameCache.peek(id);
  if (game) rememberGameMutation({ ...game, parentGameId, relationshipType });
}

function rememberGame(game: Game): Game {
  const before = gameCache.peek(game.id);
  // A list omits achievements, so retain detail-page and summary enrichment.
  if (before && !game.achievements.length && before.achievements.length)
    game.achievements = before.achievements;
  if (before && !game.achievementTotal && before.achievementTotal) {
    game.achievementTotal = before.achievementTotal;
    game.achievementPercent = before.achievementPercent;
  }
  return gameCache.put(game);
}

/**
 * Remember a game the app just changed. Pass the revision captured before the
 * request when there is one: a save answered after the account changed is
 * returned to its caller but not cached.
 */
export function rememberGameMutation(game: Game, started?: number): Game {
  if (started !== undefined && !isCurrentAccountRequest(started)) return game;
  gameMutationVersions.set(game.id, ++mutationVersion);
  return rememberGame(game);
}

export function rememberFetchedGame(game: Game, started: number): Game {
  if (!isCurrentAccountRequest(started)) return game;
  const current = gameCache.peek(game.id);
  return current && (gameMutationVersions.get(game.id) ?? 0) > started
    ? current
    : rememberGame(game);
}

export function invalidateGameLibrary(): void {
  mutationVersion++;
}

export function forgetGame(id: string): void {
  gameCache.remove(id);
  gameMutationVersions.set(id, ++mutationVersion);
}

/** Mark the list complete, unless the account changed since `started`. */
export function markGameListLoaded(started: number): void {
  if (isCurrentAccountRequest(started)) gameCache.markListLoaded();
}
