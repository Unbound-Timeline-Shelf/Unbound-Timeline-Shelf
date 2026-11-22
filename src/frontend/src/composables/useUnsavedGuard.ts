// Unsaved work is never thrown away silently (UX-AUDIT-APP A1, A2). While a
// form is dirty, leaving its page, the history step that opened it (browser
// Back, the Android back gesture) or the whole document (reload, closing the
// tab) asks first. Browsers show their own generic wording for a reload; the
// in-app question is used everywhere else.
import { inject, onBeforeUnmount, onMounted } from "vue";
import {
  onBeforeRouteLeave,
  onBeforeRouteUpdate,
  routerKey,
  type RouteLocationNormalized,
} from "vue-router";
import { useConfirm } from "../state/dialog";

const confirm = useConfirm();
let pendingQuestion: Promise<boolean> | null = null;

/** One "Discard changes?" question at a time, however many places ask. */
export function askDiscardChanges(): Promise<boolean> {
  pendingQuestion ??= confirm({
    title: "Discard changes?",
    message: "You have unsaved changes. They will be lost if you leave now.",
    confirmLabel: "Discard changes",
    cancelLabel: "Keep editing",
    danger: true,
  }).finally(() => {
    pendingQuestion = null;
  });
  return pendingQuestion;
}

export interface UnsavedGuardOptions {
  // A same-page address change that closes this form, such as Back removing
  // the `?edit=` step that opened a dialog.
  closesOn?: (
    to: RouteLocationNormalized,
    from: RouteLocationNormalized,
  ) => boolean;
}

export function useUnsavedGuard(
  dirty: () => boolean,
  options: UnsavedGuardOptions = {},
) {
  // set once the person agreed to discard, or the form saved, so the close
  // that follows does not ask a second time
  let released = false;
  const isDirty = () => !released && dirty();

  async function confirmDiscard(): Promise<boolean> {
    if (!isDirty()) return true;
    const discard = await askDiscardChanges();
    if (discard) released = true;
    return discard;
  }
  function release() {
    released = true;
  }

  function beforeUnload(event: BeforeUnloadEvent) {
    if (!isDirty()) return;
    event.preventDefault();
    // older Chromium and Safari still need returnValue set
    event.returnValue = "";
  }
  onMounted(() => window.addEventListener("beforeunload", beforeUnload));
  onBeforeUnmount(() =>
    window.removeEventListener("beforeunload", beforeUnload),
  );

  if (inject(routerKey, null)) {
    onBeforeRouteLeave(() => confirmDiscard());
    const closesOn = options.closesOn;
    if (closesOn)
      onBeforeRouteUpdate((to, from) =>
        closesOn(to, from) ? confirmDiscard() : true,
      );
  }

  return { isDirty, confirmDiscard, release };
}
