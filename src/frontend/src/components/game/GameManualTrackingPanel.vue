<script setup lang="ts">
import { formatInstant } from "../../utils/dates";
import { computed, ref, watch } from "vue";
import { copyName } from "../../utils/gameCopies";
import {
  createManualAchievement,
  fetchAchievementTotals,
  fetchCompletion,
  releaseCompletion,
  removeManualAchievement,
  setCompletion,
  setManualAchievementEarned,
  updateManualAchievement,
  type AchievementTotals,
  type CompletionTotals,
  type ManualAchievement,
} from "../../services/gameTracking";
import { useConfirm } from "../../state/dialog";
import {
  localInputToUnixSeconds,
  toLocalInput,
} from "../../utils/achievements";

const props = defineProps<{
  gameId: string;
  part: "completion" | "achievements";
}>();
const emit = defineEmits<{ changed: [] }>();
const confirm = useConfirm();

const STATUSES = [
  "BACKLOG",
  "PLAYING",
  "ON_HOLD",
  "PLAYED",
  "BEATEN",
  "MASTERED",
  "DROPPED",
  "WISHLIST",
];
const label = (status: string) =>
  status.charAt(0) + status.slice(1).toLowerCase().replace(/_/g, " ");

const completion = ref<CompletionTotals | null>(null);
// The per-copy statuses are the advanced part of Completion: shown when the copies
// disagree (something to look at), otherwise one click away.
const completionOpen = ref<boolean | null>(null);
const completionExpanded = computed(
  () =>
    completionOpen.value ??
    new Set(completion.value?.versions.map((v) => v.status)).size > 1,
);
const totals = ref<AchievementTotals | null>(null);
const error = ref<string | null>(null);
const busy = ref(false);
const editingId = ref<string | null>(null);
const adding = ref(false);
const draft = ref({
  title: "",
  description: "",
  category: "",
  iconUrl: "",
  scope: "version" as "version" | "umbrella",
  achieved: false,
  earnedAt: "",
});
const nowInput = () => toLocalInput(new Date());
const earnedAtInvalid = computed(() => {
  if (!draft.value.achieved) return false;
  const seconds = localInputToUnixSeconds(draft.value.earnedAt);
  return seconds === null || seconds > Date.now() / 1000 + 60;
});

