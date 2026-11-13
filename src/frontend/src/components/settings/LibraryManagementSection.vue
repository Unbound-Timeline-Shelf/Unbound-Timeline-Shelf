<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import {
  fetchGames,
  refreshGameAchievements,
  refreshGameMetadata,
  previewGameMetadataRefresh,
  fetchGameTrash,
  restoreGame,
} from "../../services/games";
import type {
  RefreshMetadataOptions,
  RefreshMetadataOutcome,
  RefreshMetadataResult,
  RefreshMetadataPreview,
  TrashedGame,
} from "../../services/games";
import type { Game } from "../../types/game";
import ToggleButton from "./ToggleButton.vue";
import {
  startTask,
  updateTask,
  completeTask,
  errorTask,
  addFeedItem,
} from "../../state/taskProgress";

// how many games are refreshed in parallel at once, high enough to be a
// real speedup, low enough not to hammer external metadata APIs or exceed
// the browser's ~6 connections-per-origin cap (each in-flight game can hold
// a search + patch + 2 asset-check requests briefly, so this stays well
// under 6 even with other page activity sharing the same origin)
const REFRESH_CONCURRENCY = 4;

async function runInBatches<T, R>(
  items: T[],
  concurrency: number,
  worker: (item: T) => Promise<R>,
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let cursor = 0;
  async function runNext(): Promise<void> {
    const index = cursor++;
    if (index >= items.length) return;
    results[index] = await worker(items[index]);
    await runNext();
  }
  await Promise.all(
    Array.from({ length: Math.min(concurrency, items.length) }, runNext),
  );
  return results;
}

interface RefreshLogEntry {
  title: string;
  result: RefreshMetadataResult;
}

const refreshing = ref(false);
const refreshProgress = ref({ done: 0, total: 0 });
const refreshResults = ref<RefreshLogEntry[]>([]);
const imagesAddedCount = ref(0);
const refreshError = ref<string | null>(null);

const refreshUpdateText = ref(true);
const refreshFillMissingArt = ref(true);
const refreshOverwriteArt = ref(false);
const lastRunOverwrote = ref(false);

const updatedCount = computed(
  () => refreshResults.value.filter((r) => r.result === "updated").length,
);
const noMatchCount = computed(
  () => refreshResults.value.filter((r) => r.result === "no-match").length,
);
const errorCount = computed(
  () => refreshResults.value.filter((r) => r.result === "error").length,
);
const unresolvedResults = computed(() =>
  refreshResults.value.filter((r) => r.result !== "updated"),
);

function refreshResultLabel(result: RefreshMetadataResult): string {
  switch (result) {
    case "no-match":
      return "no match";
    case "conflict":
      return "conflicting provider ids, nothing changed";
    case "rejected":
      return "a rejected match, nothing changed";
    case "needs-confirmation":
      return "the title-only match changed since the preview, not applied";
    default:
      return "failed";
  }
}

function currentOptions(): RefreshMetadataOptions {
  return {
    updateText: refreshUpdateText.value,
    fillMissingArt: refreshFillMissingArt.value,
    overwriteExistingArt: refreshOverwriteArt.value,
  };
}

function describeOptions(options: RefreshMetadataOptions): string[] {
  const parts: string[] = [];
  if (options.updateText) {
    parts.push(
      "description, developer, publisher, release date, age rating, and tags/features",
    );
  }
  if (options.overwriteExistingArt) {
    parts.push("cover/banner art: INCLUDING replacing art you already have");
  } else if (options.fillMissingArt) {
    parts.push("cover/banner art, only for games that currently have none");
  }
  return parts;
}

// --- Dry run: shows exactly which games would actually change before any
// real write happens, the old flow committed on a single generic
// window.confirm with no visibility into whether a refresh would even do
// anything different from what's already there. ------------------------
interface PreviewEntry {
  game: Game;
  preview: RefreshMetadataPreview;
}
const previewing = ref(false);
const previewResults = ref<PreviewEntry[]>([]);
const showPreviewDialog = ref(false);
const previewError = ref<string | null>(null);
// title-only matches the user ticked in the preview; none is applied unticked
const confirmedTitleMatches = ref<Set<string>>(new Set());
// id matches whose withheld (unverified) source the user ticked to accept
const confirmedIdMatches = ref<Set<string>>(new Set());

