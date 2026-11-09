// SPEC-UI-POLISH-PASS §6.7: the shared date vectors, which the backend's
// core/date_display.py also reads, run through the one formatter.
import { afterEach, describe, expect, it, vi } from "vitest";
import vectors from "./fixtures/date-format-vectors.json";
import {
  DEFAULT_DATE_PREFS,
  formatDay,
  formatDayMonth,
  formatInstant,
  formatMonthYear,
  formatTime,
  formatWeekday,
  monthName,
  placeholder,
  type DatePrecision,
  type DatePrefs,
  type DateStyle,
  type TimeFormat,
} from "../utils/dates";
import { preferences } from "../state/preferences";
import { DEFAULT_PREFERENCES } from "../services/preferences";

const prefs = (
  style: string,
  time: string | null = null,
  chosen = false,
): DatePrefs => ({
  date_format: style as DateStyle,
  time_format: time as TimeFormat | null,
  date_format_chosen: chosen,
});

afterEach(() => {
  preferences.value = { ...DEFAULT_PREFERENCES };
});

describe("shared vectors", () => {
  it("is version 1 of the shared file", () => {
    expect(vectors.version).toBe(1);
  });

  it.each(vectors.days)(
    "$style day $input ($precision) is $expected",
    ({ style, input, precision, expected }) => {
      expect(
        formatDay(input, precision as DatePrecision | null, prefs(style)),
      ).toBe(expected);
    },
  );

  it.each(vectors.instants)(
    "$style/$time_format instant $iso is $expected",
    ({ style, time_format, unix, iso, expected }) => {
      const p = prefs(style, time_format);
      expect(formatInstant(unix, { prefs: p, timeZone: "UTC" })).toBe(expected);
      expect(formatInstant(iso, { prefs: p, timeZone: "UTC" })).toBe(expected);
    },
  );

  it.each(vectors.placeholders)(
    "$style placeholder (partial $partial) is $expected",
    ({ style, partial, expected }) => {
      expect(placeholder(prefs(style), partial)).toBe(expected);
    },
  );

  it.each(vectors.relative)(
    "$ago seconds ago reads $expected",
    ({ ago, expected }) => {
      const now = new Date(vectors.relative_now);
      const at = vectors.relative_now_unix - ago;
      expect(
        formatInstant(at, {
          prefs: prefs("dmy"),
          relative: true,
          now,
          timeZone: "UTC",
        }),
      ).toBe(expected);
    },
  );
});

describe("the default", () => {
  it("is day/month/year for everyone who has not chosen", () => {
    expect(DEFAULT_PREFERENCES.date_format).toBe("dmy");
    expect(DEFAULT_DATE_PREFS.date_format).toBe("dmy");
    expect(formatDay("2020-09-17")).toBe("17/09/2020");
  });

  it("follows the shared preferences when no prefs are passed", () => {
    preferences.value = { ...DEFAULT_PREFERENCES, date_format: "mdy" };
    expect(formatDay("2020-09-17")).toBe("09/17/2020");
    preferences.value = { ...DEFAULT_PREFERENCES, date_format: "long" };
    expect(formatDay("2020-09-17")).toBe("17 September 2020");
    preferences.value = { ...DEFAULT_PREFERENCES, date_format: "iso" };
    expect(formatDay("2020-09-17")).toBe("2020-09-17");
  });

  it("treats an unknown stored style as the default", () => {
    expect(formatDay("2020-09-17", null, prefs("locale"))).toBe("17/09/2020");
  });
});

describe("helpers", () => {
  it("names months and weekdays in English while the app is English", () => {
    expect(monthName(9)).toBe("September");
    expect(monthName(9, "short")).toBe("Sep");
    expect(formatWeekday(new Date(2020, 8, 17), "long")).toBe("Thursday");
    expect(formatWeekday(new Date(2020, 8, 17))).toBe("Thu");
  });

  it("keeps English names whatever the document language", () => {
    // typed entry only reads English month names; display must agree
    vi.stubGlobal("document", { documentElement: { lang: "ru" } });
    try {
      expect(monthName(1)).toBe("January");
      expect(formatWeekday(new Date(2020, 8, 17), "long")).toBe("Thursday");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("formats months, short days and clocks in the person's style", () => {
    expect(formatMonthYear(2020, 9, prefs("dmy"))).toBe("September 2020");
    expect(formatMonthYear(2020, 9, prefs("iso"))).toBe("2020-09");
    const day = new Date(2020, 8, 7, 14, 5);
    expect(formatDayMonth(day, prefs("dmy"))).toBe("07/09");
    expect(formatDayMonth(day, prefs("mdy"))).toBe("09/07");
    expect(formatDayMonth(day, prefs("long"))).toBe("7 September");
    expect(formatDayMonth(day, prefs("iso"))).toBe("2020-09-07");
    expect(formatTime(day, { prefs: prefs("dmy") })).toBe("14:05");
    expect(formatTime(day, { prefs: prefs("mdy") })).toBe("2:05 PM");
    expect(formatTime(day, { prefs: prefs("mdy", "24h") })).toBe("14:05");
    expect(formatInstant(day, { prefs: prefs("dmy"), time: false })).toBe(
      "07/09/2020",
    );
    expect(formatInstant(day, { prefs: prefs("dmy"), weekday: "short" })).toBe(
      "Mon 07/09/2020 14:05",
    );
  });

  it("returns empty text for missing or invalid values", () => {
    expect(formatDay(null)).toBe("");
    expect(formatInstant(undefined)).toBe("");
    expect(formatInstant("not a date")).toBe("");
  });

  it("formats in another zone when asked", () => {
    // 23:30 UTC is already the next day in Tokyo
    const at = Date.UTC(2020, 8, 17, 23, 30) / 1000;
    expect(
      formatInstant(at, { prefs: prefs("iso"), timeZone: "Asia/Tokyo" }),
    ).toBe("2020-09-18 08:30");
  });
});
