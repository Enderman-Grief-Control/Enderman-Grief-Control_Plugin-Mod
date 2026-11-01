# Compatibility runner

Tests one exact plugin/mod jar on one exact Minecraft version, under one install profile, against a real Paper or Fabric dedicated server or a real Fabric client, and records the evidence. It exists so the 1.21.x support claims can be backed by repeatable, per-version results rather than samples.

A run never reports `pass` from a process exit code or a log line alone. Pickup and placement are judged from the blocks and carried state in a controlled arena. A result file is written even when the game crashes, a step times out, or the run is interrupted.

## Requirements

- Python 3.10+ (standard library only).
- Java matching the target's `java_major` (21 for every 1.21.x row). The run is blocked if the major version differs. Extended client checks also need `javac` 21 (by default the one beside `--java`).
- Node.js 22+ for the server rows' protocol clients (the locked Mineflayer 4.39.0 and minecraft-protocol 1.68.0 require it). Install the pinned client once with `npm ci --prefix scripts/compatibility/bot`. Before any download or server start, a preflight checks the Node version and that the installed packages match the lock.
- Client rows: a Windows host with an unlocked interactive desktop and a GPU. On any other host every client check is `blocked (client_access)` before anything launches.
- Network access to PaperMC, Fabric meta and Maven, Mojang, Modrinth's CDN, and GitHub on first use. Downloads are cached by hash under `test-servers/compat-cache/`.

## Run one row

```sh
python scripts/compatibility/run.py --platform fabric --side server --minecraft 1.21 \
    --profile standard --artifact fabric-1.2.0 --out test-servers/compat-runs/fabric-1.21-standard
```

`--artifact` defaults to the target's `artifact_id` in the manifest; it must name a manifest artifact for the same platform. `--profile` defaults to `standard`. `--out` must be a new or empty directory; without it, the run uses `test-servers/compat-runs/<run id>/`. Each run selects one row, one artifact, and one profile. Any of the twelve versions `1.21` through `1.21.11` can be selected without code changes.

The exit code is 0 for `pass`, 1 for anything else, and 2 for an invalid invocation. Use `result.json` as the evidence; the exit code is only a convenience.

Other entry points:

| Command | Purpose |
| --- | --- |
| `run.py --validate-only` | Validate the manifest (all 36 targets, profiles, pins, hashes, artifact kinds). |
| `run.py --list-targets` / `--list-profiles` | Show each target's pinned build or Loader and dependency pins / each install profile. |
| `run.py ... --control` | Control: the profile's dependencies without the project jar (server: `startup` and `other_mob`; client: `client_startup` and `integrated_world_load`), to separate environment failures from artifact failures. |
| `run.py --side client ... --negative-control` | The client core scenario without the project jar. It must observe griefing; it is a harness self-check, never a row result. |
| `run.py ... --limit server_start_s=300` | Override a limit for a slow host. Overrides are recorded in the result. |
| `run.py --side client ... --assets-from DIR` | Copy SHA-1-verified asset objects from a launcher assets folder (holding `objects/`) instead of downloading them. |
| `matrix.py --platform fabric --side client --profiles documented --out DIR` | Run each version under each selected profile as an independent run and write `matrix-summary.md` with a row verdict. |
| `pin.py [targets\|dependencies]` | Maintainer tool: resolve official builds, Loader launchers, client trust-chain pins, and dependency jars into the manifest. Review the diff before committing. |
| `candidate.py build\|register` | Build a candidate jar from a clean tree, or register a jar built elsewhere, as a manifest `candidate` artifact. |

## Install profiles

A profile is what a user installs: the project jar plus the dependency jars it names. Claims follow the documented install (maintainer decision, 2026-10-25), so the documented profiles are the ones a row must pass.