// a dry run answers "preview" for a game it matched
function previewMatched(preview: RefreshMetadataPreview): boolean {
  return preview.status === "preview" || preview.status === "updated";
}

function isTitleOnly(entry: PreviewEntry): boolean {
  return entry.preview.match === "title";
}

// an id match that withheld another source's fields/id pending confirmation (#492 review):
// the verified source alone may report no changes, so this must not be folded into
// "already up to date" the way a title-only match with no changes would be.
function hasUnverifiedSource(entry: PreviewEntry): boolean {
  return (
    entry.preview.match === "id" &&
    Object.keys(entry.preview.unverifiedSources ?? {}).length > 0
  );
}

// either kind of match that is only applied once the person confirms this exact candidate
function needsCandidateConfirmation(entry: PreviewEntry): boolean {
  return isTitleOnly(entry) || hasUnverifiedSource(entry);
}

function isConfirmed(entry: PreviewEntry): boolean {
  return isTitleOnly(entry)
    ? confirmedTitleMatches.value.has(entry.game.id)
    : confirmedIdMatches.value.has(entry.game.id);
}

const previewWithChanges = computed(() =>
  previewResults.value.filter(
    (e) =>
      previewMatched(e.preview) &&
      (e.preview.changedFields.length ||
        e.preview.wouldAddKeyArt ||
        e.preview.wouldAddBanner ||
        hasUnverifiedSource(e)),
  ),
);

function toggleTitleMatch(entry: PreviewEntry, confirmed: boolean) {
  const next = new Set(confirmedTitleMatches.value);
  if (confirmed) next.add(entry.game.id);
  else next.delete(entry.game.id);
  confirmedTitleMatches.value = next;
}

function toggleIdMatch(entry: PreviewEntry, confirmed: boolean) {
  const next = new Set(confirmedIdMatches.value);
  if (confirmed) next.add(entry.game.id);
  else next.delete(entry.game.id);
  confirmedIdMatches.value = next;
}

function candidateIds(preview: RefreshMetadataPreview): string {
  return Object.entries(preview.candidate?.providerIds ?? {})
    .map(([namespace, identity]) => `${namespace}:${identity}`)
    .join(", ");
}

function unverifiedSourceNames(preview: RefreshMetadataPreview): string {
  return Object.keys(preview.unverifiedSources ?? {}).join(", ");
}

// what "Apply" sends: id matches, plus any title-only or unverified-source match the user ticked
const previewToApply = computed(() =>
  previewWithChanges.value.filter(
    (e) => !needsCandidateConfirmation(e) || isConfirmed(e),
  ),
);
const previewTitleOnly = computed(
  () => previewWithChanges.value.filter(isTitleOnly).length,
);
const previewUnverifiedId = computed(
  () => previewWithChanges.value.filter(hasUnverifiedSource).length,
);
const previewUnchanged = computed(
  () =>
    previewResults.value.filter(
      (e) => previewMatched(e.preview) && !previewWithChanges.value.includes(e),
    ).length,
);
const previewNoMatch = computed(
  () =>
    previewResults.value.filter((e) => e.preview.status === "no-match").length,
);
const previewErrors = computed(
  () => previewResults.value.filter((e) => e.preview.status === "error").length,
);
// conflicting provider ids, or a candidate the user rejected: never applied
const previewRefused = computed(
  () =>
    previewResults.value.filter(
      (e) => e.preview.status === "conflict" || e.preview.status === "rejected",
    ).length,
);

function previewChangeSummary(preview: RefreshMetadataPreview): string {
  const parts = [...preview.changedFields];
  if (preview.wouldAddKeyArt) parts.push("cover art");
  if (preview.wouldAddBanner) parts.push("banner art");
  return parts.join(", ");
}

