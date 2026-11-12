import { ref, watch } from "vue";
import { fetchCurrentUser } from "../services/auth";
import type { CurrentUser } from "../services/auth";
import { resetEntityCaches } from "../utils/entityCache";

export const currentUser = ref<CurrentUser | null>(null);
// The shared game, movie, TV and anime snapshots belong to one account
// (#468). Drop them the moment the signed-in account changes or signs out,
// before any page can draw the previous account's titles; responses still
// in flight for that account are then never stored.
watch(
  () => currentUser.value?.id,
  () => resetEntityCaches(),
  { flush: "sync" },
);
export const authChecked = ref(false);
export const authCheckFailed = ref(false);
let checkGeneration = 0;

export async function ensureAuthChecked() {
  if (!authChecked.value || authCheckFailed.value) await checkAuth();
}
export async function checkAuth() {
  const generation = ++checkGeneration;
  authCheckFailed.value = false;
  try {
    const user = await fetchCurrentUser();
    if (generation !== checkGeneration) return;
    currentUser.value = user;
  } catch {
    if (generation !== checkGeneration) return;
    authCheckFailed.value = true;
    // Keep auth failure separate from a confirmed unauthenticated response
    // so startup routing can distinguish an unavailable backend from login.
    currentUser.value = null;
  }
  authChecked.value = true;
}
