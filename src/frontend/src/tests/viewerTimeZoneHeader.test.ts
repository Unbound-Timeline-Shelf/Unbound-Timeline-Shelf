import { describe, expect, it, vi } from "vitest";
import {
  TIME_ZONE_HEADER,
  installViewerTimeZoneHeader,
} from "../services/viewerTimeZoneHeader";

function setup() {
  const real = vi.fn<
    (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
  >(async () => new Response("{}"));
  const target = {
    fetch: real as unknown as typeof fetch,
    location: { origin: "https://tracker.example" } as Location,
  };
  installViewerTimeZoneHeader(target, () => "Australia/Perth");
  const sent = (call = 0) =>
    new Headers(real.mock.calls[call]?.[1]?.headers).get(TIME_ZONE_HEADER);
  return { target, real, sent };
}

describe("device time zone on the app's own API requests (#473)", () => {
  it("adds the device zone to same-origin /api requests and keeps their headers", async () => {
    const { target, real, sent } = setup();
    await target.fetch("/api/tv/1/seasons/2", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    expect(sent()).toBe("Australia/Perth");
    const init = real.mock.calls[0]?.[1];
    expect(new Headers(init?.headers).get("Content-Type")).toBe(
      "application/json",
    );
    expect(init?.method).toBe("PATCH");
    expect(init?.body).toBe("{}");
  });

  it("covers absolute URLs and Request objects on this origin", async () => {
    const { target, sent } = setup();
    await target.fetch(new URL("https://tracker.example/api/rewatches"));
    await target.fetch(
      new Request("https://tracker.example/api/movie/update/1", {
        method: "PATCH",
        headers: { Authorization: "Bearer x" },
      }),
    );
    expect(sent(0)).toBe("Australia/Perth");
    expect(sent(1)).toBe("Australia/Perth");
  });

  it("never sends the zone to other origins or non-API paths", async () => {
    const { target, real } = setup();
    await target.fetch("https://other.example/api/x");
    await target.fetch("/assets/app.js");
    await target.fetch("not a url");
    for (const call of real.mock.calls) {
      expect(call[1]).toBeUndefined();
    }
  });

  it("keeps a zone the caller chose", async () => {
    const { target, sent } = setup();
    await target.fetch("/api/rewatches", {
      headers: { [TIME_ZONE_HEADER]: "Europe/London" },
    });
    expect(sent()).toBe("Europe/London");
  });
});