async function previewRefresh() {
  const options = currentOptions();
  previewing.value = true;
  previewError.value = null;
  if (!describeOptions(options).length) {
    previewError.value =
      "Nothing is selected to refresh: check at least one option below.";
    previewing.value = false;
    return;
  }
  previewResults.value = [];
  confirmedTitleMatches.value = new Set();
  confirmedIdMatches.value = new Set();
  let taskId: string | null = null;
  try {
    const games = await fetchGames();
    taskId = startTask("Checking what would change", games.length, {
      indeterminate: false,
    });
    let done = 0;
    previewResults.value = await runInBatches(
      games,
      REFRESH_CONCURRENCY,
      async (game: Game) => {
        const preview = await previewGameMetadataRefresh(game, options);
        done++;
        if (taskId) updateTask(taskId, done);
        return { game, preview };
      },
    );
    if (taskId)
      completeTask(taskId, `${previewWithChanges.value.length} would change`);
    showPreviewDialog.value = true;
  } catch (err) {
    previewError.value =
      err instanceof Error ? err.message : "Failed to check for changes";
    if (taskId) errorTask(taskId, previewError.value);
  } finally {
    previewing.value = false;
  }
}

async function applyPreviewedRefresh() {
  showPreviewDialog.value = false;
  const options = currentOptions();
  // only the games the preview actually flagged as changing, the button
  // itself is labeled "Apply to N games" using this same count, so applying
  // to every previewed game (including no-match/already-up-to-date/error
  // ones) would silently do more than what was shown and confirmed. A
  // title-only match is included only when the user ticked it.
  const entries = previewToApply.value;

  refreshing.value = true;
  refreshResults.value = [];
  imagesAddedCount.value = 0;
  lastRunOverwrote.value = options.overwriteExistingArt;
  refreshError.value = null;

  let taskId: string | null = null;
  try {
    refreshProgress.value = { done: 0, total: entries.length };
    taskId = startTask("Refreshing metadata", entries.length);
    let doneCount = 0;

    await runInBatches(entries, REFRESH_CONCURRENCY, async (entry) => {
      const game = entry.game;
      // a ticked title-only or unverified-source match is confirmed by the exact
      // candidate shown; the server refuses it if the lookup now finds a different one
      const outcome: RefreshMetadataOutcome = await refreshGameMetadata(
        game,
        options,
        needsCandidateConfirmation(entry)
          ? (entry.preview.candidate?.providerIds ?? {})
          : null,
      );
      refreshResults.value.push({ title: game.title, result: outcome.status });
      if (outcome.keyArtAdded) imagesAddedCount.value++;
      if (outcome.bannerAdded) imagesAddedCount.value++;
      doneCount++;
      refreshProgress.value.done = doneCount;
      if (taskId) {
        updateTask(taskId, doneCount);
        addFeedItem(
          taskId,
          outcome.status === "updated"
            ? `${game.title} updated`
            : `${game.title}: ${refreshResultLabel(outcome.status)}`,
        );
      }
    });

    if (taskId)
      completeTask(
        taskId,
        `${updatedCount.value} updated, ${errorCount.value} failed`,
      );
  } catch (err) {
    refreshError.value =
      err instanceof Error ? err.message : "Failed to refresh metadata";
    if (taskId) errorTask(taskId, refreshError.value);
  } finally {
    refreshing.value = false;
  }
}

// --- Achievements: re-reads every game's achievements from the service it
// came from (Steam, PlayStation, RetroAchievements), one light call per game,
// with no library sync. Only games that already have achievements are
// refreshed. ---------------------------------------------------------------
const refreshingAchievements = ref(false);
const achievementProgress = ref({ done: 0, total: 0 });
const achievementSummary = ref<{
  games: number;
  achievements: number;
  unlocked: number;
  hidden: number;
  failed: number;
  reason: string | null;
  warnings: string[];
} | null>(null);
const achievementError = ref<string | null>(null);

