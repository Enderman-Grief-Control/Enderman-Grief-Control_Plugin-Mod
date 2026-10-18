# Compatibility runner

Tests one exact plugin/mod jar on one exact Minecraft version, against a real Paper or Fabric dedicated server, and records the evidence. It exists so the 1.21.x support claims can be backed by repeatable, per-version results rather than samples.

A run never reports `pass` from a process exit code or a log line alone. Pickup and placement are judged from the blocks and carried state in a controlled arena. A result file is written even when the server crashes, a step times out, or the run is interrupted.

## Requirements

- Python 3.10+ (standard library only).
- Java matching the target's `java_major` (21 for every 1.21.x row). The run is blocked if the major version differs.
- Node.js 22+ for the protocol clients (the locked Mineflayer 4.39.0 and minecraft-protocol 1.68.0 require it). Install the pinned client once with `npm ci --prefix scripts/compatibility/bot`. Before any download or server start, a preflight checks the Node version and that the installed packages match the lock.
- Network access to PaperMC, Fabric meta, Mojang, and GitHub on first use. Downloads are cached by hash under `test-servers/compat-cache/`.

## Run one row

```sh
python scripts/compatibility/run.py --platform paper --side server --minecraft 1.21 \
    --artifact paper-1.2.0 --out test-servers/compat-runs/paper-1.21
```

`--artifact` defaults to the target's `artifact_id` in the manifest. It must name a manifest artifact for the same platform. `--out` must be a new or empty directory; without it, the run uses `test-servers/compat-runs/<run id>/`. Each run selects one row and one artifact. Any of the twelve versions `1.21` through `1.21.11` can be selected without code changes.

The exit code is 0 for `pass`, 1 for anything else, and 2 for an invalid invocation. Use `result.json` as the evidence; the exit code is only a convenience.

Other entry points:

| Command | Purpose |
| --- | --- |
| `run.py --validate-only` | Validate the manifest (all 36 targets, pins, hashes, artifact platforms). |
| `run.py --list-targets` | Show each target's pinned build or Loader, which server it runs, or why it is unavailable. |
| `run.py ... --control` | Vanilla control: same server and arena with no project artifact. It runs only `startup` and `other_mob`, to separate environment failures from artifact failures. |
| `run.py ... --limit server_start_s=300` | Override a limit for a slow host. Overrides are recorded in the result. |
| `matrix.py --platform paper --out DIR` | Run each version as an independent row and write `matrix-summary.md`. |
| `pin.py` | Maintainer tool: resolve official builds, Loader launchers, and hashes into the manifest. Review the diff before committing. |

## What a server row does

1. Verifies the Java major version and the protocol-client preflight. Then it acquires the pinned server jar or Fabric launcher and the artifact. Every file is checked against its pinned SHA-256 and size, with a size cap and the row deadline enforced while streaming. Manifest file names must be plain basenames, and every cache or install path is checked to stay inside its directory before anything is written or deleted.
2. Builds a fresh workspace with one project jar and a flat world. The server is bound to `127.0.0.1` with `online-mode=false` and a generated, single-use RCON password, which is redacted from `server.properties` when the run ends. A cached install from an earlier run is reused only if every file matches an official hash (see Install verification); otherwise it is deleted and the official launcher or Paperclip installs from scratch.
3. Starts the server. The artifact's own startup lines, the `Done` line, and the absence of Mixin errors are asserted. It then connects RCON and two protocol clients: `CompatOp` (operator) and `CompatGuest`.
4. Builds a glass arena with an identical reference copy, so one `execute if blocks` query proves that no block changed.
5. Runs the checks below, including one clean stop and restart on the same jar.
6. Stops the server and kills only processes this run started. Termination, RCON closing, and password redaction run as independent steps, so a failure in one never skips the others. It then verifies the install tree and the Mojang jar, closes every log, and hashes the evidence files. After `result.json` is written, every recorded digest is checked again against the saved bytes.

The whole-row limit (`row_s`, default 20 minutes) is enforced by a watchdog thread. At the deadline it closes RCON and kills the owned processes, which unblocks any waiting call. The row is then reported as `blocked (timeout_or_no_observation)` with the usual cleanup and result. `matrix.py` adds an outer per-row limit (`--row-timeout`, default 1500 s) that kills the row's whole process tree, then redacts that row's RCON password, because the killed run cannot. Ctrl+C does the same. A row killed this way has no `result.json` and is listed as `blocked`.

