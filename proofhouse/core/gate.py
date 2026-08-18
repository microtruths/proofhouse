# proofhouse.core.gate — the gate engine
"""
Phase 2a — reads pre-committed thresholds, returns pass/fail with no
interpretation.

Parent plan §5.4: *"Reports 0.0495 against >= 0.05 as a fail."* That is not a
hypothetical. v10's IC gate hit exactly it at the >=100-episode stratum:
"IC = 0.0495, rounds to 0.050 but t = 2.11 with n = 1,820 — not a pass under
strict comparison." This module renders the comparison and nothing else. No
rounding, no "essentially passes", no narrative.

THREE PROPERTIES THIS MODULE EXISTS TO HOLD
-------------------------------------------
1. **Purity.** No clock, no filesystem, no randomness, no network. Given the
   same recorded inputs it produces the same verdict forever — that is what
   makes a stored verdict meaningful rather than a snapshot of a mood.
   Enforced structurally by test S-3, which AST-scans this file's imports.

2. **No eval().** `gate.combination` and derived-threshold formulas are
   registry-supplied strings. Handing them to Python's `eval()` would be
   arbitrary code execution driven by a registry field, inside a tool whose
   entire premise is integrity. Both get real parsers over a closed grammar.
   Unknown token -> refuse, never guess.

3. **A4 diagnostics isolation.** The signature accepts ScoredResult and has no
   parameter through which a Diagnostics object could arrive. The verdict
   function cannot consult diagnostics because it is not given them. Phase 1a
   built the separation; this is where it earns its keep.

THE DISTINCTION THIS MODULE IS MOST LIKELY TO GET WRONG
--------------------------------------------------------
From A-12 §A12.5: *"Operational failure and data outcome must never be
conflated."* Two superficially similar situations, opposite handling:

  * The strategy ran and could not form a position — NaN, empty cohort, no
    fills. That is an **economic outcome**: the edge was not there when it
    was needed. It counts as a **LOSS** and the criterion fails.

  * The pipeline broke — a statistic was never produced, the adapter raised,
    the artifact was missing. That is **not a gate outcome at all**. This
    module raises `GateExecutionError` and refuses to render a verdict. Halt,
    repair, re-run.

An automated system gets this wrong by default, and getting it wrong in the
permissive direction manufactures false negatives that look like evidence.
"""

import math

from proofhouse.chain.canonical import decimal_str
from proofhouse.schema.models import VALID_COMPARATORS

# Reserved words in the combination grammar. A criterion may not be named one
# of these — `criteria: {not: ...}` would make "not g1" ambiguous.
_RESERVED = frozenset({"and", "or", "not"})

# The complete function whitelist for derived-threshold formulas. Adding to
# this is a deliberate act, not a convenience: every entry here is something a
# registered threshold may depend on, and therefore something an independent
# verifier must reproduce exactly.
_FORMULA_FUNCTIONS = frozenset({"median", "mean", "std", "abs"})

_ASCII_ALPHA = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_ASCII_DIGIT = frozenset("0123456789")

# Identifiers are ASCII-only, deliberately. Python's str.isalpha() accepts
# Unicode, which would let a criterion named with a Cyrillic 'а' sit in a
# registry looking identical to one named with an ASCII 'a'. In a tool whose
# output is meant to be auditable by someone who did not write the record,
# visually-identical distinct identifiers are a hole, not a feature.
_IDENT_START = _ASCII_ALPHA | {"_"}
_IDENT_BODY = _ASCII_ALPHA | _ASCII_DIGIT | {"_"}


class GateError(Exception):
    """Base for every failure in this module."""


class GateRefusal(GateError):
    """The gate specification is malformed and cannot be evaluated.

    A refusal is NOT a failed gate. Nothing is recorded as an outcome; the
    registration is broken and must be fixed. Raised for unparseable
    combinations, unknown criterion references, formula syntax errors, and
    whitelist violations.
    """


class GateExecutionError(GateError):
    """The pipeline did not deliver what the gate needs.

    Distinct from GateRefusal (the spec is wrong) and from a failing verdict
    (the data said no). This is "we never got a real answer" — a missing
    statistic, an absent derived input. Per §2.5 this is not a gate outcome:
    halt, repair, re-run. Never record a verdict from a failed execution.
    """


