# proofhouse.core.verify — full integrity check
"""
Phase 2.5 §5.1 — everything `verify` checks beyond per-log chain
recomputation.

Until now `verify` meant "recompute one chain", reachable only as a line
inside `status`, and only for the registry and the ledger. The amendment,
fill and rejected-ideas logs were never verified by anything a user could
run.

WHAT THIS IS FOR
-----------------
The hash chains prove nobody edited a record after writing it. They prove
nothing about whether the records are CONSISTENT WITH EACH OTHER: a gate row
citing an entry that does not exist, an amendment whose successor never
registered, a validation window that predates the hypothesis it validates, a
locked document citing a catalog entry that was never written. Each of those
verifies clean at the chain level and is wrong.

Failure catalog #26 is the canonical case: a locked document cited
failure-catalog entry #26 for three weeks while the catalog ended at #25.
Nothing was tampered with; the citation simply pointed at nothing. Check 5
below is that case, mechanized — and it is the check most likely to be
softened the first time it fires on something inconvenient, which is why it
returns a hard failure rather than a warning.
"""

import os

from proofhouse.core.amend import check_amendment_ordering
from proofhouse.core.manifest import manifest_digest, result_digest
from proofhouse.schema.validate import ValidationError, validate_entry
from proofhouse.schema.versions import SCHEMA_VERSION


# A THIRD SEVERITY, AND WHY IT IS NOT A WARNING.
#
# Records written before a check existed cannot be checked by it. R-000012
# carries no manifest digest; R-000013 carries no source run and no criterion
# outcomes. Those are neither clean nor tampered, and both readings are
# wrong in a way that matters:
#
#   - as an ERROR, verify never returns 0 again, and a permanently failing
#     check is one people learn to skip. This repo has recorded that failure
#     mode twice already (the block check and the citation resolver).
#   - as a WARNING, "we could not check this" sits among 24 findings that all
#     mean "we checked, and the evidence is weaker than it looks". Those are
#     different epistemic states, and the whole point of the temporal check
#     is that collapsing two different states into one reading is the defect.
#   - as SILENCE, an unverifiable record reads as a verified one, which is
#     the reading this tool exists to prevent.
#
# So: a distinct class, counted separately, and excluded from `ok`. The
# repo's own precedent is explicit — a gate row with no entry_hash is
# already reported rather than passed silently.
UNVERIFIABLE = "unverifiable"


class Finding:
    """One integrity problem.

    Attributes:
        check: Which check produced it ('chain', 'schema', 'blocks',
            'runnable', 'temporal', 'ordering', 'references', 'manifests').
        severity: 'error', 'warning', or 'unverifiable'. Only errors fail
            the verify; see UNVERIFIABLE above for why the third class is
            not one of the first two.
        where: The record the problem is in.
        message: What is wrong.
    """

    def __init__(self, check, severity, where, message):
        self.check = check
        self.severity = severity
        self.where = where
        self.message = message

    def to_dict(self):
        return {
            "check": self.check, "severity": self.severity,
            "where": self.where, "message": self.message,
        }

    def __repr__(self):
        return "{} [{}] {}: {}".format(
            self.severity.upper(), self.check, self.where, self.message)


def _chain(name, log, findings):
    """Recompute one hash chain."""
    if log is None:
        return
    ok, errors = log.verify()
    if ok:
        return
    for error in errors:
        findings.append(Finding(
            "chain", "error", name,
            error.get("message", str(error)) if isinstance(error, dict)
            else str(error),
        ))


