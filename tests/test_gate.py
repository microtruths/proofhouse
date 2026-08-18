# tests/test_gate.py — Gate engine tests
"""
Phase 2 Build Plan §6 — hand-computed tests, written before implementation.

G-1   0.0495 against >= 0.05 -> FAIL. The v10 case, verbatim.
G-2   Exactly 0.05 against >= 0.05 -> pass; against > 0.05 -> fail.
G-3   "g1 and g2" with (pass, fail) -> fail; "g1 or g2" -> pass.
G-4   "not g1", nested parens, operator precedence.
G-5   Unknown token in a combination -> refuse, no partial evaluation.
G-6   eval()-style injection in combination or formula -> refused.
G-7   Derived threshold 0.5 * median(sigma_w) over hand-built sigma values.
G-8   NaN statistic -> infeasibility -> loss.
G-9   A missing statistic produces NO verdict at all, not a fail.
G-10  Same inputs -> same verdict, twice (purity).
G-11  The verdict function cannot access diagnostics (signature-level).

Plus foreclosure (D2) and the S-3 purity scan.
"""

import ast
import math
import os

import pytest

from proofhouse.core.gate import (
    GateExecutionError,
    GateRefusal,
    compute_foreclosure,
    evaluate_formula,
    evaluate_gate,
    parse_combination,
)
from proofhouse.eval.types import Diagnostics, ScoredResult


def _entry(combination="g1", criteria=None, statistic="ic", **kwargs):
    """Minimal entry carrying only what the gate engine reads."""
    entry = {
        "id": "H-TEST-001",
        "gate": {
            "statistic": statistic,
            "combination": combination,
            "criteria": criteria if criteria is not None else {
                "g1": {"comparator": ">=", "threshold": {"literal": "0.05"}},
            },
        },
        "pass_licenses": "A shadow trial on the live sleeve for 6 weeks.",
        "pass_does_not_establish": "Deployable alpha.",
    }
    entry.update(kwargs)
    return entry


def _scored(value, statistic="ic", trial_count=14):
    return ScoredResult(statistic=statistic, value=value,
                        trial_count=trial_count)


class TestG1StrictComparison:
    """G-1: the case this whole module exists for."""

    def test_g1_0495_against_005_is_a_fail(self):
        """0.0495 >= 0.05 is FALSE. It rounds to 0.050 and that is irrelevant.

        v10's IC gate at the >=100-episode stratum: "IC = 0.0495, rounds to
        0.050 but t = 2.11 with n = 1,820 — not a pass under strict
        comparison."
        """
        verdict = evaluate_gate(_entry(), {"ic": _scored(0.0495)})

        assert verdict.passed is False
        assert verdict.criteria[0].passed is False
        assert verdict.criteria[0].actual == "0.049500000000000002"
        assert verdict.criteria[0].threshold == "0.050000000000000003"

    def test_g1_recorded_agentcopy_ic_fails(self):
        """The real AgentCopy number: IC 0.038897 against >= 0.05."""
        verdict = evaluate_gate(_entry(), {"ic": _scored(0.038897)})
        assert verdict.passed is False

    def test_verdict_carries_licensing_text(self):
        verdict = evaluate_gate(_entry(), {"ic": _scored(0.06)})
        assert verdict.passed is True
        assert "shadow trial" in verdict.pass_licenses
        assert verdict.pass_does_not_establish == "Deployable alpha."


class TestG2Ties:
    """G-2: the comparator carries tie semantics. There is no other place."""

    def test_g2_ge_passes_an_exact_tie(self):
        verdict = evaluate_gate(_entry(), {"ic": _scored(0.05)})
        assert verdict.passed is True

    def test_g2_gt_fails_an_exact_tie(self):
        entry = _entry(criteria={
            "g1": {"comparator": ">", "threshold": {"literal": "0.05"}},
        })
        verdict = evaluate_gate(entry, {"ic": _scored(0.05)})
        assert verdict.passed is False

    @pytest.mark.parametrize("comparator,expected", [
        (">=", True),    # tie passes
        (">", False),    # tie fails
        ("<=", True),    # tie passes
        ("<", False),    # tie fails
    ])
    def test_g2_every_comparator_on_an_exact_tie(self, comparator, expected):
        entry = _entry(criteria={
            "g1": {"comparator": comparator, "threshold": {"literal": "0.05"}},
        })
        verdict = evaluate_gate(entry, {"ic": _scored(0.05)})
        assert verdict.passed is expected

    def test_tie_handling_field_is_not_consulted(self):
        """Even if a stray tie_handling field exists, the comparator wins.

        V-5 rejects the field at registration; this proves the engine would
        ignore it anyway, so the two enforcement points cannot disagree.
        """
        entry = _entry(criteria={
            "g1": {"comparator": ">", "threshold": {"literal": "0.05"},
                   "tie_handling": "pass"},
        })
        verdict = evaluate_gate(entry, {"ic": _scored(0.05)})
        assert verdict.passed is False


