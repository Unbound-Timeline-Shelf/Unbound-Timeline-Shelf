// Plugin connections as the host reports them. The host asks each plugin for its
// account status and runs the sync itself; the browser only renders this state.

import { providerRequestError } from "./apiError";
import { currentUser } from "../state/auth";

export interface PluginConnectionSync {
  available: boolean;
  unavailable_reason: string;
  running: boolean;
  paused: boolean;
  // idle | partial | completed | failed | cancelled | disabled | paused
  status: string;
  last_run_at: number | null;
  last_summary: string;
  // what the last run means (#466); absent from older hosts, see syncOutcome
  outcome?: PluginSyncOutcome;
  can_continue?: boolean;
  step_limit_reached?: boolean;
  autosync_enabled?: boolean;
  next_run_at?: number | null;
}

export type PluginSyncOutcome =
  | "running"
  | "completed"
  | "incomplete"
  | "rate_limited"
  | "paused"
  | "disconnected"
  | "disabled"
  | "cancelled"
  | "failed"
  | "idle"
  | "never";

/** The host's outcome word, or the same answer worked out from the status of an older host. */
export function syncOutcome(sync: PluginConnectionSync): PluginSyncOutcome {
  if (sync.outcome) return sync.outcome;
  if (sync.running) return "running";
  if (!sync.available) return "disconnected";
  if (sync.paused || sync.status === "paused") return "paused";
  if (sync.status === "partial") return "incomplete";
  if (["completed", "failed", "cancelled", "disabled"].includes(sync.status))
    return sync.status as PluginSyncOutcome;
  return sync.last_run_at ? "idle" : "never";
}

/** Saved work remains and a deliberate continuation may run now. */
export function canContinueSync(sync: PluginConnectionSync): boolean {
  return sync.can_continue ?? syncOutcome(sync) === "incomplete";
}

/** A short, honest line for a connection row; null when the time of the last run says it. */
export function syncOutcomeText(sync: PluginConnectionSync): string | null {
  switch (syncOutcome(sync)) {
    case "incomplete":
      return sync.step_limit_reached
        ? "More to sync: this run reached its step limit. Continue to sync the rest."
        : "More to sync. Continue to sync the rest now.";
    case "rate_limited":
      return "The service is limiting requests. Try again later.";
    case "paused":
      return "Paused in Background tasks.";
    case "disconnected":
      return sync.unavailable_reason || "Reconnect to sync.";
    case "disabled":
      return "Autosync was turned off.";
    case "cancelled":
      return "Last sync was cancelled. Saved progress was kept.";
    case "failed":
      return "Last sync failed";
    default:
      return null;
  }
}

export interface PluginConnectionState {
  plugin_id: string;
  connection_id: string;
  connected: boolean;
  display_name: string | null;
  error: boolean;
  // null when the connection declares no sync
  sync: PluginConnectionSync | null;
}

export function pluginConnectionKey(
  pluginId: string,
  connectionId: string,
): string {
  return `${pluginId}:${connectionId}`;
}

const connectionsUrl = "/api/connections/plugins";

const connectionRequests = new Map<
  string | undefined,
  Promise<PluginConnectionState[]>
>();

export function clearPluginConnectionStatuses(): void {
  connectionRequests.clear();
}

export function fetchPluginConnections(): Promise<PluginConnectionState[]> {
  const account = currentUser.value?.id;
  const pending = connectionRequests.get(account);
  if (pending) return pending;
  const request = (async () => {
    const response = await fetch(connectionsUrl, { credentials: "include" });
    if (!response.ok) throw await providerRequestError(response);
    const body = (await response.json()) as {
      connections: PluginConnectionState[];
    };
    return body.connections;
  })().finally(() => {
    if (connectionRequests.get(account) === request)
      connectionRequests.delete(account);
  });
  connectionRequests.set(account, request);
  return request;
}

export async function startPluginConnectionSync(
  pluginId: string,
  connectionId: string,
): Promise<PluginConnectionSync> {
  const response = await fetch(
    `${connectionsUrl}/${encodeURIComponent(pluginId)}/${encodeURIComponent(connectionId)}/sync`,
    { method: "POST", credentials: "include" },
  );
  if (!response.ok) throw await providerRequestError(response);
  return ((await response.json()) as { sync: PluginConnectionSync }).sync;
}
