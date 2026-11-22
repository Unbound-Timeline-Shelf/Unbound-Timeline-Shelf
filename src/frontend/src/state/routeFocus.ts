import { nextTick, ref } from "vue";
import { START_LOCATION, type Router } from "vue-router";
import { changesDestination } from "./scrollMemory";

// After a navigation to another page (or settings section), focus moves to
// that page's heading and its name is announced, so a keyboard or screen
// reader user starts at the new content instead of on <body> or back in the
// sidebar (UX-AUDIT-APP A15). A filter, search, sort, tab or dialog step on
// the same page never moves focus: typing in the library search would lose
// its caret otherwise (critic note on §3.3).

/** Text for the polite live region in App.vue. */
export const routeAnnouncement = ref("");

const HEADING = "#main-content h1";
const WAIT_MS = 3000;

// The next frame; a page or test environment torn down while a navigation
// settles has no requestAnimationFrame left, so fall back to a timer.
function nextFrame(callback: () => void): void {
  if (typeof requestAnimationFrame === "function")
    requestAnimationFrame(callback);
  else setTimeout(callback, 16);
}
const hasDocument = () => typeof document !== "undefined";

function visible(element: HTMLElement): boolean {
  return element.getClientRects().length > 0;
}

/** The current view's heading, once it has rendered. */
export function viewHeading(root: ParentNode = document): HTMLElement | null {
  const headings = [...root.querySelectorAll<HTMLElement>(HEADING)];
  return headings.find(visible) ?? null;
}

/** Focus without scrolling; headings become focusable only by script. */
export function focusHeading(heading: HTMLElement): void {
  if (!heading.hasAttribute("tabindex")) heading.setAttribute("tabindex", "-1");
  heading.dataset.routeFocus = "";
  heading.focus({ preventScroll: true });
}

// Focus is only moved when it is not already somewhere the person chose on
// the new page: still on <body>, on an element the navigation removed, or in
// the navigation that started it.
function focusIsFree(): boolean {
  const active = document.activeElement as HTMLElement | null;
  if (!active || active === document.body || !active.isConnected) return true;
  return !active.closest("#main-content") || active.id === "main-content";
}

function waitForHeading(signal: {
  cancelled: boolean;
}): Promise<HTMLElement | null> {
  const started = performance.now();
  return new Promise((resolve) => {
    const look = () => {
      if (signal.cancelled || !hasDocument()) return resolve(null);
      const heading = viewHeading();
      if (heading) return resolve(heading);
      if (performance.now() - started > WAIT_MS) return resolve(null);
      nextFrame(look);
    };
    look();
  });
}

let pending: { cancelled: boolean } | null = null;

export function installRouteFocus(router: Router): void {
  if (typeof window === "undefined") return;
  router.afterEach(async (to, from, failure) => {
    if (failure || from === START_LOCATION) return;
    if (to.path === from.path && !changesDestination(to, from)) return;
    if (pending) pending.cancelled = true;
    const signal = (pending = { cancelled: false });
    // the previous view is still on screen until the new one renders
    await nextTick();
    await new Promise<void>((resolve) => nextFrame(resolve));
    const heading = await waitForHeading(signal);
    if (signal.cancelled || !hasDocument()) return;
    pending = null;
    const name = heading?.textContent?.trim() || document.title;
    routeAnnouncement.value = "";
    // a fresh value, so the same title twice is announced twice
    nextFrame(() => (routeAnnouncement.value = name));
    if (heading && focusIsFree()) focusHeading(heading);
  });
}

/** The skip link's target: the page heading, or the content region. */
export function skipToContent(event: Event): void {
  const heading = viewHeading();
  const main = document.getElementById("main-content");
  const target = heading ?? main;
  if (!target) return;
  event.preventDefault();
  if (heading) focusHeading(heading);
  else main!.focus({ preventScroll: false });
  target.scrollIntoView({ block: "nearest" });
}