class TestG3Combination:
    """G-3: and / or across two criteria."""

    TWO = {
        "g1": {"comparator": ">=", "threshold": {"literal": "0.05"},
               "statistic": "ic"},
        "g2": {"comparator": ">=", "threshold": {"literal": "1.5"},
               "statistic": "t"},
    }

    def test_g3_and_with_one_failure_is_a_fail(self):
        entry = _entry(combination="g1 and g2", criteria=self.TWO)
        verdict = evaluate_gate(entry, {
            "ic": _scored(0.06, "ic"),
            "t": _scored(1.1, "t"),
        })
        assert verdict.passed is False
        by_name = {c.name: c.passed for c in verdict.criteria}
        assert by_name == {"g1": True, "g2": False}

    def test_g3_or_with_one_success_is_a_pass(self):
        entry = _entry(combination="g1 or g2", criteria=self.TWO)
        verdict = evaluate_gate(entry, {
            "ic": _scored(0.06, "ic"),
            "t": _scored(1.1, "t"),
        })
        assert verdict.passed is True

    def test_g3_and_with_both_passing(self):
        entry = _entry(combination="g1 and g2", criteria=self.TWO)
        verdict = evaluate_gate(entry, {
            "ic": _scored(0.06, "ic"),
            "t": _scored(2.6, "t"),
        })
        assert verdict.passed is True


class TestG4ParserShapes:
    """G-4: not, nesting, precedence."""

    def test_g4_not(self):
        entry = _entry(combination="not g1")
        verdict = evaluate_gate(entry, {"ic": _scored(0.0495)})
        assert verdict.passed is True

    def test_g4_precedence_not_binds_tighter_than_and(self):
        """'not a and b' parses as '(not a) and b', matching Python."""
        node = parse_combination("not a and b")
        assert node == ("and", ("not", ("ident", "a")), ("ident", "b"))

    def test_g4_precedence_and_binds_tighter_than_or(self):
        """'a or b and c' parses as 'a or (b and c)'."""
        node = parse_combination("a or b and c")
        assert node == ("or", ("ident", "a"),
                        ("and", ("ident", "b"), ("ident", "c")))

    def test_g4_parens_override_precedence(self):
        node = parse_combination("(a or b) and c")
        assert node == ("and", ("or", ("ident", "a"), ("ident", "b")),
                        ("ident", "c"))

    def test_g4_nested_parens(self):
        node = parse_combination("((a))")
        assert node == ("ident", "a")

    def test_g4_double_negation(self):
        node = parse_combination("not not a")
        assert node == ("not", ("not", ("ident", "a")))

    def test_g4_left_associativity(self):
        node = parse_combination("a and b and c")
        assert node == ("and", ("and", ("ident", "a"), ("ident", "b")),
                        ("ident", "c"))


