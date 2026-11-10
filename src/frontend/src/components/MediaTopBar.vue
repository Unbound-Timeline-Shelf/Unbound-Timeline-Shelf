<script setup lang="ts">
// The Media-area bar: AppTopBar with the Movies / TV Shows / Anime / Collections
// switcher on the left, and on the right whatever the page adds (its
// `actions` slot).
import AppTopBar from "./AppTopBar.vue";
import MediaKindSwitch from "./MediaKindSwitch.vue";
import { sidebarListsFolder } from "../state/navigationFolders";

defineProps<{
  active: "movie" | "tv" | "anime" | "lists" | "search";
}>();
</script>

<template>
  <AppTopBar>
    <!-- left out while the sidebar lists the same pages (UX-AUDIT-APP A19) -->
    <MediaKindSwitch v-if="!sidebarListsFolder('media')" :active="active" />
    <template #actions>
      <slot name="actions" />
    </template>
  </AppTopBar>
</template>