| Check | Passes only when |
| --- | --- |
| `startup` | Both server runs reach `Done` with the artifact loaded and no Mixin errors; RCON answers; both clients spawn; run 1 stops with exit code 0. |
| `pickup` | A tagged, AI-ticking Enderman produces a pickup denial inside the arena; the arena is unchanged; the Enderman carries nothing. |
| `placement` | An Enderman verified to carry dirt produces a placement denial in the arena with the carrier policy set to `off`; nothing is placed; it still carries dirt. |
| `carrier_off` / `carrier_alert` / `carrier_auto_clear` | A discovered stationary holder is kept, alerted (log and both clients), or cleared (log, both clients, no item dropped). |
| `other_mob` | A sheep turns grass into dirt with protection on, so the plugin has not suppressed all mob griefing. |
| `commands_permissions` | The guest is denied; the operator's status, toggle, set, carrier-policy, and reload commands answer correctly; RCON settings match the config file on disk. |
| `persistence` | Non-default settings appear in status and on disk, survive a reload, and survive a restart. |
| `announcements_cooldown` | Both clients get `x1` for the first pickup denial. At least one more denial is held back inside the 10-second cooldown, nothing is announced while denials are paused past it, and the next denial brings one message with the full pending count (`x2` or more). Placement, alert, and clear announcements also reach both clients. |

If an observed contradiction occurs, the check is `fail`. A changed arena counts as a contradiction even when no denial was logged. If no attributable attempt happens within the limits (three fresh trials of at most 30 seconds or 20,000 ticks each), the check is `blocked`, never `pass`. An `x1` flush after the cooldown is also `blocked`: it fits the contract, but no denial was held back, so suppression and roll-up were not observed. When `startup` fails, every later check is blocked.

## Results

Each run writes `result.json`, `summary.md`, `runner.log`, the server output for each start, the client transcripts, an RCON transcript, and copies of logs, crash reports, and configs under `evidence/`.

