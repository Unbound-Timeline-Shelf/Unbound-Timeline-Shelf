# Durable manual Steam imports

Proposal for [#464](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/464). Not built. It records where
a manual Steam import runs today, what is already durable, and the schema decision that has to come first.

## Where each stage runs today

| Stage | Settings / Games **Sync / import** (manual) | **Steam autosync** (Background tasks, scheduled or Run now) |
| --- | --- | --- |
| Inventory and playtime | Server, one request (`POST /api/library-sync/steam?achievements=later`) | Server |
| Achievements | Browser loop, 5 games per `POST .../steam/achievements` | Server (`achievements="now"`) |
| Wishlist | Browser, `POST .../steam/wishlist` | Server |
| Store details and artwork | Browser loop, 5 games per `POST .../steam/enrich` | Server, 5 games per call |

The autosync job (`features/connection_jobs.py`) already runs every stage on the server under the durable job lease
(`job_runs.job_lease`, one conditional UPDATE, so a manual Run now and a scheduled run cannot overlap), revalidates
the user and connection before starting, and records the result. Closing the browser does not stop it.

The manual path stops after inventory if the page is closed or reloaded: the achievement, wishlist and enrichment
loops live in `services/librarySync.ts` (`finishSteamImport`). What was committed stays; nothing is duplicated on
the next run, because each stage is idempotent per game. Nothing records which games were left, so the remaining
stages only happen on the next full import.

## What durable resume needs

Neither path keeps a checkpoint between stages. A host restart in the middle of the autosync job loses the run; the
next run starts from inventory again (safe, but slow, and repeats provider calls). The acceptance criteria in #464
(resume after a host or worker restart, explicit paused state, reopening shows owned progress) need:

1. **A persisted run record** per account and provider: stage (`inventory`, `achievements`, `wishlist`, `enrich`,
   `done`), the remaining game ids for the current stage, counts so far, and the last provider back-off
   (`Retry-After`, bounded). This is a new table and migration; job settings and run history do not hold a
   per-stage id list.
2. **One runner**: the manual entry points start (or join) the same server job instead of running the browser
   loops, and poll its status, as plugin connections do since #466.
3. **Bounded steps**: each scheduler tick takes one batch of the current stage and saves the checkpoint, so a restart
   resumes from the saved ids. Live owner and connection checks run before each batch.
4. **Honest outcomes** shown separately: inventory committed, achievements incomplete, wishlist failed, enrichment
   pending, paused, rate limited.

## Decisions needed before implementation

* Approve the run-record table above (or an alternative) and how long finished records are kept.
* Decide whether the browser-driven steps stay available for the transition or are removed in the same release.
  The current Sync / import result screen relies on the synchronous result (per-game feed, retry lists for missing
  artwork), so removing them changes that screen.
* Coordinate the "which games are incomplete" selection with
  [#465](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/465).

Until then, **Settings > Background tasks > Steam autosync > Run now** is the supported way to run a complete Steam
import that does not depend on the page staying open.