class CriterionOutcome:
    """One criterion's comparison, rendered.

    Everything needed to re-derive the pass/fail by hand from the record.
    Numeric fields are canonical decimal strings, not floats, so a stored
    outcome hashes identically for an independent verifier (canonical rule 3).

    Attributes:
        name: Criterion name as registered (e.g. 'g1').
        statistic: Name of the statistic compared.
        comparator: One of > >= < <=.
        threshold: Threshold as a canonical decimal string.
        threshold_source: 'literal' or 'derived'.
        actual: Observed statistic as a canonical decimal string, or None
            when infeasible.
        passed: True if the comparison held.
        infeasible: True if the statistic was NaN/None — counted as a loss.
        derived_formula: The pinned formula, when threshold_source='derived'.
        derived_source_blocks: Blocks the derived inputs came from.
    """

    def __init__(self, name, statistic, comparator, threshold,
                 threshold_source, actual, passed, infeasible=False,
                 derived_formula=None, derived_source_blocks=None):
        self.name = name
        self.statistic = statistic
        self.comparator = comparator
        self.threshold = threshold
        self.threshold_source = threshold_source
        self.actual = actual
        self.passed = passed
        self.infeasible = infeasible
        self.derived_formula = derived_formula
        self.derived_source_blocks = derived_source_blocks or []

    def to_dict(self):
        """Serialize for the gate ledger row. Nulls are dropped by the
        canonical serializer, so optional fields cost nothing when unset."""
        out = {
            "name": self.name,
            "statistic": self.statistic,
            "comparator": self.comparator,
            "threshold": self.threshold,
            "threshold_source": self.threshold_source,
            "actual": self.actual,
            "passed": self.passed,
        }
        if self.infeasible:
            out["infeasible"] = True
        if self.derived_formula is not None:
            out["derived_formula"] = self.derived_formula
            out["derived_source_blocks"] = list(self.derived_source_blocks)
        return out

    def __repr__(self):
        return "CriterionOutcome({} {} {} {} -> {})".format(
            self.name, self.actual, self.comparator, self.threshold,
            "PASS" if self.passed else "FAIL"
        )


class Verdict:
    """The rendered gate result.

    Attributes:
        entry_id: Registry entry this verdict belongs to.
        combination: The registered combination expression, verbatim.
        criteria: List of CriterionOutcome, in registered order.
        passed: The overall verdict.
        pass_licenses: What a pass licenses (resolved from the entry).
        pass_does_not_establish: What a pass does NOT establish.
    """

    def __init__(self, entry_id, combination, criteria, passed,
                 pass_licenses=None, pass_does_not_establish=None):
        self.entry_id = entry_id
        self.combination = combination
        self.criteria = criteria
        self.passed = passed
        self.pass_licenses = pass_licenses
        self.pass_does_not_establish = pass_does_not_establish

    def to_dict(self):
        """Serialize for the gate ledger row."""
        return {
            "entry_id": self.entry_id,
            "combination": self.combination,
            "criteria": [c.to_dict() for c in self.criteria],
            "passed": self.passed,
            "pass_licenses": self.pass_licenses,
            "pass_does_not_establish": self.pass_does_not_establish,
        }

    def __repr__(self):
        return "Verdict({} -> {}, {} criteria)".format(
            self.entry_id, "PASS" if self.passed else "FAIL", len(self.criteria)
        )


# ---------------------------------------------------------------------------
# Combination grammar
#
#   expr    := or_expr
#   or_expr := and_expr ("or" and_expr)*
#   and_expr:= not_expr ("and" not_expr)*
#   not_expr:= "not" not_expr | atom
#   atom    := "(" expr ")" | IDENT
#
# Precedence: not > and > or, matching Python and ordinary reading. Nodes are
# tuples: ("ident", name) | ("not", node) | ("and", l, r) | ("or", l, r).
# ---------------------------------------------------------------------------

