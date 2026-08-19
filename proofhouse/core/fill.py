# proofhouse.core.fill — mechanical filling of pending registry fields
"""
Phase 2c §1 — A3's partial-lock mechanism, proven on a real case.

A field may be registered as PENDING with its rule pinned:

    power_check:
      inputs: {effect_size: "0.5", blocks: 3, prior_trials: 14}
      value: {pending: true, fill_rule: "power(effect_size, blocks, prior_trials)"}

Filling it is *executing the registration*, not amending it — the rule and the
inputs were both fixed before any data was seen, so no discretion is exercised
and nothing an amendment exists to catch has happened. This mirrors v11's
re-lock, which inserted T and the checkpoint outcome and nothing else.

A FILL IS NOT AN AMENDMENT, BUT IT MOVES THE HASH
--------------------------------------------------
Decision §7.2: a fill is recorded as its own chained event carrying
`entry_hash_after`, exactly as an amendment does. Two consequences:

  * `check_amendment_ordering` must consult fills alongside amendments when
    reconstructing which entry version was in force at each run. Reading only
    amendments would flag every post-fill gate run as having run ahead of its
    paperwork — a false positive in the one check whose worth depends on it
    firing rarely.
  * A fill must NOT trip the re-run lock. Making a fill invalidate the
    validation window would punish the mechanism A3 built precisely to avoid
    amendments.

THE INPUTS ARE PINNED, THE LEDGER IS NOT CONSULTED
---------------------------------------------------
Decision §7.1: `prior_trials` comes from the registered inputs, never from the
live ledger. The standing rule that "trial count comes FROM the ledger, never
a parameter" governs SCORING, where a hand-passed count is something a
researcher could lowball to weaken their own multiple-testing correction. Here
the count sits in an append-only, content-hashed record, and pinning it is
what makes the fill reproducible from the record alone — a fill that read the
live ledger would produce a different number depending on when it ran.
"""

from datetime import datetime, timezone

from proofhouse.chain.canonical import decimal_str
from proofhouse.core.amend import _ChainedLog
from proofhouse.eval.power import compute_power_check, compute_power_events

# Blocks are the 2-month unit from parent plan §2.4.1. Pinned here, in one
# named place, because the conversion below is the kind of error that produces
# no symptom: a power number is plausible at any magnitude, so reading
# `blocks: 3` as three YEARS rather than three two-month blocks would be wrong
# by a factor of sqrt(6) in the standard error and look entirely reasonable.
BLOCK_MONTHS = 2
MONTHS_PER_YEAR = 12

_ASCII_ALPHA = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_ASCII_DIGIT = frozenset("0123456789")
_IDENT_BODY = _ASCII_ALPHA | _ASCII_DIGIT | {"_"}


class FillRefused(Exception):
    """The fill cannot proceed. Nothing is written.

    Covers an unknown rule, an argument list that does not match the rule's
    signature, a field that is not pending, and a re-fill.
    """


def blocks_to_span_years(blocks):
    """Convert a block count to calendar years.

    3 blocks is HALF A YEAR, not three years.
    """
    return (int(blocks) * BLOCK_MONTHS) / float(MONTHS_PER_YEAR)


