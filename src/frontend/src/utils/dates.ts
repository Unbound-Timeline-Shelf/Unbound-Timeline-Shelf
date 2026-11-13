// The one place the app turns a date into text (SPEC-UI-POLISH-PASS §6.5).
// Everything else calls formatDay / formatInstant and friends, so every date
// on every page follows the signed-in person's date-format preference:
//
//   dmy  (default)  17/09/2020      September 2020   2020   17/09/2020 14:05
//   long            17 September 2020                       17 September 2020, 14:05
//   mdy             09/17/2020                              09/17/2020 2:05 PM
//   iso             2020-09-17      2020-09                 2020-09-17 14:05
//
// Storage and the API stay ISO; only display changes. A date-only value
// ("2026-09-12", what <input type="date"> and the API's date fields use) is a
// calendar day, not an instant: `new Date("2026-09-12")` reads it as UTC
// midnight, which shows as the day before anywhere west of UTC, so day
// strings are read as local days here. noHardcodedDateFormats.test.ts fails
// when a locale date call appears anywhere else.
import { preferences } from "../state/preferences";

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;
const PARTIAL = /^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$/;

export type DateStyle = "dmy" | "long" | "mdy" | "iso";
export type TimeFormat = "24h" | "12h";
export const DATE_STYLES: readonly DateStyle[] = ["dmy", "long", "mdy", "iso"];
export const DEFAULT_DATE_STYLE: DateStyle = "dmy";

// How much of a release date is known (CONTRACTS-v1 C5). The API sends a year
// or a month as the first day of its period plus this precision; a day has no
// precision (null), as every date stored before partial dates existed.
export type DatePrecision = "year" | "month" | "day";

export interface DatePrefs {
  date_format: DateStyle;
  // null: 24h, except month/day/year which reads as 12h by default
  time_format: TimeFormat | null;
  // the person picked a format themselves (settings, the first-visit offer)
  date_format_chosen: boolean;
}

// Kept for the existing callers' signatures (releaseDate.ts and the forms).
export type DateFormatPreferences = DatePrefs;

export const DEFAULT_DATE_PREFS: DatePrefs = {
  date_format: DEFAULT_DATE_STYLE,
  time_format: null,
  date_format_chosen: false,
};

export const DATE_STYLE_LABELS: Record<DateStyle, string> = {
  dmy: "31/12/2026 (day/month/year)",
  long: "31 December 2026",
  mdy: "12/31/2026 (month/day/year)",
  iso: "2026-12-31 (ISO)",
};

function currentPrefs(prefs?: Partial<DatePrefs> | null): DatePrefs {
  const source = prefs ?? (preferences.value as Partial<DatePrefs>);
  const style = DATE_STYLES.includes(source?.date_format as DateStyle)
    ? (source!.date_format as DateStyle)
    : DEFAULT_DATE_STYLE;
  const time =
    source?.time_format === "24h" || source?.time_format === "12h"
      ? source.time_format
      : null;
  return {
    date_format: style,
    time_format: time,
    date_format_chosen: source?.date_format_chosen === true,
  };
}

// ---------------------------------------------------------------- names

const EN_MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];
const EN_WEEKDAYS = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

// Month and weekday names are English. The app has no interface translation,
// and typed entry (parseEntry) only understands English month names, so
// display and entry always agree. A translated interface must bring its own
// name tables here, including the genitive month forms that languages such as
// Russian, Polish and Czech use inside a date ("17 января 2020").

// month 1-12
export function monthName(
  month: number,
  width: "long" | "short" = "long",
): string {
  const name = EN_MONTHS[month - 1] ?? "";
  return width === "short" ? name.slice(0, 3) : name;
}

export function formatWeekday(
  date: Date,
  width: "long" | "short" = "short",
): string {
  const name = EN_WEEKDAYS[date.getDay()] ?? "";
  return width === "short" ? name.slice(0, 3) : name;
}

// ---------------------------------------------------------------- days

const pad2 = (n: number) => String(n).padStart(2, "0");
// Years are padded to four digits by hand: toISOString-style output for
// year 999 would otherwise drop the zero.
const pad4 = (n: number) => String(n).padStart(4, "0");

