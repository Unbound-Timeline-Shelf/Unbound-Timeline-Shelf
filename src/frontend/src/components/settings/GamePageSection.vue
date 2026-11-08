<script setup lang="ts">
// What every game's page shows by default: which tabs, which buttons, and the
// tab it opens on. A game can override any of it from its own Edit dialog.
// Saved on the server as it is changed.
import { ref, onMounted } from "vue";
import PageSettingsEditor from "../PageSettingsEditor.vue";
import SegmentedControl from "./SegmentedControl.vue";
import { queuePreferences } from "../../services/preferences";
import type { Preferences } from "../../services/preferences";
import {
  preferences as sharedPreferences,
  preferencesLoaded,
  preferencesError,
  loadSharedPreferences,
} from "../../state/preferences";
import { resolvePage } from "../../utils/gamePage";
import type { PageSettings } from "../../utils/gamePage";

const settings = ref<PageSettings>(
  resolvePage(sharedPreferences.value.game_page, null),
);
const loaded = ref(preferencesLoaded.value && !preferencesError.value);
const error = ref<string | null>(null);

onMounted(async () => {
  if (loaded.value) return;
  try {
    await loadSharedPreferences();
    if (preferencesError.value) throw new Error(preferencesError.value);
    settings.value = resolvePage(sharedPreferences.value.game_page, null);
    launchClient.value = sharedPreferences.value.launch_default_client ?? "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to load settings.";
  } finally {
    loaded.value = true;
  }
});

async function change(next: PageSettings) {
  const previous = settings.value;
  settings.value = next;
  error.value = null;
  try {
    const { prefs, latest } = await queuePreferences({ game_page: next });
    if (latest) {
      settings.value = resolvePage(prefs.game_page, null);
      sharedPreferences.value = prefs;
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to save.";
    settings.value = previous;
  }
}

// The client a game's Play button uses on every device (#319). Automatic keeps the
// choice made in this browser, then the store the game came from.
type LaunchClient = Preferences["launch_default_client"];
const launchClient = ref<LaunchClient>(
  sharedPreferences.value.launch_default_client ?? "",
);
const launchOptions = [
  { value: "", label: "Automatic" },
  { value: "playnite", label: "Playnite" },
  { value: "steam", label: "Steam" },
  { value: "gog", label: "GOG Galaxy" },
];
async function changeLaunchClient(next: LaunchClient) {
  const previous = launchClient.value;
  launchClient.value = next;
  error.value = null;
  try {
    const { prefs, latest } = await queuePreferences({
      launch_default_client: next,
    });
    if (latest) {
      launchClient.value = prefs.launch_default_client ?? "";
      sharedPreferences.value = prefs;
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to save.";
    launchClient.value = previous;
  }
}
</script>

<template>
  <section class="settings-section">
    <h2>Game page</h2>
    <p class="section-hint">
      Choose what a game's page shows, for every game. Hide the tabs you never
      use, or set them to Auto so they appear once a game has something in them.
      A single game can override any of this from its Edit dialog, on the Page
      tab.
    </p>
    <p v-if="error" class="ui-error-box" role="alert">{{ error }}</p>
    <PageSettingsEditor
      v-if="loaded"
      :model-value="settings"
      @update:model-value="change($event as PageSettings)"
    />
    <div v-if="loaded" class="field launch-default">
      <span id="launch-default-label">Play button opens games in</span>
      <SegmentedControl
        aria-labelledby="launch-default-label"
        :model-value="launchClient"
        :options="launchOptions"
        @update:model-value="changeLaunchClient($event as LaunchClient)"
      />
      <small
        >Used in every browser you sign in to, whenever the game can be opened
        there. Automatic uses the app you last picked in this browser, then the
        store the game came from. The other options stay in the Play
        menu.</small
      >
    </div>
  </section>
</template>

<style scoped>
.settings-section h2 {
  margin: 0 0 8px;
  padding-left: 12px;
  border-left: 3px solid var(--ui-accent-text);
  font-size: 1rem;
  color: var(--ui-text);
}
.section-hint {
  color: var(--ui-dim);
  font-size: 0.82rem;
  line-height: 1.6;
  margin: 0 0 20px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 20px 0 0;
  font-size: 0.82rem;
  color: var(--ui-text);
}
.field small {
  color: var(--ui-faint);
}
.error {
  color: var(--ui-error);
  font-size: 0.85rem;
}
.settings-section h2 {
  border: 0;
  padding: 0;
  font: var(--ui-weight-heading) var(--ui-font-heading)/1.4
    var(--ui-font-family);
}
</style>
