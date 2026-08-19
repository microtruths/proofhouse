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
        check: Which check produced it ('chain', 'schema', 'temporal',
            'ordering', 'references').
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
