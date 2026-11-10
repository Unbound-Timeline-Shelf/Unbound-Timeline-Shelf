<script setup lang="ts">
import { useQueryState } from "../../composables/useQueryState";
import SettingsTabs from "./SettingsTabs.vue";
import MetadataSourcesSection from "./MetadataSourcesSection.vue";
import ScanSettingsSection from "./ScanSettingsSection.vue";
import SteamTagsSection from "./SteamTagsSection.vue";
import SteamWishlistSection from "./SteamWishlistSection.vue";
import MediaRefreshSection from "./MediaRefreshSection.vue";
import { METADATA_SETTINGS_TABS as TABS } from "../../state/settingsRegistry";

const props = defineProps<{ initialTab?: string }>();

// the open tab is part of the address, so Back returns to the previous tab
// and a reload keeps it (UX-AUDIT-APP §3.1)
const tab = useQueryState("tab", {
  values: TABS.map((t) => t.id),
  fallback: () =>
    TABS.some((t) => t.id === props.initialTab) ? props.initialTab! : "sources",
});
</script>

<template>
  <div>
    <SettingsTabs v-model="tab" :tabs="TABS" />
    <MetadataSourcesSection v-if="tab === 'sources'" />
    <template v-else-if="tab === 'scan'">
      <ScanSettingsSection />
      <SteamTagsSection />
      <SteamWishlistSection />
    </template>
    <MediaRefreshSection v-else />
  </div>
</template>