def verify_all(registry=None, ledger=None, amendment_log=None,
               fill_log=None, rejected_log=None, block_registry=None,
               citation_resolver=None, manifests_dir=None):
    """Run every integrity check. Returns (ok, findings).

    Args:
        registry, ledger, amendment_log, fill_log, rejected_log: the logs to
            check. Any may be None; a missing log is skipped, not failed.
        block_registry: for schema re-validation.
        citation_resolver: callable(citation) -> bool, resolving an external
            citation. When None, external citations are reported as UNCHECKED
            warnings rather than silently passed — an unverifiable claim must
            not read as a verified one.
        manifests_dir: where run manifests live. When None the digest
            comparisons are skipped, and the row-to-row checks still run.

    Returns:
        (ok, findings) where ok is True only if no finding has severity
        'error'. UNVERIFIABLE findings do not fail the verify; they are not
        clean either, which is why they are their own class.
    """
    findings = []

    _chain("registry", registry, findings)
    _chain("ledger", ledger, findings)
    _chain("amendments", amendment_log, findings)
    _chain("fills", fill_log, findings)
    _chain("rejected", rejected_log, findings)

    entries = registry.list_entries() if registry else []
    by_id = {e["id"]: e for e in entries}

    _check_schema(entries, block_registry, findings)
    _check_blocks(block_registry, findings)
    _check_runnable(entries, block_registry, findings)
    _check_temporal(entries, block_registry, findings)
    _check_references(entries, by_id, ledger, amendment_log, fill_log,
                      citation_resolver, findings)
    _check_ordering(entries, ledger, amendment_log, fill_log, findings)
    _check_manifests(by_id, ledger, manifests_dir, block_registry, findings)

    ok = not any(f.severity == "error" for f in findings)
    return ok, findings


def _check_schema(entries, block_registry, findings):
    """Every record validates under ITS OWN recorded schema_version.

    Validating everything against the current version would report a v1 record
    as broken the moment v2 lands, which trains you to ignore the check.
    """
    for entry in entries:
        version = entry.get("schema_version")
        if version is None:
            findings.append(Finding(
                "schema", "error", entry.get("id", "?"),
                "no schema_version — the record cannot state which rules it "
                "was written under"))
            continue
        if version != SCHEMA_VERSION:
            # A record from an older schema is not an error. It is only an
            # error if we cannot check it, which we say explicitly.
            findings.append(Finding(
                "schema", "warning", entry.get("id", "?"),
                "recorded under schema_version {} while the tool is at {}; "
                "not re-validated".format(version, SCHEMA_VERSION)))
            continue
        try:
            validate_entry(entry, block_registry=block_registry)
        except ValidationError as exc:
            findings.append(Finding(
                "schema", "error", entry.get("id", "?"), str(exc)))


def _check_blocks(block_registry, findings):
    """A block's segment and its consumption record must agree.

    Segment R asserts a block is untouched. Nothing used to check that against
    `consumed_by` sitting in the same record, so the live registry carried a
    block labelled Reserve that two pipelines had read — found by a person
    reconciling by hand, which is the wrong way to find it.

    Reserve is the only segment whose label is a claim about the PAST rather
    than a permission. E/S1/S2/V/L say what a block may be used for; R says
    nothing has been read from it. Claims about the past can be discovered
    wrong, and this is the check that discovers them.

    The contradiction clears when a consumption note explains it — segment
    stays, explanation attaches. That keeps the one-way ladder intact, at the
    cost of R meaning "sealed unless a note says otherwise" rather than
    "sealed".
    """
    if block_registry is None:
        return

    for block in block_registry.list_blocks():
        block_id = block.get("id", "?")
        readers = {c.get("entry_id") for c in block.get("consumed_by") or []}
        segment = block.get("segment")

        for entry_id in block_registry.unexplained_consumption(block_id):
            findings.append(Finding(
                "blocks", "error", block_id,
                "segment {} but entry {} has read it. {} claims the block is "
                "untouched and the registry says otherwise. Either file a "
                "consumption note saying what was read and what the block is "
                "now unfit for, or the label is simply "
                "wrong.".format(segment, entry_id, segment)))

        # A note for an entry that never read the block is how a blank cheque
        # would be reintroduced: pre-clear a block, and every later reading
        # passes this check silently.
        for note in block.get("consumption_notes") or []:
            if note.get("entry_id") not in readers:
                findings.append(Finding(
                    "blocks", "error", block_id,
                    "consumption note names entry {}, which never consumed "
                    "this block. A note filed ahead of consumption pre-clears "
                    "every later reading of it.".format(note.get("entry_id"))))

        # Acknowledged and permitted, but not equal evidence to a block held
        # clean — and, like the holdout case, invisible everywhere else. Warned
        # rather than errored for the same reason: the tool cannot adjudicate
        # an attestation, only refuse to let the weaker case pass as the
        # stronger one silently.
        ack = block.get("promotion_acknowledgment")
        if ack:
            findings.append(Finding(
                "blocks", "warning", block_id,
                "promoted {}→{} over prior consumption by {}. Validation data "
                "that something had already read: the claim it was unexamined "
                "rests on the recorded rationale — {!r} — rather than on "
                "nothing having touched it.".format(
                    ack.get("from_segment"), block.get("segment"),
                    ", ".join(ack.get("prior_consumption") or []),
                    ack.get("rationale"))))


