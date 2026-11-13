// C5.5: a release date is shown as precisely as it is known, in the person's
// date format, lists sort a partial date as the start of its period, and a
// typed partial date is read in the person's order (SPEC-UI-POLISH-PASS §7).
import { afterEach, describe, expect, it } from "vitest";
import {
  compareReleaseDates,
  formatDisplayDate,
  formatPartialDate,
  parseEntry,
  placeholder,
} from "../utils/dates";
import type { DatePrefs, DateStyle } from "../utils/dates";
import { formatReleaseDate, newestRelease } from "../utils/releaseDate";
import { preferences } from "../state/preferences";
import { DEFAULT_PREFERENCES } from "../services/preferences";

const style = (date_format: DateStyle, chosen = false): DatePrefs => ({
  date_format,
  time_format: null,
  date_format_chosen: chosen,
});
const iso = style("iso");

afterEach(() => {
  preferences.value = { ...DEFAULT_PREFERENCES };
});

describe("formatPartialDate", () => {
  describe.each(["dmy", "long", "mdy"] as const)("in the %s style", (s) => {
    it("shows a year as the year alone, never its first day", () => {
      const shown = formatPartialDate("2011-01-01", "year", style(s));
      expect(shown).toBe("2011");
      expect(shown).not.toBe(formatDisplayDate("2011-01-01"));
    });

    it("shows a month as its name and year, without a day", () => {
      const shown = formatPartialDate("2011-04-01", "month", style(s));
      expect(shown).toBe("April 2011");
      expect(shown).not.toMatch(/(^|\D)1(\D|$)/);
    });
  });

  it("shows a day in each style", () => {
    expect(formatPartialDate("2011-04-19", null, style("dmy"))).toBe(
      "19/04/2011",
    );
    expect(formatPartialDate("2011-04-19", "day", style("long"))).toBe(
      "19 April 2011",
    );
    expect(formatPartialDate("2011-04-19", null, style("mdy"))).toBe(
      "04/19/2011",
    );
  });

  it("shows the stored value as written in the iso style, for each precision", () => {
    expect(formatPartialDate("2011-01-01", "year", iso)).toBe("2011");
    expect(formatPartialDate("2011-04-01", "month", iso)).toBe("2011-04");
    expect(formatPartialDate("2011-04-19", null, iso)).toBe("2011-04-19");
    expect(formatPartialDate("2011-04-19", "day", iso)).toBe("2011-04-19");
    // a partial value as Atlas writes it
    expect(formatPartialDate("2011-04", "month", iso)).toBe("2011-04");
  });

  it("reads the precision from a partial value's shape", () => {
    expect(formatPartialDate("2011", null, style("dmy"))).toBe("2011");
    expect(formatPartialDate("2011-04", null, style("dmy"))).toBe("April 2011");
  });

  it("is empty without a date", () => {
    expect(formatPartialDate(null, "year", style("dmy"))).toBe("");
    expect(formatPartialDate("", null, iso)).toBe("");
  });
});

describe("compareReleaseDates", () => {
  it("puts a year before a day of the same year, and a month between", () => {
    const dates = [
      { date: "2011-04-19", precision: null },
      { date: "2011-01-01", precision: "day" as const },
      { date: "2011-04-01", precision: "month" as const },
      { date: "2011-01-01", precision: "year" as const },
      { date: "2010-12-31", precision: null },
    ];
    expect(
      [...dates].sort(compareReleaseDates).map((d) => [d.date, d.precision]),
    ).toEqual([
      ["2010-12-31", null],
      ["2011-01-01", "year"],
      ["2011-01-01", "day"],
      ["2011-04-01", "month"],
      ["2011-04-19", null],
    ]);
  });
});

describe("formatReleaseDate", () => {
  it("follows the signed-in person's date preferences", () => {
    const game = {
      releaseDate: "2011-04-19",
      releaseDatePrecision: null,
    };
    preferences.value = { ...DEFAULT_PREFERENCES, date_format: "iso" };
    expect(formatReleaseDate(game)).toBe("2011-04-19");
    preferences.value = { ...DEFAULT_PREFERENCES, date_format: "mdy" };
    expect(formatReleaseDate(game)).toBe("04/19/2011");
    preferences.value = { ...DEFAULT_PREFERENCES };
    expect(formatReleaseDate(game)).toBe("19/04/2011");
    expect(
      formatReleaseDate({
        releaseDate: "2027-01-01",
        releaseDatePrecision: "year",
      }),
    ).toBe("2027");
  });
});

describe("newestRelease", () => {
  it("orders the library newest first, a day ahead of its own year, missing dates last", () => {
    const games = [
      { id: "none", releaseDate: null, releaseDatePrecision: null },
      {
        id: "year",
        releaseDate: "2011-01-01",
        releaseDatePrecision: "year" as const,
      },
      { id: "day", releaseDate: "2011-04-19", releaseDatePrecision: null },
      {
        id: "month",
        releaseDate: "2011-04-01",
        releaseDatePrecision: "month" as const,
      },
      { id: "older", releaseDate: "2010-06-01", releaseDatePrecision: null },
    ];
    expect([...games].sort(newestRelease).map((g) => g.id)).toEqual([
      "day",
      "month",
      "year",
      "older",
      "none",
    ]);
  });
});