def _tokenize_combination(text):
    """Tokenize a combination expression.

    Returns:
        List of (kind, value, position) where kind is 'IDENT', 'and', 'or',
        'not', '(' or ')'.

    Raises:
        GateRefusal: On any character outside the grammar.
    """
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\n\r":
            i += 1
            continue
        if ch in "()":
            tokens.append((ch, ch, i))
            i += 1
            continue
        if ch in _IDENT_START:
            j = i
            while j < n and text[j] in _IDENT_BODY:
                j += 1
            word = text[i:j]
            tokens.append((word if word in _RESERVED else "IDENT", word, i))
            i = j
            continue
        # Everything else — operators, quotes, dots, calls, non-ASCII — is
        # outside the grammar. This is the line that stops `combination` from
        # being an injection vector: there is no character class that reaches
        # an evaluator.
        raise GateRefusal(
            "gate.combination: unexpected character {!r} at position {} in "
            "{!r}. The combination grammar admits only criterion names, "
            "'and', 'or', 'not' and parentheses.".format(ch, i, text)
        )
    return tokens


class _CombinationParser:
    """Recursive-descent parser for combination expressions."""

    def __init__(self, tokens, text):
        self.tokens = tokens
        self.text = text
        self.pos = 0

    def _peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return (None, None, len(self.text))

    def _advance(self):
        tok = self._peek()
        self.pos += 1
        return tok

    def parse(self):
        if not self.tokens:
            raise GateRefusal(
                "gate.combination is empty. Required, no default (E1/V-3)."
            )
        node = self._parse_or()
        if self.pos != len(self.tokens):
            kind, value, position = self._peek()
            raise GateRefusal(
                "gate.combination: trailing token {!r} at position {} in {!r}. "
                "The expression is complete before it.".format(
                    value, position, self.text)
            )
        return node

    def _parse_or(self):
        node = self._parse_and()
        while self._peek()[0] == "or":
            self._advance()
            node = ("or", node, self._parse_and())
        return node

    def _parse_and(self):
        node = self._parse_not()
        while self._peek()[0] == "and":
            self._advance()
            node = ("and", node, self._parse_not())
        return node

    def _parse_not(self):
        if self._peek()[0] == "not":
            self._advance()
            return ("not", self._parse_not())
        return self._parse_atom()

    def _parse_atom(self):
        kind, value, position = self._advance()
        if kind == "IDENT":
            return ("ident", value)
        if kind == "(":
            node = self._parse_or()
            if self._peek()[0] != ")":
                raise GateRefusal(
                    "gate.combination: unclosed '(' in {!r}".format(self.text)
                )
            self._advance()
            return node
        if kind is None:
            raise GateRefusal(
                "gate.combination: expression ends early in {!r} — expected a "
                "criterion name.".format(self.text)
            )
        raise GateRefusal(
            "gate.combination: unexpected {!r} at position {} in {!r}".format(
                value, position, self.text)
        )


def parse_combination(text):
    """Parse a combination expression into an AST.

    Args:
        text: The registered `gate.combination` string.

    Returns:
        An AST node tuple.

    Raises:
        GateRefusal: If the expression is empty or malformed.
    """
    if text is None or not str(text).strip():
        raise GateRefusal(
            "gate.combination is missing or empty. Required, no default "
            "(E1/V-3)."
        )
    return _CombinationParser(_tokenize_combination(str(text)), str(text)).parse()


def combination_identifiers(node):
    """Return the set of criterion names referenced by an AST."""
    kind = node[0]
    if kind == "ident":
        return {node[1]}
    if kind == "not":
        return combination_identifiers(node[1])
    return combination_identifiers(node[1]) | combination_identifiers(node[2])


def evaluate_combination(node, values):
    """Evaluate a combination AST against per-criterion booleans.

    Args:
        node: AST from parse_combination.
        values: Dict of {criterion_name: bool}.

    Returns:
        bool.

    Raises:
        GateRefusal: If the expression names a criterion not in `values`.
    """
    kind = node[0]
    if kind == "ident":
        if node[1] not in values:
            raise GateRefusal(
                "gate.combination references criterion {!r}, which is not in "
                "gate.criteria. Known criteria: {}. No partial evaluation — a "
                "combination that names something that does not exist is a "
                "broken registration, not a failing gate.".format(
                    node[1], sorted(values) or "(none)")
            )
        return values[node[1]]
    if kind == "not":
        return not evaluate_combination(node[1], values)
    if kind == "and":
        # Both sides evaluated deliberately, no short-circuit: an unknown
        # identifier on the right of a False `and` must still refuse rather
        # than be skipped over. Refusal must not depend on evaluation order.
        left = evaluate_combination(node[1], values)
        right = evaluate_combination(node[2], values)
        return left and right
    left = evaluate_combination(node[1], values)
    right = evaluate_combination(node[2], values)
    return left or right