def _ancestry(entries):
    """{entry_id: every id it descends from, transitively}.

    Registry.ancestor_ids does the same walk against the live registry; this
    one runs over a list of records, because verify is given entries rather
    than a Registry. Both are cycle-safe by a seen set.
    """
    by_id = {e.get("id"): e for e in entries}
    resolved = {}

    def walk(entry_id, seen):
        if entry_id in resolved:
            return resolved[entry_id]
        if entry_id in seen or entry_id not in by_id:
            return set()
        seen = seen | {entry_id}
        out = set()
        for parent in by_id[entry_id].get("depends_on") or []:
            out.add(parent)
            out |= walk(parent, seen)
        resolved[entry_id] = out
        return out

    for entry in entries:
        walk(entry.get("id"), set())
    return resolved


def _check_runnable(entries, block_registry, findings):
    """An entry that can never gate should say so before you try.

    Allowing an amendment to reuse blocks its lineage never spent creates a
    time-of-check/time-of-use gap. Both entries declare the same V blocks;
    whichever gates first consumes them; E4 then refuses the other forever.
    Soundness holds — E4 is the backstop — but the tool would let you
    register something it will later refuse to run and say nothing at the
    moment the choice is still open.

    There is no supersession concept in the schema: `status` is always
    "registered", so nothing marks a parent as retired. Until there is, this
    is how "which of these two is the live one?" gets answered by something
    other than a person's memory.

    Two findings, and the warning is the error's early form:

      error   — a declared V block is already spent by a DIFFERENT entry, so
                this one cannot gate at all
      warning — several live entries declare the same V block and none has
                spent it yet; the first to gate takes it

    The warning self-clears when one of them gates, which is what keeps it
    from becoming an alarm nobody hears.
    """
    if block_registry is None:
        return

    ancestors = _ancestry(entries)

    claimants = {}
    # {(entry_id, spender): [blocks]} — one finding per pair rather than one
    # per block. An entry colliding on six blocks is one situation, and six
    # identically-shaped lines is how a reader learns to skim the section.
    collisions = {}

    for entry in entries:
        entry_id = entry.get("id", "?")
        for block_id in entry.get("validation_blocks") or []:
            claimants.setdefault(block_id, []).append(entry_id)

            try:
                block = block_registry.get_block(block_id)
            except KeyError:
                continue        # V-8 / E6 territory, not this check's
            if block.get("segment") != "V":
                continue
            for consumption in block.get("consumed_by") or []:
                spender = consumption.get("entry_id")
                if spender == entry_id:
                    continue
                # SUPERSESSION, INFERRED FROM THE ONE SIGNAL THERE IS.
                #
                # If the spender descends from this entry, this entry was
                # amended into it and the successor took the blocks — the
                # intended path, not a collision. Without this the correct
                # workflow (amend, then gate the successor) left a permanent
                # error on every superseded parent, and an error that can
                # never clear is one people learn to skip. Same failure this
                # tool has now recorded twice.
                if entry_id in ancestors.get(spender, set()):
                    continue
                collisions.setdefault((entry_id, spender), []).append(block_id)

    for (entry_id, spender), block_ids in sorted(collisions.items()):
        findings.append(Finding(
            "runnable", "error", entry_id,
            "declares validation block(s) {}, already spent by entry {}, "
            "which is not a descendant of it. E4 makes V-blocks single-use, "
            "so this entry can never be gated on them — the refusal is "
            "guaranteed and arrives only when you try.".format(
                sorted(block_ids), spender)))

    for block_id in sorted(claimants):
        entry_ids = set(claimants[block_id])
        # A claimant that some other claimant descends from was amended into
        # it, so it is not contesting anything — the successor is the live
        # one and the record says so. Without this, the ordinary
        # amend-then-amend-again chain warned on every block it carried
        # forward, which is the same false positive the error branch had and
        # for the same reason.
        contenders = sorted(
            e for e in entry_ids
            if not any(e in ancestors.get(other, set())
                       for other in entry_ids if other != e))
        if len(contenders) < 2:
            continue
        try:
            block = block_registry.get_block(block_id)
        except KeyError:
            continue
        if block.get("segment") != "V" or block.get("consumed_by"):
            continue        # already spent — reported as an error above
        findings.append(Finding(
            "runnable", "warning", block_id,
            "claimed as a validation block by {} entries with no amendment "
            "relationship ({}), and spent by none of them. Whichever gates "
            "first takes it and the others become unrunnable. Nothing in the "
            "record says which is the live one.".format(
                len(contenders), ", ".join(contenders))))


