// A book's cover always comes through the server's own copy: a web address is
// rewritten to the local route, and anything else shows no cover at all.
import { describe, expect, it } from "vitest";
import { localBookCover } from "../utils/mediaImages";

describe("localBookCover", () => {
  it("rewrites a web address to the server's local copy", () => {
    const url = localBookCover("b1", "https://covers.test/1.jpg");
    expect(url).toMatch(/^\/api\/media-image\/book\/b1\/cover\?v=[0-9a-z]+$/);
  });

  it("shows nothing for an empty cover", () => {
    expect(localBookCover("b1", null)).toBeNull();
    expect(localBookCover("b1", "")).toBeNull();
    expect(localBookCover("b1", undefined)).toBeNull();
  });

  it.each([
    "data:image/png;base64,AAAA",
    "/uploads/cover.jpg",
    "covers/1.jpg",
    "javascript:alert(1)",
    "//covers.test/1.jpg",
  ])("never hands %s to the page", (value) => {
    expect(localBookCover("b1", value)).toBeNull();
  });
});