# ---------------------------------------------------------------------------
# Derived-threshold formula grammar
#
#   expr   := term (("+"|"-") term)*
#   term   := factor (("*"|"/") factor)*
#   factor := "-" factor | atom
#   atom   := NUMBER | FUNC "(" expr ")" | IDENT | "(" expr ")"
#
# Values are floats or lists of floats. Arithmetic requires scalars; the
# aggregate functions require lists. That type discipline is deliberate:
# `0.5 * sigma_w` where sigma_w is a distribution should refuse, not silently
# broadcast into something that looks like a number.
# ---------------------------------------------------------------------------

def _tokenize_formula(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\n\r":
            i += 1
            continue
        if ch in "()+-*/,":
            tokens.append((ch, ch, i))
            i += 1
            continue
        if ch in _ASCII_DIGIT or ch == ".":
            j = i
            seen_dot = False
            while j < n and (text[j] in _ASCII_DIGIT or text[j] == "."):
                if text[j] == ".":
                    if seen_dot:
                        raise GateRefusal(
                            "derived formula: malformed number at position {} "
                            "in {!r}".format(i, text)
                        )
                    seen_dot = True
                j += 1
            # Exponent notation, e.g. 1e-4.
            if j < n and text[j] in "eE":
                k = j + 1
                if k < n and text[k] in "+-":
                    k += 1
                if k < n and text[k] in _ASCII_DIGIT:
                    while k < n and text[k] in _ASCII_DIGIT:
                        k += 1
                    j = k
            raw = text[i:j]
            try:
                tokens.append(("NUMBER", float(raw), i))
            except ValueError:
                raise GateRefusal(
                    "derived formula: malformed number {!r} at position {} in "
                    "{!r}".format(raw, i, text)
                )
            i = j
            continue
        if ch in _IDENT_START:
            j = i
            while j < n and text[j] in _IDENT_BODY:
                j += 1
            tokens.append(("IDENT", text[i:j], i))
            i = j
            continue
        raise GateRefusal(
            "derived formula: unexpected character {!r} at position {} in "
            "{!r}. Admitted: numbers, variable names, the whitelisted "
            "functions {}, and + - * / ( ).".format(
                ch, i, text, sorted(_FORMULA_FUNCTIONS))
        )
    return tokens


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _mean(values):
    return math.fsum(values) / float(len(values))


def _std(values):
    # SAMPLE standard deviation, ddof=1. Pinned here rather than left to the
    # caller because the two conventions differ by sqrt(n/(n-1)) and a
    # threshold is exactly the place where that silently moves a verdict.
    # These inputs are samples drawn from a null distribution, so the sample
    # estimator is the correct one.
    n = len(values)
    if n < 2:
        raise GateRefusal(
            "derived formula: std() needs at least 2 values (sample standard "
            "deviation, ddof=1); got {}.".format(n)
        )
    mu = _mean(values)
    return math.sqrt(math.fsum((v - mu) ** 2 for v in values) / float(n - 1))


_AGGREGATES = {"median": _median, "mean": _mean, "std": _std}


class _FormulaParser:
    """Recursive-descent parser/evaluator for derived-threshold formulas."""

    def __init__(self, tokens, text, variables):
        self.tokens = tokens
        self.text = text
        self.variables = variables
        self.pos = 0

    def _peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return (None, None, len(self.text))

    def _advance(self):
        tok = self._peek()
        self.pos += 1
        return tok

    def parse(self):
        if not self.tokens:
            raise GateRefusal("derived formula is empty.")
        value = self._parse_expr()
        if self.pos != len(self.tokens):
            _, val, position = self._peek()
            raise GateRefusal(
                "derived formula: trailing token {!r} at position {} in "
                "{!r}".format(val, position, self.text)
            )
        return self._require_scalar(value, "the formula result")

    def _require_scalar(self, value, what):
        if isinstance(value, list):
            raise GateRefusal(
                "derived formula: {} is a distribution of {} values, not a "
                "number. Wrap it in one of {} to reduce it.".format(
                    what, len(value), sorted(_AGGREGATES))
            )
        return value

    def _parse_expr(self):
        value = self._parse_term()
        while self._peek()[0] in ("+", "-"):
            op = self._advance()[0]
            left = self._require_scalar(value, "the left operand of '%s'" % op)
            right = self._require_scalar(
                self._parse_term(), "the right operand of '%s'" % op)
            value = left + right if op == "+" else left - right
        return value

    def _parse_term(self):
        value = self._parse_factor()
        while self._peek()[0] in ("*", "/"):
            op = self._advance()[0]
            left = self._require_scalar(value, "the left operand of '%s'" % op)
            right = self._require_scalar(
                self._parse_factor(), "the right operand of '%s'" % op)
            if op == "/":
                if right == 0.0:
                    raise GateRefusal(
                        "derived formula: division by zero in {!r}".format(
                            self.text)
                    )
                value = left / right
            else:
                value = left * right
        return value

    def _parse_factor(self):
        if self._peek()[0] == "-":
            self._advance()
            return -self._require_scalar(self._parse_factor(), "a negated value")
        if self._peek()[0] == "+":
            self._advance()
            return self._require_scalar(self._parse_factor(), "a signed value")
        return self._parse_atom()

    def _parse_atom(self):
        kind, value, position = self._advance()
        if kind == "NUMBER":
            return value
        if kind == "(":
            inner = self._parse_expr()
            if self._peek()[0] != ")":
                raise GateRefusal(
                    "derived formula: unclosed '(' in {!r}".format(self.text)
                )
            self._advance()
            return inner
        if kind == "IDENT":
            if value in _FORMULA_FUNCTIONS:
                return self._parse_call(value, position)
            if value not in self.variables:
                raise GateRefusal(
                    "derived formula: unknown name {!r} at position {} in "
                    "{!r}. Known variables: {}. Whitelisted functions: {}. "
                    "Unknown names are refused, never resolved from any "
                    "ambient scope.".format(
                        value, position, self.text,
                        sorted(self.variables) or "(none)",
                        sorted(_FORMULA_FUNCTIONS))
                )
            return self.variables[value]
        if kind is None:
            raise GateRefusal(
                "derived formula ends early in {!r}".format(self.text)
            )
        raise GateRefusal(
            "derived formula: unexpected {!r} at position {} in {!r}".format(
                value, position, self.text)
        )

    def _parse_call(self, name, position):
        if self._peek()[0] != "(":
            raise GateRefusal(
                "derived formula: {}() must be called, not referenced, at "
                "position {} in {!r}".format(name, position, self.text)
            )
        self._advance()
        arg = self._parse_expr()
        if self._peek()[0] == ",":
            raise GateRefusal(
                "derived formula: {}() takes exactly one argument in "
                "{!r}".format(name, self.text)
            )
        if self._peek()[0] != ")":
            raise GateRefusal(
                "derived formula: unclosed call to {}() in {!r}".format(
                    name, self.text)
            )
        self._advance()

        if name == "abs":
            return abs(self._require_scalar(arg, "the argument to abs()"))
        if not isinstance(arg, list):
            raise GateRefusal(
                "derived formula: {}() takes a distribution, got a single "
                "number, in {!r}".format(name, self.text)
            )
        if not arg:
            raise GateRefusal(
                "derived formula: {}() got an empty distribution in "
                "{!r}".format(name, self.text)
            )
        return _AGGREGATES[name](arg)


def evaluate_formula(text, variables):
    """Evaluate a derived-threshold formula. No eval(), closed grammar.

    Args:
        text: The pinned formula, e.g. '0.5 * median(sigma_w)'.
        variables: Dict of {name: float or list-of-float}.

    Returns:
        float.

    Raises:
        GateRefusal: On any syntax error, unknown name, type mismatch, or
            attempt to reach outside the whitelist.
    """
    if text is None or not str(text).strip():
        raise GateRefusal("derived threshold has no formula.")
    return _FormulaParser(
        _tokenize_formula(str(text)), str(text), variables or {}
    ).parse()


# ---------------------------------------------------------------------------
# Threshold resolution
# ---------------------------------------------------------------------------

def _require_finite(crit_name, value, what):
    """Refuse a threshold that is not a finite number.

    A non-finite threshold does not fail loudly — it fails INVISIBLY, which is
    why this is a refusal rather than a comparison that happens to come out
    False:

        threshold "-inf"  ->  every >= comparison is True  ->  unconditional
                              PASS, with a record that reads as legitimate
        threshold "inf"   ->  every >= comparison is False ->  unconditional
                              FAIL that looks like evidence
        threshold "nan"   ->  EVERY comparison is False, in both directions
        threshold "1e400" ->  silently overflows to inf

    The unconditional-PASS case is the one that matters. §2.1's promise is
    that the engine renders a comparison and nothing else; a threshold no
    observation can fail is not a comparison, and a gate that cannot fail is
    worse than no gate because it produces a passing record.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GateRefusal(
            "gate.criteria.{}: {} is {!r}, not a number.".format(
                crit_name, what, value)
        )
    if math.isnan(value) or math.isinf(value):
        raise GateRefusal(
            "gate.criteria.{}: {} resolves to {}, which no observation can be "
            "compared against meaningfully. A threshold of -inf passes "
            "unconditionally, inf and nan fail unconditionally, and all three "
            "produce a record that reads as a real verdict. Register a finite "
            "threshold.".format(crit_name, what, value)
        )
    return float(value)


def _resolve_threshold(crit_name, threshold, derived_inputs, block_registry):
    """Resolve a criterion's threshold to a float.

    Returns:
        (value, source, formula, source_blocks)

    Raises:
        GateRefusal: Malformed threshold spec, or a derived threshold whose
            source blocks are validation blocks (E2 anti-circularity).
        GateExecutionError: A derived threshold whose inputs were not
            supplied.
    """
    if not isinstance(threshold, dict):
        raise GateRefusal(
            "gate.criteria.{}.threshold must be a mapping with exactly one of "
            "'literal' or 'derived'; got {!r}".format(crit_name, threshold)
        )
    has_literal = "literal" in threshold
    has_derived = "derived" in threshold
    if has_literal == has_derived:
        raise GateRefusal(
            "gate.criteria.{}.threshold must carry exactly one of 'literal' "
            "or 'derived'; got keys {}".format(
                crit_name, sorted(threshold) or "(none)")
        )

    if has_literal:
        raw = threshold["literal"]
        try:
            parsed = float(raw)
        except (TypeError, ValueError):
            raise GateRefusal(
                "gate.criteria.{}.threshold.literal is not a number: "
                "{!r}".format(crit_name, raw)
            )
        return (_require_finite(crit_name, parsed, "threshold.literal"),
                "literal", None, [])

    derived = threshold["derived"] or {}
    formula = derived.get("formula")
    source_blocks = list(derived.get("source_blocks", []))

    # E2 anti-circularity, re-checked HERE and not only at registration.
    # Phase 1's review established why: a registration-time check on a field
    # that is empty at registration time is not a check. The rule from v11
    # Registration 5 is verbatim — "do not re-derive T from validation data
    # (that would be fitting the threshold to the data it polices)."
    if block_registry is not None:
        for block_id in source_blocks:
            try:
                block = block_registry.get_block(block_id)
            except KeyError:
                raise GateRefusal(
                    "gate.criteria.{}: derived threshold names source block "
                    "{!r}, which is not in the block registry.".format(
                        crit_name, block_id)
                )
            if block.get("segment") == "V":
                raise GateRefusal(
                    "gate.criteria.{}: derived threshold draws on block {} "
                    "(segment V). A threshold may not be derived from the "
                    "validation data it polices (E2 / A2).".format(
                        crit_name, block_id)
                )

    supplied = (derived_inputs or {}).get(crit_name)
    if supplied is None:
        raise GateExecutionError(
            "gate.criteria.{}: derived threshold {!r} needs its inputs "
            "supplied via derived_inputs[{!r}], and none were. This is a "
            "pipeline failure, not a gate outcome — no verdict is "
            "rendered.".format(crit_name, formula, crit_name)
        )

    # The same finiteness rule applies to a derived threshold. A formula can
    # reach inf by overflow or nan through its inputs without any single step
    # looking wrong, so checking only literals would leave the hole open on
    # the path that has more ways to fall into it.
    value = _require_finite(
        crit_name, evaluate_formula(formula, supplied),
        "derived threshold {!r}".format(formula))
    return value, "derived", formula, source_blocks


# ---------------------------------------------------------------------------
# The comparison itself
# ---------------------------------------------------------------------------

def _compare(actual, comparator, threshold):
    """Apply a comparator. Ties are carried by the comparator itself.

    B5, already decided: `>` fails ties, `>=` passes them. There is no
    tie_handling field and V-5 rejects one if it appears — a second place to
    express tie semantics is a second place for them to disagree.
    """
    if comparator == ">":
        return actual > threshold
    if comparator == ">=":
        return actual >= threshold
    if comparator == "<":
        return actual < threshold
    return actual <= threshold


def _is_infeasible(value):
    """True when the statistic exists but has no usable value.

    NaN, None and infinities mean the strategy could not form a usable
    position — an economic outcome, counted as a loss. Distinct from the
    statistic being absent, which is a pipeline failure and raises.

    Infinity counts as infeasible rather than as an enormous win: an infinite
    Sharpe means zero measured variance, which is a degenerate result rather
    than a spectacular one. Reading it as a win would let a division-by-almost-
    zero pass a gate.
    """
    if value is None:
        return True
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return True
    return math.isnan(as_float) or math.isinf(as_float)


def evaluate_gate(entry, scored_results, block_registry=None,
                  derived_inputs=None):
    """Render a pre-committed gate against scored results.

    Pure: no clock, no filesystem, no randomness. Same inputs, same verdict,
    forever.

    Args:
        entry: The registered entry dict.
        scored_results: Dict of {statistic_name: ScoredResult}. A criterion
            selects its statistic via `criteria.<name>.statistic`, falling
            back to `gate.statistic`.
        block_registry: Optional BlockRegistry, used only to re-check E2
            anti-circularity on derived thresholds.
        derived_inputs: Optional {criterion_name: {var: float or list}},
            supplying the null-distribution values a derived formula needs.
            The caller computes these from the criterion's source blocks; this
            module never reads data, which is what keeps it pure.

    Returns:
        Verdict.

    Raises:
        GateRefusal: The gate specification is malformed. Not an outcome.
        GateExecutionError: The pipeline did not deliver a required statistic
            or derived input. Not an outcome — halt, repair, re-run.
    """
    gate = (entry or {}).get("gate") or {}
    criteria = gate.get("criteria") or {}
    combination_text = gate.get("combination")

    node = parse_combination(combination_text)

    if not criteria:
        raise GateRefusal(
            "gate.criteria is empty but gate.combination is {!r}. There is "
            "nothing to evaluate.".format(combination_text)
        )

    for name in criteria:
        if name in _RESERVED:
            raise GateRefusal(
                "gate.criteria contains a criterion named {!r}, which is a "
                "reserved word in the combination grammar.".format(name)
            )

    referenced = combination_identifiers(node)
    unknown = referenced - set(criteria)
    if unknown:
        raise GateRefusal(
            "gate.combination references {} which {} not in gate.criteria. "
            "Known criteria: {}.".format(
                sorted(unknown),
                "is" if len(unknown) == 1 else "are",
                sorted(criteria))
        )

    # A criterion defined but never referenced is a registration error, not a
    # harmless extra. It reads as governed while contributing nothing — the
    # shape of failure catalog #26, where a record cited something that was
    # not actually wired in.
    unused = set(criteria) - referenced
    if unused:
        raise GateRefusal(
            "gate.criteria defines {} which gate.combination never "
            "references. A criterion that cannot affect the verdict must not "
            "sit in the record looking as though it does.".format(
                sorted(unused))
        )

    default_statistic = gate.get("statistic")
    outcomes = []
    values = {}

    for name in sorted(criteria):
        crit = criteria[name] or {}
        comparator = crit.get("comparator")
        if comparator not in VALID_COMPARATORS:
            raise GateRefusal(
                "gate.criteria.{}.comparator is {!r}; must be one of "
                "{}.".format(name, comparator, sorted(VALID_COMPARATORS))
            )

        statistic = crit.get("statistic") or default_statistic
        if not statistic:
            raise GateRefusal(
                "gate.criteria.{} names no statistic and gate.statistic is "
                "unset.".format(name)
            )

        threshold, source, formula, source_blocks = _resolve_threshold(
            name, crit.get("threshold"), derived_inputs, block_registry
        )

        if statistic not in scored_results:
            # ABSENT is not NaN. The statistic was never produced, so there is
            # no economic outcome to record. §2.5: pipeline failure is not a
            # gate outcome.
            raise GateExecutionError(
                "gate.criteria.{} needs statistic {!r}, which is not in "
                "scored_results (present: {}). The pipeline did not produce "
                "it. Halt, repair, re-run — this is not a failing gate and no "
                "verdict is rendered.".format(
                    name, statistic, sorted(scored_results) or "(none)")
            )

        result = scored_results[statistic]
        # A4, enforced rather than trusted: nothing but a ScoredResult may
        # reach the comparison. The type check is cheap; retrofitting the
        # boundary after something else has been passed through it is not.
        if not hasattr(result, "value") or not hasattr(result, "statistic"):
            raise GateRefusal(
                "scored_results[{!r}] is {!r}, not a ScoredResult. The gate "
                "evaluates ScoredResult only — diagnostics are structurally "
                "outside the verdict (A4/E3).".format(
                    statistic, type(result).__name__)
            )

        actual = result.value
        # A bool is a type error, not an economic outcome, so it is refused
        # rather than counted as a loss. Python would otherwise compare True
        # as 1.0 and pass a `>= 1` gate — a verdict rendered against something
        # that was never a measurement.
        if isinstance(actual, bool):
            raise GateRefusal(
                "gate.criteria.{}: statistic {!r} has boolean value {!r}. A "
                "gate compares measurements, and True is not one — Python "
                "would silently compare it as 1.0.".format(
                    name, statistic, actual)
            )
        if _is_infeasible(actual):
            # B5: infeasibility counts as a LOSS. The cohort could not form,
            # so the edge was not there when it was needed. That is a result.
            outcomes.append(CriterionOutcome(
                name=name, statistic=statistic, comparator=comparator,
                threshold=decimal_str(threshold), threshold_source=source,
                actual=None, passed=False, infeasible=True,
                derived_formula=formula, derived_source_blocks=source_blocks,
            ))
            values[name] = False
            continue

        actual = float(actual)
        passed = _compare(actual, comparator, threshold)
        outcomes.append(CriterionOutcome(
            name=name, statistic=statistic, comparator=comparator,
            threshold=decimal_str(threshold), threshold_source=source,
            actual=decimal_str(actual), passed=passed,
            derived_formula=formula, derived_source_blocks=source_blocks,
        ))
        values[name] = passed

    passed = evaluate_combination(node, values)

    return Verdict(
        entry_id=entry.get("id"),
        combination=str(combination_text),
        criteria=outcomes,
        passed=passed,
        pass_licenses=entry.get("pass_licenses"),
        pass_does_not_establish=entry.get("pass_does_not_establish"),
    )


# ---------------------------------------------------------------------------
# Foreclosure — advisory only (D2)
# ---------------------------------------------------------------------------

_FORECLOSURE_MAX_UNKNOWNS = 16


def compute_foreclosure(combination, known_outcomes):
    """Is the verdict already determined, whatever the remaining criteria do?

    A-12 proved W6+W7 could determine v11's verdict regardless of W8, letting
    W8 stay unspent and clean. A validation block is scarce, so this is worth
    computing.

    IT MUST NEVER AUTO-STOP. A-12 is careful about why its stop was
    legitimate: *"The stop fires on arithmetic, never on a judgment that the
    result looks bad."* This function returns arithmetic. Acting on it is a
    human decision, filed as an amendment — exactly as A-12 was.

    Args:
        combination: The registered combination string.
        known_outcomes: Dict of {criterion_name: bool} for criteria already
            evaluated. Criteria named in the combination but absent here are
            treated as still-unknown.

    Returns:
        The forced verdict (True/False) if every assignment of the unknown
        criteria yields the same result; None if the outcome is still live.

    Raises:
        GateRefusal: Malformed combination, or more unknowns than can be
            enumerated.
    """
    node = parse_combination(combination)
    names = combination_identifiers(node)
    unknown = sorted(names - set(known_outcomes))

    if not unknown:
        return evaluate_combination(node, dict(known_outcomes))

    if len(unknown) > _FORECLOSURE_MAX_UNKNOWNS:
        raise GateRefusal(
            "compute_foreclosure: {} unknown criteria exceeds the {} that can "
            "be enumerated exhaustively. Refusing rather than sampling — an "
            "advisory that is right most of the time is worse than "
            "none.".format(len(unknown), _FORECLOSURE_MAX_UNKNOWNS)
        )

    seen = set()
    for mask in range(1 << len(unknown)):
        assignment = dict(known_outcomes)
        for bit, name in enumerate(unknown):
            assignment[name] = bool(mask & (1 << bit))
        seen.add(evaluate_combination(node, assignment))
        if len(seen) > 1:
            return None
    return seen.pop()
