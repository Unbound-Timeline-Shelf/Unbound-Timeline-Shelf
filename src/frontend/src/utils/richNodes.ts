import { formatInstant } from "./dates";
// The server renders Markdown to a tree of plain nodes; clients draw it with
// text interpolation only. This module re-checks every link before it is drawn
// so a bug on either side cannot turn content into a script or unsafe URL.

export interface RichNode {
  t: string;
  v?: string;
  c?: RichNode[];
  href?: string;
  level?: number;
}

export type SafeHref =
  { kind: "external"; href: string } | { kind: "route"; href: string };

const HTTPS_URL =
  /^https:\/\/[A-Za-z0-9.-]+\.[A-Za-z][A-Za-z0-9-]*(\/[^\s\\]*)?$/;
const APP_PATH = /^\/(?!\/)[A-Za-z0-9._~!$&'()*+,;=:@/?-]*$/;

export function safeHref(value: string | undefined | null): SafeHref | null {
  if (!value || value.length > 2048) return null;
  if (HTTPS_URL.test(value) && !value.includes("@")) {
    // a link into this app's own API is never an "external" link (#398)
    if (namesOwnApi(value)) return null;
    return { kind: "external", href: value };
  }
  if (
    APP_PATH.test(value) &&
    !value.split("?")[0]!.split("/").includes("..") &&
    !/^\/api(\/|$)/.test(value)
  ) {
    return { kind: "route", href: value };
  }
  return null;
}

// Hostnames, not hosts: browser cookies are not isolated by port. The path is
// decoded first, so `/%61pi/...` is refused like `/api/...`.
function namesOwnApi(value: string): boolean {
  if (typeof window === "undefined" || !window.location?.hostname) return false;
  try {
    const url = new URL(value);
    if (
      url.hostname.replace(/\.$/, "").toLowerCase() !==
      window.location.hostname.replace(/\.$/, "").toLowerCase()
    ) {
      return false;
    }
    return /^\/api(\/|$)/.test(decodeURIComponent(url.pathname));
  } catch {
    return true;
  }
}

export function headingTag(level: number | undefined): string {
  return `h${Math.min(Math.max(level ?? 3, 3), 5)}`;
}

export function formatStamp(seconds: number): string {
  return formatInstant(seconds);
}
