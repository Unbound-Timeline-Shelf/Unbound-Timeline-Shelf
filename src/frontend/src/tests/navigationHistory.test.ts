// @vitest-environment happy-dom
// UX-AUDIT-APP §3.1: one navigation rule. A tab, section or view you can be
// on is in the address and adds a history step; refinements do not.
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import {
  createMemoryHistory,
  createRouter,
  RouterView,
  type LocationQueryRaw,
} from "vue-router";
import { expect, it } from "vitest";
import { useQueryState } from "../composables/useQueryState";
import { libraryHistoryMode } from "../utils/gameLibraryQuery";

const TABS = ["manage", "duplicates", "trash"] as const;
const Tabs = defineComponent({
  setup() {
    const tab = useQueryState("tab", {
      values: TABS,
      fallback: () => "manage",
      omitFallback: true,
    });
    return () =>
      h(
        "div",
        TABS.map((id) =>
          h(
            "button",
            {
              "data-tab": id,
              "aria-selected": String(tab.value === id),
              onClick: () => (tab.value = id),
            },
            id,
          ),
        ),
      );
  },
});

async function mountWithRouter(start: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/settings", component: Tabs },
      { path: "/", component: { render: () => h("p") } },
    ],
  });
  await router.push("/");
  await router.push(start);
  const wrapper = mount(defineComponent({ setup: () => () => h(RouterView) }), {
    global: { plugins: [router] },
  });
  await flushPromises();
  return { router, wrapper };
}
const selected = (wrapper: ReturnType<typeof mount>) =>
  wrapper.find('[aria-selected="true"]').attributes("data-tab");

it("writes a chosen tab to the address as a new history step", async () => {
  const { router, wrapper } = await mountWithRouter(
    "/settings?section=library",
  );
  await wrapper.find('[data-tab="duplicates"]').trigger("click");
  await flushPromises();
  expect(router.currentRoute.value.query).toEqual({
    section: "library",
    tab: "duplicates",
  });
  await wrapper.find('[data-tab="trash"]').trigger("click");
  await flushPromises();
  expect(selected(wrapper)).toBe("trash");

  // Back after two tab changes walks back two steps, then leaves
  router.back();
  await flushPromises();
  expect(selected(wrapper)).toBe("duplicates");
  router.back();
  await flushPromises();
  expect(selected(wrapper)).toBe("manage");
  expect(router.currentRoute.value.query.tab).toBeUndefined();
  wrapper.unmount();
});

it("opens the tab a link or reload names, and ignores unknown ones", async () => {
  const linked = await mountWithRouter("/settings?tab=trash");
  expect(selected(linked.wrapper)).toBe("trash");
  linked.wrapper.unmount();
  const unknown = await mountWithRouter("/settings?tab=nope");
  expect(selected(unknown.wrapper)).toBe("manage");
  unknown.wrapper.unmount();
});

it("does not add a step for choosing the tab already shown", async () => {
  const { router, wrapper } = await mountWithRouter("/settings?tab=trash");
  const before = router.currentRoute.value.fullPath;
  await wrapper.find('[data-tab="trash"]').trigger("click");
  await flushPromises();
  router.back();
  await flushPromises();
  expect(before).toBe("/settings?tab=trash");
  expect(router.currentRoute.value.fullPath).toBe("/");
  wrapper.unmount();
});

it("keeps the tab in the component when there is no router", async () => {
  const wrapper = mount(Tabs);
  await wrapper.find('[data-tab="trash"]').trigger("click");
  expect(selected(wrapper)).toBe("trash");
});

it.each([
  [{}, { filters: "0" }, "replace"],
  [{ filters: "0" }, { filters: "1" }, "replace"],
  [{ filters: "0" }, { filters: "0", status: "playing" }, "push"],
  [{ filters: "0", status: "playing" }, { filters: "0" }, "push"],
  [{ filters: "0" }, { filters: "0", sort: "rating" }, "push"],
  [{ filters: "0" }, { filters: "0", tag: ["RPG"] }, "push"],
  [{ filters: "0", tag: "RPG" }, { filters: "0", tag: ["RPG"] }, "replace"],
  [{ filters: "0" }, { filters: "0", q: "Ne" }, "search"],
  [{ filters: "0", q: "Ne" }, { filters: "0", q: "Neon" }, "search"],
  [
    { filters: "0", q: "Neon" },
    { filters: "0", q: "Neon", status: "beaten" },
    "push",
  ],
] as [LocationQueryRaw, LocationQueryRaw, string][])(
  "files the library change %j → %j as %s (A16)",
  (current, next, mode) => {
    expect(libraryHistoryMode({ ...current }, { ...next })).toBe(mode);
  },
);