def _check_temporal(entries, block_registry, findings):
    """Separate an EMBARGO from a HOLDOUT, because they are not equal evidence.

    * **Embargo** — the window had not finished when the entry was registered.
      Peeking was physically impossible, so the claim rests on nothing.
    * **Holdout** — the window had already concluded, and the claim that it
      went unexamined rests entirely on attestation.

    Both are legitimate designs; they are not equally strong, and nothing else
    in the record distinguishes them. This is reported as a WARNING rather
    than an error because the tool cannot adjudicate an attestation — it can
    only refuse to let the weaker case pass as the stronger one silently.

    Found by running verify against the retro-filed v11 W6 entry: W6's test
    period ended 2026-06-30 and v11's original lock was 2026-07-23. The
    validation window had concluded three weeks before registration. That is
    invisible to a reader and to every other check in this file.
    """
    if block_registry is None:
        return
    for entry in entries:
        created = str(entry.get("created_utc") or "")
        if not created:
            continue
        for block_id in entry.get("validation_blocks") or []:
            try:
                block = block_registry.get_block(block_id)
            except KeyError:
                continue        # reported by the reference check
            end = block.get("end")
            if not end:
                continue
            # str() both sides: YAML parses an unquoted 2026-07-01 into a date
            # object, and comparing that to a string raises rather than
            # returning False. Compare on the shared date prefix.
            end_text = str(end)[:10]
            if end_text and end_text < created[:10]:
                findings.append(Finding(
                    "temporal", "warning", entry.get("id", "?"),
                    "validation block {} ended {} but the entry was "
                    "registered {}. This is a HOLDOUT, not a temporal "
                    "embargo: the data existed at registration, so 'it was "
                    "not examined' rests on attestation rather than on the "
                    "data not yet existing. Weaker evidence, and nothing "
                    "else in the record says so.".format(
                        block_id, end_text, created[:10])))


