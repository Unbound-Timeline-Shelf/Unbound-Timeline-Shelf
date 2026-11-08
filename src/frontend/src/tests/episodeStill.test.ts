import { expect, it } from "vitest";
import { episodeStillUrl } from "../utils/episodeStill";

it("loads episode stills through the host image cache, never the stored address (#398)", () => {
  expect(
    episodeStillUrl("tv", "e1", "http://192.168.1.1/admin/still.jpg"),
  ).toBe("/api/media-image/tv/episodes/e1/still");
  expect(
    episodeStillUrl("anime", "a/b", "https://user:pw@cdn.example.com/x.jpg"),
  ).toBe("/api/media-image/anime/episodes/a%2Fb/still");
  expect(episodeStillUrl("tv", "e1", null)).toBeNull();
  expect(episodeStillUrl("tv", "e1", "")).toBeNull();
});
