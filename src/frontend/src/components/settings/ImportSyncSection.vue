<script setup lang="ts">
// One place that lists every way into the library (UX-AUDIT-APP A20).
// Store connections, imports from other sites, restoring an export, the
// Upload page and the server's own integrations were each in a different
// area; this hub links to each of them where it lives, so nothing moves.
import { computed } from "vue";
import { RouterLink } from "vue-router";
import AppIcon from "../AppIcon.vue";
import LibrarySyncMenu from "../LibrarySyncMenu.vue";
import { currentUser } from "../../state/auth";
import { settingsLocation } from "../../state/settingsRegistry";

interface HubLink {
  title: string;
  description: string;
  to: string | { path: string; query: Record<string, string> };
  action: string;
}

const links = computed<HubLink[]>(() => [
  {
    title: "Game stores and services",
    description:
      "Connect Steam, PlayStation, Xbox, GOG, RetroAchievements and plugin services, then sync them.",
    to: settingsLocation("connections"),
    action: "Open connections",
  },
  {
    title: "Import from another site",
    description:
      "Bring in lists from AniList, MyAnimeList, Letterboxd, IMDb, Yamtrack or a CSV file.",
    to: settingsLocation("export", { tab: "import" }),
    action: "Import a list",
  },
  {
    title: "Restore or back up",
    description:
      "Restore games from an export, export your library, or set automatic backups.",
    to: settingsLocation("export", { tab: "restore" }),
    action: "Restore and back up",
  },
  {
    title: "Upload files",
    description:
      "Drop screenshots, clips and save files, then assign them to a game.",
    to: "/upload",
    action: "Open Upload",
  },
  {
    title: "Automatic syncing",
    description:
      "See and schedule the background tasks that keep your libraries in sync.",
    to: settingsLocation("background-tasks"),
    action: "Open my background tasks",
  },
  ...(currentUser.value?.is_admin
    ? [
        {
          title: "Server integrations",
          description:
            "Deployment credentials for account integrations and metadata providers on this server.",
          to: settingsLocation("server-integrations"),
          action: "Open server integrations",
        },
      ]
    : []),
]);
</script>

<template>
  <section class="import-sync" aria-labelledby="import-sync-intro">
    <p id="import-sync-intro" class="intro">
      Everything that brings games, media and files into your library, in one
      list. Each link opens the page where that source is set up.
    </p>
    <div class="sync-now">
      <span>Sync a connected store now</span>
      <LibrarySyncMenu />
    </div>
    <ul class="hub-links">
      <li v-for="link in links" :key="link.title">
        <RouterLink :to="link.to" class="hub-link">
          <span class="hub-text">
            <strong>{{ link.title }}</strong>
            <small>{{ link.description }}</small>
          </span>
          <span class="hub-action"
            >{{ link.action }}<AppIcon name="chevron" :size="16"
          /></span>
        </RouterLink>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.import-sync {
  display: grid;
  gap: 18px;
}
.intro {
  margin: 0;
  color: var(--ui-dim);
  line-height: 1.5;
}
.sync-now {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border: 1px solid var(--ui-border-soft);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface-2);
}
.hub-links {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
}
.hub-link {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 4px;
  border-bottom: 1px solid var(--ui-border-soft);
  color: var(--ui-text);
  text-decoration: none;
}
.hub-links li:last-child .hub-link {
  border-bottom: 0;
}
.hub-link:hover strong {
  color: var(--ui-accent-text);
}
.hub-text {
  display: grid;
  gap: 4px;
  min-width: 0;
}
.hub-text small {
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
  line-height: 1.45;
}
.hub-action {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  gap: 4px;
  color: var(--ui-accent-text);
  font-size: var(--ui-font-small);
  font-weight: 600;
}
@media (max-width: 760px) {
  .hub-link {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}
</style>