def _rule_power(inputs):
    """Resolve `power(effect_size, blocks, prior_trials)`.

    Maps the registered rule's vocabulary onto `compute_power_check`, whose
    signature is (expected_sharpe, span_years, trial_count):

        effect_size  -> expected_sharpe   direct
        blocks       -> span_years        UNIT CONVERSION, see above
        prior_trials -> trial_count       direct, from the pinned inputs

    threshold_quantile is left at its 0.95 default: one-sided, matching V-13's
    house choice, which is legitimate because the direction is fixed before
    the data is seen.
    """
    # Bools are numbers in Python. Refused here for the same reason
    # power_events refuses them and gate.py refuses them as statistics:
    # `prior_trials: true` becomes a single trial and the resulting number is
    # entirely plausible.
    for name in ("effect_size", "blocks", "prior_trials"):
        if isinstance(inputs.get(name), bool):
            raise FillRefused(
                "power() input {} is the boolean {!r}, not a number.".format(
                    name, inputs[name])
            )

    try:
        effect_size = float(inputs["effect_size"])
        blocks = int(inputs["blocks"])
        prior_trials = int(inputs["prior_trials"])
    except KeyError as exc:
        raise FillRefused(
            "power() requires inputs effect_size, blocks and prior_trials; "
            "{} is missing from the registered power_check.inputs".format(exc)
        )
    except (TypeError, ValueError) as exc:
        raise FillRefused(
            "power() inputs are not numeric: {}".format(exc)
        )

    span_years = blocks_to_span_years(blocks)
    try:
        result = compute_power_check(
            expected_sharpe=effect_size,
            span_years=span_years,
            trial_count=prior_trials,
        )
    except ValueError as exc:
        # A degenerate registration (0 blocks, 0 prior trials) surfaced as a
        # bare ValueError out of `fill` rather than as a refusal.
        raise FillRefused("power() refused: {}".format(exc))

    return {
        "value": result["power"],
        "flagged": bool(result["flagged"]),
        # The intermediates, so a reader re-derives the number independently
        # rather than taking it on trust. span_years is here specifically
        # because it is the step where the unit error would hide.
        "derivation": {
            "span_years": decimal_str(span_years),
            "se": decimal_str(result["se"]),
            "z_effect": decimal_str(result["z_effect"]),
            "z_threshold": decimal_str(result["z_threshold"]),
            "threshold_quantile": decimal_str(
                result["inputs"]["threshold_quantile"]),
        },
    }


# The complete rule whitelist. A fill rule is a NAMED RECIPE, not arithmetic.
#
# 2a's formula parser deliberately admits single-argument calls only, and
# widening that grammar to n-ary calls would enlarge the surface guarding the
# gate in order to serve a feature that needs no grammar at all. A lookup has
# nothing to escape from.
def _rule_power_events(inputs):
    """Resolve `power_events(effect_ratio, n_events, n_clusters, icc,
    prior_trials)`.

    Every input maps straight through — there is no unit conversion here,
    which is the one respect in which this rule is simpler than `power()`.
    `effect_ratio` is dimensionless by construction (a mean over its own
    standard deviation), which is exactly why it can score the designs the
    Sharpe rule cannot.

    icc IS REQUIRED. A missing icc refuses rather than defaulting to zero.
    Defaulting would assert independence across events — the assumption that
    inflates power more than any other — and a warning does not help, because
    the warning is transient and the number it produced goes into the record
    and stays there. This is the same refusal `power()` already makes when a
    registration has no Sharpe-denominated effect size, and that refusal is
    recorded in the v11 fixture as correct behaviour.
    """
    missing = [name for name in
               ("effect_ratio", "n_events", "n_clusters", "icc",
                "prior_trials")
               if name not in inputs]
    if missing:
        raise FillRefused(
            "power_events() requires inputs effect_ratio, n_events, "
            "n_clusters, icc and prior_trials; {} missing from the registered "
            "power_check.inputs. In particular icc has NO DEFAULT: icc=0 "
            "asserts every event is independent, which is the assumption most "
            "likely to inflate this number. Register it explicitly, including "
            "as 0 if that is genuinely meant.".format(", ".join(missing))
        )

    # Booleans are numbers in Python: int(True) is 1 and float(True) is 1.0,
    # so `icc: true` would silently become perfect within-cluster correlation
    # and `prior_trials: true` a single trial. Both produce a plausible power
    # number from a registration that plainly meant something else. gate.py
    # already refuses bools as type errors for exactly this reason; the same
    # predicate belongs here.
    for name in ("effect_ratio", "n_events", "n_clusters", "icc",
                 "prior_trials"):
        if isinstance(inputs[name], bool):
            raise FillRefused(
                "power_events() input {} is the boolean {!r}, not a number. "
                "Python would read it as {}, producing a plausible power from "
                "a registration that meant something else.".format(
                    name, inputs[name], int(inputs[name]))
            )

    try:
        effect_ratio = float(inputs["effect_ratio"])
        n_events = int(inputs["n_events"])
        n_clusters = int(inputs["n_clusters"])
        icc = float(inputs["icc"])
        prior_trials = int(inputs["prior_trials"])
    except (TypeError, ValueError) as exc:
        # A null icc lands here rather than in the missing-key branch above,
        # so name it explicitly — "could not convert None" would not tell a
        # reader which input to go and register.
        raise FillRefused(
            "power_events() inputs are not numeric ({}). Note that a null or "
            "blank icc is not a zero icc — it must be registered as a "
            "number.".format(exc)
        )

    try:
        result = compute_power_events(
            effect_ratio=effect_ratio,
            n_events=n_events,
            n_clusters=n_clusters,
            icc=icc,
            trial_count=prior_trials,
        )
    except ValueError as exc:
        raise FillRefused("power_events() refused: {}".format(exc))

    # deff is the step where a unit or clustering error would hide, the same
    # role span_years plays in power()'s derivation. n_eff_cap and pct_of_cap
    # are here because past roughly G/icc more events stop buying power, and a
    # design planning to accumulate its way out of a weak result needs to know
    # that before it starts.
    derivation = {
        "m_bar": decimal_str(result["m_bar"]),
        "deff": decimal_str(result["deff"]),
        "n_eff": decimal_str(result["n_eff"]),
        "saturated": bool(result["saturated"]),
        "z_effect": decimal_str(result["z_effect"]),
        "z_threshold": decimal_str(result["z_threshold"]),
        "threshold_quantile": decimal_str(
            result["inputs"]["threshold_quantile"]),
    }
    # Absent when icc is 0, because then there IS no cap. Recording "inf"
    # would put a value in the record that parses back as a number.
    if result["n_eff_cap"] is not None:
        derivation["n_eff_cap"] = decimal_str(result["n_eff_cap"])
        derivation["pct_of_cap"] = decimal_str(result["pct_of_cap"])

    return {
        "value": result["power"],
        "flagged": bool(result["flagged"]),
        "derivation": derivation,
    }