class TestG5RefusesMalformed:
    """G-5: unknown token -> refuse. No partial evaluation, ever."""

    @pytest.mark.parametrize("combination", [
        "g1 && g2",
        "g1 + g2",
        "g1 == g2",
        "g1.attr",
        "g1[0]",
        "'g1'",
        "g1 and",
        "and g1",
        "(g1",
        "g1)",
        "()",
        "",
        "   ",
        "g1 g2",
    ])
    def test_g5_malformed_combination_refuses(self, combination):
        with pytest.raises(GateRefusal):
            parse_combination(combination)

    def test_g5_missing_combination_refuses(self):
        with pytest.raises(GateRefusal):
            parse_combination(None)

    def test_g5_unknown_criterion_name_refuses(self):
        """A combination naming something not in criteria is a broken
        registration, not a failing gate."""
        entry = _entry(combination="g1 and g99")
        with pytest.raises(GateRefusal) as exc:
            evaluate_gate(entry, {"ic": _scored(0.06)})
        assert "g99" in str(exc.value)

    def test_g5_unreferenced_criterion_refuses(self):
        """A criterion that cannot affect the verdict must not sit in the
        record looking as though it does — the #26 shape."""
        entry = _entry(combination="g1", criteria={
            "g1": {"comparator": ">=", "threshold": {"literal": "0.05"}},
            "g2": {"comparator": ">=", "threshold": {"literal": "1.5"}},
        })
        with pytest.raises(GateRefusal) as exc:
            evaluate_gate(entry, {"ic": _scored(0.06)})
        assert "g2" in str(exc.value)

    def test_g5_reserved_word_as_criterion_name_refuses(self):
        entry = _entry(combination="not", criteria={
            "not": {"comparator": ">=", "threshold": {"literal": "0.05"}},
        })
        with pytest.raises(GateRefusal):
            evaluate_gate(entry, {"ic": _scored(0.06)})

    def test_g5_bad_comparator_refuses(self):
        entry = _entry(criteria={
            "g1": {"comparator": "≥", "threshold": {"literal": "0.05"}},
        })
        with pytest.raises(GateRefusal):
            evaluate_gate(entry, {"ic": _scored(0.06)})

    def test_g5_threshold_with_both_kinds_refuses(self):
        entry = _entry(criteria={
            "g1": {"comparator": ">=", "threshold": {
                "literal": "0.05",
                "derived": {"formula": "1", "source_blocks": []},
            }},
        })
        with pytest.raises(GateRefusal):
            evaluate_gate(entry, {"ic": _scored(0.06)})

    def test_g5_no_short_circuit_hides_a_bad_name(self):
        """'g1 and g99' must refuse even though g1 is False and Python's own
        `and` would never look at the right side. Refusal cannot depend on
        evaluation order."""
        entry = _entry(combination="g1 and g99", criteria={
            "g1": {"comparator": ">=", "threshold": {"literal": "0.05"}},
        })
        with pytest.raises(GateRefusal):
            evaluate_gate(entry, {"ic": _scored(0.01)})


class TestG6NoEval:
    """G-6: nothing in a registry field reaches an evaluator."""

    @pytest.mark.parametrize("payload", [
        "__import__('os').system('echo pwned')",
        "().__class__.__bases__[0].__subclasses__()",
        "eval('1+1')",
        "g1 if True else g2",
        "lambda: 1",
        "exec('x=1')",
        "open('/etc/passwd')",
    ])
    def test_g6_combination_injection_refused(self, payload):
        with pytest.raises(GateRefusal):
            parse_combination(payload)

    @pytest.mark.parametrize("payload", [
        "__import__('os').system('echo pwned')",
        "open('/etc/passwd').read()",
        "sigma_w.__class__",
        "eval('1')",
        "[x for x in range(3)]",
    ])
    def test_g6_formula_injection_refused(self, payload):
        with pytest.raises(GateRefusal):
            evaluate_formula(payload, {"sigma_w": [1.0, 2.0]})

    def test_g6_formula_cannot_reach_builtins(self):
        """Only the four whitelisted functions resolve. `len`, `sum`, `min`
        and friends are unknown names, not fallbacks to Python."""
        for name in ("len", "sum", "min", "max", "pow", "round", "float"):
            with pytest.raises(GateRefusal):
                evaluate_formula("{}(sigma_w)".format(name),
                                 {"sigma_w": [1.0, 2.0]})

    def test_g6_unicode_homoglyph_identifier_refused(self):
        """A Cyrillic 'а' is not an ASCII 'a'. Two visually identical names in
        one registry would be unauditable."""
        with pytest.raises(GateRefusal):
            parse_combination("g1 аnd g2")


