# Selective Home improvements

Decision record for [#474](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/474) and the focused
Home proposals [#475](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/475),
[#476](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/476),
[#477](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/477),
[#478](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/478) and
[#480](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/480). Only
[#479](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/479) (explicit refresh and last successful
update) is implemented; everything else on this page is a proposal and does not authorize implementation.

## What Home loads today

Measured from the code in `HomeHub.vue` and `services/homeWidgets.ts`, not from a large-library timing run:

| Selected widgets | Requests on first open | Size grows with |
| --- | --- | --- |
| None (quiet Home) | Preferences only | Nothing |
| Any of Continue playing, Recently added, Library summary, Random picker, Revisit your backlog, On this day, Getting started, a collection shelf | `GET /api/game/list` in pages of 1000 until the whole library is read, then grouped in the browser | Every game in the library (one request per 1000 games) |
| This week | One `GET /api/stats/weekly-digest` | Fixed-size summary |
| A plugin widget | That plugin's own frame and its own requests | The plugin |

Every built-in game widget shares the one library read, so selecting five of them costs the same as selecting
one. The cost that grows is that single read: on a large library it dominates the time before the first rail can
render. Refresh (#479) reuses exactly these loaders and only reloads the targets the selected widgets need.

A timing run on a realistic large library (thousands of games, Modern and Classic, desktop and phone) has **not**
been recorded. It needs a seeded large-library environment; the numbers above are request counts and growth, not
latency. Record initial-interactive time and the bytes of the library read before approving any widget below.

## Budget for any new widget

A new Home widget is acceptable only when all of these hold:

1. An unselected widget makes no request.
2. A selected widget reads a bounded summary (a fixed `limit`, at most a few dozen rows) from its own endpoint,
   never the whole game, movie, TV or anime library.
3. It renders and fails independently: its own loading, error and retry state, and old content kept on failure.
4. It goes through the existing `home_widgets` / `home_widget_config` preferences and chooser, and a missing or
   disabled source shows the existing "Unavailable widget" placeholder instead of dropping the saved choice.
5. Account switching and stale responses use the same generation guard as the existing loaders.

## Keep / adapt / defer

| Candidate | Decision | Reason |
| --- | --- | --- |
| Explicit refresh and last successful update (#479) | **Shipped** | Small, reuses the selected loaders, and makes partial failure visible. |
| Cross-media Continue widget (#475) | **Adapt, not built** | Useful, but needs a bounded "next unwatched episode" summary endpoint for TV and anime first; today's season and progress reads are per show. Eligibility to define: in-progress status only, unknown episode totals shown as "next episode" without a count, specials excluded, completed and dropped excluded. |
| Started but inactive titles (#476) | **Defer** | Needs trustworthy activity. Games now have explicit logged plays (#467) and `last_played_at` no longer moves on corrections, but films and shows have no equivalent watch event, so a media rule would fall back to `updated_at`, which an edit also changes. Revisit once watch activity is recorded. |
| Release and episode schedule summary (#477) | **Adapt, not built** | The calendar already owns layers, privacy, date-only versus timed entries and timezones. A Home summary should reuse the calendar range read (`GET /api/calendar?from_date=&to_date=`) with a 7-day window, plus a server-side row limit it does not have yet, rather than reimplementing any of that. |
| Saved media list shelves (#478) | **Defer** | Game collection shelves exist. Media lists need a bounded "first N covers" read per list before they can be a rail without fetching the full media library. |
| Generated versus user collections (#480) | **Defer, no hiding** | No collection records whether it was generated. Inferring it from member sources can match a user's own "Steam" or franchise collection, so nothing may be hidden or regrouped by inference. If wanted, record an explicit `origin` when the importer creates a collection, leave existing rows as "unknown", and only ever offer grouping (never hiding by default) for recorded generated rows. |
| Grid, pages, drag and resize, presets, layout import/export | **Defer** | Product scope, not a fix. Any later adoption must migrate `home_widgets` and `home_widget_config` without loss and never import grants or secrets. |
| Decorative widgets, achievement rails, library health, new fallback art, a second theme system | **Defer** | No evidence they answer the loading and usability concerns. Collector's Archive keeps goals and bounties. |

## Owner decisions needed

* Approve or reject #475 and #477 as described, so a bounded endpoint can be built for each.
* Provide or approve a large-library environment for the timing run above.
* Confirm that #476, #478 and #480 stay deferred for v0.0.5.
