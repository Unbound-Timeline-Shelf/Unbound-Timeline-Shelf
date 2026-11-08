# Custom achievements in the shared achievement list

Proposal for [#450](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/450). Not built; it records the
current split and the decision the maintainer needs to make before implementation.

## Current state

| | Imported / Atlas achievements | Custom (manual) achievements |
| --- | --- | --- |
| Table | `achievements` (one row per provider and external id) | `game_manual_achievements` (owned by the user, per game or per identity umbrella) |
| Written by | Library syncs and achievement refreshes | Only the user, through `/api/game-identity/.../manual-achievements` |
| UI | Achievements list on the game page: filters, sorting, pins and notes (kept in this browser, per game), and the edit dialog (`AchievementEditDialog.vue`: progress Synced / Unlocked / Locked, earned date, note) | Separate **Your achievements** panel (`GameManualTrackingPanel.vue`) |
| Fields | name, description, icon, locked icon, unlocked and date, hidden, global percent, tier, medal, points | title, description, icon, category, position, achieved and date, match key, origin |

Syncs never touch `game_manual_achievements`, which is the property the issue asks to keep.

## Proposal

1. **Keep the two tables.** A shared table would make every sync and re-key path responsible for not touching
   custom rows. The separate owner-scoped table already guarantees that, and the library export already carries
   custom rows on their own (`manual_achievements`).
2. **Merge them in the read model only.** The game page's achievement list reads one combined list in which
   each item carries `kind: "imported" | "custom"`. Filters, sorting, pinning and search work over both; custom
   rows are labelled "Custom".
3. **One edit dialog.** `AchievementEditDialog.vue` opens for both kinds. For custom rows it edits title,
   description, icon, category, earned and date and the note, and offers Delete; the Synced progress choice and
   fields only a provider can know (global percent, tier, medal) are not shown.
4. **Creating** happens from the same list ("Add custom achievement"), so the separate panel can be retired once
   the list covers it.

## Open questions for the maintainer

* Should custom achievements get `hidden` and `points`? Both need a migration on `game_manual_achievements`.
* Should custom rows count toward completion percentages and Mastered, or stay separate as today?
* Pins and notes are kept in the browser per game, keyed by achievement id. Custom ids are UUIDs from another
  table, so they cannot collide, but confirm whether pins and notes should move to the account before both kinds
  share them.

## Why it is not in the Phase 7 lane

The issue's first scope item is a product decision (share the list and dialog or not). The change also touches the
game page's achievement composable, the edit dialog, completion counting and the manual tracking panel, which are
being changed by other open work. It should land as its own focused PR after the decision above.
