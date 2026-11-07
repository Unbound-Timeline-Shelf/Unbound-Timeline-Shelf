// Changes applied to many selected games at once: the same field values
// (bulk edit) or a move to trash (#462), each game exactly as deleteGame
// sends it, restorable for 7 days from Settings.
import { mockGames } from "../data/mockGames";
import { failedRequest } from "./apiError";
import { forgetGame, invalidateGameLibrary } from "./gameCache";
import { denormalizeStatus, playCompatibilityPayload } from "./games";
import type { GamePlayCompatibility, GameStatus } from "../types/game";

export interface BulkDeleteOutcome {
  deleted: string[];
  // not one of this account's games (or already gone for good)
  notFound: string[];
  alreadyDeleted: string[];
  failed: { id: string; detail: string }[];
}

// The server moves at most this many games per request (GAME_BULK_DELETE_LIMIT).
const BULK_DELETE_BATCH = 100;

/**
 * Move several games to trash, each exactly as deleteGame does (#462). Larger
 * selections go in several requests; a request that fails outright reports
 * every game in it as failed and the rest carry on, so the outcome always
 * matches what the server actually did.
 */
export async function bulkDeleteGames(
  gameIds: readonly string[],
): Promise<BulkDeleteOutcome> {
  const ids = [...new Set(gameIds)];
  const outcome: BulkDeleteOutcome = {
    deleted: [],
    notFound: [],
    alreadyDeleted: [],
    failed: [],
  };
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    for (const id of ids) {
      const index = mockGames.findIndex((g) => g.id === id);
      if (index === -1) outcome.notFound.push(id);
      else {
        mockGames.splice(index, 1);
        outcome.deleted.push(id);
      }
    }
    return outcome;
  }
  for (let i = 0; i < ids.length; i += BULK_DELETE_BATCH) {
    const batch = ids.slice(i, i + BULK_DELETE_BATCH);
    try {
      const response = await fetch("/api/game/bulk-delete", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ game_ids: batch }),
      });
      if (!response.ok) throw await failedRequest(response);
      const result: {
        deleted: string[];
        not_found: string[];
        already_deleted: string[];
        failed: { id: string; detail: string }[];
      } = await response.json();
      outcome.deleted.push(...result.deleted);
      outcome.notFound.push(...result.not_found);
      outcome.alreadyDeleted.push(...result.already_deleted);
      outcome.failed.push(...result.failed);
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Request failed";
      outcome.failed.push(...batch.map((id) => ({ id, detail })));
    }
  }
  // none of these is an active game of this account any more
  for (const id of [
    ...outcome.deleted,
    ...outcome.notFound,
    ...outcome.alreadyDeleted,
  ])
    forgetGame(id);
  invalidateGameLibrary();
  return outcome;
}

export interface BulkEditFields {
  playCompatibility?: GamePlayCompatibility | null;
  status?: GameStatus;
  favorite?: boolean;
  developer?: string | null;
  publisher?: string | null;
  series?: string | null;
  ageRating?: string | null;
  platform?: string | null;
  priority?: string | null;
  tags?: string[];
  features?: string[];
}

export async function bulkUpdateGames(
  gameIds: string[],
  fields: BulkEditFields,
): Promise<number> {
  const payload: Record<string, unknown> = { game_ids: gameIds };
  if (fields.playCompatibility !== undefined)
    payload.play_compatibility = playCompatibilityPayload(
      fields.playCompatibility,
    );
  if (fields.status !== undefined)
    payload.status = denormalizeStatus(fields.status);
  if (fields.favorite !== undefined) payload.favorite = fields.favorite;
  if (fields.developer !== undefined) payload.developer = fields.developer;
  if (fields.publisher !== undefined) payload.publisher = fields.publisher;
  if (fields.series !== undefined) payload.series = fields.series;
  if (fields.ageRating !== undefined) payload.age_rating = fields.ageRating;
  if (fields.platform !== undefined) payload.platform = fields.platform;
  if (fields.priority !== undefined) payload.priority = fields.priority;
  if (fields.tags !== undefined) payload.tags = fields.tags;
  if (fields.features !== undefined) payload.features = fields.features;

  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    let count = 0;
    for (const game of mockGames) {
      if (!gameIds.includes(game.id)) continue;
      if (fields.playCompatibility !== undefined)
        game.playCompatibility = fields.playCompatibility;
      if (fields.status !== undefined) game.status = fields.status;
      if (fields.favorite !== undefined) game.favorite = fields.favorite;
      if (fields.developer !== undefined) game.developer = fields.developer;
      if (fields.publisher !== undefined) game.publisher = fields.publisher;
      if (fields.series !== undefined) game.series = fields.series;
      if (fields.ageRating !== undefined) game.ageRating = fields.ageRating;
      if (fields.tags !== undefined) game.tags = fields.tags;
      if (fields.features !== undefined) game.features = fields.features;
      count++;
    }
    return count;
  }

  const response = await fetch("/api/game/bulk-update", {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await failedRequest(response);
  const result: { updated: number } = await response.json();
  invalidateGameLibrary();
  return result.updated;
}