def _check_references(entries, by_id, ledger, amendment_log, fill_log,
                      citation_resolver, findings):
    """Every reference resolves.

    THE #26 CASE. A locked document cited failure-catalog entry #26 for three
    weeks while the catalog ended at #25. Nothing was tampered with — the
    citation pointed at nothing. A dangling citation from a locked record is a
    HARD FAILURE, not a warning: the record claims support it does not have,
    and that is the same defect as an unwired function, one level up.
    """
    for entry in entries:
        entry_id = entry.get("id", "?")

        for dep in entry.get("depends_on") or []:
            if dep not in by_id:
                findings.append(Finding(
                    "references", "error", entry_id,
                    "depends_on names {}, which is not in the "
                    "registry".format(dep)))

        for citation in entry.get("prior_art") or []:
            if not isinstance(citation, str) or not citation.strip():
                findings.append(Finding(
                    "references", "error", entry_id,
                    "prior_art contains an empty citation"))

        # Citations are checked across EVERY prose field, not only prior_art.
        # The #26 case was a citation in a locked document's body — v11's
        # pre-registration §1.2 — not in a citations list. Restricting the
        # check to the field named "prior_art" would miss the exact failure it
        # was written for.
        for where, text in _prose_fields(entry):
            if citation_resolver is None:
                if _looks_like_a_citation(text):
                    findings.append(Finding(
                        "references", "warning", entry_id,
                        "{}: citation not checked (no catalog available): "
                        "{!r}".format(where, text[:60])))
            elif not citation_resolver(text):
                findings.append(Finding(
                    "references", "error", entry_id,
                    "{}: citation does not resolve: {!r}. A locked record "
                    "citing something that is not there claims support it "
                    "does not have (failure catalog #26).".format(
                        where, text[:80])))

    known = set(by_id)
    for row in (ledger.all_rows() if ledger else []):
        ref = row.get("registry_ref")
        if ref and ref not in known:
            findings.append(Finding(
                "references", "error", row.get("run_id", "?"),
                "ledger row references entry {}, which is not in the "
                "registry".format(ref)))

    for record in (amendment_log.all_rows() if amendment_log else []):
        if record.get("entry_id") not in known:
            findings.append(Finding(
                "references", "error", record.get("amendment_id", "?"),
                "amendment references entry {}, which is not in the "
                "registry".format(record.get("entry_id"))))

    for record in (fill_log.all_rows() if fill_log else []):
        if record.get("entry_id") not in known:
            findings.append(Finding(
                "references", "error", record.get("fill_id", "?"),
                "fill references entry {}, which is not in the "
                "registry".format(record.get("entry_id"))))


def _looks_like_a_citation(text):
    import re
    return bool(re.search(r"(?:failure\s+)?catalog\s*#\s*\d+", text, re.I))


def _prose_fields(value, _path=""):
    """Yield (dotted.path, text) for every string in a record that carries a
    catalog citation."""
    if isinstance(value, str):
        if _looks_like_a_citation(value):
            yield _path or "(root)", value
        return
    if isinstance(value, dict):
        for key in sorted(value):
            if key in ("hash", "prev"):
                continue
            child = "{}.{}".format(_path, key) if _path else key
            for item in _prose_fields(value[key], child):
                yield item
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            child = "{}[{}]".format(_path, index)
            for found in _prose_fields(item, child):
                yield found


