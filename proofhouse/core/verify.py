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
from proofhouse.schema.validate import ValidationError, validate_entry
from proofhouse.schema.versions import SCHEMA_VERSION


class Finding:
    """One integrity problem.

    Attributes:
        check: Which check produced it ('chain', 'schema', 'blocks',
            'runnable', 'temporal', 'ordering', 'references').
        severity: 'error' or 'warning'. Errors fail the verify.
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
               citation_resolver=None):
    """Run every integrity check. Returns (ok, findings).

    Args:
        registry, ledger, amendment_log, fill_log, rejected_log: the logs to
            check. Any may be None; a missing log is skipped, not failed.
        block_registry: for schema re-validation.
        citation_resolver: callable(citation) -> bool, resolving an external
            citation. When None, external citations are reported as UNCHECKED
            warnings rather than silently passed — an unverifiable claim must
            not read as a verified one.

    Returns:
        (ok, findings) where ok is True only if no finding has severity
        'error'.
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