_FILL_RULES = {
    "power": (_rule_power, ("effect_size", "blocks", "prior_trials")),
    "power_events": (_rule_power_events,
                     ("effect_ratio", "n_events", "n_clusters", "icc",
                      "prior_trials")),
}


def parse_fill_rule(rule):
    """Split a pinned rule into (name, argument names).

    Deliberately not a grammar — a rule is `name(arg, arg, ...)` and anything
    else is refused rather than interpreted.

    Raises:
        FillRefused: On any shape that is not exactly one call.
    """
    if not rule or not str(rule).strip():
        raise FillRefused("fill_rule is missing or empty.")
    text = str(rule).strip()

    if not text.endswith(")") or "(" not in text:
        raise FillRefused(
            "fill_rule must be of the form name(arg, ...); got {!r}".format(
                text)
        )
    name, _, arg_text = text[:-1].partition("(")
    name = name.strip()
    if "(" in arg_text:
        raise FillRefused(
            "fill_rule admits exactly one call, not nesting: {!r}".format(text)
        )

    args = [a.strip() for a in arg_text.split(",")] if arg_text.strip() else []
    for token in [name] + args:
        if not token or not all(c in _IDENT_BODY for c in token):
            raise FillRefused(
                "fill_rule {!r} contains {!r}, which is not a plain "
                "identifier. Fill rules name a recipe; they do not compute "
                "anything.".format(text, token)
            )
    return name, args


def resolve_fill_rule(rule, inputs):
    """Execute a pinned fill rule against registered inputs.

    Args:
        rule: The pinned `fill_rule` string.
        inputs: The registered `inputs` mapping.

    Returns:
        Dict with 'value', 'flagged' and 'derivation'.

    Raises:
        FillRefused: Unknown rule, or an argument list that does not match the
            rule's signature exactly.
    """
    name, args = parse_fill_rule(rule)

    if name not in _FILL_RULES:
        raise FillRefused(
            "Unknown fill rule {!r}. Known rules: {}. There is no fallback — "
            "a rule the tool does not recognise cannot be filled "
            "'approximately'.".format(name, sorted(_FILL_RULES))
        )

    fn, expected = _FILL_RULES[name]
    if tuple(args) != expected:
        raise FillRefused(
            "fill_rule {!r} takes {} in that order; got {}. Argument names are "
            "checked rather than positions, because a rule whose arguments "
            "were silently reordered would still produce a plausible "
            "number.".format(name, list(expected), args)
        )

    return fn(inputs)


