// Actions the Games toolbar applies to the current selection: adding the
// selected games to a collection and moving them to trash (#462).
import { ref, type Ref } from "vue";
import { addGameToCollection } from "../services/games";
import { bulkDeleteGames } from "../services/gameBulkActions";
import { usePrompt } from "../state/dialog";
import type { Game } from "../types/game";

export function useGameSelectionActions(options: {
  games: Ref<Game[]>;
  selectedIds: Ref<Set<string>>;
  selectMode: Ref<boolean>;
  selectedGame: Ref<Game | null>;
  bulkEditResultCount: Ref<number | null>;
  loadGames: () => Promise<unknown>;
}) {
  const {
    games,
    selectedIds,
    selectMode,
    selectedGame,
    bulkEditResultCount,
    loadGames,
  } = options;
  const prompt = usePrompt();

  // bulk-add selected games to a collection, a keyboard/click alternative to
  // dragging cards onto a collection, which this codebase has no drag-and-drop
  // library to build (see the arrow-based reorder in CollectionDetail.vue for
  // the same tradeoff elsewhere)
  const bulkAddingToCollection = ref(false);
  async function bulkAddToCollection() {
    if (!selectedIds.value.size) return;
    const name = await prompt({
      title: "Add to collection",
      message: `Add ${selectedIds.value.size} selected game(s) to which collection?`,
      confirmLabel: "Add",
    });
    if (!name || !name.trim()) return;
    const trimmed = name.trim();
    bulkAddingToCollection.value = true;
    try {
      await Promise.all(
        [...selectedIds.value].map((id) => addGameToCollection(id, trimmed)),
      );
      bulkEditResultCount.value = selectedIds.value.size;
      selectMode.value = false;
      selectedIds.value = new Set();
      await loadGames();
    } finally {
      bulkAddingToCollection.value = false;
    }
  }

  // bulk removal (#462): every selected game goes to trash exactly as a single
  // delete sends it, restorable for 7 days from Settings
  const confirmingBulkDelete = ref(false);
  const bulkDeleting = ref(false);
  const bulkDeleteMessage = ref<string | null>(null);
  const bulkDeleteFailures = ref<
    { id: string; title: string; detail: string }[]
  >([]);
  function openBulkDelete() {
    if (!selectedIds.value.size) return;
    bulkDeleteMessage.value = null;
    bulkDeleteFailures.value = [];
    confirmingBulkDelete.value = true;
  }
  async function confirmBulkDelete() {
    if (bulkDeleting.value || !selectedIds.value.size) return;
    bulkDeleting.value = true;
    const ids = [...selectedIds.value];
    const titles = new Map(games.value.map((game) => [game.id, game.title]));
    try {
      const outcome = await bulkDeleteGames(ids);
      const removed = outcome.deleted.length + outcome.alreadyDeleted.length;
      const gone = outcome.notFound.length;
      bulkDeleteFailures.value = outcome.failed.map((failure) => ({
        id: failure.id,
        title: titles.get(failure.id) ?? failure.id,
        detail: failure.detail,
      }));
      bulkDeleteMessage.value = [
        `Moved ${removed} game${removed === 1 ? "" : "s"} to trash`,
        gone && `${gone} no longer in your library`,
        outcome.failed.length &&
          `${outcome.failed.length} could not be removed and stay selected`,
      ]
        .filter(Boolean)
        .join("; ");
      // only what failed stays selected, so trying again is one click
      selectedIds.value = new Set(outcome.failed.map((failure) => failure.id));
      const preview = selectedGame.value?.id;
      if (preview && ids.includes(preview) && !selectedIds.value.has(preview))
        selectedGame.value = null;
      confirmingBulkDelete.value = false;
    } finally {
      bulkDeleting.value = false;
      // always reload: the library must show what the server now holds
      await loadGames();
    }
  }

  return {
    bulkAddingToCollection,
    bulkAddToCollection,
    confirmingBulkDelete,
    bulkDeleting,
    bulkDeleteMessage,
    bulkDeleteFailures,
    openBulkDelete,
    confirmBulkDelete,
  };
}