class TestG7DerivedThresholds:
    """G-7: 0.5 * median(sigma_w) over hand-built values."""

    SIGMA = [0.02, 0.04, 0.06, 0.08, 0.10]   # median 0.06 -> threshold 0.03

    def test_g7_median_formula(self):
        """median([0.02,0.04,0.06,0.08,0.10]) = 0.06; 0.5 * 0.06 = 0.03."""
        assert evaluate_formula("0.5 * median(sigma_w)",
                                {"sigma_w": self.SIGMA}) == pytest.approx(0.03)

    def test_g7_gate_uses_the_derived_value(self):
        entry = _entry_with_derived("0.5 * median(sigma_w)", ["B-E-01"])
        verdict = evaluate_gate(
            entry, {"ic": _scored(0.04)},
            derived_inputs={"g1": {"sigma_w": self.SIGMA}},
        )
        # 0.04 >= 0.03 -> pass
        assert verdict.passed is True
        outcome = verdict.criteria[0]
        assert outcome.threshold_source == "derived"
        assert outcome.derived_formula == "0.5 * median(sigma_w)"
        assert outcome.derived_source_blocks == ["B-E-01"]
        assert float(outcome.threshold) == pytest.approx(0.03)

    def test_g7_derived_value_just_below_fails(self):
        entry = _entry_with_derived("0.5 * median(sigma_w)", ["B-E-01"])
        verdict = evaluate_gate(
            entry, {"ic": _scored(0.0299)},
            derived_inputs={"g1": {"sigma_w": self.SIGMA}},
        )
        assert verdict.passed is False

    def test_g7_even_length_median_averages_the_middle_pair(self):
        assert evaluate_formula("median(x)",
                                {"x": [1.0, 2.0, 3.0, 4.0]}) == pytest.approx(2.5)

    def test_g7_mean_and_std(self):
        values = {"x": [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]}
        assert evaluate_formula("mean(x)", values) == pytest.approx(5.0)
        # Sample std (ddof=1) of that set: sqrt(32/7) = 2.13809...
        assert evaluate_formula("std(x)", values) == pytest.approx(
            math.sqrt(32.0 / 7.0))

    def test_g7_std_of_one_value_refuses(self):
        with pytest.raises(GateRefusal):
            evaluate_formula("std(x)", {"x": [1.0]})

    def test_g7_arithmetic_precedence_and_parens(self):
        v = {"x": [1.0, 3.0]}   # mean 2.0
        assert evaluate_formula("1 + 2 * mean(x)", v) == pytest.approx(5.0)
        assert evaluate_formula("(1 + 2) * mean(x)", v) == pytest.approx(6.0)
        assert evaluate_formula("-mean(x)", v) == pytest.approx(-2.0)
        assert evaluate_formula("abs(-mean(x))", v) == pytest.approx(2.0)
        assert evaluate_formula("1e-2", {}) == pytest.approx(0.01)

    def test_g7_distribution_without_an_aggregate_refuses(self):
        """'0.5 * sigma_w' must refuse rather than broadcast."""
        with pytest.raises(GateRefusal):
            evaluate_formula("0.5 * sigma_w", {"sigma_w": self.SIGMA})

    def test_g7_aggregate_of_a_scalar_refuses(self):
        with pytest.raises(GateRefusal):
            evaluate_formula("median(x)", {"x": 1.0})

    def test_g7_empty_distribution_refuses(self):
        with pytest.raises(GateRefusal):
            evaluate_formula("median(x)", {"x": []})

    def test_g7_division_by_zero_refuses(self):
        with pytest.raises(GateRefusal):
            evaluate_formula("1 / 0", {})

    def test_g7_missing_derived_inputs_is_execution_error(self):
        """Not a fail — the pipeline never supplied the null distribution."""
        entry = _entry_with_derived("0.5 * median(sigma_w)", ["B-E-01"])
        with pytest.raises(GateExecutionError):
            evaluate_gate(entry, {"ic": _scored(0.04)})

    def test_g7_e2_anticircularity_rechecked_at_gate_time(self):
        """A derived threshold drawing on a V-segment block is refused HERE,
        not only at registration. Phase 1's review established why: a
        registration-time check on a field that is empty at registration time
        is not a check."""
        entry = _entry_with_derived("0.5 * median(sigma_w)", ["B-V-01"])
        with pytest.raises(GateRefusal) as exc:
            evaluate_gate(
                entry, {"ic": _scored(0.04)},
                block_registry=_FakeBlockRegistry({"B-V-01": "V"}),
                derived_inputs={"g1": {"sigma_w": self.SIGMA}},
            )
        assert "segment V" in str(exc.value)

    def test_g7_non_v_source_blocks_are_allowed(self):
        entry = _entry_with_derived("0.5 * median(sigma_w)", ["B-E-01"])
        verdict = evaluate_gate(
            entry, {"ic": _scored(0.04)},
            block_registry=_FakeBlockRegistry({"B-E-01": "E"}),
            derived_inputs={"g1": {"sigma_w": self.SIGMA}},
        )
        assert verdict.passed is True

    def test_g7_unknown_source_block_refuses(self):
        entry = _entry_with_derived("0.5 * median(sigma_w)", ["B-NOPE"])
        with pytest.raises(GateRefusal):
            evaluate_gate(
                entry, {"ic": _scored(0.04)},
                block_registry=_FakeBlockRegistry({"B-E-01": "E"}),
                derived_inputs={"g1": {"sigma_w": self.SIGMA}},
            )


