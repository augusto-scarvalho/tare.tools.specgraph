# Experiment storage

Run copies are temporary. A lot is closed only after its evidence is validated
and its recoverable copies are removed.

The executable control is `tools/experiment_storage.py`. The `init` defaults
are 8 GiB for registered lots and local consolidated archives, with a minimum
of 10 GiB free on the filesystem chosen for `--root`. Adjust `--capacity-gib`
and `--minimum-free-gib` to the operator's environment. This is a logical
reservation: it creates no filler file and promises no exclusivity against other
programs. The shared registry prevents concurrent reservations above the limit;
a managed run checks growth and free space every second and when it ends. It is
not a hard operating-system quota; a write can exceed the limit between checks.

1. Use a single temporary root per environment. Choose an absolute root with
   `--root`; the examples use `/srv/specgraph/.reconciliation` on Linux and
   `C:/tare-scratch/.reconciliation` on Windows. Each lot keeps its copies,
   indexes, logs and intermediate packages inside one subdirectory of that
   root. Use short names inside it when needed; do not create other roots
   (`C:/`, `C:/t`, `C:/tmp` or the system temporary directory) for the
   artifacts the experiment controls. Before preparing a lot, measure the free
   space, estimate the peak size of the copies and say so if the run would
   use up the available margin on the disk.
2. Keep the active copies isolated. Plan creation and release per cell, without
   accumulating complete projects from finished runs.
3. Keep a compressed frozen source, deduplicated by hash when possible, plus the
   candidates' deltas, protocols, calls, receipts, assessments and the counters
   needed to reproduce and audit the result.
4. Validate hashes and replay before removing workspaces, controls and
   temporary indexes. Check the local files against the preserved source and
   deltas; unexpected changes stay protected until they are identified.
5. Confirm that the processes have ended and that no action is pending. Remove
   only directories explicitly identified as temporary for the lot.
6. Remove the whole lot directory when closing it, including empty directories
   and receipt copies already preserved. Identify and preserve unique files
   before that removal. Also remove partial packaging copies and large debug
   logs once the required records are preserved. Keep a small cleanup receipt
   with the scope and the space recovered.
7. Confirm the free space when closing. Development checkouts, Git histories,
   models and active databases have a separate life cycle from these temporaries.

## Running lots

Under `--root`, the registry is `experiment-storage/registry.json`; new lots go
in `experiments/<id>` and consolidated archives in `evidence-store`. Older,
unregistered directories stay protected: their existence or age never
authorizes deletion.

Example on Linux or WSL, from the SpecGraph repository:

```bash
python3 tools/experiment_storage.py --root /srv/specgraph/.reconciliation status
python3 tools/experiment_storage.py --root /srv/specgraph/.reconciliation create pilot-01 --reserve-gib 2
python3 tools/experiment_storage.py --root /srv/specgraph/.reconciliation exec --timeout 1800 pilot-01 -- python3 /absolute/path/runner.py --output '{run}/pilot'
python3 tools/experiment_storage.py --root /srv/specgraph/.reconciliation consolidate pilot-01 --reason 'Results assessed; preserve the complete run'
```

On Windows, use the native Python and `C:/tare-scratch/.reconciliation`. In the
`exec` subcommand, options such as `--timeout` come before the ID; the
arguments after `--` are passed to the program without a shell. `{run}` is
replaced with the lot's path. Preparation and execution can be separate
commands inside the same lot. The commands must direct their own outputs to
`{run}`; the controller does not intercept arbitrary absolute paths they choose.

The children's environment points `TARE_SPECGRAPH_STATE_DIR`, `TMPDIR`, `TEMP`,
`TMP`, `XDG_CACHE_HOME`, `UV_CACHE_DIR`, `PIP_CACHE_DIR` and `CARGO_TARGET_DIR`
at the lot. The `qualify_context_pilot.py` pilot accepts this shared state,
records the binding in its protocol and refuses a change between preparation
and execution. Older callers without an override keep their previous behavior;
new experiments should go through the storage controller.

States: `OPEN` → `RUNNING` → `FINISHED` → `CONSOLIDATED` → `RETIRED`.
A containment failure leaves `NEEDS_REVIEW`; an interrupted controller can
leave `RUNNING` or `RETIRING`. None of these states is deleted automatically.
Recovering after an interruption requires checking the processes, the intact
archive and the registered path; nothing unlocks because time has passed.

`consolidate` preserves the whole lot: a manifest with paths, hashes,
permissions and timestamps, plus one blob per distinct content in a tar.gz. It
reads every blob back and compares it with the source again. Files linked to an
external Git repository, junctions and external symlinks require an explicit
migration. When restoring across filesystems, rounding of timestamps and
permissions is reported; the original values stay in the manifest.

After consolidation, collection runs automatically; `--keep-workspace` defers
it. `create` also collects lots that are already consolidated before reserving
space. `gc` only shows the result; `gc --apply` applies it. Collection requires
a present and intact consolidated archive, an ownership marker for new lots, a
terminal state and all content equal to the manifest. Any new or modified file
blocks removal. The directory is renamed and checked again before removal.
Intermediate states are recorded.

Containment controls the descendants run by the wrapper. The additional
inspection reads the working directory and executable of local processes and
the descriptors visible on Linux; it is not isolation against arbitrary
programs on another host or processes that cannot be inspected. Do not run
editors or external writers concurrently on a lot being closed.

For an old leftover, register its exact path in the registry's
`legacy_allowlist` and use `create <id> --legacy-source <path> --reserve-gib <peak>`.
This archives a WSL source directly without duplicating everything on the
Windows drive. The migration never uses broad prefixes such as `/tmp` or
age-based deletion. Legacy sources can only be handled on the system that
understands their path.

## Remote evidence copy

The operator may keep a private remote copy of the consolidated archives, for
example in a cloud folder. The folder ID and its permissions belong to the
operator's private configuration, outside the repository.

After uploading, read the metadata back, download the file and verify its
SHA-256 and its whole manifest. Record the check with `verify-remote <id>
--downloaded <downloaded-file> --file-id <id> --url <observed-url>`. Only then
does `evict <id>` remove the local tar.gz, and only if the workspace is already
`RETIRED`. A successful upload alone never authorizes deletion. The small
record with ID, URL, hash and date stays local. The remote check is a point in
time; later changes to the remote copy are not monitored by a background
service.

Upload and download are done by the operator or an agent; consolidation,
validation and local collection are programmatic commands. No scheduled task
deletes folders in the background.

`restore --archive <tar.gz> --destination <new-directory>` rebuilds the files
without running anything from the archive. It never overwrites an existing
directory. Files already versioned in Git must not be removed from the tree just
because they were uploaded: that requires an explicit migration of the evidence
references and their consumers.
