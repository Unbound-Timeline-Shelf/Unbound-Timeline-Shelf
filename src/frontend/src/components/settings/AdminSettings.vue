<script setup lang="ts">
import { useQueryState } from "../../composables/useQueryState";
import SettingsTabs from "./SettingsTabs.vue";
import AdminSection from "./AdminSection.vue";
import OidcSettingsSection from "./OidcSettingsSection.vue";
import ServerIntegrationsSection from "./ServerIntegrationsSection.vue";
import AppSettingsSection from "./AppSettingsSection.vue";
import LimitsSection from "./LimitsSection.vue";
import DevToolsSection from "./DevToolsSection.vue";
import AdminNotificationSettings from "./AdminNotificationSettings.vue";

const props = defineProps<{ initialTab?: string }>();

// Admin-only server settings in one place. Tasks and Logs keep their own
// entries in the sidebar.
const TABS = [
  { id: "users", label: "Users" },
  { id: "sso", label: "Single sign-on" },
  { id: "integrations", label: "Integrations" },
  { id: "app", label: "Application" },
  { id: "notifications", label: "Notifications" },
  { id: "limits", label: "Limits" },
  { id: "dev-tools", label: "Dev Tools" },
];
// the open tab is part of the address, so Back returns to the previous tab
// and a reload keeps it (UX-AUDIT-APP §3.1)
const tab = useQueryState("tab", {
  values: TABS.map((t) => t.id),
  fallback: () =>
    TABS.some((t) => t.id === props.initialTab) ? props.initialTab! : "users",
});
</script>

<template>
  <div>
    <SettingsTabs v-model="tab" :tabs="TABS" />
    <AdminSection v-if="tab === 'users'" />
    <OidcSettingsSection v-else-if="tab === 'sso'" />
    <ServerIntegrationsSection v-else-if="tab === 'integrations'" />
    <AppSettingsSection v-else-if="tab === 'app'" />
    <AdminNotificationSettings v-else-if="tab === 'notifications'" />
    <LimitsSection v-else-if="tab === 'limits'" />
    <DevToolsSection v-else />
  </div>
</template>