- **Statuses:** `pass`, `fail`, `blocked` (attempted, but a prerequisite or observation was missing), and `unknown` (never attempted). The overall result is `pass` only when every required check passed and there were no run errors, such as a non-zero exit code, a killed process, an RCON error, or a server-jar hash mismatch.
- **Failure classes:** `artifact`, `game_or_loader`, `harness_or_protocol`, `environment_or_download`, `timeout_or_no_observation`, `client_access`, and `unclassified`. A Fabric crash is classed as `artifact` only when the Mixin error names a Mixin config bundled in the jar under test.
- **Recorded identity:** the manifest SHA-256, runner git commit, Java and Node versions, the official Minecraft server jar SHA-1 and size (checked against Mojang's pin), library jar hashes, and the client protocol version that was negotiated. If a row reached `Done` but its Mojang jar was not observed, or the jar or install tree fails verification, the row cannot pass.

## Install verification

The runner checks every file a server installs against a hash from a pinned source. It does this before reusing a cached install and again after every run, and only a clean run that reached `Done` refreshes the cache.

- **Paper:** the pinned `paper.jar` lists every library, patched jar, and the Mojang jar that Paperclip writes, each with its SHA-256 (`META-INF/libraries.list`, `versions.list`, `download-context`, `patches.list`).
- **Fabric:** the Mojang server jar (pinned SHA-1) lists its bundled libraries with SHA-256. The manifest pins each loader library's official SHA-256 from Fabric meta, or from Fabric Maven's published `.sha256` file when meta omits one. The launcher's generated launch jar may contain only its manifest and properties file, its class path must point only at the verified loader libraries, and its main class must be the pinned one.

A file that is not listed or whose hash differs is rejected. Missing files are allowed, because Paperclip deletes its patch inputs and the official installer recreates anything absent. On a fresh install, the official launcher or Paperclip downloads the files during the run, so they are verified after the run rather than before it. Paperclip also checks its own hashes as it runs.

## Fabric client rows

The runner cannot drive a graphical client. `run.py --platform fabric --side client --minecraft 1.21 --client-evidence-template --out DIR` writes a blank manual evidence form. Fill it in from a fresh client profile with the pinned Loader and the exact artifact, then rerun with `--client-evidence DIR/client-evidence.json`. Without a form, every client check is `blocked (client_access)`. A dedicated-server result is never used as client evidence.

The form is rejected unless every identity field matches the selected row: row, manifest SHA-256, artifact ID and SHA-256, Minecraft version, Loader, profile, and Java major. Further rules:

- A form `pass` needs an observation and an evidence file inside the form's directory, or it is downgraded to `blocked`.
- Mod Menu checks are `blocked` while the row pins no Mod Menu jar, and otherwise require the form to report that exact jar.
- Save/Cancel, settings persistence, world transition, and monitor state are required on rows whose manifest sets `extended_checks_required` (1.21, 1.21.5, and 1.21.11).
- Any recorded `fail` rejects the row.
- The reported jar hash is the observer's claim and is recorded as `reported_sha256`.

## Manifest

`manifest.json` is a reviewed lock file, not a query for the latest builds. Each target pins an exact Paper build (with its channel) or an exact Loader and installer launcher, plus the Mojang server jar SHA-1, the Java major version, the client protocol version, and a command adapter. Artifacts are immutable IDs with SHA-256 values, so a rebuilt candidate needs a new ID even if its filename is reused.

Notes from pinning, 2026-10-18:

- The claim under test is Minecraft 1.21.x compliance, so each row names the server that players on that release actually join. PaperMC publishes no 1.21.2 server: 1.21.3 is a hotfix that uses the same protocol (768), and 1.21.2 players join Paper 1.21.3. The `paper/server/1.21.2` row therefore runs Paper 1.21.3 build 83 with a protocol 768 client. It records `server_minecraft` and a written `server_basis`, and the summary shows both. The manifest rejects a different server release without a basis. Every other row runs its own release.
- Each server start asserts that the startup banner names the pinned server: the Paper version and build, or the Minecraft and Fabric Loader versions.
- Paper 1.21.5 (build 114, kept from the baseline) and 1.21.9 (build 59) have only `ALPHA` builds.
- The pinned `minecraft-data` treats 1.21.2 and 1.21.7 as 1.21 (protocol 767). Those rows therefore connect as 1.21.3 (protocol 768) and 1.21.8 (protocol 772). The negotiated protocol is recorded, and a mismatch causes a failed login, never a pass.
- Minecraft 1.21.11 renamed game rules to snake case. The `snake-case-gamerules` adapter is used there; setup reads each rule back and blocks the run if the name or value does not match.

## Implementation notes

- **Client game mode:** the clients play in creative mode. Paper's entity activation range does not keep mobs near a spectator fully active, so a sheep beside one never ate grass. Endermen cannot target creative players.
- **RCON:** requests are sent strictly one at a time. The vanilla RCON reader drops the connection if one socket read contains more than one packet.
- **Windows paths:** deep library paths are handled with extended-length paths when they are copied or hashed.
- **Client dependency advisories:** `npm audit` (2026-10-18) reports one moderate `uuid` advisory, reached through Microsoft and Mojang online-authentication packages. The bot always connects with `auth: 'offline'` and never loads that code. npm's only automatic fix is a downgrade to Mineflayer 1.4.0, so the advisory is recorded rather than "fixed".

## Tests

```sh
python -m unittest discover -s scripts/compatibility/tests -t scripts/compatibility
```

These offline tests cover:

- Manifest validation, including unsafe file names and library paths.
- Path containment for traversal, absolute, drive, and junction or symlink escapes.
- Download size and deadline limits.
- Install verification against official hashes, including a tampered library, an unlisted file, and a tampered launch jar.
- Cleanup when the server stop fails, the strict jar-identity check, evidence digests after `finish`, the row watchdog (including during the restart), and the matrix outer-limit kill and redaction.
- The false-pass guards, including an `x1` cooldown flush, and the rate-limit model.
- Platform parsing and RCON framing, size, and deadline limits.
- Process-tree cleanup, written so it stays safe when an assertion fails.
- Client-form identity and gating rules, the client preflight, and the CLI's blocked paths.

CI runs them along with `--validate-only`.
