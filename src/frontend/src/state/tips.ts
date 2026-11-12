// One-time tips are dismissed per account, not per browser (UX-AUDIT-APP
// A24): a tip closed on the laptop stays closed on the phone. The list lives
// in the account's preferences (dismissed_tips).
import { computed } from "vue";
import { queuePreferences } from "../services/preferences";
import { currentUser } from "./auth";
import {
  preferences,
  preferencesError,
  preferencesLoaded,
} from "./preferences";

export const BULK_EDIT_TIP = "library.bulk-edit";

// tips this browser dismissed before they followed the account
const LEGACY_KEYS: Record<string, string> = {
  [BULK_EDIT_TIP]: "seenBulkEditHint",
};

function legacyDismissed(tip: string): boolean {
  const key = LEGACY_KEYS[tip];
  if (!key) return false;
  try {
    return localStorage.getItem(key) === "true";
  } catch {
    return false;
  }
}

/** Whether a tip should show: only once the account's choice is known. */
export function tipVisible(tip: string) {
  return computed(() => {
    if (!preferencesLoaded.value || preferencesError.value) return false;
    if (preferences.value.dismissed_tips.includes(tip)) return false;
    if (legacyDismissed(tip)) {
      // carry the old per-browser answer over to the account, once
      void dismissTip(tip);
      return false;
    }
    return true;
  });
}

/** Hides the tip at once and remembers that on the account. */
export async function dismissTip(tip: string): Promise<void> {
  const current = preferences.value.dismissed_tips;
  if (current.includes(tip)) return;
  const next = [...current, tip].slice(-64);
  const accountId = currentUser.value?.id;
  preferences.value = { ...preferences.value, dismissed_tips: next };
  try {
    const result = await queuePreferences({ dismissed_tips: next });
    if (currentUser.value?.id === accountId && result.latest)
      preferences.value = {
        ...preferences.value,
        dismissed_tips: result.prefs.dismissed_tips,
      };
  } catch {
    // stays hidden for this visit; it may come back next time
  }
}