async function load(): Promise<void> {
  error.value = null;
  try {
    if (props.part === "completion") {
      completion.value = await fetchCompletion(props.gameId);
    } else {
      totals.value = await fetchAchievementTotals(props.gameId);
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not load.";
  }
}
watch(() => [props.gameId, props.part], load, { immediate: true });

async function act(work: () => Promise<void>, changes = false): Promise<void> {
  busy.value = true;
  error.value = null;
  try {
    await work();
    await load();
    if (changes) emit("changed");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not save.";
  } finally {
    busy.value = false;
  }
}

const pick = (gameId: string, event: Event) =>
  act(async () => {
    completion.value = await setCompletion(
      gameId,
      (event.target as HTMLSelectElement).value,
    );
  }, true);
const release = (gameId: string) =>
  act(async () => {
    completion.value = await releaseCompletion(gameId);
  }, true);

function startAdd(): void {
  draft.value = {
    title: "",
    description: "",
    category: "",
    iconUrl: "",
    scope: "version",
    achieved: false,
    earnedAt: nowInput(),
  };
  editingId.value = null;
  adding.value = true;
}
function startEdit(item: ManualAchievement): void {
  draft.value = {
    title: item.title,
    description: item.description ?? "",
    category: item.category ?? "",
    iconUrl: "",
    scope: item.scope,
    achieved: item.achieved,
    earnedAt: item.achievedAt
      ? toLocalInput(new Date(item.achievedAt * 1000))
      : nowInput(),
  };
  adding.value = false;
  editingId.value = item.id;
}
function cancel(): void {
  adding.value = false;
  editingId.value = null;
}
const blank = (value: string) => value.trim() || null;
const saveDraft = () =>
  act(async () => {
    const d = draft.value;
    if (!d.title.trim()) throw new Error("Give the achievement a title.");
    if (earnedAtInvalid.value)
      throw new Error("Choose an earned time that is not in the future.");
    const earnedAt = d.achieved ? localInputToUnixSeconds(d.earnedAt) : null;
    if (editingId.value) {
      await updateManualAchievement(editingId.value, {
        title: d.title,
        description: blank(d.description),
        category: blank(d.category),
        ...(d.iconUrl.trim() ? { iconUrl: d.iconUrl.trim() } : {}),
        achieved: d.achieved,
        achievedAt: earnedAt,
      });
    } else {
      await createManualAchievement(props.gameId, {
        title: d.title,
        description: blank(d.description),
        category: blank(d.category),
        iconUrl: blank(d.iconUrl),
        scope: d.scope,
        achieved: d.achieved,
        achievedAt: earnedAt,
      });
    }
    cancel();
  });
const toggle = (item: ManualAchievement) =>
  act(async () => {
    await setManualAchievementEarned(item.id, !item.achieved);
  });
async function remove(item: ManualAchievement): Promise<void> {
  if (
    await confirm({
      title: "Remove achievement",
      message: `Remove "${item.title}" from your tracking? Imported achievements are not affected.`,
      confirmLabel: "Remove",
    })
  )
    await act(async () => removeManualAchievement(item.id));
}
const when = (seconds: number | null) =>
  seconds ? formatInstant(seconds) : "";
const earnedDay = (seconds: number) => formatInstant(seconds, { time: false });
const manualItems = computed(() => totals.value?.manual ?? []);

// Presentation (SPEC-UI-POLISH-PASS §8): a filter by state, and the
// categories as sets with their own headers once more than one is used.
type ManualFilter = "all" | "earned" | "open";
const manualFilter = ref<ManualFilter>("all");
const earnedCount = computed(
  () => manualItems.value.filter((item) => item.achieved).length,
);
const FILTERS = computed(() => [
  { value: "all" as const, label: "All", count: manualItems.value.length },
  { value: "earned" as const, label: "Earned", count: earnedCount.value },
  {
    value: "open" as const,
    label: "To earn",
    count: manualItems.value.length - earnedCount.value,
  },
]);
const shownItems = computed(() =>
  manualItems.value.filter(
    (item) =>
      manualFilter.value === "all" ||
      (manualFilter.value === "earned") === item.achieved,
  ),
);
const categoryOf = (item: ManualAchievement) => item.category?.trim() ?? "";
const categories = computed(() => [
  ...new Set(manualItems.value.map(categoryOf).filter((name) => name)),
]);
// one group per category in the order they first appear; no headers while
// everything shares one category (or none)
const groups = computed(() => {
  const grouped = new Map<string, ManualAchievement[]>();
  for (const item of shownItems.value)
    grouped.set(categoryOf(item), [
      ...(grouped.get(categoryOf(item)) ?? []),
      item,
    ]);
  const headed = categories.value.length > 1;
  return [...grouped.entries()]
    .sort(([a], [b]) => Number(!a) - Number(!b))
    .map(([name, items]) => {
      const all = manualItems.value.filter((item) => categoryOf(item) === name);
      return {
        key: name || "-",
        name: headed ? name || "Other" : "",
        items,
        earned: all.filter((item) => item.achieved).length,
        total: all.length,
      };
    });
});
// a preview of the icon address while editing, on a checkerboard so a
// transparent icon shows its edges
const iconPreview = computed(() => {
  const url = draft.value.iconUrl.trim();
  return /^https?:[/][/][^\s]+$/i.test(url) ? url : "";
});
const several = computed(() => (totals.value?.versions.length ?? 0) > 1);
</script>

<template>
  <section
    v-if="part === 'completion' && completion"
    class="manual-tracking"
    aria-labelledby="completion-title"
  >
    <header>
      <h2 id="completion-title">Completion</h2>
      <span
        class="summary"
        :class="{ partial: completion.partial }"
        role="status"
        >{{ completion.summary }}</span
      >
      <button
        class="ui-btn ui-btn-secondary"
        type="button"
        aria-controls="completion-versions"
        :aria-expanded="completionExpanded"
        @click="completionOpen = !completionExpanded"
      >
        {{ completionExpanded ? "Hide" : "Per copy" }}
      </button>
    </header>
    <ul v-show="completionExpanded" id="completion-versions">
      <li v-for="v in completion.versions" :key="v.gameId">
        <div class="name">
          <RouterLink v-if="v.gameId !== gameId" :to="`/games/${v.gameId}`">{{
            v.title
          }}</RouterLink>
          <strong v-else>{{ v.title }}</strong>
          <span class="hint">{{ copyName(v) }}</span>
        </div>
        <label class="status">
          <span class="sr-only">Status of {{ v.title }}</span>
          <select
            class="ui-field"
            :value="v.status"
            :disabled="busy"
            @change="pick(v.gameId, $event)"
          >
            <option v-for="s in STATUSES" :key="s" :value="s">
              {{ label(s) }}
            </option>
          </select>
        </label>
        <span class="origin">
          {{ v.userSet ? "Set by you" : "From imports" }}
        </span>
        <button
          v-if="v.userSet"
          class="ui-btn ui-btn-secondary"
          type="button"
          :disabled="busy"
          @click="release(v.gameId)"
        >
          Let imports update
        </button>
      </li>
    </ul>
    <p v-show="completionExpanded" class="hint">
      A status you set stays as you chose it; Steam, Playnite and other imports
      do not change it.
    </p>
    <p v-if="error" class="manual-error" role="alert">{{ error }}</p>
  </section>

  <section
    v-else-if="part === 'achievements' && totals"
    class="manual-tracking"
    aria-labelledby="manual-ach-title"
  >
    <header>
      <h2 id="manual-ach-title">Your achievements</h2>
      <span class="summary" role="status">{{
        totals.umbrella.summary ?? "None yet"
      }}</span>
      <button
        class="ui-btn ui-btn-secondary"
        type="button"
        :disabled="busy"
        @click="startAdd"
      >
        Add achievement
      </button>
    </header>
    <ul v-if="several" class="versions" aria-label="Achievements per version">
      <li v-for="v in totals.versions" :key="v.gameId">
        <span class="name"
          >{{ v.title }}<span class="hint">{{ copyName(v) }}</span></span
        >
        <span
          >{{ v.unlocked }} of {{ v.total }} earned ({{
            v.manual.total
          }}
          yours)</span
        >
      </li>
    </ul>
    <form v-if="adding || editingId" class="editor" @submit.prevent="saveDraft">
      <label
        >Title
        <input v-model="draft.title" class="ui-field" maxlength="500" required
      /></label>
      <label
        >Description
        <textarea v-model="draft.description" class="ui-field" rows="2" />
      </label>
      <label
        >Category
        <input
          v-model="draft.category"
          class="ui-field"
          maxlength="100"
          list="manual-ach-categories"
          placeholder="Story, Collectibles, DLC…"
        />
        <datalist id="manual-ach-categories">
          <option v-for="name in categories" :key="name" :value="name" />
        </datalist>
        <span class="hint"
          >Achievements that share a category are listed together.</span
        ></label
      >
      <div class="icon-field">
        <label
          >Icon address (optional)
          <input
            v-model="draft.iconUrl"
            class="ui-field"
            type="url"
            placeholder="https://"
        /></label>
        <span class="icon-preview" aria-hidden="true">
          <img v-if="iconPreview" :src="iconPreview" alt="" />
          <span v-else class="icon-placeholder">No icon</span>
        </span>
      </div>
      <label class="earned"
        ><input v-model="draft.achieved" type="checkbox" /> Earned</label
      >
      <label v-if="draft.achieved"
        >Earned on
        <input
          v-model="draft.earnedAt"
          class="ui-field"
          type="datetime-local"
          :max="nowInput()"
          required
        />
        <span v-if="earnedAtInvalid" class="hint" role="alert"
          >Choose a date and time that is not in the future.</span
        ></label
      >
      <label v-if="adding"
        >Applies to
        <select v-model="draft.scope" class="ui-field">
          <option value="version">This version only</option>
          <option value="umbrella">Every version of this game</option>
        </select>
      </label>
      <div class="actions">
        <button
          class="ui-btn ui-btn-primary"
          type="submit"
          :disabled="busy || earnedAtInvalid"
        >
          {{ editingId ? "Save changes" : "Add" }}
        </button>
        <button
          class="ui-btn ui-btn-secondary"
          type="button"
          :disabled="busy"
          @click="cancel"
        >
          Cancel
        </button>
      </div>
    </form>
    <div
      v-if="manualItems.length > 1"
      class="manual-filter"
      role="group"
      aria-label="Show achievements"
    >
      <button
        v-for="option in FILTERS"
        :key="option.value"
        type="button"
        class="filter-chip"
        :aria-pressed="manualFilter === option.value"
        @click="manualFilter = option.value"
      >
        {{ option.label }} <span class="count">{{ option.count }}</span>
      </button>
    </div>
    <template v-if="manualItems.length">
      <section
        v-for="group in groups"
        :key="group.key"
        class="manual-group"
        :aria-label="group.name || undefined"
      >
        <h3 v-if="group.name" class="group-title">
          {{ group.name }}
          <span class="count">{{ group.earned }} of {{ group.total }}</span>
        </h3>
        <ul class="items">
          <li
            v-for="item in group.items"
            :key="item.id"
            :class="{ done: item.achieved }"
          >
            <label class="earned">
              <input
                type="checkbox"
                :checked="item.achieved"
                :disabled="busy"
                @change="toggle(item)"
              />
              <span class="sr-only">Earned: </span>
              <img
                v-if="item.iconUrl"
                :src="item.iconUrl"
                alt=""
                width="32"
                height="32"
              />
              <span v-else class="icon-dot" aria-hidden="true"></span>
              <span class="ach-name">{{ item.title }}</span>
            </label>
            <span class="tags">
              <span v-if="item.category && !group.name" class="tag">{{
                item.category
              }}</span>
              <span v-if="item.scope === 'umbrella'" class="tag"
                >All versions</span
              >
              <time
                v-if="item.achieved && item.achievedAt"
                class="tag tag-earned"
                :datetime="new Date(item.achievedAt * 1000).toISOString()"
                :title="when(item.achievedAt)"
                >Earned {{ earnedDay(item.achievedAt) }}</time
              >
              <span v-else-if="item.achieved" class="tag tag-earned"
                >Earned</span
              >
              <span v-else class="tag tag-open">To earn</span>
            </span>
            <p v-if="item.description" class="desc">{{ item.description }}</p>
            <div class="actions">
              <button
                class="ui-btn ui-btn-secondary ui-btn-sm"
                type="button"
                :disabled="busy"
                :aria-label="`Edit ${item.title}`"
                @click="startEdit(item)"
              >
                Edit
              </button>
              <button
                class="ui-btn ui-btn-secondary ui-btn-sm"
                type="button"
                :disabled="busy"
                :aria-label="`Remove ${item.title}`"
                @click="remove(item)"
              >
                Remove
              </button>
            </div>
          </li>
        </ul>
      </section>
      <p v-if="!shownItems.length" class="hint">
        {{
          manualFilter === "earned"
            ? "None earned yet."
            : "Every one is earned."
        }}
      </p>
    </template>
    <p v-if="!manualItems.length && !adding" class="hint">
      Track achievements the game does not report by itself. They are kept apart
      from Steam and other imports and are never replaced by a sync.
    </p>
    <p v-if="error" class="manual-error" role="alert">{{ error }}</p>
  </section>
  <p v-else-if="error" class="manual-error" role="alert">{{ error }}</p>
</template>

<style scoped>
.manual-tracking {
  padding: 20px;
  border: 1px solid var(--ui-border);
  border-radius: 12px;
  margin-bottom: 24px;
}
header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
h2 {
  margin: 0;
  font-size: 1.15rem;
}
.summary {
  flex: 1 1 14rem;
  color: var(--ui-dim);
}
.summary.partial {
  color: var(--ui-accent-text);
}
ul {
  list-style: none;
  padding: 0;
  margin: 12px 0 0;
}
li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  padding: 12px 0;
  border-top: 1px solid var(--ui-border);
}
.name a {
  color: var(--ui-accent-text);
}
.name {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 140px;
  overflow-wrap: anywhere;
}
.hint {
  color: var(--ui-dim);
  font-size: 0.9rem;
}
.origin {
  font-size: 0.85rem;
  color: var(--ui-dim);
}
.earned {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 160px;
}
.earned img {
  border-radius: 6px;
}
.desc {
  flex-basis: 100%;
  margin: 0;
  overflow-wrap: anywhere;
}
.editor {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}
.editor label {
  display: grid;
  gap: 4px;
}
/* the Earned box sits beside its word, not above it */
.editor label.earned {
  display: flex;
  align-items: center;
  gap: 8px;
}
.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.manual-error {
  color: var(--ui-danger);
}

