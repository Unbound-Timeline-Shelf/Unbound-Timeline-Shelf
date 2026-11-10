// @vitest-environment happy-dom
// UX-AUDIT-APP A1/A2: unsaved edits are never thrown away silently.
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h, ref } from "vue";
import { createMemoryHistory, createRouter, RouterView } from "vue-router";
import { afterEach, beforeAll, expect, it } from "vitest";
import UiModal from "../components/UiModal.vue";
import { activeDialog, closeDialog } from "../state/dialog";
import { useUnsavedGuard } from "../composables/useUnsavedGuard";

beforeAll(() => {
  // happy-dom's <dialog> lacks the modal API
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
});
afterEach(() => {
  while (activeDialog.value) closeDialog(false);
  document.body.innerHTML = "";
});

function modal(dirty: boolean) {
  return mount(UiModal, {
    props: { title: "Edit game", dirty },
    slots: { default: () => h("input", { name: "title" }) },
    attachTo: document.body,
  });
}
const dialogElement = () =>
  document.querySelector("dialog.ui-modal") as HTMLDialogElement;
const escape = () =>
  dialogElement().dispatchEvent(new Event("cancel", { cancelable: true }));

it("closes a clean dialog on Escape without asking", async () => {
  const wrapper = modal(false);
  escape();
  await flushPromises();
  expect(activeDialog.value).toBeNull();
  expect(wrapper.emitted("close")).toHaveLength(1);
  wrapper.unmount();
});

it("asks before Escape closes a dialog with unsaved edits", async () => {
  const wrapper = modal(true);
  const cancel = new Event("cancel", { cancelable: true });
  dialogElement().dispatchEvent(cancel);
  // the native close is prevented, so the <dialog> stays open
  expect(cancel.defaultPrevented).toBe(true);
  await flushPromises();
  expect(activeDialog.value?.title).toBe("Discard changes?");
  expect(wrapper.emitted("close")).toBeUndefined();

  closeDialog(false); // Keep editing
  await flushPromises();
  expect(wrapper.emitted("close")).toBeUndefined();

  escape();
  await flushPromises();
  closeDialog(true); // Discard changes
  await flushPromises();
  expect(wrapper.emitted("close")).toHaveLength(1);
  wrapper.unmount();
});

it("asks before × or the backdrop close a dirty dialog", async () => {
  const wrapper = modal(true);
  dialogElement()
    .querySelector<HTMLButtonElement>('[aria-label="Close dialog"]')!
    .click();
  await flushPromises();
  expect(activeDialog.value?.title).toBe("Discard changes?");
  // a second request while the question is open does not stack another
  escape();
  await flushPromises();
  closeDialog(false);
  await flushPromises();
  expect(activeDialog.value).toBeNull();
  expect(wrapper.emitted("close")).toBeUndefined();
  wrapper.unmount();
});

it("reopens and asks when the browser force-closes a dirty dialog", async () => {
  const wrapper = modal(true);
  dialogElement().close();
  dialogElement().dispatchEvent(new Event("close"));
  await flushPromises();
  expect(dialogElement().hasAttribute("open")).toBe(true);
  expect(activeDialog.value?.title).toBe("Discard changes?");
  wrapper.unmount();
});

it("asks the browser to confirm a reload while dirty", async () => {
  const dirty = ref(true);
  const wrapper = mount(
    defineComponent({
      setup() {
        useUnsavedGuard(() => dirty.value);
        return () => h("form");
      },
    }),
  );
  const unload = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(unload);
  expect(unload.defaultPrevented).toBe(true);

  dirty.value = false;
  const clean = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(clean);
  expect(clean.defaultPrevented).toBe(false);
  wrapper.unmount();
});

it("asks before Back removes the history step that opened a dirty editor", async () => {
  const dirty = ref(true);
  const Editor = defineComponent({
    setup() {
      useUnsavedGuard(() => dirty.value, {
        closesOn: (to, from) =>
          from.query.edit !== undefined && to.query.edit === undefined,
      });
      return () => h("form");
    },
  });
  const Page = defineComponent({
    setup: () => () => h("main", [h(Editor)]),
  });
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/games/:id", component: Page },
      { path: "/games", component: { render: () => h("p") } },
    ],
  });
  await router.push("/games");
  await router.push("/games/1");
  await router.push("/games/1?edit=basics");
  const app = mount(defineComponent({ setup: () => () => h(RouterView) }), {
    global: { plugins: [router] },
  });
  await flushPromises();

  // Back from the editor step: the question appears and "Keep editing" stays
  const back = router.push("/games/1");
  await flushPromises();
  expect(activeDialog.value?.title).toBe("Discard changes?");
  closeDialog(false);
  await back;
  expect(router.currentRoute.value.fullPath).toBe("/games/1?edit=basics");

  // leaving the page entirely asks too; discarding lets it go
  const leave = router.push("/games");
  await flushPromises();
  expect(activeDialog.value?.title).toBe("Discard changes?");
  closeDialog(true);
  await leave;
  expect(router.currentRoute.value.fullPath).toBe("/games");
  app.unmount();
});
