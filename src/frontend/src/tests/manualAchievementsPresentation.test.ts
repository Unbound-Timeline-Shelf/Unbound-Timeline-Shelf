// @vitest-environment happy-dom
// SPEC-UI-POLISH-PASS §8: your own achievements are listed by category once
// more than one is used, can be filtered to earned or still to earn, and say
// when each was earned in the account's date format.
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import GameManualTrackingPanel from "../components/game/GameManualTrackingPanel.vue";
import type { ManualAchievement } from "../services/gameTracking";
import { DEFAULT_PREFERENCES } from "../services/preferences";
import { preferences } from "../state/preferences";
import { platinumOf } from "../utils/achievements";
import type { Achievement } from "../types/game";

const state = vi.hoisted(() => ({ manual: [] as unknown[] }));
vi.mock("../state/dialog", () => ({
  useConfirm: () => vi.fn(async () => true),
}));
vi.mock("../services/gameTracking", () => ({
  fetchAchievementTotals: vi.fn(async () => ({
    umbrella: {
      total: state.manual.length,
      unlocked: 0,
      percent: 0,
      manual: state.manual.length,
      provider: 0,
      summary: null,
    },
    versions: [],
    manual: state.manual,
  })),
}));

const item = (
  title: string,
  category: string | null,
  achievedAt: number | null = null,
): ManualAchievement => ({
  id: title,
  scope: "version",
  gameId: "g0",
  title,
  description: null,
  iconUrl: null,
  category,
  position: 0,
  achieved: achievedAt !== null,
  achievedAt,
  matchKey: null,
});

async function panel(manual: ManualAchievement[]) {
  state.manual = manual;
  const wrapper = mount(GameManualTrackingPanel, {
    props: { gameId: "g0", part: "achievements" },
    global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } },
  });
  await flushPromises();
  return wrapper;
}
const names = (w: Awaited<ReturnType<typeof panel>>) =>
  w.findAll(".ach-name").map((n) => n.text());

afterEach(() => {
  preferences.value = { ...DEFAULT_PREFERENCES };
});

describe("your achievements", () => {
  // 14 March 2024 12:00 UTC
  const EARNED = 1710417600;

  it("groups by category, uncategorised last, with earned counts", async () => {
    const wrapper = await panel([
      item("Weathered", null),
      item("Lighthouse keeper", "Story", EARNED),
      item("Shell collector", "Collectibles"),
      item("Night crossing", "Story"),
    ]);
    expect(wrapper.findAll(".group-title").map((t) => t.text())).toEqual([
      "Story 1 of 2",
      "Collectibles 0 of 1",
      "Other 0 of 1",
    ]);
    expect(names(wrapper)).toEqual([
      "Lighthouse keeper",
      "Night crossing",
      "Shell collector",
      "Weathered",
    ]);
  });

  it("shows no headers while one category (or none) is used", async () => {
    const wrapper = await panel([item("A", "Story"), item("B", "Story")]);
    expect(wrapper.find(".group-title").exists()).toBe(false);
    expect(wrapper.findAll(".tag").map((t) => t.text())).toContain("Story");
  });

  it("filters to earned or still to earn", async () => {
    const wrapper = await panel([
      item("Lighthouse keeper", "Story", EARNED),
      item("Night crossing", "Story"),
    ]);
    const chips = wrapper.findAll(".filter-chip");
    expect(chips.map((c) => c.text())).toEqual([
      "All 2",
      "Earned 1",
      "To earn 1",
    ]);
    await chips[1].trigger("click");
    expect(names(wrapper)).toEqual(["Lighthouse keeper"]);
    expect(chips[1].attributes("aria-pressed")).toBe("true");
    await chips[2].trigger("click");
    expect(names(wrapper)).toEqual(["Night crossing"]);
  });

  it("says when each was earned in the chosen date format", async () => {
    preferences.value = { ...preferences.value, date_format: "mdy" };
    const wrapper = await panel([item("Lighthouse keeper", null, EARNED)]);
    const earned = wrapper.get("time.tag-earned");
    expect(earned.text()).toBe("Earned 03/14/2024");
    expect(earned.attributes("datetime")).toBe("2024-03-14T12:00:00.000Z");
  });

  it("previews only a web address as the icon", async () => {
    const wrapper = await panel([]);
    await wrapper.get(".manual-head button, button.ui-btn").trigger("click");
    const icon = wrapper.get("input[type='url']");
    await icon.setValue("javascript:alert(1)");
    expect(wrapper.find(".icon-preview img").exists()).toBe(false);
    await icon.setValue("https://example.invalid/icon.png");
    expect(wrapper.get(".icon-preview img").attributes("src")).toBe(
      "https://example.invalid/icon.png",
    );
  });
});

describe("the platinum summary", () => {
  const ach = (id: string, tier: string | null, unlocked = false) =>
    ({ id, tierOverride: tier, unlocked }) as unknown as Achievement;

  it("finds the platinum trophy unless it is a hidden one still locked", () => {
    const list = [ach("a", null), ach("p", "platinum")];
    expect(platinumOf(list, () => false)?.id).toBe("p");
    expect(platinumOf(list, (a) => a.id === "p")).toBeNull();
    expect(platinumOf([ach("a", "gold")], () => false)).toBeNull();
  });
});