class TestNonFiniteThresholds:
    """Audit finding, 2026-08-18. A gate that cannot fail is worse than no
    gate, because it produces a passing record.

    Found by probing rather than by the tests: neither registration nor the
    engine rejected a non-finite threshold, so `threshold: "-inf"` rendered an
    unconditional PASS whose record read as a legitimate verdict.
    """

    def _entry(self, literal):
        return _entry(criteria={
            "g1": {"comparator": ">=", "threshold": {"literal": literal}},
        })

    @pytest.mark.parametrize("literal", [
        "-inf",       # the dangerous one: unconditional PASS
        "inf",        # unconditional FAIL that looks like evidence
        "nan",        # every comparison False, in both directions
        "Infinity",
        "-Infinity",
        "NaN",
        "1e400",      # silently overflows to inf
    ])
    def test_non_finite_threshold_is_refused(self, literal):
        with pytest.raises(GateRefusal) as exc:
            evaluate_gate(self._entry(literal), {"ic": _scored(0.0389)})
        assert "finite" in str(exc.value).lower()

    def test_minus_infinity_no_longer_passes_unconditionally(self):
        """The specific regression: this returned PASS for any observation."""
        with pytest.raises(GateRefusal):
            evaluate_gate(self._entry("-inf"), {"ic": _scored(-99999.0)})

    def test_a_derived_threshold_that_overflows_is_also_refused(self):
        """Checking only literals would leave the hole open on the path with
        more ways to fall into it."""
        entry = _entry_with_derived("1e400 * mean(x)", [])
        with pytest.raises(GateRefusal):
            evaluate_gate(entry, {"ic": _scored(0.5)},
                          derived_inputs={"g1": {"x": [1.0, 3.0]}})

    def test_a_derived_threshold_from_nan_inputs_is_refused(self):
        entry = _entry_with_derived("mean(x)", [])
        with pytest.raises(GateRefusal):
            evaluate_gate(entry, {"ic": _scored(0.5)},
                          derived_inputs={"g1": {"x": [float("nan"), 1.0]}})

    def test_finite_thresholds_still_work(self):
        assert evaluate_gate(
            self._entry("0.05"), {"ic": _scored(0.06)}).passed is True


class TestG8Infeasibility:
    """G-8: NaN is an economic outcome and counts as a loss."""

    def test_g8_nan_is_a_loss(self):
        verdict = evaluate_gate(_entry(), {"ic": _scored(float("nan"))})
        assert verdict.passed is False
        assert verdict.criteria[0].infeasible is True
        assert verdict.criteria[0].actual is None

    def test_g8_none_is_a_loss(self):
        verdict = evaluate_gate(_entry(), {"ic": _scored(None)})
        assert verdict.passed is False
        assert verdict.criteria[0].infeasible is True

    def test_g8_infeasible_loses_under_a_less_than_comparator_too(self):
        """The loss is not "the comparison came out False" — it is a loss
        regardless of direction. A `<` criterion must not be handed a free
        pass by an absent cohort."""
        entry = _entry(criteria={
            "g1": {"comparator": "<", "threshold": {"literal": "0.05"}},
        })
        verdict = evaluate_gate(entry, {"ic": _scored(float("nan"))})
        assert verdict.passed is False

    def test_g8_infinite_statistic_is_infeasible_not_an_enormous_win(self):
        """Audit finding. An infinite Sharpe means zero measured variance —
        degenerate, not spectacular. Reading it as a win would let a
        division-by-almost-zero pass a gate."""
        verdict = evaluate_gate(_entry(), {"ic": _scored(float("inf"))})
        assert verdict.passed is False
        assert verdict.criteria[0].infeasible is True

    def test_g8_bool_statistic_is_refused_not_compared_as_one(self):
        """Audit finding. Python compares True as 1.0, so a boolean value
        silently passed a `>= 1` gate — a verdict rendered against something
        that was never a measurement. A type error, not an economic
        outcome, so it refuses rather than counting as a loss."""
        entry = _entry(criteria={
            "g1": {"comparator": ">=", "threshold": {"literal": "1"}},
        })
        with pytest.raises(GateRefusal) as exc:
            evaluate_gate(entry, {"ic": _scored(True)})
        assert "boolean" in str(exc.value).lower()

    def test_g8_infeasible_under_not_still_loses_the_criterion(self):
        """`not g1` inverts the criterion, as registered. The point of G-8 is
        that the criterion itself is a loss; the combination then does
        whatever it was registered to do."""
        entry = _entry(combination="not g1")
        verdict = evaluate_gate(entry, {"ic": _scored(float("nan"))})
        assert verdict.criteria[0].passed is False
        assert verdict.passed is True


