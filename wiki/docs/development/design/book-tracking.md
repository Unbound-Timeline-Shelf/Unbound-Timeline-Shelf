# Design proposal: book tracking

Status: accepted design, confirmed on 19 October 2026. Phase 1 (works, editions, reads, trash, library
views and export format 3) is implemented; see "Deviations in phase 1" in §9. It
answers [#265](https://github.com/Unbound-Timeline-Shelf/Unbound-Timeline-Shelf/issues/265)
(part of #248; upstream Rosefall #57). Confirmed scope and implementation phases appear below.

## 1. Problem and current state

Users have asked for books alongside games, movies, TV and anime. The upstream
request names books and "other media" but defines no metadata, progress or identity
model, so this page proposes one for books only.

The host currently models each tracked kind as its own feature, not through a shared
media base class:

- Games: `database/models/game.py`, `api/routes/games.py`, `api/schemas/game.py`, plus a
  large game-identity subsystem (`features/game_identity*`) and ownership in
  `features/game_ownership.py`.
- Movies, TV shows, anime: separate tables (`database/models/movies.py`, `tv_show.py`,
  `anime.py`), routes (`api/routes/movies.py`, `tv_shows.py`, `anime.py`) and schemas.
  They deliberately repeat columns (the models carry `# pylint: disable=duplicate-code`
  comments to "preserve independent database table/enum contracts"). Each has its own
  status enum (for example `MovieStatus`), `provider_ids` JSONB, `locked_fields`,
  soft delete via `deleted_at`, and a per-user `user_id`.
- Shared plumbing exists only at the edges: `api/routes/media_common.py`,
  `media_lists.py`, `media_images.py`, `media_stats.py`, `media_extras.py`, the
  `MediaKindSwitch.vue` and `MediaAddModal.vue` frontend components, and library
  export (`api/schemas/library_export.py`, `LibraryExport.format_version = 2` with one
  list per kind).
- The media kind is a closed set spelled in several places: `MediaType`
  (`game|movie|tv_show|anime`) in `plugin_api/metadata_contracts.py`, and
  `Literal["movie","tv","anime"]` in `api/routes/media_images.py`,
  `api/routes/notifications.py` and `api/schemas/media_extras.py`. Adding a kind
  therefore touches the public plugin contract, not only host code.
- Metadata providers: Steam, IGDB, SteamGridDB, TVmaze, AniList, AniZip and OMDb are
  hardcoded host features under `features/metadata/builtin`; optional providers are
  plugins registering through `metadata_providers.register` (see
  [metadata providers](../metadata-providers.md)). None is book-aware.
- #265 states that a generic replacement is not an accepted architecture, so this
  proposal does not assume one.

## 2. Goals and non-goals

Goals for the first deliverable:

1. A book is a first-class tracked item with its own table, routes, schema and library
   view, following the pattern movies/TV/anime already use.
2. Distinguish a **work** (the abstract book) from an **edition** (a specific
   publication/ISBN) without forcing every user to manage editions.
3. Track reading status, progress (page or percentage), ownership and rereads.
4. Round-trip import/export through the existing library snapshot.
5. Work without any provider: manual entry is complete; search is added later through an
   optional plugin.

Non-goals:

- No shared "media item" base table, no merge of movies/TV/anime/games into a generic
  model, and no refactor of existing kinds. Other media types (comics, manga, podcasts,
  music) remain separately scoped.
- No rewrite of existing media contracts, migrations or export keys. Books add a new export version and a
  later, additive plugin metadata contract change.
- No reading-device sync, ebook file hosting, DRM handling or highlights/annotations.
- No social features, recommendations or review import in the first deliverable.
- No new hardcoded metadata provider in the first phase (see section 4.5).

## 3. Proposed model

Names follow the existing snake_case conventions. All tables are per-user and
soft-deleted like movies.

### 3.1 Tables

`books` (one row per work the user tracks):

| Column | Notes |
| --- | --- |
| `id`, `user_id`, `deleted_at` | as movies |
| `title`, `sort_title`, `subtitle` | `sort_title` derived with `core/titles.derive_sort_title` |
| `authors` | `ARRAY(String)`, ordered, display names only in the first version |
| `description`, `first_publish_year`, `language`, `genres`, `tags`, `series_name`, `series_position` | all nullable |
| `provider_ids` | JSONB, same shape as other kinds (for example `openlibrary`, `isbn`) |
| `cover_url`, `locked_fields` | same semantics as other kinds |
| `status` | `BookStatus` enum: `WISHLIST`, `BACKLOG`, `IN_PROGRESS`, `ON_HOLD`, `READ`, `DROPPED`; favorite is a boolean as for anime |
| `rating`, `priority`, `note`, `favorite` | mirror anime/movie fields |
| `active_edition_id` | nullable FK to the edition currently being read |

`book_editions` (zero or more per work):

| Column | Notes |
| --- | --- |
| `id`, `book_id` | cascade delete |
| `isbn_10`, `isbn_13`, `other_identifiers` | normalized, nullable; `other_identifiers` JSONB |
| `format` | `paperback`, `hardcover`, `ebook`, `audiobook`, `other` (audiobook is a format of a work here, not a separate media type) |
| `publisher`, `publish_date`, `page_count`, `language`, `cover_url` | nullable |
| `ownership` | `owned`, `borrowed`, `library`, `wishlist`, `none` |
| `acquired_at`, `note` | nullable |

`book_reads` (one row per read-through; enables rereads):

| Column | Notes |
| --- | --- |
| `id`, `book_id`, `edition_id` (nullable) | |
| `started_on`, `finished_on` | dates, nullable |
| `progress_unit` | `page` or `percent` |
| `progress_value` | numeric, validated against the unit (percent 0 to 100; page 0 to the edition's `page_count` when known, otherwise a fixed sane bound) |
| `outcome` | `reading`, `finished`, `abandoned` |

`status` on the work is user-editable and stored; finishing a read moves it to `READ`,
mirroring how anime handles `rewatches`. A work with no edition rows is valid (the
common "title and author only" case).

### 3.2 Identity

- Work identity inside one account is the row `id`. Cross-provider identity is
  `provider_ids`, as for other kinds.
- Edition identity is a normalized ISBN-13 when present (ISBN-10 converted), otherwise
  none. Two editions of one work with the same ISBN-13 are rejected with a validation
  error, not merged silently.
- Merging two works is deferred; do not automatically merge separate works or present an unsupported merge action.

### 3.3 Examples

1. Multiple editions. "Dune" is tracked once. Editions: a paperback (owned, 535 pages),
   an audiobook (borrowed, progress in percent) and a hardcover wishlist entry. Two
   reads: one finished on the paperback, one on the audiobook at 63 percent with
   `outcome = reading`. The work shows `IN_PROGRESS` with the audiobook as the active
   edition.
2. Incomplete metadata. A user adds "The thing my aunt gave me" with no author, ISBN or
   edition. It saves as a work with `authors = []`. A later metadata attach never
   overwrites a field listed in `locked_fields`.
3. User correction. A provider returns the wrong first-publication year. The user edits
   it; the field name enters `locked_fields` and later refreshes preserve it, exactly as
   movies behave today.

## 4. API, UI, plugin contract

### 4.1 Backend routes (additive)

`api/routes/books.py`, following `movies.py`: `GET/POST /api/books`,
`GET/PATCH/DELETE /api/books/{id}`, with nested `.../editions` and `.../reads`
collections. Every handler resolves the owner the way movies do and returns 404 for
another user's row. Soft delete participates in the existing trash feature
(`features/trash`). Schemas live in `api/schemas/book.py`.

### 4.2 Frontend

`BookLibrary.vue`, `BookDetail.vue` and `BookFormModal.vue`, plus a further entry in
`MediaKindSwitch.vue`. Reuse `MediaTile`, `MediaDetailHero`, `MediaTrackingFields` and
`MediaFormShell`. Progress entry is one field switchable between page and percent. Home
and statistics integration are out of scope for the first deliverable.

### 4.3 Migration

One additive Alembic revision creating `books`, `book_editions`, `book_reads` and their
enums, extending the single linear head. No existing table is altered. Downgrade drops
the new tables and enums. The generated migration is reviewed manually per AGENTS.md.

### 4.4 Import and export

- `LibraryExport` gains a dedicated book-export schema, distinct from a read-through record, and a `books` list
  defaulting to empty. Bump `format_version` from 2 to 3. Older exports still import; an older build must reject an
  unsupported version clearly rather than silently dropping books. Update version dispatch and validation together.
- Editions and reads nest inside each exported book so one round trip is self-contained.
- Importers for Goodreads CSV, StoryGraph CSV and ISBN lists follow the
  `features/imports/yamtrack.py` pattern (bounded size and rows, preview before
  commit). They are a later phase.

### 4.5 Metadata providers and the plugin boundary

- Core versus optional: only Steam, IGDB, SteamGridDB, TVmaze, AniList, AniZip and OMDb
  are hardcoded. This proposal adds **no** hardcoded book provider. Sources such as Open
  Library, Hardcover or Google Books would be optional plugins in
  `Unbound-Timeline-Shelf/Plugins`, using the existing
  `metadata_providers.register` boundary and its permission, credential and account
  opt-out handling.
- *Done in phase 3 (Plugin API 1.1.20):* `book` is a media type, a registration may list all
  five, and the contract carries `MetadataBook`, candidate `authors`/`isbn_13` and the `isbn`
  request option. The planning note follows.
- Contract effect: `MediaType` is a public closed enum and registrations accept at most
  four media types (`media_types` has `max_length=4`). Book providers need an additive
  contract bump adding `book`, widening that bound, and regenerating the JSON schema
  snapshots in `wiki/docs/assets/plugin-metadata/`. Older packages stay valid because
  they never declare `book`. This is the only public-contract change and must follow the
  AGENTS.md section 6.3 procedure (contract, permission semantics, host enforcement,
  tests, docs, plugin-repository examples).
- Until a plugin exists, books work with manual entry and local-only search.
- `plugin_api/media_sync.py` accepts only `movie|tv_show|anime`; books are not added
  there by this proposal.

## 5. Alternatives considered

| Option | Benefit | Cost |
| --- | --- | --- |
| A. Dedicated feature (proposed) | Matches repository precedent, no regression risk to existing kinds | Some column repetition, as already accepted for movies/TV/anime |
| B. Generic `media_items` table with a `kind` column and JSON attributes | One table for every future kind | Rewrites existing contracts, weak typing, contradicts the "no accepted generic architecture" evidence |
| C. Shared mixin for common columns across all kinds | Less repetition | Touches four existing models and their migrations for a books-only change |
| D. Books as a plugin only | No host change | Plugins cannot own first-class library tables or views |

Option A is recommended. Option C can be revisited after books exist, with real
duplication evidence.

## 6. Privacy, authorization and abuse

- All rows are owner-scoped; cross-user access returns 404, as for other kinds.
- Reading history is personal data and is excluded from any public or shared surface by
  default. Delegated integrations (`api/schemas/delegation.py` scopes) get no books
  access unless a new scope is defined later.
- Provider calls send only the search text or identifier the user entered, never library
  contents.
- Free-text fields are length-bounded and rendered as text, not HTML.
- Import size and row limits follow `yamtrack.py` (`MAX_BYTES`, `MAX_ROWS`).

## 7. Phasing

1. Model, migration, CRUD routes, owner-scoped tests, manual-entry UI, export/import
   round trip. This is the first deliverable.
2. Library filters, statistics and Home decisions.
3. Additive plugin contract bump (`book` media type), done in Plugin API **1.1.20** (the same contract as
   releases, see [metadata providers](../metadata-providers.md#plugin-api-1120-books)). Any book provider belongs
   in the plugins repository; the host still has no hardcoded book provider. *Amended 2026-11-10:* the
   programme's Phase 9, approved on 2026-11-08, names the providers that this item had deferred: books from
   Atlas through the `metadata-atlas` plugin, and an optional `metadata-openlibrary` plugin for hosts without
   Atlas. Both are plugins, built in their own repositories against this contract.
4. CSV importers.
5. Revisit shared abstractions only if duplication across five kinds proves costly.

## 8. Test strategy

- Model/API: CRUD, validation (ISBN check digit, progress bounds), duplicate-ISBN
  rejection, soft delete and trash restore.
- Authorization: user A cannot read, patch, delete or list user B's books, editions or
  reads.
- Round trip: export then import yields equal books, editions and reads; an export that
  predates books still imports; `locked_fields` survive.
- Migration: upgrade from the current head, downgrade, single-head check.
- Frontend: Vitest for progress unit switching, multi-edition display and empty-metadata
  rendering; lint, format and typecheck per AGENTS.md.
- Plugin (phase 3): schema snapshot tests, plus a conformance test in the plugins
  repository using the real installed package.

## 9. Confirmed decisions and implementation boundary

The maintainer's answers on [#265](https://github.com/Unbound-Timeline-Shelf/Unbound-Timeline-Shelf/issues/265) approve the
dedicated feature, optional editions, audiobook format with percent progress, an export-version bump, and an
additive book metadata contract in phase 3. There is no new hardcoded provider. Delegation scopes and concrete
provider integrations are deferred until needed. Series remain plain fields; work merging is deferred.
These decisions complete the design, not book CRUD or provider support. Each phase needs its own implementation,
regression coverage and migration/contract review.

### Deviations in phase 1 (M9, `programme/m9-books-phase1`)

- **Routes** follow the host's media convention (`/api/book/list|create|get|update|delete`, `/trash`,
  `/{id}/restore|purge`, `/trash/empty`) instead of the REST shape sketched in §4. Editions and reads
  are `POST /api/book/{id}/editions|reads` and `PATCH|DELETE .../{edition_id|read_id}`; each answers
  with the whole work.
- **Field names:** `rating` is `rating_overall` (as on the other media). `first_publish_year` became the
  C5 partial date `first_published` + `first_published_precision` (C15 §6.2/§6.5), and an edition's
  publish date is a partial date too. Typed book dates accept years 1000-2100.
- **ISBN uniqueness** is also a partial unique index (`book_id`, `isbn_13`), not only an API check.
- **Statuses** fill the five library tabs: Wishlist and Backlog are *Plan to Read*, On hold is *On Hold*.
- **Export** is `LibraryExport` format 3 with `books` (editions and reads nested). An import of format > 3
  is refused with a 400 saying to update the app; formats 1-2 import as before.
- **Older builds do not refuse format 3 (gap against §4.4).** Releases before this change have no
  `format_version` check in `/api/import/media` or `/api/import/library`, so importing a format-3
  export into one of them silently drops the books. That cannot be fixed retroactively: only builds
  from this change onward refuse a format newer than the one they know.
- **Restore keeps the read rules.** A restored read that breaks the API's rules (page progress past
  its edition's page count, percent over 100, a finish before its start) is dropped with an error
  naming the work; the work, its editions and its other reads are still restored. The read routes
  and the restore share one check (`reading_problem`).
- **Tags:** book tags are part of the tag catalogue (counts, discovery and alias suggestions).
- **Not in phase 1:** the activity log, the media-image proxy (covers load from the typed `cover_url`
  with `referrerpolicy=no-referrer`, on the book page and on the List/Shelf/Board library tiles), the
  plugin contract, the CSV export, the command palette, and any metadata provider. The book page has
  its own hero rather than `MediaDetailHero`.

### Deviations in phase 3 (M10, `programme/m10-plugin-api-books`)

- **Contract number:** books join Plugin API 1.1.20 instead of a separate bump. One constant,
  `UNIFIED_CONTRACT` in `plugin_api/base_contracts.py`, gates the release fields and the book fields
  (`RELEASES_CONTRACT` is now an alias).
- **Older books:** `MetadataBook.first_published` and an edition's `publish_date` are their own partial
  dates with years 1000–2100, not a `MetadataRelease` (whose years start in 1950).
- **Edition field names** follow the host's edition columns (`publish_date`, `page_count`), and two editions
  in one result may not share an ISBN-13.
- **The ISBN is not a provider id.** `provider_ids` stay confined to the provider's own namespaces; the
  ISBN a provider matched travels in the candidate's `isbn_13`, and an ISBN search sends the `isbn`
  request option.
- **No refresh for books yet.** A provider's book is applied when the person adds the book or adds an
  edition; there is no "Refresh metadata" for a book, and an edition's provider key is not stored
  (editions are matched by ISBN).
- **Search and add** is in the *Add book* form rather than the shared media-add dialog.
- **Contract range:** a 1.1.20 manifest may declare `api_contract_max` (C16 §22.6); the host runs the plugin
  at the highest contract both support.
- **Book gate at registration only.** A registration listing `book` is refused below 1.1.20 when the plugin
  registers it; there is no second check when stored rows are read, and no declaration-time gate at
  `core.py` module load (C15 §6.3), because core provider declarations are host-owned and none lists `book`.
- **Edition on ISBN search only.** The add form adds an edition with the work only when the search text was
  an ISBN equal to the result's `isbn_13`; the chosen result's provider ids replace any applied earlier, and
  its cover is shown through the host's artwork preview rather than loaded from the provider URL.

## 10. Acceptance mapping

| #265 criterion | Where addressed |
| --- | --- |
| First deliverable and non-goals explicit; other media separate | Sections 2 and 7 |
| Storage/API/UI, migration and plugin-contract effects without copying feature trees | Sections 3 and 4 |
| Examples for multiple editions, incomplete metadata, corrections; authorization and round-trip tests planned | Sections 3.3 and 8 |
| Open decisions with recommendations; no broad rewrite | Sections 5 and 9 |