describe("parseEntry", () => {
  const ok = (text: string, prefs: DatePrefs = style("dmy")) => {
    const result = parseEntry(text, prefs);
    if (result.kind !== "ok") throw new Error(`${text}: ${result.kind}`);
    return [result.value.text, result.value.precision, result.value.date];
  };

  it("reads ISO in any style, never ambiguous", () => {
    for (const s of ["dmy", "long", "mdy", "iso"] as const) {
      expect(ok("2011", style(s))).toEqual(["2011", "year", "2011-01-01"]);
      expect(ok("2011-04", style(s))).toEqual([
        "2011-04",
        "month",
        "2011-04-01",
      ]);
      expect(ok("2011-04-03", style(s))).toEqual([
        "2011-04-03",
        "day",
        "2011-04-03",
      ]);
    }
  });

  it("reads month names and their three-letter prefixes", () => {
    expect(ok("17 Sep 2020")).toEqual(["2020-09-17", "day", "2020-09-17"]);
    expect(ok("Sep 17, 2020")).toEqual(["2020-09-17", "day", "2020-09-17"]);
    expect(ok("September 2020")).toEqual(["2020-09", "month", "2020-09-01"]);
    expect(ok("17 september 2020", style("mdy"))[0]).toBe("2020-09-17");
    expect(parseEntry("17 Se 2020", style("dmy")).kind).toBe("error");
  });

  it("reads mm/yyyy as a month", () => {
    expect(ok("09/2020")).toEqual(["2020-09", "month", "2020-09-01"]);
    expect(ok("9/2020", style("mdy"))).toEqual([
      "2020-09",
      "month",
      "2020-09-01",
    ]);
  });

  it("reads a/b/yyyy in the person's order", () => {
    expect(ok("17/09/2020")[0]).toBe("2020-09-17");
    expect(ok("09/17/2020", style("mdy"))[0]).toBe("2020-09-17");
    expect(ok("17.09.2020", style("long"))[0]).toBe("2020-09-17");
    expect(ok("17-09-2020", style("iso"))[0]).toBe("2020-09-17");
  });

  it("takes the only valid reading when the other is impossible", () => {
    // 17 cannot be a month, so mdy still reads 17/09/2020 as 17 September
    expect(ok("17/09/2020", style("mdy"))[0]).toBe("2020-09-17");
    expect(ok("09/17/2020", style("dmy"))[0]).toBe("2020-09-17");
  });

  it("asks when both numbers could be the month and no format was chosen", () => {
    const result = parseEntry("03/04/2011", style("dmy"));
    expect(result.kind).toBe("ambiguous");
    if (result.kind !== "ambiguous") return;
    expect(result.options.map((o) => o.text)).toEqual([
      "2011-04-03",
      "2011-03-04",
    ]);
    const us = parseEntry("03/04/2011", style("mdy"));
    expect(us.kind === "ambiguous" && us.options[0].text).toBe("2011-03-04");
  });

  it("does not ask once the person chose their format, or when both numbers match", () => {
    expect(ok("03/04/2011", style("dmy", true))[0]).toBe("2011-04-03");
    expect(ok("03/04/2011", style("mdy", true))[0]).toBe("2011-03-04");
    expect(ok("04/04/2011")[0]).toBe("2011-04-04");
  });

  it("refuses impossible days, including 29 February outside a leap year", () => {
    expect(parseEntry("29/02/2023", style("dmy")).kind).toBe("error");
    expect(ok("29/02/2024")[0]).toBe("2024-02-29");
    expect(parseEntry("31/04/2011", style("dmy", true)).kind).toBe("error");
    expect(parseEntry("13/13/2011", style("dmy")).kind).toBe("error");
  });

  it("keeps a year or month release date within 1950-2100 (C5.1)", () => {
    expect(parseEntry("1949", style("dmy")).kind).toBe("error");
    expect(parseEntry("2101-05", style("dmy")).kind).toBe("error");
    expect(ok("1950")[0]).toBe("1950");
    // a day keeps the host's wider range
    expect(ok("1900-05-04")[0]).toBe("1900-05-04");
  });

  it("is empty for blank input and explains other input", () => {
    expect(parseEntry("  ", style("dmy")).kind).toBe("empty");
    const bad = parseEntry("next spring", style("mdy"));
    expect(bad.kind === "error" && bad.message).toBe(
      "Enter the date as mm/dd/yyyy, mm/yyyy or yyyy.",
    );
  });

  it("can require a full day", () => {
    const result = parseEntry("2011", style("dmy"), { partial: false });
    expect(result.kind).toBe("error");
  });
});

describe("placeholder", () => {
  it("shows the expected order for each style", () => {
    expect(placeholder(style("dmy"))).toBe("dd/mm/yyyy, mm/yyyy or yyyy");
    expect(placeholder(style("mdy"), false)).toBe("mm/dd/yyyy");
    expect(placeholder(style("iso"))).toBe("yyyy-mm-dd, yyyy-mm or yyyy");
    expect(placeholder(style("long"), false)).toBe("17 September 2020");
  });
});