class TestG9PipelineFailureIsNotAnOutcome:
    """G-9: the distinction an automated system gets wrong by default."""

    def test_g9_missing_statistic_raises_rather_than_failing(self):
        """A-12 §A12.5: operational failure and data outcome must never be
        conflated. Getting this wrong permissively manufactures false
        negatives that look like evidence."""
        with pytest.raises(GateExecutionError):
            evaluate_gate(_entry(), {})

    def test_g9_wrong_statistic_name_raises(self):
        with pytest.raises(GateExecutionError):
            evaluate_gate(_entry(), {"sharpe": _scored(0.9, "sharpe")})

    def test_g9_execution_error_is_not_a_refusal(self):
        """The two exception types are distinct on purpose: a refusal means
        fix the registration, an execution error means fix the pipeline."""
        with pytest.raises(GateExecutionError) as exc:
            evaluate_gate(_entry(), {})
        assert not isinstance(exc.value, GateRefusal)


class TestG10Purity:
    """G-10: same inputs, same verdict, forever."""

    def test_g10_repeated_evaluation_is_identical(self):
        entry = _entry(combination="g1 and g2", criteria={
            "g1": {"comparator": ">=", "threshold": {"literal": "0.05"},
                   "statistic": "ic"},
            "g2": {"comparator": ">=", "threshold": {"literal": "1.5"},
                   "statistic": "t"},
        })
        results = {"ic": _scored(0.06, "ic"), "t": _scored(2.6, "t")}

        first = evaluate_gate(entry, results).to_dict()
        second = evaluate_gate(entry, results).to_dict()
        assert first == second

    def test_g10_evaluation_does_not_mutate_its_inputs(self):
        import copy
        entry = _entry()
        results = {"ic": _scored(0.06)}
        entry_before = copy.deepcopy(entry)
        evaluate_gate(entry, results)
        assert entry == entry_before

    def test_s3_gate_module_imports_nothing_impure(self):
        """S-3: AST-scan gate.py for anything that could make the verdict
        depend on when or where it ran. Purity is the property that makes a
        stored verdict meaningful rather than a snapshot of a mood, so it is
        enforced structurally rather than by intention."""
        forbidden = {
            "datetime", "time", "random", "os", "sys", "pathlib",
            "socket", "requests", "urllib", "subprocess", "secrets",
            "uuid", "tempfile",
        }
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "proofhouse", "core", "gate.py",
        )
        with open(path) as fh:
            tree = ast.parse(fh.read(), filename=path)

        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    found.add(node.module.split(".")[0])

        assert not (found & forbidden), (
            "gate.py imports {}, which can make a verdict depend on when or "
            "where it ran".format(sorted(found & forbidden))
        )

    def test_s3_gate_module_never_calls_eval_or_exec(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "proofhouse", "core", "gate.py",
        )
        with open(path) as fh:
            tree = ast.parse(fh.read(), filename=path)

        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not (called & {"eval", "exec", "compile", "__import__"})