interface DayParts {
  year: number;
  month: number; // 1-12
  day: number;
}

function dayText(parts: DayParts, style: DateStyle): string {
  const { year, month, day } = parts;
  switch (style) {
    case "iso":
      return `${pad4(year)}-${pad2(month)}-${pad2(day)}`;
    case "mdy":
      return `${pad2(month)}/${pad2(day)}/${pad4(year)}`;
    case "long":
      return `${day} ${monthName(month)} ${pad4(year)}`;
    default:
      return `${pad2(day)}/${pad2(month)}/${pad4(year)}`;
  }
}

function monthText(year: number, month: number, style: DateStyle): string {
  return style === "iso"
    ? `${pad4(year)}-${pad2(month)}`
    : `${monthName(month)} ${pad4(year)}`;
}

// A month on its own, for charts and calendar headers: "September 2020"
// ("2020-09" in ISO).
export function formatMonthYear(
  year: number,
  month: number,
  prefs?: Partial<DatePrefs> | null,
): string {
  return monthText(year, month, currentPrefs(prefs).date_format);
}

// A day without its year, for calendar columns and short labels: "17/09",
// "09/17", "17 September"; ISO keeps the full day.
export function formatDayMonth(
  date: Date,
  prefs?: Partial<DatePrefs> | null,
): string {
  const style = currentPrefs(prefs).date_format;
  const day = date.getDate();
  const month = date.getMonth() + 1;
  if (style === "iso")
    return dayText({ year: date.getFullYear(), month, day }, "iso");
  if (style === "mdy") return `${pad2(month)}/${pad2(day)}`;
  if (style === "long") return `${day} ${monthName(month)}`;
  return `${pad2(day)}/${pad2(month)}`;
}