async function refreshAllAchievements() {
  refreshingAchievements.value = true;
  achievementSummary.value = null;
  achievementError.value = null;
  let taskId: string | null = null;
  try {
    // This action imports new catalogues too, so games with no achievement
    // rows yet must be checked rather than filtered out of the batch.
    const targets = await fetchGames();
    achievementProgress.value = { done: 0, total: targets.length };
    taskId = startTask("Importing and refreshing achievements", targets.length);
    const totals = { achievements: 0, unlocked: 0, hidden: 0, failed: 0 };
    const warnings: string[] = [];
    let reason: string | null = null;
    let done = 0;
    await runInBatches(targets, REFRESH_CONCURRENCY, async (game: Game) => {
      try {
        const result = await refreshGameAchievements(game.id);
        totals.achievements += result.achievements;
        totals.unlocked += result.unlocked;
        totals.hidden += result.hidden;
        for (const warning of result.warnings ?? []) {
          warnings.push(`${game.title}: ${warning}`);
        }
      } catch (err) {
        totals.failed++;
        // keep the first reason so a failure says why, instead of just a count
        reason ??= err instanceof Error ? err.message : "Request failed";
      }
      done++;
      achievementProgress.value.done = done;
      if (taskId) updateTask(taskId, done);
    });
    achievementSummary.value = {
      games: targets.length - totals.failed,
      ...totals,
      reason,
      warnings,
    };
    if (taskId)
      completeTask(
        taskId,
        `${targets.length - totals.failed} checked, ${totals.failed} failed`,
      );
  } catch (err) {
    achievementError.value =
      err instanceof Error ? err.message : "Failed to refresh achievements";
    if (taskId) errorTask(taskId, achievementError.value);
  } finally {
    refreshingAchievements.value = false;
  }
}

// duplicate folder_location scan, entirely client-side against the already
// fetched game list, no new backend endpoint needed
const scanningDuplicates = ref(false);
const duplicateGroups = ref<{ folderLocation: string; titles: string[] }[]>([]);
const duplicateScanRan = ref(false);
const duplicateScanError = ref<string | null>(null);

async function scanForDuplicateFolders() {
  scanningDuplicates.value = true;
  duplicateScanRan.value = false;
  duplicateScanError.value = null;
  try {
    const games = await fetchGames();
    const byFolder = new Map<string, string[]>();
    for (const game of games) {
      if (!game.folderLocation) continue;
      const list = byFolder.get(game.folderLocation) ?? [];
      list.push(game.title);
      byFolder.set(game.folderLocation, list);
    }
    duplicateGroups.value = Array.from(byFolder.entries())
      .filter(([, titles]) => titles.length > 1)
      .map(([folderLocation, titles]) => ({ folderLocation, titles }));
    duplicateScanRan.value = true;
  } catch (err) {
    duplicateScanError.value =
      err instanceof Error ? err.message : "Failed to scan for duplicates";
  } finally {
    scanningDuplicates.value = false;
  }
}

// --- Deleted games: soft-deleted, recoverable for 7 days before the
// background sweep purges them for good (features/trash/sweep.py) --------
const gameTrash = ref<TrashedGame[]>([]);
const loadingGameTrash = ref(false);
const gameTrashError = ref<string | null>(null);
const restoringGameId = ref<string | null>(null);

async function refreshGameTrash() {
  loadingGameTrash.value = true;
  gameTrashError.value = null;
  try {
    gameTrash.value = await fetchGameTrash();
  } catch (err) {
    gameTrashError.value =
      err instanceof Error ? err.message : "Failed to load deleted games";
  } finally {
    loadingGameTrash.value = false;
  }
}
onMounted(refreshGameTrash);

function daysUntil(unixSeconds: number): number {
  return Math.max(0, Math.ceil((unixSeconds - Date.now() / 1000) / 86400));
}

async function restoreGameById(game: TrashedGame) {
  restoringGameId.value = game.id;
  gameTrashError.value = null;
  try {
    await restoreGame(game.id);
    await refreshGameTrash();
  } catch (err) {
    gameTrashError.value =
      err instanceof Error ? err.message : "Failed to restore game";
  } finally {
    restoringGameId.value = null;
  }
}
</script>