class TestG11DiagnosticsIsolation:
    """G-11: A4 is structural, not conventional."""

    def test_g11_signature_has_no_diagnostics_parameter(self):
        import inspect
        params = set(inspect.signature(evaluate_gate).parameters)
        assert params == {
            "entry", "scored_results", "block_registry", "derived_inputs"
        }
        assert not any("diagnos" in p.lower() for p in params)

    def test_g11_a_diagnostics_object_cannot_be_scored(self):
        """Passing Diagnostics where a ScoredResult belongs is refused, not
        duck-typed into working."""
        with pytest.raises(GateRefusal) as exc:
            evaluate_gate(_entry(), {"ic": Diagnostics({"regime": "bull"})})
        assert "ScoredResult" in str(exc.value)

    def test_g11_diagnostics_on_the_entry_are_never_read(self):
        """An entry carrying diagnostics evaluates identically to one without.
        The gate cannot consult them because it is not given them."""
        plain = evaluate_gate(_entry(), {"ic": _scored(0.06)}).to_dict()
        with_diag = evaluate_gate(
            _entry(diagnostics=[
                {"name": "no_loss_wallet_concentration", "trigger": ">0.3"},
            ]),
            {"ic": _scored(0.06)},
        ).to_dict()
        assert plain == with_diag


class TestForeclosure:
    """D2: advisory arithmetic. Never auto-stop."""

    def test_foreclosed_by_a_failing_and(self):
        """One False in a conjunction decides it, whatever the rest do —
        the A-12 case: W6+W7 determined the verdict regardless of W8."""
        assert compute_foreclosure("g1 and g2 and g3", {"g1": False}) is False

    def test_foreclosed_by_a_passing_or(self):
        assert compute_foreclosure("g1 or g2 or g3", {"g1": True}) is True

    def test_not_foreclosed_while_live(self):
        assert compute_foreclosure("g1 and g2 and g3", {"g1": True}) is None

    def test_fully_known_returns_the_verdict(self):
        assert compute_foreclosure(
            "g1 and g2", {"g1": True, "g2": True}) is True
        assert compute_foreclosure(
            "g1 and g2", {"g1": True, "g2": False}) is False

    def test_nothing_known_is_not_foreclosed(self):
        assert compute_foreclosure("g1 and g2", {}) is None

    def test_a_tautology_is_foreclosed_with_nothing_known(self):
        assert compute_foreclosure("g1 or not g1", {}) is True

    def test_too_many_unknowns_refuses_rather_than_sampling(self):
        combination = " and ".join("g{}".format(i) for i in range(20))
        with pytest.raises(GateRefusal):
            compute_foreclosure(combination, {})


class TestSerialization:
    """The verdict has to survive into a ledger row unambiguously."""

    def test_numeric_fields_are_decimal_strings_not_floats(self):
        """Canonical rule 3 forbids floats in hashed records. A threshold a
        few ulps either side decides a verdict, so the record must be
        byte-reproducible by an independent verifier."""
        verdict = evaluate_gate(_entry(), {"ic": _scored(0.0495)})
        row = verdict.to_dict()
        crit = row["criteria"][0]
        assert isinstance(crit["actual"], str)
        assert isinstance(crit["threshold"], str)

    def test_infeasible_flag_only_present_when_true(self):
        clean = evaluate_gate(_entry(), {"ic": _scored(0.06)})
        assert "infeasible" not in clean.to_dict()["criteria"][0]
        nan = evaluate_gate(_entry(), {"ic": _scored(float("nan"))})
        assert nan.to_dict()["criteria"][0]["infeasible"] is True

    def test_verdict_records_the_combination_verbatim(self):
        entry = _entry(combination="  g1  ")
        verdict = evaluate_gate(entry, {"ic": _scored(0.06)})
        assert verdict.combination == "  g1  "


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry_with_derived(formula, source_blocks=None):
    return _entry(criteria={
        "g1": {
            "comparator": ">=",
            "threshold": {"derived": {
                "formula": formula,
                "source_blocks": list(source_blocks or []),
            }},
        },
    })


class _FakeBlockRegistry:
    """Minimal stand-in exposing only get_block, which is all the gate uses."""

    def __init__(self, segments):
        self._segments = segments

    def get_block(self, block_id):
        if block_id not in self._segments:
            raise KeyError(block_id)
        return {"id": block_id, "segment": self._segments[block_id]}
