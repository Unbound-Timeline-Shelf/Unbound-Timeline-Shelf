// Which sidebar folders (Games, Media) are open, shared so the top bars can
// avoid repeating what the sidebar already lists (UX-AUDIT-APP A19: one
// entry per destination). SidebarNav owns the value; the bars only read it.
import { ref } from "vue";
import { effectiveSidebarMode, navigationViewport } from "./sidebarMode";

export const expandedNavigationFolders = ref<ReadonlySet<string>>(new Set());

// True when the folder's pages are on screen in the sidebar: the full
// pinned pane on a desktop or tablet with that folder open. The icon rail,
// the overlay menu and the phone's bottom navigation hide them.
export function sidebarListsFolder(id: string): boolean {
  return (
    navigationViewport.value !== "phone" &&
    effectiveSidebarMode.value === "pinned" &&
    expandedNavigationFolders.value.has(id)
  );
}
