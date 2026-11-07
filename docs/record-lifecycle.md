# Observation records and history

Implemented 2026-10-26. This is general record keeping and technical onboarding. Completing a record never issues
an actuator command, changes a controller profile or starts a model request.

## User flow

The highlighted **Start here** button is next to the box selector. Its guide covers connection checks, one record
per plant, optional starting points and dates, observations, and closing a record. Starting points describe provenance;
they do not select a growing program. Unknown dates stay unknown.

Each active record has **View history** and **Finish record**. Completion needs an end date and an explicit checkbox;
the date cannot precede recorded observations. The record then leaves active tabs and remains in **History & archives**.
Its end date, closing note and complete observation history are kept read-only. Its age stops at that end date.
Create a new record for a new plant. Concurrent records and their journals are independent. The connected devices
continue with their existing settings; completion does not turn them off.

The original imported record can be completed. It cannot be moved to a different box. The old controller profile is
separate and is not reassigned to a successor record. Once that original record is completed, selecting a new record
does not expose the old record's Hank context as its own.

## After the harvest

Added 2026-11-01 (`vgrow/harvest.py`). An active record has **Harvest** next to **Finish record**. Harvesting needs a
day that is not before the start, the observed stage or the last height. The plant then leaves its box: the box interval
ends at midnight after that day, the box no longer counts it, and its card moves on the overview to **After the harvest**.
From there the steps dialog moves it between drying, curing and ready (going back is allowed, each step keeps the day it
began), takes journal notes, and leads to **To the archive**, which is the completion above; its date cannot precede the
current step. Heights end with the harvest, a harvested plant cannot be moved into a box again. Stored in the additive
table `post_harvest`; events `harvested` and `stage`; the frozen snapshot carries `harvested`, `post_stage`, `post_since`.

Each step has a moment of Hank (`web/hank-scenes.js`, `stage.moment`): he cuts and lifts the harvest, checks the
branches on the drying line with the lens, airs the jar, labels it and raises his hat, and files the record card in the
archive box. The plant cards show the step's still frame. Like completion, no step sends a device command. The step after
curing is called "ready": the cockpit shows no consumption (KCanG section 6).

## Storage and coverage

The inventory database adds `record_lifecycle`, `record_events` and `record_places`. Initialization is transactional,
additive and idempotent. Existing records get an unknown starting point and an import event; earlier changes are not
reconstructed. Edits keep previous values and recorded stage labels in events. A completed record stores a JSON
snapshot of all its observations. New writes to it are rejected, including stale clients trying to reopen it through
the old archive endpoint. The separate original `plant.json` and `plant-log.json` are not rewritten.

Box assignments begin when this tracking becomes available, and end on a move or completion. An interval captures
the controller identity. If its identity becomes known later, its sensor interval begins then. Earlier unknown
assignments are not backfilled from a plant's start date. Simultaneous plants share box observations; these are not
measurements of individual plants. Moving a record to an unconnected box never copies readings from the connected box.

Authenticated endpoints:

- `GET /api/workspace/records/{id}`: complete active record or frozen completed snapshot.
- `GET /api/workspace/records/{id}/export`: ZIP with `record.json`, `manifest.json` and available sensor rows in chunks
  of at most one day per file. Reading uses the interval's controller, rather than the current controller. The manifest
  gives row counts and first/last readings. Missing periods are not interpolated. Memory is bounded to a day of rows;
  the temporary ZIP is removed after transfer.

The preview shows up to 100 observations per section; the export includes every stored observation. Sensor rows stay
in the measurement database and may gain late point-log backfill; the observation snapshot is frozen, while an export
can include newly available sensor rows inside the closed time interval. Photos, recordings, feeding history and AI
results are still held by the original box/profile. They have no verified per-plant ownership and are not copied into
an individual record's ZIP. Retain the complete state directory in backups to preserve those files too.

## Local model implications

Recording dates, height history, journals and completion uses SQLite and ordinary Python. Increasing the number of
records does not itself invoke or load a model. No remote model API was added.

The current implementation uses the existing controller profile for Hank and photo analysis. The shared `ModelLane`
in `vgrow/hank/ollama.py` permits one model job at a time; chat and photo requests use a 4,096-token context. This change
does not add individual AI analysis of additional records or benchmark concurrent camera analysis.

A future general record assistant should receive the selected record's ID, relevant dated observations and a compact
history summary. Keep box observations identified as shared evidence. Retrieve older records when requested rather
than loading all histories into every prompt. Queue image work and cache results by image, subject and analysis version.
Separate simultaneous subjects require explicit image assignments; an entire box image is not proof that a visible
feature belongs to a selected record. More image jobs mainly add waiting time; hardware capacity must be measured
before promising analysis frequency. Model training is not needed to keep separate records.

## Validation

`tests/test_record_lifecycle.py` covers completion of the imported record, frozen history, independent successors,
date validation, full exports beyond the preview limit, device/box isolation and authenticated endpoints.
`scripts/workspace_screens.py` uses an isolated local demo, exercises the guide and replacement flow, and checks DE/EN,
light/dark and phone/desktop layouts. It never operates real hardware.