<template>
  <section class="settings-section">
    <h2>Refresh Metadata</h2>
    <p class="section-hint">
      Re-fetches from Steam / SteamGridDB for every game: only applied when a
      search result's title matches exactly, so nothing gets guessed. Notes and
      achievements are never touched by this, no matter which options below are
      on.
    </p>

    <div class="refresh-options">
      <ToggleButton v-model="refreshUpdateText" label="Update text fields">
        Update text fields (description, developer, publisher, release date, age
        rating, tags/features): always a full refresh, replacing whatever's
        already there
      </ToggleButton>
      <ToggleButton
        v-model="refreshFillMissingArt"
        label="Fill missing art"
        :disabled="refreshOverwriteArt"
      >
        Fill in cover/banner art for games that currently have none
      </ToggleButton>
      <ToggleButton v-model="refreshOverwriteArt" label="Overwrite art">
        <span class="warning-text"
          >Also replace art on games that already have it (overwrites anything
          you've set)</span
        >
      </ToggleButton>
    </div>

    <button
      type="button"
      class="secondary-button"
      :disabled="refreshing || previewing"
      @click="previewRefresh"
    >
      {{
        previewing
          ? "Checking what would change…"
          : refreshing
            ? `Refreshing… (${refreshProgress.done}/${refreshProgress.total})`
            : "Preview Refresh"
      }}
    </button>
    <p class="section-hint" style="margin: 8px 0 0">
      Shows exactly which games would actually change before anything is
      written: nothing is applied until you confirm.
    </p>

    <div v-if="previewError" class="form-error">{{ previewError }}</div>
    <div v-if="refreshError" class="form-error">{{ refreshError }}</div>

    <div v-if="!refreshing && refreshResults.length" class="refresh-summary">
      <p>
        {{ updatedCount }} updated, {{ noMatchCount }} skipped (no match),
        {{ errorCount }} failed.
        <template v-if="imagesAddedCount">
          {{ imagesAddedCount }} image{{ imagesAddedCount === 1 ? "" : "s" }}
          {{
            lastRunOverwrote
              ? "set (including replacing existing art)."
              : "added for previously blank games."
          }}
        </template>
      </p>
      <ul v-if="unresolvedResults.length" class="refresh-list">
        <li v-for="entry in unresolvedResults" :key="entry.title">
          {{ entry.title }}:
          {{ refreshResultLabel(entry.result) }}
        </li>
      </ul>
    </div>
  </section>

  <section class="settings-section">
    <h2>Import and Refresh Achievements</h2>
    <p class="section-hint">
      Checks every game for achievement definitions from metadata providers such
      as Atlas, then refreshes unlocked progress from its platform source
      (Steam, PlayStation or RetroAchievements). Catalogue definitions can be
      matched to platform progress through linked IDs. This doesn't sync your
      library, and your notes and pins are kept.
    </p>
    <button
      type="button"
      class="secondary-button"
      :disabled="refreshingAchievements"
      @click="refreshAllAchievements"
    >
      {{
        refreshingAchievements
          ? `Checking… (${achievementProgress.done}/${achievementProgress.total})`
          : "Import / refresh achievements"
      }}
    </button>
    <div v-if="achievementError" class="form-error">{{ achievementError }}</div>
    <div
      v-if="!refreshingAchievements && achievementSummary"
      class="refresh-summary"
    >
      <p>
        {{ achievementSummary.games }} game{{
          achievementSummary.games === 1 ? "" : "s"
        }}
        checked:
        {{ achievementSummary.achievements.toLocaleString() }} achievements,
        {{ achievementSummary.unlocked.toLocaleString() }} unlocked,
        {{ achievementSummary.hidden.toLocaleString() }} hidden<template
          v-if="achievementSummary.failed"
        >
          . {{ achievementSummary.failed }} failed<template
            v-if="achievementSummary.reason"
          >
            ({{ achievementSummary.reason }})</template
          ></template
        >.
      </p>
      <ul v-if="achievementSummary.warnings.length" class="form-error">
        <li v-for="warning in achievementSummary.warnings" :key="warning">
          {{ warning }}
        </li>
      </ul>
    </div>
  </section>

  <div
    v-if="showPreviewDialog"
    class="confirm-backdrop"
    @click.self="showPreviewDialog = false"
  >
    <div class="confirm-dialog preview-dialog">
      <h3>Refresh preview</h3>
      <p>
        <strong>{{ previewWithChanges.length }}</strong> game{{
          previewWithChanges.length === 1 ? "" : "s"
        }}
        would actually change, {{ previewUnchanged }} matched but are already up
        to date, {{ previewNoMatch }} have no match by provider id or exact
        title, {{ previewRefused }} have conflicting or rejected provider ids,
        {{ previewErrors }} failed to check.
      </p>
      <p v-if="previewTitleOnly" class="section-hint">
        {{ previewTitleOnly }} matched by title only, not by a provider id the
        game already has. Tick each one that is the right game; unticked ones
        are left alone.
      </p>
      <p v-if="previewUnverifiedId" class="section-hint">
        {{ previewUnverifiedId }} matched by a provider id the game already has,
        but another source could only confirm it by title and its fields/id are
        withheld until you confirm it is the same game. Tick each one that is
        correct; unticked ones apply only the verified source's data.
      </p>
      <ul v-if="previewWithChanges.length" class="preview-change-list">
        <li
          v-for="entry in previewWithChanges"
          :key="entry.game.id"
          :class="{
            'preview-title-only': isTitleOnly(entry),
            'preview-unverified-id': hasUnverifiedSource(entry),
          }"
        >
          <label
            v-if="needsCandidateConfirmation(entry)"
            class="preview-title-confirm"
          >
            <input
              type="checkbox"
              :checked="isConfirmed(entry)"
              :aria-label="`Apply the ${isTitleOnly(entry) ? 'title-only' : 'unverified-source'} match for ${entry.game.title}`"
              @change="
                isTitleOnly(entry)
                  ? toggleTitleMatch(
                      entry,
                      ($event.target as HTMLInputElement).checked,
                    )
                  : toggleIdMatch(
                      entry,
                      ($event.target as HTMLInputElement).checked,
                    )
              "
            />
            <span class="preview-change-title">{{ entry.game.title }}</span>
            <span class="preview-title-badge">{{
              isTitleOnly(entry) ? "title only" : "unverified source"
            }}</span>
          </label>
          <span v-else class="preview-change-title">{{
            entry.game.title
          }}</span>
          <span class="preview-change-fields">
            {{ previewChangeSummary(entry.preview) }}
            <template v-if="isTitleOnly(entry) && candidateIds(entry.preview)">
              <br />would link {{ candidateIds(entry.preview) }}
            </template>
            <template v-if="hasUnverifiedSource(entry)">
              <br />{{ unverifiedSourceNames(entry.preview) }} withheld its data
              pending confirmation
            </template>
          </span>
        </li>
      </ul>
      <p v-else class="section-hint" style="margin-top: 10px">
        Nothing would actually change: every matched game already has this data.
      </p>
      <div class="confirm-actions">
        <button
          type="button"
          class="secondary-button"
          @click="showPreviewDialog = false"
        >
          Cancel
        </button>
        <button
          type="button"
          class="primary-button"
          :disabled="!previewToApply.length"
          @click="applyPreviewedRefresh"
        >
          Apply to {{ previewToApply.length }} game{{
            previewToApply.length === 1 ? "" : "s"
          }}
        </button>
      </div>
    </div>
  </div>

  <div class="settings-divider"></div>

  <section class="settings-section">
    <h2>Duplicate Folders</h2>
    <p class="section-hint">
      Checks whether more than one game is pointing at the same storage folder:
      a sign something got mis-imported or renamed incorrectly.
    </p>

    <button
      type="button"
      class="secondary-button"
      :disabled="scanningDuplicates"
      @click="scanForDuplicateFolders"
    >
      {{ scanningDuplicates ? "Scanning…" : "Scan for Duplicates" }}
    </button>

    <div v-if="duplicateScanError" class="form-error">
      {{ duplicateScanError }}
    </div>
    <div v-if="duplicateScanRan" class="refresh-summary">
      <p v-if="!duplicateGroups.length">No duplicate folders found.</p>
      <ul v-else class="refresh-list">
        <li v-for="group in duplicateGroups" :key="group.folderLocation">
          <strong>{{ group.folderLocation }}</strong
          >: {{ group.titles.join(", ") }}
        </li>
      </ul>
    </div>
  </section>

  <div class="settings-divider"></div>

  <section class="settings-section">
    <h2>Deleted Games</h2>
    <p class="section-hint">
      A deleted game moves here first: recoverable for 7 days before it's purged
      for good, along with its screenshots, clips, saves, notes, and everything
      else.
    </p>

    <p v-if="loadingGameTrash">Loading…</p>
    <div v-if="gameTrashError" class="form-error">{{ gameTrashError }}</div>
    <p v-if="!loadingGameTrash && !gameTrash.length" class="empty-hint">
      Nothing in trash.
    </p>
    <ul v-else class="trash-list">
      <li v-for="game in gameTrash" :key="game.id" class="trash-row">
        <span class="trash-name">{{ game.title }}</span>
        <span class="trash-meta"
          >purges in {{ daysUntil(game.purge_at) }}d</span
        >
        <button
          type="button"
          class="secondary-button"
          :disabled="restoringGameId === game.id"
          @click="restoreGameById(game)"
        >
          {{ restoringGameId === game.id ? "Restoring…" : "Restore" }}
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.settings-section h2 {
  margin: 0 0 12px;
  font: var(--ui-weight-heading) var(--ui-font-heading)/1.4
    var(--ui-font-family);
  color: var(--ui-text);
}
.section-hint {
  color: var(--ui-dim);
  font-size: 0.82rem;
  line-height: 1.6;
  margin: 0 0 16px;
}
.settings-divider {
  height: 1px;
  background: var(--ui-border);
  margin: 24px 0;
}
.refresh-options {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 16px;
}
.warning-text {
  color: #f0b458;
}
.secondary-button {
  background: color-mix(in srgb, var(--ui-text) 8%, transparent);
  color: var(--ui-text);
  border: none;
  border-radius: var(--ui-radius-control);
  padding: 10px 18px;
  font-weight: 600;
  cursor: pointer;
}
.secondary-button:hover:not(:disabled) {
  background: color-mix(in srgb, var(--ui-text) 14%, transparent);
}
.secondary-button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.form-error {
  color: var(--ui-error);
  font-size: 13px;
  background: rgba(220, 38, 38, 0.1);
  border: 1px solid rgba(220, 38, 38, 0.3);
  border-radius: var(--ui-radius-control);
  padding: 8px 10px;
  margin-top: 10px;
}
.refresh-summary {
  margin-top: 14px;
  font-size: 0.82rem;
  color: var(--ui-text);
}
.refresh-summary p {
  margin: 0 0 8px;
}
.refresh-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 160px;
  overflow-y: auto;
  color: var(--ui-dim);
  font-size: 0.78rem;
}