def _load_manifest(manifests_dir, run_id):
    """The manifest for a run, or None if there is no file for it."""
    if not manifests_dir:
        return None
    path = os.path.join(manifests_dir, "{}.yaml".format(run_id))
    if not os.path.exists(path):
        return None
    import yaml
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def _check_manifests(by_id, ledger, manifests_dir, block_registry, findings):
    """The result, the verdict and the record must be the same three numbers.

    `verify_all` never opened a manifest. The chains proved nobody edited a
    ROW, and the file holding the numbers a verdict was rendered against sat
    outside them entirely — so editing `R-000012.yaml` changed what
    `proofhouse report` printed and every chain still verified clean. That is
    the hole; this is the check that closes it.

    Four claims, each checkable against a record that is chained:

      1. the eval row's digests still describe the manifest on disk
      2. the gate row's source run exists and is an eval run of the same entry
      3. the gate row's criterion outcomes carry the numbers that manifest
         holds — the verdict was rendered against those values and no others
      4. the blocks a gate row spent are recorded as consumed by that entry

    Anything a legacy row cannot answer is UNVERIFIABLE, never clean and
    never tampered. Read the UNVERIFIABLE constant for why that is a third
    class rather than a warning.
    """
    if ledger is None:
        return

    rows = ledger.all_rows()
    by_run = {r.get("run_id"): r for r in rows}

    for row in rows:
        kind = row.get("kind")
        run_id = row.get("run_id", "?")
        entry_id = row.get("registry_ref")

        # A kind='maintenance' replay records a verdict too, and is checked
        # exactly like the decision row it re-renders. Only when it actually
        # carries one: `maintenance` is also the kind for a universe-
        # construction note, and treating those as unverifiable verdicts
        # would fill the output with findings about rows that never claimed
        # to be verdicts.
        renders_a_verdict = kind == "gate" or (
            kind == "maintenance"
            and (row.get("criteria") or row.get("source_eval_run_id")))

        if kind in ("eval", "subperiod"):
            _check_eval_row(row, run_id, manifests_dir, findings)
        elif renders_a_verdict:
            _check_gate_row(row, run_id, entry_id, by_run, manifests_dir,
                            findings)

        if renders_a_verdict:
            _check_gate_blocks(row, run_id, entry_id, by_id, block_registry,
                               findings)


def _check_eval_row(row, run_id, manifests_dir, findings):
    """An eval row's digests still describe the manifest on disk."""
    recorded_result = row.get("result_hash")
    recorded_manifest = row.get("manifest_hash")

    if not recorded_result or not recorded_manifest:
        findings.append(Finding(
            "manifests", UNVERIFIABLE, run_id,
            "eval row carries no manifest digest, so the result it produced "
            "is not inside the hash chain. Nothing here says the numbers on "
            "disk are the numbers this run computed — the row predates the "
            "digest and cannot be checked, which is not the same as being "
            "clean."))
        return

    if not manifests_dir:
        findings.append(Finding(
            "manifests", UNVERIFIABLE, run_id,
            "eval row records digests but no manifests directory was "
            "supplied, so they were not compared against anything."))
        return

    manifest = _load_manifest(manifests_dir, run_id)
    if manifest is None:
        findings.append(Finding(
            "manifests", "error", run_id,
            "eval row records a manifest digest but no manifest exists at "
            "{}/{}.yaml. The row claims a file that is not there — §5.3: a "
            "result without a manifest is not a result.".format(
                manifests_dir, run_id)))
        return

    actual_result = result_digest(manifest.get("result"))
    if actual_result != recorded_result:
        findings.append(Finding(
            "manifests", "error", run_id,
            "the manifest's result does not match the result_hash on this "
            "row: row says {}, file hashes to {}. The row is chained, so the "
            "file is what moved — the recorded numbers are not the numbers "
            "this run produced.".format(recorded_result, actual_result)))

    actual_manifest = manifest_digest(manifest)
    if actual_manifest != recorded_manifest:
        findings.append(Finding(
            "manifests", "error", run_id,
            "the manifest does not match the manifest_hash on this row: row "
            "says {}, file hashes to {}. Provenance around the result (code "
            "hash, data hash, seed, execution mode) decides what the numbers "
            "are a measurement OF.".format(
                recorded_manifest, actual_manifest)))


def _recorded_statistics(result):
    """{statistic: canonical decimal string} for every gate-evaluable value.

    The primary lives in `value` and the secondaries in
    `secondary_statistics`, both canonicalized at rest. `metadata` is
    deliberately absent: a value a gate compares against a threshold does
    not live there any more, and a verdict citing one is reported rather
    than checked against it.
    """
    values = {}
    statistic = (result or {}).get("statistic")
    if statistic is not None:
        values[statistic] = (result or {}).get("value")
    for name, value in ((result or {}).get("secondary_statistics")
                        or {}).items():
        values[name] = value
    return values