// For display: date-only strings become that local calendar day, anything
// else (a full ISO timestamp) is parsed as usual.
export function parseDisplayDate(value: string): Date {
  const match = DATE_ONLY.exec(value);
  if (!match) return new Date(value);
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

// A calendar day ("2020-09-17", or a partial "2020" / "2020-09"), never more
// precise than known: a year is "2020", never "1 January 2020". `precision`
// is the API's release-date precision; null is a day. A full timestamp is
// shown as the local day it falls on.
export function formatDay(
  value: string | null | undefined,
  precision?: DatePrecision | null,
  prefs?: Partial<DatePrefs> | null,
): string {
  if (!value) return "";
  const style = currentPrefs(prefs).date_format;
  const match = PARTIAL.exec(value);
  if (!match) return formatInstant(value, { prefs, time: false });
  const known: DatePrecision =
    precision === "year" || !match[2]
      ? "year"
      : precision === "month" || !match[3]
        ? "month"
        : "day";
  const year = Number(match[1]);
  if (known === "year") return pad4(year);
  const month = Number(match[2]);
  if (known === "month") return monthText(year, month, style);
  return dayText({ year, month, day: Number(match[3]) }, style);
}

// ---------------------------------------------------------------- instants

export type InstantInput = number | string | Date | null | undefined;

// unix seconds, an ISO string or a Date
export function toDate(value: InstantInput): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const date =
    value instanceof Date
      ? value
      : typeof value === "number"
        ? new Date(value * 1000)
        : parseDisplayDate(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

interface InstantParts extends DayParts {
  hour: number;
  minute: number;
  second: number;
  weekday: number;
}

const zoneFormats = new Map<string, Intl.DateTimeFormat>();
function partsIn(date: Date, timeZone?: string): InstantParts {
  if (!timeZone)
    return {
      year: date.getFullYear(),
      month: date.getMonth() + 1,
      day: date.getDate(),
      hour: date.getHours(),
      minute: date.getMinutes(),
      second: date.getSeconds(),
      weekday: date.getDay(),
    };
  if (timeZone === "UTC")
    return {
      year: date.getUTCFullYear(),
      month: date.getUTCMonth() + 1,
      day: date.getUTCDate(),
      hour: date.getUTCHours(),
      minute: date.getUTCMinutes(),
      second: date.getUTCSeconds(),
      weekday: date.getUTCDay(),
    };
  let format = zoneFormats.get(timeZone);
  if (!format) {
    format = new Intl.DateTimeFormat("en-US", {
      timeZone,
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
      weekday: "short",
      hourCycle: "h23",
    });
    zoneFormats.set(timeZone, format);
  }
  const got: Record<string, string> = {};
  for (const part of format.formatToParts(date)) got[part.type] = part.value;
  return {
    year: Number(got.year),
    month: Number(got.month),
    day: Number(got.day),
    hour: Number(got.hour) % 24,
    minute: Number(got.minute),
    second: Number(got.second),
    weekday: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(
      got.weekday ?? "",
    ),
  };
}

function clockText(
  parts: { hour: number; minute: number; second: number },
  prefs: DatePrefs,
  seconds: boolean,
): string {
  const twelve =
    prefs.time_format === "12h" ||
    (prefs.time_format === null && prefs.date_format === "mdy");
  const tail = `${pad2(parts.minute)}${seconds ? `:${pad2(parts.second)}` : ""}`;
  if (!twelve) return `${pad2(parts.hour)}:${tail}`;
  const hour = parts.hour % 12 || 12;
  return `${hour}:${tail} ${parts.hour < 12 ? "AM" : "PM"}`;
}

function zoneName(date: Date, timeZone?: string): string {
  try {
    const part = new Intl.DateTimeFormat("en-US", {
      timeZone,
      timeZoneName: "short",
    })
      .formatToParts(date)
      .find((p) => p.type === "timeZoneName");
    return part?.value ?? "";
  } catch {
    return "";
  }
}

export interface InstantOptions {
  prefs?: Partial<DatePrefs> | null;
  // show the time of day (default true)
  time?: boolean;
  // show the date (default true); time: true, date: false is a clock
  date?: boolean;
  seconds?: boolean;
  // "just now", "5 min ago", "3 h ago", "yesterday", then the absolute form
  relative?: boolean;
  // prefix the weekday: "Mon 17/09/2020"
  weekday?: "short" | "long";
  // append the zone's short name: "14:05 GMT+1"
  zoneName?: boolean;
  // format in this IANA zone instead of the device's
  timeZone?: string;
  now?: Date;
}

// A moment in time (unix seconds, ISO string or Date) in the person's date
// style, in the device's time zone unless `timeZone` says otherwise.
export function formatInstant(
  value: InstantInput,
  options: InstantOptions = {},
): string {
  const date = toDate(value);
  if (!date) return "";
  const prefs = currentPrefs(options.prefs);
  if (options.relative) {
    const now = options.now ?? new Date();
    const ago = Math.floor((now.getTime() - date.getTime()) / 1000);
    const key = (d: Date) => {
      const p = partsIn(d, options.timeZone);
      return Date.UTC(p.year, p.month - 1, p.day);
    };
    const daysAgo = Math.round((key(now) - key(date)) / 86_400_000);
    if (ago >= 0 && ago < 60) return "just now";
    if (ago >= 60 && ago < 3600) return `${Math.floor(ago / 60)} min ago`;
    if (ago >= 3600 && ago < 86_400 && daysAgo === 0)
      return `${Math.floor(ago / 3600)} h ago`;
    if (ago > 0 && daysAgo === 1) return "yesterday";
  }
  const parts = partsIn(date, options.timeZone);
  const showDate = options.date !== false;
  const showTime = options.time !== false;
  let text = showDate ? dayText(parts, prefs.date_format) : "";
  if (showDate && options.weekday) {
    const weekday = new Date(2021, 0, 3 + parts.weekday);
    text = `${formatWeekday(weekday, options.weekday)} ${text}`;
  }
  if (showTime) {
    const clock = clockText(parts, prefs, options.seconds === true);
    text = !showDate
      ? clock
      : prefs.date_format === "long"
        ? `${text}, ${clock}`
        : `${text} ${clock}`;
  }
  if (options.zoneName) {
    const zone = zoneName(date, options.timeZone);
    if (zone) text = `${text} ${zone}`;
  }
  return text;
}

// Just the time of day: "14:05" or "2:05 PM".
export function formatTime(
  value: InstantInput,
  options: Omit<InstantOptions, "date" | "time"> = {},
): string {
  return formatInstant(value, { ...options, date: false, time: true });
}

// Kept for existing callers: a day string or timestamp as a day. The old
// Intl options are ignored on purpose; the person's style decides.
export function formatDisplayDate(value: string | null | undefined): string {
  return formatDay(value, null);
}

// A release date for display, never more precise than what is known.
export function formatPartialDate(
  value: string | null | undefined,
  precision: DatePrecision | null | undefined,
  prefs?: Partial<DatePrefs> | null,
): string {
  return formatDay(value, precision ?? null, prefs);
}

// ---------------------------------------------------------------- keys

// "YYYY-MM-DD" of the local calendar day a Date falls on: a grouping key,
// never display text.
export function dayKey(date: Date): string {
  return `${pad4(date.getFullYear())}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

// The device's IANA time zone ("Europe/London"), or "UTC" when unknown.
export function deviceTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

// "YYYY-MM-DD" of the local calendar day an instant falls on, the value an
// <input type="date"> needs. With `timeZone`, the day in that zone.
export function toLocalDateInput(
  value: string | Date | null | undefined,
  timeZone?: string,
): string {
  if (!value) return "";
  if (typeof value === "string" && DATE_ONLY.test(value)) return value;
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "";
  if (!timeZone) return dayKey(date);
  const parts = partsIn(date, timeZone);
  return `${pad4(parts.year)}-${pad2(parts.month)}-${pad2(parts.day)}`;
}

// Unix seconds at local midnight of a "YYYY-MM-DD" day, so it reads back
// (toLocalDateInput / formatDay) as the same day that was picked.
export function localDateInputToUnixSeconds(value: string): number | null {
  const match = DATE_ONLY.exec(value);
  if (!match) return null;
  const date = new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
  );
  return Math.floor(date.getTime() / 1000);
}

/** Orders newest first; a missing date sorts last. Accepts ISO strings or unix seconds. */
export function newest(
  a: string | number | null | undefined,
  b: string | number | null | undefined,
): number {
  const time = (v: string | number | null | undefined) =>
    typeof v === "number" ? v * 1000 : Date.parse(v ?? "") || 0;
  return time(b) - time(a);
}

const PRECISION_RANK: Record<DatePrecision, number> = {
  year: 0,
  month: 1,
  day: 2,
};

// Orders release dates oldest first by the start of their period, and a less
// precise date first on the same start: "2011" before "April 2011" before
// "1 April 2011" (C5.5). A missing precision is a day.
export function compareReleaseDates(
  a: { date: string; precision?: DatePrecision | null },
  b: { date: string; precision?: DatePrecision | null },
): number {
  return (
    a.date.slice(0, 10).localeCompare(b.date.slice(0, 10)) ||
    PRECISION_RANK[a.precision ?? "day"] - PRECISION_RANK[b.precision ?? "day"]
  );
}

// ---------------------------------------------------------------- entry

// What a date field expects, in the person's order (§7.1).
export function placeholder(
  prefs?: Partial<DatePrefs> | null,
  partial = true,
): string {
  const style = currentPrefs(prefs).date_format;
  const day = {
    dmy: "dd/mm/yyyy",
    long: "17 September 2020",
    mdy: "mm/dd/yyyy",
    iso: "yyyy-mm-dd",
  }[style];
  if (!partial) return day;
  return {
    dmy: "dd/mm/yyyy, mm/yyyy or yyyy",
    long: "17 September 2020, September 2020 or 2020",
    mdy: "mm/dd/yyyy, mm/yyyy or yyyy",
    iso: "yyyy-mm-dd, yyyy-mm or yyyy",
  }[style];
}

export interface PartialValue {
  // "YYYY", "YYYY-MM" or "YYYY-MM-DD"
  text: string;
  // the first day of the period, what the API's date field takes
  date: string;
  precision: DatePrecision;
}

export type EntryResult =
  | { kind: "empty" }
  | { kind: "ok"; value: PartialValue }
  | { kind: "ambiguous"; options: [PartialValue, PartialValue] }
  | { kind: "error"; message: string };

export interface EntryOptions {
  // a year or month alone is allowed (release dates); false: a full day only
  partial?: boolean;
  // C5.1: a year or month release date is 1950-2100
  partialYears?: [number, number];
  dayYears?: [number, number];
}

function daysIn(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function monthFromName(word: string): number | null {
  const key = word.toLowerCase().replace(/\.$/, "");
  if (key.length < 3) return null;
  const index = EN_MONTHS.findIndex((m) => m.toLowerCase().startsWith(key));
  if (
    index >= 0 &&
    (key.length === 3 || EN_MONTHS[index]!.toLowerCase() === key)
  )
    return index + 1;
  return null;
}

function build(
  year: number,
  month: number | null,
  day: number | null,
  options: Required<EntryOptions>,
): PartialValue | string {
  const precision: DatePrecision =
    month === null ? "year" : day === null ? "month" : "day";
  const [min, max] =
    precision === "day" ? options.dayYears : options.partialYears;
  if (year < min || year > max) return `Use a year from ${min} to ${max}.`;
  if (month !== null && (month < 1 || month > 12))
    return "That month does not exist.";
  if (day !== null && month !== null && (day < 1 || day > daysIn(year, month)))
    return `${monthName(month)} ${year} has no day ${day}.`;
  const text =
    pad4(year) +
    (month !== null ? `-${pad2(month)}` : "") +
    (day !== null ? `-${pad2(day)}` : "");
  return {
    text,
    date: `${pad4(year)}-${pad2(month ?? 1)}-${pad2(day ?? 1)}`,
    precision,
  };
}

// Reads what someone typed into a date field (§7.2). ISO, month names and
// "mm/yyyy" are never ambiguous; "a/b/yyyy" is read in the person's order,
// and is ambiguous when both numbers could be the month and the person never
// chose a format, so the field asks which one was meant.
export function parseEntry(
  input: string,
  prefs?: Partial<DatePrefs> | null,
  entryOptions: EntryOptions = {},
): EntryResult {
  const options: Required<EntryOptions> = {
    partial: entryOptions.partial ?? true,
    partialYears: entryOptions.partialYears ?? [1950, 2100],
    dayYears: entryOptions.dayYears ?? [1800, 3000],
  };
  const text = input.trim().replace(/\s+/g, " ").replace(/,/g, "");
  if (!text) return { kind: "empty" };
  const p = currentPrefs(prefs);
  const done = (built: PartialValue | string): EntryResult => {
    if (typeof built === "string") return { kind: "error", message: built };
    if (!options.partial && built.precision !== "day")
      return { kind: "error", message: "Enter the full day." };
    return { kind: "ok", value: built };
  };
  let m: RegExpExecArray | null;
  if ((m = /^(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?$/.exec(text)))
    return done(
      build(
        Number(m[1]),
        m[2] ? Number(m[2]) : null,
        m[3] ? Number(m[3]) : null,
        options,
      ),
    );
  // "17 Sep 2020", "Sep 17 2020", "September 2020"
  if ((m = /^(\d{1,2}) ([A-Za-z.]+) (\d{4})$/.exec(text))) {
    const month = monthFromName(m[2]!);
    if (month) return done(build(Number(m[3]), month, Number(m[1]), options));
  }
  if ((m = /^([A-Za-z.]+) (\d{1,2}) (\d{4})$/.exec(text))) {
    const month = monthFromName(m[1]!);
    if (month) return done(build(Number(m[3]), month, Number(m[2]), options));
  }
  if ((m = /^([A-Za-z.]+) (\d{4})$/.exec(text))) {
    const month = monthFromName(m[1]!);
    if (month) return done(build(Number(m[2]), month, null, options));
  }
  if ((m = /^(\d{1,2})[/.-](\d{4})$/.exec(text)))
    return done(build(Number(m[2]), Number(m[1]), null, options));
  if ((m = /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$/.exec(text))) {
    const a = Number(m[1]);
    const b = Number(m[2]);
    const year = Number(m[3]);
    const monthFirst = p.date_format === "mdy";
    const preferred = monthFirst
      ? build(year, a, b, options)
      : build(year, b, a, options);
    const other = monthFirst
      ? build(year, b, a, options)
      : build(year, a, b, options);
    if (typeof preferred !== "string" && typeof other !== "string") {
      if (a === b || p.date_format_chosen) return done(preferred);
      return { kind: "ambiguous", options: [preferred, other] };
    }
    if (typeof preferred !== "string") return done(preferred);
    if (typeof other !== "string") return done(other);
    return done(preferred);
  }
  return {
    kind: "error",
    message: `Enter the date as ${placeholder(p, options.partial)}.`,
  };
}

// What a form sends for a release date: the first day of the period and how
// much of it is known. A partial "YYYY" / "YYYY-MM" (from a provider's
// suggestion) becomes that period's first day.
export function releaseInput(
  value: string | null | undefined,
  precision: DatePrecision | null | undefined,
): { releaseDate: string | null; releaseDatePrecision?: DatePrecision } {
  const match = value ? PARTIAL.exec(value) : null;
  if (!match) return { releaseDate: null };
  if (!match[2])
    return { releaseDate: `${match[1]}-01-01`, releaseDatePrecision: "year" };
  if (!match[3])
    return {
      releaseDate: `${match[1]}-${match[2]}-01`,
      releaseDatePrecision: "month",
    };
  return { releaseDate: value!, releaseDatePrecision: precision ?? "day" };
}

// ---------------------------------------------------------------- offer

// US time zones (SPEC-UI-POLISH-PASS §7.4). Not every America/* zone: Canada,
// Mexico and Brazil write the day first, and they must not see the offer.
const US_ZONES = new Set([
  "America/New_York",
  "America/Detroit",
  "America/Chicago",
  "America/Menominee",
  "America/Denver",
  "America/Boise",
  "America/Phoenix",
  "America/Los_Angeles",
  "America/Anchorage",
  "America/Juneau",
  "America/Sitka",
  "America/Metlakatla",
  "America/Yakutat",
  "America/Nome",
  "America/Adak",
  "Pacific/Honolulu",
  "America/Puerto_Rico",
]);
const US_ZONE_PREFIXES = [
  "America/Kentucky/",
  "America/Indiana/",
  "America/North_Dakota/",
];

export function isUsTimeZone(zone: string): boolean {
  return US_ZONES.has(zone) || US_ZONE_PREFIXES.some((p) => zone.startsWith(p));
}

// Whether this browser looks American: its first language is en-US, or the
// device is in a US time zone.
export function looksAmerican(
  language: string | undefined = typeof navigator === "undefined"
    ? undefined
    : (navigator.languages?.[0] ?? navigator.language),
  zone: string = deviceTimeZone(),
): boolean {
  return /^en-US$/i.test(language ?? "") || isUsTimeZone(zone);
}

// Whether to show the one-time date-format offer: only to a browser that
// looks American, only once (any answer sets date_format_offered), never to
// someone who already picked a format, and not on top of the welcome dialog
// or the quick tour (the welcome dialog's "Save & take a tour" starts the tour
// the moment it closes; the offer waits until the tour ends). `hidden` is the
// offer being closed this session even though saving that failed.
export function shouldOfferDateFormat(
  prefs: {
    date_format_offered?: boolean;
    date_format_chosen?: boolean;
    ui_welcome_completed?: boolean;
  },
  context: {
    american?: () => boolean;
    tourActive?: boolean;
    hidden?: boolean;
  } = {},
): boolean {
  if (prefs.date_format_offered || prefs.date_format_chosen) return false;
  if (prefs.ui_welcome_completed === false) return false;
  if (context.tourActive || context.hidden) return false;
  return (context.american ?? (() => looksAmerican()))();
}
