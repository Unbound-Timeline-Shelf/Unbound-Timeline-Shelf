// @vitest-environment happy-dom
import { mount } from "@vue/test-utils";
import { defineComponent, h, KeepAlive, ref } from "vue";
import { beforeEach, expect, it } from "vitest";
import {
  cancelScrollRestore,
  scrollKey,
  useScrollReady,
  viewsReady,
  waitForScrollTarget,
} from "../state/scrollMemory";

let height = 900;
beforeEach(() => {
  height = 900;
  Object.defineProperty(document.documentElement, "scrollHeight", {
    configurable: true,
    get: () => height,
  });
  Object.defineProperty(window, "innerHeight", {
    configurable: true,
    value: 800,
  });
});

it("asks a paged list for more pages until the position can be reached", async () => {
  let pages = 1;
  const view = mount(
    defineComponent({
      setup() {
        useScrollReady(
          () => true,
          async () => {
            pages += 1;
            height = 900 * pages;
          },
        );
        return () => h("div");
      },
    }),
  );
  expect(await waitForScrollTarget(2500)).toBe(true);
  // 2500 + an 800px viewport needs 3300px of document, i.e. four pages
  expect(pages).toBe(4);
  view.unmount();
});

it("gives up when the person scrolls before the page is ready", async () => {
  const ready = ref(false);
  const view = mount(
    defineComponent({
      setup() {
        useScrollReady(() => ready.value);
        return () => h("div");
      },
    }),
  );
  const pending = waitForScrollTarget(400);
  window.dispatchEvent(new Event("wheel"));
  expect(await pending).toBe(false);
  view.unmount();
});

it("abandons a restore when another navigation starts", async () => {
  const view = mount(
    defineComponent({
      setup() {
        useScrollReady(() => false);
        return () => h("div");
      },
    }),
  );
  const pending = waitForScrollTarget(400);
  cancelScrollRestore();
  expect(await pending).toBe(false);
  view.unmount();
});

it("stops waiting for a kept-alive view once it is put away", async () => {
  const show = ref(true);
  const Waiting = defineComponent({
    name: "WaitingView",
    setup() {
      useScrollReady(() => false);
      return () => h("div");
    },
  });
  const host = mount(
    defineComponent({
      setup: () => () =>
        h(KeepAlive, null, [show.value ? h(Waiting) : h("span")]),
    }),
  );
  expect(viewsReady()).toBe(false);
  show.value = false;
  await host.vm.$nextTick();
  await new Promise((resolve) => setTimeout(resolve));
  expect(viewsReady()).toBe(true);
  host.unmount();
});

it("treats the library's filter-panel flag as the same place", () => {
  expect(scrollKey("/games", { filters: "0" })).toBe(scrollKey("/games", {}));
  expect(scrollKey("/games", { status: "playing", filters: "1" })).toBe(
    scrollKey("/games", { status: "playing" }),
  );
  expect(scrollKey("/games", { status: "playing" })).not.toBe(
    scrollKey("/games", {}),
  );
});