| Platform | Profile | Sides | Jars | Expected outcome | Documented |
| --- | --- | --- | --- | --- | --- |
| Paper | `standard` | server | plugin | the ten server checks | yes |
| Fabric | `standard` | server, client | mod + the row's Fabric API | server: the ten checks; client: the core checks | yes |
| Fabric | `modmenu` | client | mod + Fabric API + the row's Mod Menu | the core checks, `mod_menu_load`, and the extended checks where required | yes |
| Fabric | `missing-dependency` | server, client | mod only | Loader refuses before the game starts and names `fabric-api` | yes |
| Fabric | `bundled` | server, client | mod only | the checks (1.2.0's documented install) | no |
| Fabric | `bundled-modmenu` | client | mod + Mod Menu | the client checks (the 10F configuration) | no |

The workspace installs exactly the profile's jars, each verified against its pin, and `startup` (or `client_startup`) asserts that the mods folder holds exactly those jars and that each dependency's descriptor names its pinned mod and version. A result under an undocumented profile never counts toward a claim. `matrix.py` gives a row the verdict `pass` only when every documented profile for that side ran and passed; `incomplete` when one did not run; and `fixture` for a fixture artifact.

`dependency_refusal` (the `missing-dependency` profile) passes only when all four hold: the project descriptor declares each omitted mod ID in `depends`; the game never starts (no `Done` on a server, no integrated server on a client); Loader logs `Incompatible mods found!` and names every omitted ID; and there is no Mixin error or crash report. A start, a crash, or any other outcome fails. On a client, Loader opens its error screen instead of exiting, so the session is ended once the refusal is logged.

## What a server row does

1. Verifies the Java major version and the protocol-client preflight. Then it acquires the pinned server jar or Fabric launcher and every jar of the profile. Every file is checked against its pinned SHA-256 and size, with a size cap and the row deadline enforced while streaming. Manifest file names must be plain basenames, and every cache or install path is checked to stay inside its directory before anything is written or deleted.
2. Builds a fresh workspace with the profile's jars and a flat world. The server is bound to `127.0.0.1` with `online-mode=false` and a generated, single-use RCON password, which is redacted from `server.properties` when the run ends. A cached install from an earlier run is reused only if every file matches an official hash (see Install verification); otherwise it is deleted and the official launcher or Paperclip installs from scratch.
3. Starts the server. The artifact's own startup lines, the `Done` line, and the absence of Mixin errors are asserted. It then connects RCON and two protocol clients: `CompatOp` (operator) and `CompatGuest`.
4. Builds a glass arena with an identical reference copy, so one `execute if blocks` query proves that no block changed.
5. Runs the checks below, including one clean stop and restart on the same jars.
6. Stops the server and kills only processes this run started. Termination, RCON closing, and password redaction run as independent steps, so a failure in one never skips the others. It then verifies the install tree and the Mojang jar, closes every log, and hashes the evidence files. After `result.json` is written, every recorded digest is checked again against the saved bytes.

Under `missing-dependency`, the row runs only the refusal stage (a 90-second start limit, `refusal_s`) and records `dependency_refusal`.

The whole-row limit (`row_s`, default 20 minutes) is enforced by a watchdog thread. At the deadline it closes RCON and kills the owned processes, which unblocks any waiting call. The row is then reported as `blocked (timeout_or_no_observation)` with the usual cleanup and result. `matrix.py` adds an outer per-run limit (`--row-timeout`, default 1500 s) that kills the run's whole process tree, then redacts that run's RCON password, because the killed run cannot. Ctrl+C does the same. A run killed this way has no `result.json` and is listed as `blocked`.

| Check | Passes only when |
| --- | --- |
| `startup` | The installed jars are exactly the profile's; both server runs reach `Done` with the artifact loaded and no Mixin errors; RCON answers; both clients spawn; run 1 stops with exit code 0. |
| `pickup` | A tagged, AI-ticking Enderman produces a pickup denial inside the arena; the arena is unchanged; the Enderman carries nothing. |
| `placement` | An Enderman verified to carry dirt produces a placement denial in the arena with the carrier policy set to `off`; nothing is placed; it still carries dirt. |
| `carrier_off` / `carrier_alert` / `carrier_auto_clear` | A discovered stationary holder is kept, alerted (log and both clients), or cleared (log, both clients, no item dropped). |
| `other_mob` | A sheep turns grass into dirt with protection on, so the plugin has not suppressed all mob griefing. |
| `commands_permissions` | The guest is denied; the operator's status, toggle, set, carrier-policy, and reload commands answer correctly; RCON settings match the config file on disk. |
| `persistence` | Non-default settings appear in status and on disk, survive a reload, and survive a restart. |
| `announcements_cooldown` | Both clients get `x1` for the first pickup denial. At least one more denial is held back inside the 10-second cooldown, nothing is announced while denials are paused past it, and the next denial brings one message with the full pending count (`x2` or more). Placement, alert, and clear announcements also reach both clients. |

If an observed contradiction occurs, the check is `fail`. A changed arena counts as a contradiction even when no denial was logged. If no attributable attempt happens within the limits (three fresh trials of at most 30 seconds or 20,000 ticks each), the check is `blocked`, never `pass`. An `x1` flush after the cooldown is also `blocked`: it fits the contract, but no denial was held back, so suppression and roll-up were not observed. When `startup` fails, every later check is blocked.

### Failure attribution

A Fabric startup failure is blamed on the installed jar that supplied the failing mod at the version Loader loaded. The project jar and Fabric API can carry same-named modules (for example `fabric-entity-events-v1`), and Loader keeps only the newest copy. So the runner reads Loader's "Loading N mods" tree, which names the copy it chose and the top-level jar that carried it. When no tree was printed (a resolution failure stops Loader first), the failing mod's ID and version are matched against each installed jar's nested-mod tree, and a mod carried by more than one jar without a known version stays unattributed. A failure in the project tree is `artifact`. A failure in a dependency is `unclassified` in the row and `game_or_loader` in the profile's `--control` run, which reproduces it without the project jar. The attribution is recorded in the check's observations.

## Fabric client rows

`run.py --platform fabric --side client --minecraft <v> --profile <p>` runs the row automatically:

1. **Launch metadata.** The manifest pins the Mojang version JSON by SHA-1, and that JSON pins the client jar, every vanilla library, the asset index, the client mappings, and the server jar. The Fabric launch profile is fetched from Fabric meta but accepted only when its main class and library set equal the target's own pins, and each Loader library is checked against its pinned SHA-256. Libraries go through the same hash-checked cache as the server rows; asset objects are verified by SHA-1 on every run.
2. **World.** A flat template world is generated once per server jar SHA-1 and settings by the pinned vanilla server of the same version, and reused only while its `level.dat` matches the recorded hash. Each run copies it into a fresh game directory under `--out` and adds a data pack for the pack format of that version (`pack_format`, or `min_format`/`max_format` from 1.21.9).
3. **Core session.** The client runs offline (dummy token, player `EGCClient`), 854×480 at GUI scale 2, straight into the world through quick play. The data pack rebuilds the server rows' arena and reports world state as `EGC-RESULT` chat lines: one tagged Enderman for 60 s (pickup), then four verified dirt holders checked every 30 s (placement) after restoring the arena from its reference copy. Data-pack functions run at permission level 2, below `/tick`, so the client runs at normal speed. Core sessions send no input and never attach the screen observer, so they reflect the documented install exactly. The client is closed with WM_CLOSE to its own window, which saves the world; its process tree is killed only when no window is left or it does not exit in time.
4. **Checks.**

| Check | Passes only when |
| --- | --- |
| `client_startup` | The installed jars are exactly the profile's; Loader's banner names the pinned Minecraft and Loader; the project is listed at its version and initializes; no Mixin error, crash, or resolution failure; the integrated server starts on the pinned version; the client closes by WM_CLOSE with exit code 0. |
| `integrated_world_load` | The player joins, and the data pack's setup report shows the arena equal to its reference (567 blocks), eight grass blocks, one Enderman carrying nothing, `mobGriefing` true, and spawning off. |
| `pickup` / `placement` | As on servers: a denial inside the arena, the arena unchanged, and the carried state as expected. A change is `fail`; no attributable attempt is `blocked`. |
| `mod_menu_load` (`modmenu`) | Loader lists Mod Menu at the pinned version beside the project, with no broken `ModMenuApi`. |
| `client_errors` | No crash or Mixin error, and every WARN/ERROR line matches the reviewed allowlist (`egc_compat/client/allowlist.py`), which gives each entry's reason and the control run that showed it. An unreviewed line makes the check `unknown`, which blocks a row pass until it is reviewed. |

### Extended checks and the screen observer

On rows whose target sets `extended_checks_required` (1.21, 1.21.5, and 1.21.11), the `modmenu` profile adds four checks, run in a second game directory with two holder worlds, A and B. Session 1 is one client process: it opens the mod's config screen through Mod Menu, checks Cancel and Save, sets Alert, travels A → title → B → title → A, and waits in A for a periodic monitor pass. Session 2 relaunches the client in the same game directory.

Every UI step is confirmed from what is actually on screen. `egc_compat/client/agent/ScreenObserver.java` is a read-only Java agent, attached only to extended sessions. It registers no class transformer and queues its reads on the client's own executor, so every read happens on the render thread between frames. It appends a JSON line to `observer.jsonl` whenever the screen changes, and as a heartbeat at least once a second: the screen class and title, the GUI and window sizes, every widget's class, message, bounds, and active/visible state, selection-list rows, and, for screens outside `net.minecraft`, the field holding each widget. It is compiled with `javac --release 21` and cached by source SHA-256. The intermediary names it needs are derived from the pinned client mappings and the pinned intermediary jar; members first declared on interfaces are looked up on those interfaces. The harness clicks the centre of the widget the observer reports, using posted window messages, and a step counts only once the observer shows the expected next state. An unconfirmed step makes the remaining extended checks `blocked (harness_or_protocol)`, never pass or fail. Screenshots (F2) and every posted action, with the observer sequence numbers around it, are kept as evidence, and the result records the agent, source, name-map, mappings, and intermediary hashes.

| Check | Passes only when |
| --- | --- |
| `mod_menu_save_cancel` | **Cancel:** the staged values are on screen, the config bytes are unchanged afterwards, reopening shows the saved values, and no carrier line is logged. **Save:** from saved OFF/Auto-Clear, Prevent ON with Stuck Holders Off is staged and shown; the file then holds exactly those values; no holder is cleared and the holder still carries its block; carrier work is logged only on `Server thread`. |
| `settings_persistence` | The saved values show in world A, world B, and on the title screen. A relaunch starts from the bytes the first session left, shows the same values, alerts the holders on join, and leaves the file unchanged after Cancel. |
| `world_transition` | Each world entry is confirmed by its data pack's world ID and session, each exit logs `All dimensions are saved`, and both processes exit 0 after WM_CLOSE. |
| `monitor_state` | Each world session alerts its own holders on the server thread soon after join (within 600 ticks) and never names another world's holder (each world keeps its holders on its own row). A title-screen Save with no world running logs no carrier action and no exception. The first periodic alert of A's second session comes no earlier than 2,400 server ticks after that session's server started, measured from game time in the data pack's reports, because the pause menu stops the integrated server. |

### Manual form (fallback)

`--client-evidence-template` writes a blank form for the selected row and profile; `--client-evidence FORM` records the row from it instead of running it. The form is rejected unless every identity field matches the selected row: row, install profile, manifest SHA-256, artifact ID and SHA-256, Minecraft version, Loader, Loader profile, and Java major. A form `pass` needs an observation and an evidence file inside the form's directory, or it is downgraded to `blocked`. Mod Menu checks need the `modmenu` profile and the pinned Mod Menu jar reported. Any recorded `fail` rejects the row. Such results are labeled `evidence_source: manual`: the hashes a form reports are the observer's claims.

## Results

Each run writes `result.json`, `summary.md`, `runner.log`, and evidence: for servers the server output for each start, client transcripts, an RCON transcript, and copies of logs, crash reports, and configs under `evidence/`; for clients each session's `stdout.log`, `latest.log`, crash reports, config after the session, and (extended sessions) `observer.jsonl`, `ui-actions.jsonl`, and screenshots under `evidence/<session>/`.

- **Statuses:** `pass`, `fail`, `blocked` (attempted, but a prerequisite or observation was missing), and `unknown` (never attempted or not reviewable). The overall result is `pass` only when every required check passed and there were no run errors, such as a non-zero exit code, a killed process, an RCON error, or a server-jar hash mismatch.
- **Failure classes:** `artifact`, `game_or_loader`, `harness_or_protocol`, `environment_or_download`, `timeout_or_no_observation`, `client_access`, and `unclassified`.
- **Schema 2** adds `profile` (name, `documented`, `expect`, and every installed jar with its role, mod ID, artifact ID, and expected and installed SHA-256), `evidence_source` (`automated` or `manual`), `selection.profile`, `selection.fixture`, `extended_checks`, and, for extended sessions, `observer`.
- **Recorded identity:** the manifest SHA-256, runner git commit, Java and Node versions, the official Minecraft server jar SHA-1 and size (checked against Mojang's pin), library jar hashes, the client protocol version that was negotiated, and for clients the version JSON SHA-1, client jar SHA-1, Fabric profile hash, every classpath library's SHA-256, asset counts, world template, and data-pack file hashes. If a server row reached `Done` but its Mojang jar was not observed, or the jar or install tree fails verification, the row cannot pass.

## Install verification

The runner checks every file a server installs against a hash from a pinned source. It does this before reusing a cached install and again after every run, and only a clean run that reached `Done` refreshes the cache.

- **Paper:** the pinned `paper.jar` lists every library, patched jar, and the Mojang jar that Paperclip writes, each with its SHA-256 (`META-INF/libraries.list`, `versions.list`, `download-context`, `patches.list`).
- **Fabric:** the Mojang server jar (pinned SHA-1) lists its bundled libraries with SHA-256. The manifest pins each loader library's official SHA-256 from Fabric meta, or from Fabric Maven's published `.sha256` file when meta omits one. The launcher's generated launch jar may contain only its manifest and properties file, its class path must point only at the verified loader libraries, and its main class must be the pinned one.

A file that is not listed or whose hash differs is rejected. Missing files are allowed, because Paperclip deletes its patch inputs and the official installer recreates anything absent. On a fresh install, the official launcher or Paperclip downloads the files during the run, so they are verified after the run rather than before it. Paperclip also checks its own hashes as it runs.

## Manifest

`manifest.json` (schema 2) is a reviewed lock file, not a query for the latest builds.

- **Targets** pin an exact Paper build (with its channel) or an exact Loader and installer launcher, plus the Mojang server jar SHA-1, the Java major version, the client protocol version, and a command adapter. Client targets pin the version JSON URL and SHA-1, the client jar, and their own Loader main class and libraries. Every Fabric target maps each mod ID its profiles install to one `dependency` artifact in `dependencies`; the validator rejects a missing or unused pin, a pin whose descriptor names another mod, one that does not list the row's version, one whose own `minecraft`/`fabricloader` predicates exclude the row or its Loader, and a server and client of one version pinning different Fabric API jars.
- **Profiles** are declared per platform; each side needs at least one documented profile, and `standard` must cover every side.
- **Artifacts** are immutable IDs with SHA-256 values. Kinds: `published` (release tag and source commit), `candidate` (source commit and an optional `build` record), `dependency` (Modrinth project and version, game versions, SHA-512, and the jar's declared predicates), and `fixture` (a harness self-test jar with a stated purpose; selectable only by `--artifact`, never a target default, and labeled in results). Each artifact has exactly one source: a `url`, a `local_path`, or, for a fixture only, `generated`. A generated jar is rebuilt from the manifest at run time (JSON entries stored uncompressed at a fixed `entry_time`), so no binary is tracked, and it must reproduce the pinned size and SHA-256 before it is cached.

Notes from pinning, 2026-10-18:

- The claim under test is Minecraft 1.21.x compliance, so each row names the server that players on that release actually join. PaperMC publishes no 1.21.2 server: 1.21.3 is a hotfix that uses the same protocol (768), and 1.21.2 players join Paper 1.21.3. The `paper/server/1.21.2` row therefore runs Paper 1.21.3 build 83 with a protocol 768 client. It records `server_minecraft` and a written `server_basis`, and the summary shows both. The manifest rejects a different server release without a basis. Every other row runs its own release.
- Each server start asserts that the startup banner names the pinned server: the Paper version and build, or the Minecraft and Fabric Loader versions.
- Paper 1.21.5 (build 114, kept from the baseline) and 1.21.9 (build 59) have only `ALPHA` builds.
- The pinned `minecraft-data` treats 1.21.2 and 1.21.7 as 1.21 (protocol 767). Those rows therefore connect as 1.21.3 (protocol 768) and 1.21.8 (protocol 772). The negotiated protocol is recorded, and a mismatch causes a failed login, never a pass.
- Minecraft 1.21.11 renamed game rules to snake case. The `snake-case-gamerules` adapter is used there; setup reads each rule back and blocks the run if the name or value does not match.

Dependency pins, 2026-10-25 (`pin.py dependencies`): each pin is the newest Modrinth release of that project that lists the row's exact version for Fabric, verified against Modrinth's SHA-512 and SHA-1, with its own `fabric.mod.json` naming the expected mod and version and accepting the row and Loader 0.19.5.

- **Fabric API**, on every Fabric server and client row: 0.102.0+1.21, 0.116.17+1.21.1, 0.106.1+1.21.2, 0.114.1+1.21.3, 0.119.4+1.21.4, 0.128.2+1.21.5, 0.128.2+1.21.6, 0.129.0+1.21.7, 0.136.1+1.21.8, 0.134.1+1.21.9, 0.138.4+1.21.10, and 0.141.6+1.21.11.
- **Mod Menu**, on every client row (re-pinned with provenance; the same jars as the 2026-10-19 pins): 11.0.5 (1.21, 1.21.1), 12.0.1 (1.21.2, 1.21.3), 13.0.4 (1.21.4), 14.0.2 (1.21.5), 15.0.2 (1.21.6-1.21.8), 16.0.1 (1.21.9, 1.21.10), and 17.0.1 (1.21.11).
- **Fixture:** `fapi-requirement-probe` is the descriptor-only jar of the 2026-10-25 Loader probe, generated from its manifest entry byte-for-byte (the same SHA-256 as the probe). It declares `fabric-api` in `depends`, so `missing-dependency` on it is the refusal check's known pass.

## Candidates

```sh
python scripts/compatibility/candidate.py build --platform fabric --version 1.2.1 [--write]
python scripts/compatibility/candidate.py register --platform fabric --version 1.2.1 \
    --jar release-jars/EndermanGriefControl-mc_1.21-fabric-1.2.1.jar --tag fabric-v1.2.1 [--write]
```

`build` refuses to run unless `fabric-mod/`, `paper-plugin/`, and `enderman-grief-control-messaging/` are clean at HEAD. It runs the release workflow's commands with `ENDERMAN_GRIEF_TEST_MODE=false`, applies the workflow's checks (test mode off in the jar, file name ending in `-<version>.jar`) and checks that the descriptor carries exactly that version. `register` applies the same checks to a jar built elsewhere, such as the release workflow's `release-jars` output, so the pre-publish matrix can run on that exact jar. Either way the jar is copied to the ignored `test-servers/compat-candidates/<artifact id>/`, a jar already in the manifest is refused, and a `candidate` artifact is printed, or saved with `--write` once the manifest validates. Candidate builds are not byte-reproducible, so IDs carry the source: `<platform>-<version>-cand-<short commit>` for a local build and `-reg-<short commit>` for a registered jar, with `-2`, `-3`, ... for another jar from the same commit. Target defaults never change; runs select a candidate with `--artifact`.

## Implementation notes

- **Client game mode (servers):** the protocol clients play in creative mode. Paper's entity activation range does not keep mobs near a spectator fully active, so a sheep beside one never ate grass. Endermen cannot target creative players.
- **RCON:** requests are sent strictly one at a time. The vanilla RCON reader drops the connection if one socket read contains more than one packet.
- **Windows paths:** deep library paths are handled with extended-length paths when they are copied or hashed. Client data-pack folders stay short because `MAX_PATH` applies to them.
- **Windows-only code** lives in `egc_compat/client/win32.py`, which is imported only when a client is about to be driven, so CI imports every other module and runs the offline tests.
- **Posted clicks and the real cursor:** the harness never moves the desktop cursor, but the game itself recentres the OS cursor over its window when a screen opens from in-game, and it clicks wherever its last mouse-move put the cursor. A click therefore posts the move and the button back to back (Windows delivers a window's posted messages before queued hardware input). The observer writes a heartbeat line at least once a second while its reads succeed, so a click or key is resent (up to three attempts in all) only when the observer stayed live and nothing on screen changed. When the observer goes quiet instead (the client drops queued tasks while it leaves a world, and the agent re-queues a read pending for over two seconds), nothing is resent and the step waits for its expected state. Run extended checks on an otherwise idle desktop: moving the real mouse over the game window can still steal a click, which then shows as a retry or, at worst, a `blocked` step, never a pass.
- **Client dependency advisories:** `npm audit` (2026-10-18) reports one moderate `uuid` advisory, reached through Microsoft and Mojang online-authentication packages. The bot always connects with `auth: 'offline'` and never loads that code. npm's only automatic fix is a downgrade to Mineflayer 1.4.0, so the advisory is recorded rather than "fixed".

## Tests

```sh
python -m unittest discover -s scripts/compatibility/tests -t scripts/compatibility
```

These offline tests cover:

- Manifest validation, including unsafe file names and library paths, profiles, dependency pins and their predicates, fixtures, and candidate build records; Fabric version predicates.
- Path containment for traversal, absolute, drive, and junction or symlink escapes.
- Download size and deadline limits.
- Install verification against official hashes, including a tampered library, an unlisted file, and a tampered launch jar.
- Profile jar sets (exact contents, tampered or extra jars), the missing-dependency stage against recorded Loader output (a clean refusal, an undeclared requirement, a start, and a crash), and failure attribution with same-named modules.
- Dependency pinning (newest release, hash and descriptor checks, pruning) and candidate registration.
- Cleanup when the server stop fails, the strict jar-identity check, evidence digests after `finish`, the row watchdog (including during the restart), and the matrix outer-limit kill, redaction, and row verdicts.
- The false-pass guards, including an `x1` cooldown flush, and the rate-limit model.
- Platform parsing and RCON framing, size, and deadline limits.
- Process-tree cleanup, written so it stays safe when an assertion fails.
- Client launch rules, library de-duplication and pinning, profile matching, launch arguments, data packs for each pack format, log facts and every client verdict, the allowlist, per-profile check sets, the blocked path off Windows, and the manual form's identity and gating rules.
- The observer's name-map derivation (from small mapping fixtures), posted-input encoding, GUI-to-window coordinates, snapshot reading, and the extended verdict helpers.

CI runs them along with `--validate-only`.
