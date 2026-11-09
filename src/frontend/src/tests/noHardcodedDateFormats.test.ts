// SPEC-UI-POLISH-PASS §6.6 (T-U1): every date the app shows goes through
// utils/dates.ts, so it follows the person's date format. This fails, naming
// file:line, as soon as a locale date call or a hand-built format appears
// anywhere else in src. Number formatting (`count.toLocaleString()`) is fine.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "..");
// the one formatter, and these tests (which build fixtures on purpose)
const EXEMPT = [/^utils[\\/]dates\.ts$/, /^tests[\\/]/];
// an explicit, reviewed exception on the same line
const ESCAPE = "date-format-ok";

const RULES: { name: string; pattern: RegExp }[] = [
  { name: "toLocaleDateString", pattern: /\.toLocaleDateString\(/ },
  { name: "toLocaleTimeString", pattern: /\.toLocaleTimeString\(/ },
  { name: "toDateString", pattern: /\.toDateString\(/ },
  {
    name: "Date#toLocaleString",
    pattern: /new Date\([^)]*\)\)?\.toLocaleString\(/,
  },
  {
    // a variable that reads as a date: d, date, when, dt, at, ...Date, ...At
    name: "Date#toLocaleString",
    pattern: /\b(?:d|date|dt|when|at|[a-z]\w*(?:Date|At))\.toLocaleString\(/,
  },
  { name: "Intl.DateTimeFormat", pattern: /\bIntl\.DateTimeFormat\b/ },
  // a hand-built display format such as `${d}/${m}/${y}` or "en-CA" tricks
  { name: "en-CA date trick", pattern: /["']en-CA["']/ },
  {
    name: "hand-built d/m/y",
    pattern:
      /\$\{[^}]*\}\/\$\{[^}]*\}\/\$\{[^}]*(?:year|Year|getFullYear)[^}]*\}/,
  },
];

interface GuardHit {
  file: string;
  line: number;
  rule: string;
  text: string;
}

function scanSource(file: string, source: string): GuardHit[] {
  const hits: GuardHit[] = [];
  source.split(/\r?\n/).forEach((text, index) => {
    if (text.includes(ESCAPE)) return;
    for (const rule of RULES)
      if (rule.pattern.test(text)) {
        hits.push({
          file,
          line: index + 1,
          rule: rule.name,
          text: text.trim(),
        });
        break;
      }
  });
  return hits;
}

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(ts|vue)$/.test(name) ? [path] : [];
  });
}

describe("no hardcoded date formats", () => {
  it("formats every date through utils/dates", () => {
    const files = sourceFiles(SRC).filter(
      (path) => !EXEMPT.some((rule) => rule.test(relative(SRC, path))),
    );
    // the scan must actually see the app, or it proves nothing
    expect(files.length).toBeGreaterThan(300);
    const hits = files.flatMap((path) =>
      scanSource(relative(SRC, path), readFileSync(path, "utf8")),
    );
    expect(
      hits.map((h) => `${h.file}:${h.line} ${h.rule}: ${h.text}`),
      "Use formatDay / formatInstant / formatTime from utils/dates instead",
    ).toEqual([]);
  });

  it("catches each way a date used to be formatted by hand", () => {
    const bad = [
      "new Date(x * 1000).toLocaleDateString()",
      "d.toLocaleTimeString([], { hour: 'numeric' })",
      "new Date(seconds * 1000).toLocaleString()",
      "return date.toLocaleString(undefined, { month: 'short' });",
      "const f = new Intl.DateTimeFormat(undefined, { month: 'short' });",
      "Intl.DateTimeFormat().resolvedOptions().timeZone",
      "const k = new Date(u.at).toDateString();",
      'date.value = new Date(x).toLocaleDateString("en-CA");',
      "label = `${day}/${month}/${d.getFullYear()}`;",
      "row.createdAt.toLocaleString()",
    ];
    for (const line of bad)
      expect(scanSource("x.ts", line), line).toHaveLength(1);
  });

  it("leaves number formatting alone", () => {
    const fine = [
      "{{ row.count.toLocaleString() }}",
      "`${n.toLocaleString()} words`",
      "(minutes / 60).toLocaleString(undefined, { maximumFractionDigits: 1 })",
      "total.value.toLocaleString()",
      "s.amount.toLocaleString()",
      "formatInstant(d, { time: false })",
    ];
    for (const line of fine) expect(scanSource("x.ts", line), line).toEqual([]);
  });

  it("honours an explicit reviewed escape on the same line", () => {
    expect(
      scanSource("x.ts", "d.toLocaleDateString() // date-format-ok: tested"),
    ).toEqual([]);
  });
});
