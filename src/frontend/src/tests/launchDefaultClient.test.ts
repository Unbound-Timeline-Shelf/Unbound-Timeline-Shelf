// @vitest-environment happy-dom
// The account's default launch client (#319): chosen once in Settings, used by every
// browser's Play button.
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import GamePageSection from "../components/settings/GamePageSection.vue";
import {
  preferences,
  preferencesError,
  preferencesLoaded,
} from "../state/preferences";
import { DEFAULT_PREFERENCES, queuePreferences } from "../services/preferences";

vi.mock("../services/preferences", async (original) => ({
  ...(await original<typeof import("../services/preferences")>()),
  fetchPreferences: vi.fn(),
  queuePreferences: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  preferences.value = { ...DEFAULT_PREFERENCES };
  preferencesLoaded.value = true;
  preferencesError.value = null;
});
afterEach(() => {
  preferences.value = { ...DEFAULT_PREFERENCES };
});

function choice(wrapper: ReturnType<typeof mount>, label: string) {
  return wrapper
    .find(".launch-default")
    .findAll("button")
    .find((button) => button.text() === label)!;
}

it("saves the chosen client to the account and shares it with the Play button", async () => {
  const saved = {
    ...DEFAULT_PREFERENCES,
    launch_default_client: "steam" as const,
  };
  vi.mocked(queuePreferences).mockResolvedValue({ prefs: saved, latest: true });
  const wrapper = mount(GamePageSection);
  await flushPromises();
  await choice(wrapper, "Steam").trigger("click");
  await flushPromises();
  expect(queuePreferences).toHaveBeenCalledWith({
    launch_default_client: "steam",
  });
  expect(preferences.value.launch_default_client).toBe("steam");
  wrapper.unmount();
});

it("puts the previous choice back when saving fails", async () => {
  preferences.value = { ...DEFAULT_PREFERENCES, launch_default_client: "gog" };
  vi.mocked(queuePreferences).mockRejectedValue(new Error("Offline"));
  const wrapper = mount(GamePageSection);
  await flushPromises();
  await choice(wrapper, "Playnite").trigger("click");
  await flushPromises();
  expect(wrapper.find('[role="alert"]').text()).toBe("Offline");
  expect(choice(wrapper, "GOG Galaxy").attributes("aria-pressed")).toBe("true");
  expect(preferences.value.launch_default_client).toBe("gog");
  wrapper.unmount();
});
