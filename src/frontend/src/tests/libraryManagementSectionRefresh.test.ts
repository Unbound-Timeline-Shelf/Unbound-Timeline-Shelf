// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import LibraryManagementSection from "../components/settings/LibraryManagementSection.vue";
import * as gamesService from "../services/games";
import type { Game } from "../types/game";

function makeGame(): Game {
  return {
    id: "game-1",
    title: "Example Game",
    updatedAt: 1,
  } as Game;
}
const GAME = makeGame();

function unverifiedIdPreview(): gamesService.RefreshMetadataPreview {
  return {
    status: "preview",
    match: "id",
    candidate: { title: "Example Game", providerIds: { steam: "123" } },
    provider: "igdb",
    providerErrors: [],
    // The verified source alone reports no changes; only the withheld,
    // unverified source would actually change anything (#492 review).
    changedFields: [],
    skippedLockedFields: [],
    wouldAddKeyArt: false,
    wouldAddBanner: false,
    gameUpdatedAt: 1,
    unverifiedSources: { rawg: { description: "A new description" } },
  };
}

vi.mock("../services/games", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/games")>()),
  fetchGames: vi.fn().mockResolvedValue([makeGame()]),
  fetchGameTrash: vi.fn().mockResolvedValue([]),
}));

afterEach(() => vi.clearAllMocks());

async function openPreview() {
  const wrapper = mount(LibraryManagementSection, {
    global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } },
  });
  await flushPromises();
  await wrapper.get("button.secondary-button").trigger("click");
  await flushPromises();
  return wrapper;
}

describe("bulk refresh preview: withheld (unverified) id-matched sources", () => {
  it("surfaces the withheld source instead of hiding it as already up to date", async () => {
    vi.spyOn(gamesService, "previewGameMetadataRefresh").mockResolvedValue(
      unverifiedIdPreview(),
    );
    const wrapper = await openPreview();

    expect(wrapper.text()).not.toContain("already up to date, 1");
    expect(wrapper.text()).toContain("unverified source");
    const checkbox = wrapper.get(
      'input[type="checkbox"][aria-label*="unverified-source"]',
    );
    expect((checkbox.element as HTMLInputElement).checked).toBe(false);

    const applyButton = wrapper.get("button.primary-button");
    expect(applyButton.text()).toContain("Apply to 0 games");
  });

  it("applies the candidate's ids/fields only once the user confirms it", async () => {
    vi.spyOn(gamesService, "previewGameMetadataRefresh").mockResolvedValue(
      unverifiedIdPreview(),
    );
    const refreshSpy = vi
      .spyOn(gamesService, "refreshGameMetadata")
      .mockResolvedValue({
        status: "updated",
        provider: "igdb",
        providerErrors: [],
        changedFields: ["description"],
        skippedLockedFields: [],
        keyArtAdded: false,
        bannerAdded: false,
        gameUpdatedAt: 2,
      });
    const wrapper = await openPreview();

    const checkbox = wrapper.get(
      'input[type="checkbox"][aria-label*="unverified-source"]',
    );
    await checkbox.setValue(true);

    const applyButton = wrapper.get("button.primary-button");
    expect(applyButton.text()).toContain("Apply to 1 game");
    await applyButton.trigger("click");
    await flushPromises();

    expect(refreshSpy).toHaveBeenCalledWith(GAME, expect.anything(), {
      steam: "123",
    });
  });

  it("declining the candidate leaves it out of what gets applied", async () => {
    vi.spyOn(gamesService, "previewGameMetadataRefresh").mockResolvedValue(
      unverifiedIdPreview(),
    );
    const refreshSpy = vi.spyOn(gamesService, "refreshGameMetadata");
    const wrapper = await openPreview();

    // never ticked: the checkbox defaults to unconfirmed
    const applyButton = wrapper.get("button.primary-button");
    expect(applyButton.text()).toContain("Apply to 0 games");
    expect((applyButton.element as HTMLButtonElement).disabled).toBe(true);
    expect(refreshSpy).not.toHaveBeenCalled();
  });
});
