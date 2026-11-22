import { afterEach, describe, expect, it, vi } from "vitest";
import {
  applyGameMetadataRefresh,
  previewGameMetadataRefresh,
} from "../services/games";
import type { Game } from "../types/game";

const game = { id: "game-1", title: "Example", updatedAt: 123 } as Game;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("game metadata refresh service", () => {
  it("maps a provider preview without applying changes", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "updated",
          provider: "Steam",
          provider_errors: [],
          changed_fields: ["description", "developer"],
          skipped_locked_fields: ["publisher"],
          would_add_key_art: true,
          would_add_banner: false,
          game_updated_at: 123,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const preview = await previewGameMetadataRefresh(game);
    expect(preview.changedFields).toEqual(["description", "developer"]);
    expect(preview.skippedLockedFields).toEqual(["publisher"]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/game/game-1/metadata/refresh",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("confirms a title-only match with the previewed candidate's ids only", async () => {
    const fetchMock = vi.fn().mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            status: "preview",
            match: "title",
            candidate: { title: "Celeste", provider_ids: { igdb: "99" } },
            provider: "IGDB",
            provider_errors: [],
            changed_fields: ["description"],
            skipped_locked_fields: [],
            would_add_key_art: false,
            would_add_banner: false,
            game_updated_at: 123,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const preview = await previewGameMetadataRefresh(game);
    expect(preview.match).toBe("title");
    expect(preview.candidate).toEqual({
      title: "Celeste",
      providerIds: { igdb: "99" },
    });
    const previewBody = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(previewBody.confirm_candidate).toBeUndefined();

    await applyGameMetadataRefresh(game);
    expect(
      JSON.parse(fetchMock.mock.calls[1][1].body).confirm_candidate,
    ).toBeUndefined();

    await applyGameMetadataRefresh(
      game,
      undefined,
      preview.candidate?.providerIds,
    );
    const applyBody = JSON.parse(fetchMock.mock.calls[2][1].body);
    expect(applyBody.confirm_candidate).toEqual({ igdb: "99" });
    expect(applyBody.confirm_title_match).toBeUndefined();
  });

  it("surfaces a stale preview conflict instead of silently applying it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("Game changed after the metadata preview.", {
          status: 409,
        }),
      ),
    );
    await expect(applyGameMetadataRefresh(game)).rejects.toThrow("409");
  });
});