class FillLog(_ChainedLog):
    """Chained record of pending-field fills.

    Same construction as AmendmentLog — content-hashed, prev inside the hashed
    payload so insertion and deletion are both detectable (failure catalog
    #27).
    """

    _prefix = "F"

    def record(self, entry_id, field, rule, inputs, resolved,
               entry_hash_before, entry_hash_after, ledger_anchor=None):
        """Record a fill.

        `ledger_anchor` is the run_id of the last ledger row at filling time,
        the same anchoring scheme amendments use, so fills and amendments sort
        into one order without depending on second-resolution timestamps.
        """
        row = {
            "fill_id": "{}-{:04d}".format(self._prefix, self._next),
            "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "entry_id": entry_id,
            "field": field,
            "fill_rule": rule,
            "inputs": dict(inputs or {}),
            "filled_value": decimal_str(resolved["value"]),
            "flagged": bool(resolved["flagged"]),
            "derivation": dict(resolved["derivation"]),
            "entry_hash_before": entry_hash_before,
            "entry_hash_after": entry_hash_after,
            "ledger_anchor": ledger_anchor,
        }
        self._next += 1
        return self._write_and_chain(row)

    def for_entry(self, entry_id):
        """Fills for one entry, in filing order."""
        return [dict(r) for r in self._rows if r.get("entry_id") == entry_id]


def compose_filled_entry(entry, fills):
    """Apply recorded fills to a registered entry, in order.

    THE REGISTERED ENTRY FILE IS NEVER REWRITTEN. Registry entries are chained
    across files by prev -> hash, so editing one in place would change its
    hash and orphan every later entry whose `prev` points at the old value.
    Append-only is not a stylistic preference here; it is what the chain is
    made of.

    So a fill lives in the fill log, and the entry a gate actually runs
    against is the registered one composed with its fills. That composed view
    is what `entry_hash_after` refers to, and what a gate row should record as
    its `entry_hash` — which is exactly what makes check_amendment_ordering
    able to tell which version was in force.

    Args:
        entry: The registered entry dict.
        fills: Fill records for it, in filing order.

    Returns:
        A new entry dict with each filled field applied. The input is not
        mutated.
    """
    import copy

    composed = copy.deepcopy(entry)
    for record in fills or []:
        field = record.get("field")
        block = composed.get(field)
        if not isinstance(block, dict) or not isinstance(
                block.get("value"), dict):
            continue
        block["value"].update({
            "pending": False,
            "filled": record.get("filled_value"),
            "flagged": record.get("flagged"),
            "derivation": dict(record.get("derivation") or {}),
            "filled_at": record.get("ts_utc"),
        })
    return composed


def fill_pending_field(entry, field, ledger_anchor=None):
    """Fill one pending field from its pinned rule.

    Returns an IN-MEMORY composed entry. The caller records the result in a
    FillLog and does NOT write it back over the registered entry file — see
    compose_filled_entry for why that would break the chain.

    Pure with respect to the entry: returns a new dict rather than mutating
    the caller's, so a refusal partway through cannot leave a half-filled
    record behind.

    Args:
        entry: The registered entry dict.
        field: Top-level field name, e.g. 'power_check'.
        ledger_anchor: run_id of the last ledger row, for ordering.

    Returns:
        (filled_entry, resolved) — the entry with the field filled, and the
        resolution detail for the log. The caller re-hashes the entry and then
        records the event, because only the caller knows the resulting hash.

    Raises:
        FillRefused: Field absent, not pending, or already filled.
    """
    import copy

    entry = copy.deepcopy(entry)
    block = entry.get(field)
    if not isinstance(block, dict):
        raise FillRefused(
            "Entry {} has no field {!r} to fill.".format(entry.get("id"), field)
        )

    value = block.get("value")
    if not isinstance(value, dict):
        raise FillRefused(
            "{}.value is not a pending-field mapping.".format(field)
        )

    if value.get("pending") is not True:
        # A3's whole point: a filled field is closed. Re-filling would let a
        # value be recomputed under different inputs while still presenting as
        # the mechanically-derived one.
        raise FillRefused(
            "{}.{} is not pending (pending={!r}). A filled field cannot be "
            "re-filled — that is the lock A3 exists to provide.".format(
                entry.get("id"), field, value.get("pending"))
        )

    resolved = resolve_fill_rule(value.get("fill_rule"), block.get("inputs"))

    value["pending"] = False
    # fill_rule is kept, not consumed: the record must stay auditable after
    # the fact, and a reader needs the rule to re-derive the value.
    value["filled"] = decimal_str(resolved["value"])
    value["flagged"] = resolved["flagged"]
    value["derivation"] = dict(resolved["derivation"])
    value["filled_at"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")

    return entry, resolved