.confirm-backdrop {
  position: fixed;
  inset: 0;
  background: color-mix(in srgb, var(--ui-bg) 65%, transparent);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 60;
}
.confirm-dialog {
  background: var(--ui-surface);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-card);
  padding: 22px;
  width: 100%;
  max-width: 360px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.6);
}
.confirm-dialog h3 {
  margin: 0 0 8px;
  color: var(--ui-text);
}
.confirm-dialog p {
  margin: 0 0 16px;
  color: var(--ui-dim);
  font-size: 13.5px;
}
.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
.preview-dialog {
  max-width: 480px;
}
.preview-change-list {
  list-style: none;
  margin: 0 0 16px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 260px;
  overflow-y: auto;
}
.preview-change-list li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  background: color-mix(in srgb, var(--ui-text) 3%, transparent);
  border: 1px solid var(--ui-border-soft);
  border-radius: var(--ui-radius-control);
  padding: 8px 10px;
  font-size: 12.5px;
}
.preview-title-confirm {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.preview-title-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: var(--ui-radius-control);
  border: 1px solid var(--ui-border-soft);
  color: var(--ui-muted);
  white-space: nowrap;
}
.preview-change-title {
  color: var(--ui-text);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.preview-change-fields {
  color: var(--ui-accent-text);
  text-align: right;
  flex-shrink: 0;
}
.primary-button {
  background: var(--ui-accent);
  color: var(--ui-on-accent);
  border: none;
  border-radius: var(--ui-radius-control);
  padding: 9px 16px;
  font-weight: 600;
  cursor: pointer;
}
.primary-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.empty-hint {
  color: var(--ui-faint);
  font-size: 0.82rem;
}
.trash-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.trash-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  background: color-mix(in srgb, var(--ui-text) 3%, transparent);
  border: 1px solid var(--ui-border-soft);
  border-radius: var(--ui-radius-control);
  font-size: 0.82rem;
}
.trash-name {
  flex: 1;
  color: var(--ui-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.trash-meta {
  color: var(--ui-faint);
  font-size: 0.76rem;
}
</style>