def _check_gate_row(row, run_id, entry_id, by_run, manifests_dir, findings):
    """A gate row names its source run and carries the numbers it compared."""
    source_id = row.get("source_eval_run_id")
    criteria = row.get("criteria")

    if not source_id or not criteria:
        absent = []
        if not source_id:
            absent.append("a source eval run")
        if not criteria:
            absent.append("its criterion outcomes")
        findings.append(Finding(
            "manifests", UNVERIFIABLE, run_id,
            "gate row records {} — so the verdict cannot be traced to the "
            "measurement it was rendered against, and the values it compared "
            "are not in the record at all. The row predates the binding; "
            "reported rather than passed silently, exactly as a gate row with "
            "no entry_hash already is.".format(
                "neither " + " nor ".join(absent) if len(absent) > 1
                else "no " + absent[0])))
        return

    source = by_run.get(source_id)
    if source is None:
        findings.append(Finding(
            "manifests", "error", run_id,
            "gate row names source eval run {}, which is not in the ledger. A "
            "verdict citing a run that never happened.".format(source_id)))
        return

    if source.get("kind") not in ("eval", "subperiod"):
        findings.append(Finding(
            "manifests", "error", run_id,
            "gate row's source run {} is kind={!r}, not an eval run.".format(
                source_id, source.get("kind"))))
    if source.get("registry_ref") != entry_id:
        findings.append(Finding(
            "manifests", "error", run_id,
            "gate row for {} cites source run {}, which is an eval of {}. A "
            "verdict must be rendered against a run of its own "
            "entry.".format(entry_id, source_id,
                            source.get("registry_ref"))))
    if row.get("result_hash") != source.get("result_hash"):
        findings.append(Finding(
            "manifests", "error", run_id,
            "gate row's result_hash {} disagrees with source run {}'s {}. The "
            "verdict and the measurement name different numbers.".format(
                row.get("result_hash"), source_id, source.get("result_hash"))))
    if (source.get("code_hash"), source.get("data_hash")) != (
            row.get("code_hash"), row.get("data_hash")):
        # The gate row reuses the eval's hashes so the ledger's
        # (code_hash, data_hash, universe) dedup does not count it as a
        # second trial. A mismatch means either the count is inflated or the
        # row is describing a different run than it cites.
        findings.append(Finding(
            "manifests", "error", run_id,
            "gate row's (code_hash, data_hash) differ from source run {}'s. "
            "The gate row reuses the eval's pair so the dedup does not count "
            "it as a second trial; a different pair inflates the "
            "multiplicity count that every threshold reads from.".format(
                source_id)))

    manifest = _load_manifest(manifests_dir, source_id)
    if manifest is None:
        if manifests_dir:
            findings.append(Finding(
                "manifests", "error", run_id,
                "gate row cites source run {} whose manifest is missing from "
                "{}. The values the verdict compared cannot be "
                "recovered.".format(source_id, manifests_dir)))
        else:
            findings.append(Finding(
                "manifests", UNVERIFIABLE, run_id,
                "no manifests directory supplied, so the recorded criterion "
                "outcomes were not compared against the result."))
        return

    recorded = _recorded_statistics(manifest.get("result"))
    for outcome in criteria:
        name = outcome.get("name")
        statistic = outcome.get("statistic")
        if statistic not in recorded:
            findings.append(Finding(
                "manifests", "error", run_id,
                "criterion {} was rendered against statistic {!r}, which the "
                "source manifest does not carry as a gate-evaluable value "
                "(it holds {}). A verdict was compared against a number that "
                "is not in the bound record.".format(
                    name, statistic, sorted(recorded))))
            continue
        if outcome.get("infeasible"):
            # Infeasibility is a LOSS, not a value, and `actual` is null by
            # construction. Nothing to compare.
            continue
        if str(outcome.get("actual")) != str(recorded[statistic]):
            findings.append(Finding(
                "manifests", "error", run_id,
                "criterion {} records actual {!r} for statistic {!r} while "
                "the bound manifest holds {!r}. One of the two moved after "
                "the fact, and the verdict is no longer the comparison the "
                "record describes.".format(
                    name, outcome.get("actual"), statistic,
                    recorded[statistic])))


