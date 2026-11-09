// A game's release date as precisely as it is known (CONTRACTS-v1 C5.5), in
// the signed-in person's date style, and the library's release-date order.
import { preferences } from "../state/preferences";
import type { Game, GameRelease } from "../types/game";
import {
  compareReleaseDates,
  formatPartialDate,
  type DateFormatPreferences,
} from "./dates";

type Released = Pick<Game, "releaseDate" | "releaseDatePrecision">;

export function formatReleaseDate(game: Released): string {
  return formatPartialDate(
    game.releaseDate,
    game.releaseDatePrecision,
    preferences.value,
  );
}

// The game page's "Released" fact, as precisely as the date is known ("2011",
// "April 2011" or the full day, C5.5), with the field its source label uses.
export function releaseFacts(
  game: Released,
): { label: string; value: string; field: string }[] {
  if (!game.releaseDate) return [];
  return [
    {
      label: "Released",
      value: formatReleaseDate(game),
      field: "release_date",
    },
  ];
}

// Newest first; a year-only "2011" counts from the start of its year, and a
// game without a release date sorts last.
export function newestRelease(a: Released, b: Released): number {
  if (!a.releaseDate || !b.releaseDate)
    return Number(!a.releaseDate) - Number(!b.releaseDate);
  return compareReleaseDates(
    { date: b.releaseDate, precision: b.releaseDatePrecision },
    { date: a.releaseDate, precision: a.releaseDatePrecision },
  );
}

const REGION_NAMES: Record<string, string> = {
  ww: "Worldwide",
  eu: "Europe",
  na: "North America",
  asia: "Asia",
  oce: "Oceania",
  latam: "Latin America",
};
const CATEGORY_NAMES: Record<string, string> = {
  early_access: "Early access",
  beta: "Beta",
  rerelease: "Re-release",
  port: "Port",
  demo: "Demo",
};

// A region code from the contract (C5): the named groups above, otherwise an
// ISO 3166-1 country, named in the viewer's language where the browser can.
export function regionName(code: string | null | undefined): string | null {
  if (!code) return null;
  const key = code.toLowerCase();
  if (REGION_NAMES[key]) return REGION_NAMES[key];
  try {
    const names = new Intl.DisplayNames(undefined, { type: "region" });
    return names.of(key.toUpperCase()) ?? key.toUpperCase();
  } catch {
    return key.toUpperCase();
  }
}

// Release platforms are free text from the providers (C5: up to 128
// characters), usually a short code such as "pc" or "ps4". The app keeps no
// platform list of its own, so the common codes are named here and anything
// else is made readable: separators become spaces, two-letter or numbered
// words are upper-cased ("pc_engine" is "PC Engine"), the rest capitalised.
// Text that already has capitals ("PlayStation 5") is shown as sent.
const PLATFORM_NAMES: Record<string, string> = {
  pc: "PC",
  windows: "Windows",
  win: "Windows",
  mac: "macOS",
  macos: "macOS",
  osx: "macOS",
  linux: "Linux",
  ios: "iOS",
  android: "Android",
  web: "Web",
  browser: "Web",
  stadia: "Stadia",
  ps1: "PlayStation",
  psx: "PlayStation",
  ps2: "PlayStation 2",
  ps3: "PlayStation 3",
  ps4: "PlayStation 4",
  ps5: "PlayStation 5",
  psp: "PSP",
  vita: "PS Vita",
  psvita: "PS Vita",
  ps_vita: "PS Vita",
  xbox: "Xbox",
  xbox360: "Xbox 360",
  xbox_360: "Xbox 360",
  xboxone: "Xbox One",
  xbox_one: "Xbox One",
  xbox_series: "Xbox Series X|S",
  xbox_series_x: "Xbox Series X|S",
  xbox_series_xs: "Xbox Series X|S",
  xsx: "Xbox Series X|S",
  switch: "Nintendo Switch",
  nintendo_switch: "Nintendo Switch",
  switch2: "Nintendo Switch 2",
  switch_2: "Nintendo Switch 2",
  nintendo_switch_2: "Nintendo Switch 2",
  wii: "Wii",
  wiiu: "Wii U",
  wii_u: "Wii U",
  "3ds": "Nintendo 3DS",
  n3ds: "Nintendo 3DS",
  ds: "Nintendo DS",
  nds: "Nintendo DS",
  gamecube: "GameCube",
  gc: "GameCube",
  n64: "Nintendo 64",
};

export function platformName(
  platform: string | null | undefined,
): string | null {
  const text = platform?.trim();
  if (!text) return null;
  const key = text.toLowerCase().replace(/[\s-]+/g, "_");
  if (PLATFORM_NAMES[key]) return PLATFORM_NAMES[key];
  if (/[A-Z]/.test(text)) return text;
  return text
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((word) =>
      word.length <= 2 || /\d/.test(word)
        ? word.toUpperCase()
        : word[0].toUpperCase() + word.slice(1),
    )
    .join(" ");
}

export interface ReleaseRow {
  key: string;
  // already formatted in the person's date style
  when: string;
  where: string;
  edition: string | null;
  // null for an ordinary full release
  category: string | null;
}

// The game's regional and edition releases, oldest first, each date no more
// precise than known (C5.5): "March 2015 · PC · Worldwide".
export function releaseRows(
  releases: readonly GameRelease[] | undefined,
  prefs: DateFormatPreferences = preferences.value,
): ReleaseRow[] {
  return [...(releases ?? [])]
    .filter((release) => !!release.date)
    .sort(compareReleaseDates)
    .map((release) => ({
      key: [
        release.platform,
        release.region,
        release.edition,
        release.category,
        release.date,
      ].join("|"),
      when: formatPartialDate(release.date, release.precision, prefs),
      where: [platformName(release.platform), regionName(release.region)]
        .filter(Boolean)
        .join(" · "),
      edition: release.edition,
      category: release.category
        ? (CATEGORY_NAMES[release.category] ?? null)
        : null,
    }));
}