.manual-filter {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}
.filter-chip {
  min-height: 32px;
  padding: 4px 12px;
  border: 1px solid var(--ui-border);
  border-radius: 999px;
  background: transparent;
  color: var(--ui-text);
  font: inherit;
  font-size: 0.85rem;
  cursor: pointer;
}
.filter-chip[aria-pressed="true"] {
  border-color: var(--ui-accent-line);
  background: var(--ui-accent-soft);
  color: var(--ui-accent-text);
}
.filter-chip .count,
.group-title .count {
  color: var(--ui-dim);
  font-weight: 400;
  font-variant-numeric: tabular-nums;
}
.group-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 16px 0 0;
  font-size: 0.95rem;
}
.items li.done .ach-name {
  font-weight: 600;
}
.items li:not(.done) img {
  opacity: 0.55;
  filter: grayscale(1);
}
.icon-dot {
  flex: 0 0 32px;
  width: 32px;
  height: 32px;
  border: 1px dashed var(--ui-border);
  border-radius: 6px;
  background: var(--ui-surface-2);
}
.ach-name {
  overflow-wrap: anywhere;
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.tag {
  padding: 2px 8px;
  border: 1px solid var(--ui-border);
  border-radius: 999px;
  color: var(--ui-dim);
  font-size: 0.78rem;
  white-space: nowrap;
}
.tag-earned {
  border-color: color-mix(in srgb, var(--ui-good) 45%, transparent);
  background: var(--ui-good-soft);
  color: var(--ui-good);
}
.icon-field {
  display: flex;
  align-items: flex-end;
  gap: 12px;
}
.icon-field > label {
  flex: 1;
  min-width: 0;
}
.icon-preview {
  display: grid;
  flex: 0 0 64px;
  place-items: center;
  width: 64px;
  height: 64px;
  overflow: hidden;
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  background-color: var(--ui-surface);
  background-image:
    linear-gradient(45deg, var(--ui-border-soft) 25%, transparent 25%),
    linear-gradient(-45deg, var(--ui-border-soft) 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, var(--ui-border-soft) 75%),
    linear-gradient(-45deg, transparent 75%, var(--ui-border-soft) 75%);
  background-position:
    0 0,
    0 6px,
    6px -6px,
    -6px 0;
  background-size: 12px 12px;
}
.icon-preview img {
  width: 64px;
  height: 64px;
  object-fit: contain;
}
.icon-placeholder {
  color: var(--ui-dim);
  font-size: 0.7rem;
  text-align: center;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
