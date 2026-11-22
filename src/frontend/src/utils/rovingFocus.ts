// One Tab stop per group of tabs or toggle buttons (UX-AUDIT-APP A15): the
// selected item (or the first) is the group's only Tab stop and the arrow
// keys, Home and End move between the items. Activation stays with Enter or
// Space, so moving focus never changes a filter or a view by itself.

/** The index an arrow, Home or End key moves to, or null for other keys. */
export function rovingTarget(
  key: string,
  index: number,
  count: number,
): number | null {
  if (count <= 0) return null;
  const last = count - 1;
  switch (key) {
    case "ArrowRight":
    case "ArrowDown":
      return index >= last ? 0 : index + 1;
    case "ArrowLeft":
    case "ArrowUp":
      return index <= 0 ? last : index - 1;
    case "Home":
      return 0;
    case "End":
      return last;
    default:
      return null;
  }
}

/** tabindex for item `index` when `active` is the selected one (-1: none). */
export function rovingTabIndex(index: number, active: number): 0 | -1 {
  return index === (active < 0 ? 0 : active) ? 0 : -1;
}

/**
 * Keydown handler for a group: moves focus between the enabled items that
 * match `selector` inside the element the listener is on.
 */
export function onRovingKeydown(
  event: KeyboardEvent,
  selector = "[data-roving]",
): void {
  if (event.altKey || event.ctrlKey || event.metaKey) return;
  const group = event.currentTarget as HTMLElement | null;
  if (!group) return;
  const items = [...group.querySelectorAll<HTMLElement>(selector)].filter(
    (item) =>
      !item.hasAttribute("disabled") &&
      item.getAttribute("aria-disabled") !== "true",
  );
  const current = items.indexOf(
    (event.target as HTMLElement | null)?.closest?.(selector) as HTMLElement,
  );
  if (current < 0) return;
  const next = rovingTarget(event.key, current, items.length);
  if (next === null) return;
  event.preventDefault();
  // tabindex stays bound to the selection, so Tab back into the group
  // always lands on the selected item
  items[next]!.focus();
}