def _check_gate_blocks(row, run_id, entry_id, by_id, block_registry, findings):
    """The blocks a verdict spent are recorded as consumed by that entry.

    The consumption ledger is what replaced a hand-maintained table of
    preserved assets, and a gate row saying it touched six validation blocks
    while the block registry records no consumption would put the two
    authoritative stores in contradiction — with the block still readable by
    the next hypothesis.
    """
    if block_registry is None or not entry_id:
        return
    if entry_id not in by_id:
        return          # dangling registry_ref, reported by the reference check

    for block_id in row.get("blocks_touched") or []:
        try:
            block = block_registry.get_block(block_id)
        except KeyError:
            findings.append(Finding(
                "manifests", "error", run_id,
                "gate row touched block {}, which is not in the block "
                "registry.".format(block_id)))
            continue
        readers = {c.get("entry_id") for c in block.get("consumed_by") or []}
        if entry_id not in readers:
            findings.append(Finding(
                "manifests", "error", run_id,
                "gate row for {} spent block {} but the block registry records "
                "no consumption by that entry (readers: {}). E4 makes a "
                "V-block single-use, and the registry is what enforces it — "
                "an unrecorded consumption leaves spent data looking "
                "fresh.".format(entry_id, block_id, sorted(readers) or "none")))


def _check_ordering(entries, ledger, amendment_log, fill_log, findings):
    """Every gate run used an entry version whose paperwork was already filed.

    check_amendment_ordering has existed since 2b and has never been called
    outside its own tests. This is its production call site.
    """
    if ledger is None:
        return
    rows = ledger.all_rows()
    for entry in entries:
        entry_id = entry.get("id")
        results = check_amendment_ordering(
            entry_id=entry_id,
            original_hash=entry.get("hash"),
            amendments=(amendment_log.for_entry(entry_id)
                        if amendment_log else []),
            ledger_rows=rows,
            fills=fill_log.for_entry(entry_id) if fill_log else [],
        )
        for result in results:
            findings.append(Finding(
                "ordering", "error", result.get("run_id", entry_id),
                result.get("reason", "ordering violation")))


def catalog_citation_resolver(catalog_path):
    """Resolve 'failure catalog #N' citations against the real catalog.

    The #26 case, made checkable: reads the highest entry number the catalog
    actually contains, so a citation beyond it fails.

    Returns None if the catalog is absent — callers should then leave
    citations UNCHECKED rather than treating absence as success.
    """
    if not os.path.exists(catalog_path):
        return None

    import re

    # Entry numbers appear as a markdown heading ("## 14. ...") or as the
    # first cell of a table row ("| 14 |"). The cell may carry emphasis --
    # the real catalog writes its two most recent entries as "| **27** |".
    #
    # The first version of this missed the emphasized form and read the real
    # catalog as ending at #25, which would have flagged every legitimate #26
    # and #27 citation as dangling. Found by running the check against the
    # actual catalog rather than a synthesized one. A citation check that
    # cries wolf is worse than no check: it teaches you to skip the output,
    # and the one real dangling reference then goes past unread.
    _HEADING = re.compile(r"\s*#{1,4}\s*(\d+)\s*\.")
    _ROW = re.compile(r"\s*\|\s*[*_`]*\s*(\d+)\s*[*_`]*\s*\|")

    highest = 0
    with open(catalog_path) as fh:
        for line in fh:
            match = _HEADING.match(line) or _ROW.match(line)
            if match:
                highest = max(highest, int(match.group(1)))

    def resolve(citation):
        numbers = re.findall(
            r"(?:failure catalog|catalog)\s*#\s*(\d+)", citation, re.I)
        return all(0 < int(n) <= highest for n in numbers)

    resolve.highest = highest
    return resolve
