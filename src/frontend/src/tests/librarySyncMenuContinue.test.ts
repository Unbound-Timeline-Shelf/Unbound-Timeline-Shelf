// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import { reactive } from "vue";
import LibrarySyncMenu from "../components/LibrarySyncMenu.vue";

// Games → Sync / import uses the same server-run sync as Connections (#466): a
// run that stopped with saved work left offers "Continue syncing", which starts
// the same bounded sync again and never turns autosync on.
const state = vi.hoisted(() => ({
  sync: vi.fn(async () => undefined),
  states: {} as Record<string, unknown>,
}));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../composables/useConnections", () => ({
  CONNECTIONS: [],
  isConnectionConnected: () => false,
  useConnections: () => ({ loadCredentials: vi.fn() }),
}));
vi.mock("../composables/usePluginConnections", async () => {
  const { ref } = await import("vue");
  return {
    usePluginConnections: () => ({
      states: state.states,
      loading: ref(false),
      load: vi.fn(),
      sync: state.sync,
      isSyncing: () => false,
    }),
  };
});
vi.mock("../state/pluginExtensions", async () => {
  const { ref } = await import("vue");
  return {
    pluginConnections: ref([
      {
        pluginId: "official.epic-games",
        contributionId: "epic-games",
        pageId: "connect",
        label: "Epic Games",
        description: "",
        order: 0,
        syncAction: { id: "sync" },
        document: {},
      },
    ]),
    refreshPluginExtensions: vi.fn(),
  };
});

it("offers to continue an incomplete server-run sync from Games", async () => {
  state.states = reactive({
    "official.epic-games:epic-games": {
      plugin_id: "official.epic-games",
      connection_id: "epic-games",
      connected: true,
      display_name: "Ada",
      error: false,
      sync: {
        available: true,
        unavailable_reason: "",
        running: false,
        paused: false,
        status: "partial",
        outcome: "incomplete",
        can_continue: true,
        step_limit_reached: true,
        last_run_at: 1,
        last_summary: "40 of 90 games saved",
      },
    },
  });
  const wrapper = mount(LibrarySyncMenu, {
    global: { stubs: { RouterLink: true } },
  });
  try {
    await flushPromises();
    const button = wrapper
      .findAll("button")
      .find((item) => item.text().includes("Epic Games"));
    expect(button?.text()).toBe("Continue syncing Epic Games");
    await button!.trigger("click");
    await flushPromises();
    expect(state.sync).toHaveBeenCalledWith(
      "official.epic-games:epic-games",
      "Epic Games",
    );
  } finally {
    wrapper.unmount();
  }
});
