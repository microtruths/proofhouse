# tests/test_scoring_pin_and_single_eval.py — BACKLOG D-10 and D-12
"""
Two findings from the 2026-10-11 stranger-agent test of the public branch.

D-10: `amend` accepted a successor that re-declared a block its parent had
already spent. The successor could never gate on it (E4) and `verify`
reported a permanent error that no later successor could clear. The registry
refused this only for an INVALIDATING amendment; a threshold change is not
one, so the check never ran.

D-12: a second `run_eval` by the same entry on a V block it had already
spent was recorded, and a plain `gate` on that run recorded a second verdict.
Same-entry consumption is idempotent on purpose (reserve → promote → validate
by one entry, test B-7), so the refusal keys on recorded eval rows instead.
And the score function was a callable the caller handed in, covered by no
pin, so a different scoring under the same statistic name was a route to a
different recorded number with nothing on file. It now lives in the pinned
artifact by default, the manifest records where it came from, `verify`
reports it in three classes and `report` prints it.
"""

import importlib.util
import os
import tempfile

import pytest

from proofhouse.adapters.csv_adapter import CSVAdapter
from proofhouse.core.artifact_pin import create_implementation_pin
from proofhouse.core.blocks import BlockRegistry
from proofhouse.core.ledger import Ledger
from proofhouse.core.registry import Registry
from proofhouse.eval.runner import run_eval
from proofhouse.eval.types import Diagnostics, ScoredResult
from proofhouse.schema.validate import ValidationError
from proofhouse.schema.versions import SCHEMA_VERSION

try:
    from tests.bound_eval import project as bound_project
    from tests.bound_eval import record_eval
except ImportError:  # pragma: no cover - import style depends on rootdir
    from bound_eval import project as bound_project, record_eval


ARTIFACT_WITH_SCORE = '''
from proofhouse.eval.stats import panel_ic
from proofhouse.eval.types import Diagnostics, ScoredResult


def run(data, parameters):
    polarity = parameters.get("polarity", 1)
    n = len(data)
    return ([polarity * (i + 1) / n for i in range(n)],
            [r for _, r in data])


def score_fn(predictions, outcomes, trial_count):
    scored = ScoredResult(statistic="panel_ic",
                          value=panel_ic(predictions, outcomes),
                          trial_count=trial_count)
    return scored, Diagnostics()
'''

ARTIFACT_WITHOUT_SCORE = '''
def run(data, parameters):
    n = len(data)
    return [(i + 1) / n for i in range(n)], [r for _, r in data]
'''


def caller_score_fn(predictions, outcomes, trial_count):
    """A score function that lives in this test file, not in any artifact."""
    from proofhouse.eval.stats import panel_ic
    scored = ScoredResult(statistic="panel_ic",
                          value=panel_ic(predictions, outcomes),
                          trial_count=trial_count)
    return scored, Diagnostics()


def _block(block_id, segment="V"):
    return {
        "id": block_id, "start": "2024-01-01", "end": "2024-02-29",
        "segment": segment, "data_complete": True,
        "completed_at": "2024-03-15", "sources": ["test"], "seams": [],
    }


def _entry(entry_id, pin, blocks, depends_on=(), effect="1.5"):
    """A complete, registrable entry (the integration test's shape)."""
    return {
        "id": entry_id,
        "schema_version": SCHEMA_VERSION,
        "name": "D-10 / D-12 test entry",
        "created_utc": "2026-10-12T00:00:00Z",
        "status": "registered",
        "depends_on": list(depends_on),
        "universe": {"id": "test-universe", "venues": ["test-venue"],
                     "instruments": "synthetic 20-day series"},
        "mechanism": {"class": "toy-model", "counterparty": "Random noise",
                      "claim": "Toy strategy", "expected_effect_size": effect,
                      "expected_decay": "N/A"},
        "signal": {
            "inputs": ["return"], "transform": "index rank",
            "frequency": "daily", "combination": "panel_ic",
            "parameters": {"polarity": 1}, "implementation": pin,
            "benchmark_sensitivity": {"benchmarks": ["b1", "b2"]},
        },
        "feasibility": {"min_order_notional_usd": "10",
                        "sleeve_range": ["100", "20000"],
                        "instruments_held": 8, "rebalance_band": "0.15"},
        "costs": {"model_ref": "costs/test-v1"},
        "gate": {
            "statistic": "panel_ic", "combination": "g1",
            "criteria": {"g1": {"comparator": ">",
                                "threshold": {"literal": "0.05"}}},
            "benchmark": "b1", "benchmark_justification": "test",
            "benchmark_sensitivity": ["b1", "b2"],
            "on_pass": "nothing", "on_fail": "nothing",
        },
        "p_value_sidedness": "one-sided",
        "fdr_method": "by",
        "fdr_method_justification": "dependent",
        "pass_licenses": "Nothing.",
        "pass_does_not_establish": "Anything.",
        "live_gate": {"min_duration_weeks": 1},
        "validation_blocks": list(blocks),
        "seam_acknowledged": False,
        "diagnostics": [],
        "power_check": {
            "inputs": {"effect_size": effect, "blocks": 1, "prior_trials": 1},
            "value": {"pending": True,
                      "fill_rule": "power(effect_size, blocks, prior_trials)"},
        },
        "prior_art": [],
    }


class Project:
    """A throwaway project with two V blocks, a pinned artifact and data."""

    def __init__(self, artifact_src=ARTIFACT_WITH_SCORE):
        self.dir = tempfile.mkdtemp()
        self.artifact = os.path.join(self.dir, "strategy.py")
        with open(self.artifact, "w") as f:
            f.write(artifact_src)
        self.pin = create_implementation_pin(self.artifact)

        csv_path = os.path.join(self.dir, "returns.csv")
        with open(csv_path, "w") as f:
            f.write("date,return\n")
            for i in range(20):
                f.write("2024-01-{:02d},{}\n".format(i + 1, 0.01 * (i % 5 - 2)))
        self.adapter = CSVAdapter(csv_path)

        self.blocks = BlockRegistry(path=os.path.join(self.dir, "blocks.yaml"))
        self.blocks.add_block(_block("V-1"))
        self.blocks.add_block(_block("V-2"))
        self.ledger = Ledger(os.path.join(self.dir, "ledger.jsonl"))
        self.registry = Registry(os.path.join(self.dir, "registry"),
                                 block_registry=self.blocks)
        self.manifests = os.path.join(self.dir, "manifests")

    def register(self, entry_id, blocks, depends_on=(), effect="1.5"):
        return self.registry.register(
            _entry(entry_id, self.pin, blocks, depends_on, effect))

    def eval(self, entry, score_fn=None, kind="eval", parent_run_id=None):
        return run_eval(entry=entry, adapter=self.adapter,
                        block_registry=self.blocks, ledger=self.ledger,
                        stage=2, score_fn=score_fn,
                        manifests_dir=self.manifests, ledger_kind=kind,
                        parent_run_id=parent_run_id)

    def spenders(self, block_id):
        return [c["entry_id"]
                for c in self.blocks.get_block(block_id).get("consumed_by", [])]

    def manifest_files(self):
        if not os.path.isdir(self.manifests):
            return []
        return sorted(os.listdir(self.manifests))


# ---------------------------------------------------------------------------
# D-10: the registry refuses a declared block anyone has already spent
# ---------------------------------------------------------------------------

class TestRegistryRefusesSpentBlocks:

    def test_non_invalidating_successor_on_a_spent_block_is_refused(self):
        """The stranger's exact move: copy the gated parent, change the
        threshold, keep the block. Nothing invalidating changed, so the
        existing check never ran and verify held a permanent error."""
        p = Project()
        p.register("H-P", ["V-1"])
        p.blocks.consume_block("V-1", "H-P")

        with pytest.raises(ValidationError) as exc:
            p.register("H-S", ["V-1"], depends_on=["H-P"])

        message = str(exc.value)
        assert "V-1" in message
        assert "H-P" in message
        assert "already spent" in message
        assert "own lineage" in message
        assert "validation_blocks: []" in message

    def test_successor_may_still_take_an_unspent_block(self):
        """Fresh means unspent, not undeclared. The intended amend-then-gate
        path keeps working."""
        p = Project()
        p.register("H-P", ["V-1"])
        successor = p.register("H-S", ["V-1"], depends_on=["H-P"])
        assert successor["validation_blocks"] == ["V-1"]

    def test_a_new_entry_on_a_block_a_stranger_spent_is_refused(self):
        p = Project()
        p.register("H-A", ["V-1"])
        p.blocks.consume_block("V-1", "H-A")

        with pytest.raises(ValidationError) as exc:
            p.register("H-B", ["V-1"])

        message = str(exc.value)
        assert "H-A" in message
        assert "not related" in message

    def test_the_message_names_only_the_spent_block(self):
        p = Project()
        p.register("H-P", ["V-1", "V-2"])
        p.blocks.consume_block("V-1", "H-P")

        with pytest.raises(ValidationError) as exc:
            p.register("H-S", ["V-1", "V-2"], depends_on=["H-P"])

        message = str(exc.value)
        assert "V-1" in message
        assert "V-2" not in message

    def test_a_successor_declaring_no_blocks_is_accepted(self):
        """The documented way to record a change after a verdict."""
        p = Project()
        p.register("H-P", ["V-1"])
        p.blocks.consume_block("V-1", "H-P")
        successor = p.register("H-S", [], depends_on=["H-P"])
        assert successor["validation_blocks"] == []


# ---------------------------------------------------------------------------
# D-12 (a): one eval per single-use block per entry
# ---------------------------------------------------------------------------

class TestOneEvalPerValidationBlock:

    def test_second_eval_on_a_spent_block_is_refused_and_records_nothing(self):
        p = Project()
        entry = p.register("H-E", ["V-1"])
        first = p.eval(entry)
        assert len(p.ledger.all_rows()) == 1
        assert p.manifest_files() == ["{}.yaml".format(first.run_id)]

        with pytest.raises(ValueError) as exc:
            p.eval(entry)

        message = str(exc.value)
        assert "already recorded eval run {}".format(first.run_id) in message
        assert "V-1" in message
        assert "--replay" in message
        # Nothing consumed, written or chained.
        assert len(p.ledger.all_rows()) == 1
        assert p.manifest_files() == ["{}.yaml".format(first.run_id)]
        assert p.spenders("V-1") == ["H-E"]

    def test_a_subperiod_run_on_the_same_block_is_allowed(self):
        """Subperiod rows decompose a parent eval; they are not a second
        measurement, and the ledger already demands their parent_run_id."""
        p = Project()
        entry = p.register("H-E", ["V-1"])
        first = p.eval(entry)
        sub = p.eval(entry, kind="subperiod", parent_run_id=first.run_id)
        assert sub.run_id != first.run_id
        assert len(p.ledger.all_rows()) == 2

    def test_a_run_that_broke_after_consuming_can_be_rerun(self):
        """README: 'Pipeline broke; a statistic was never produced. Halt,
        repair, re-run.' Consumption happens before scoring, so a broken
        first run leaves the block consumed and no row. The refusal keys on
        the row, so the repair-and-rerun path survives."""
        p = Project()
        entry = p.register("H-E", ["V-1"])

        def broken(predictions, outcomes, trial_count):
            raise RuntimeError("scoring pipeline broke")

        with pytest.raises(RuntimeError):
            p.eval(entry, score_fn=broken)
        assert p.spenders("V-1") == ["H-E"]      # consumed ...
        assert p.ledger.all_rows() == []         # ... but never recorded

        rerun = p.eval(entry)
        assert len(p.ledger.all_rows()) == 1
        assert p.ledger.all_rows()[0]["run_id"] == rerun.run_id

    def test_an_exploration_block_is_not_single_use(self):
        """The refusal is about V. An E block may be measured on repeatedly
        at stage 1, which is what exploration is."""
        p = Project()
        p.blocks.add_block(_block("E-1", segment="E"))
        entry = _entry("H-X", p.pin, ["E-1"])
        # Registry refuses E in validation_blocks (V-8); run_eval alone is
        # what is under test here, so the entry is not registered.
        for _ in range(2):
            run_eval(entry=entry, adapter=p.adapter, block_registry=p.blocks,
                     ledger=p.ledger, stage=1, manifests_dir=p.manifests)
        assert len(p.ledger.all_rows()) == 2


# ---------------------------------------------------------------------------
# D-12 (b): the score function is pinned, or its absence is recorded
# ---------------------------------------------------------------------------

class TestScoreFunctionProvenance:

    def test_omitted_score_fn_is_read_from_the_artifact_and_recorded(self):
        p = Project()
        entry = p.register("H-E", ["V-1"])
        result = p.eval(entry)
        assert result.scored.statistic == "panel_ic"
        assert result.manifest["scoring"] == {
            "source": "artifact", "code_hash": p.pin["sha256"]}
        # The block is inside the chained digest, not a side note.
        import yaml
        with open(os.path.join(p.manifests,
                               "{}.yaml".format(result.run_id))) as f:
            on_disk = yaml.safe_load(f)
        assert on_disk["scoring"]["source"] == "artifact"
        from proofhouse.core.manifest import manifest_digest
        row = p.ledger.all_rows()[-1]
        assert manifest_digest(on_disk) == row["manifest_hash"]

    def test_no_score_fn_anywhere_refuses_before_consuming(self):
        p = Project(ARTIFACT_WITHOUT_SCORE)
        entry = p.register("H-E", ["V-1"])
        with pytest.raises(ValueError) as exc:
            p.eval(entry)
        assert "defines no score_fn" in str(exc.value)
        assert p.spenders("V-1") == []
        assert p.ledger.all_rows() == []

    def test_a_callable_loaded_from_the_artifact_module_counts_as_pinned(self):
        """What the real eval scripts do: load the pinned module themselves
        and pass its score_fn. Same code, same hash: pinned."""
        p = Project()
        entry = p.register("H-E", ["V-1"])
        spec = importlib.util.spec_from_file_location("mine", p.artifact)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        result = p.eval(entry, score_fn=module.score_fn)
        assert result.manifest["scoring"]["source"] == "artifact"

    def test_a_callable_from_elsewhere_is_recorded_as_caller(self):
        p = Project()
        entry = p.register("H-E", ["V-1"])
        result = p.eval(entry, score_fn=caller_score_fn)
        assert result.manifest["scoring"] == {"source": "caller",
                                              "code_hash": None}


class TestVerifyAndReportSayWhereTheScoreCameFrom:

    def _scoring_findings(self, p):
        from proofhouse.core.verify import verify_all
        ok, findings = verify_all(registry=p.registry, ledger=p.ledger,
                                  block_registry=p.blocks,
                                  manifests_dir=p.manifests)
        return ok, [f for f in findings if f.check == "scoring"]

    def test_pinned_scoring_is_silent(self):
        p = Project()
        entry = p.register("H-E", ["V-1"])
        p.eval(entry)
        ok, scoring = self._scoring_findings(p)
        assert ok
        assert scoring == []

    def test_caller_scoring_is_a_warning_not_an_error(self):
        p = Project()
        entry = p.register("H-E", ["V-1"])
        p.eval(entry, score_fn=caller_score_fn)
        ok, scoring = self._scoring_findings(p)
        assert ok, "a caller-supplied score function was legal when it ran"
        assert [f.severity for f in scoring] == ["warning"]
        assert "attested rather than verified" in scoring[0].message

    def test_a_manifest_from_before_the_pin_is_unverifiable(self):
        from proofhouse.core.verify import UNVERIFIABLE, verify_all
        ledger, manifests_dir = bound_project()
        entry = {"id": "H-OLD", "universe": {"id": "u"},
                 "validation_blocks": []}
        scored = ScoredResult(statistic="panel_ic", value=0.1, trial_count=1)
        record_eval(ledger, manifests_dir, entry, scored, scoring=None)

        ok, findings = verify_all(ledger=ledger, manifests_dir=manifests_dir)
        scoring = [f for f in findings if f.check == "scoring"]
        # This fixture has no registry, so the row's entry reference dangles
        # and verify fails on that. The claim under test is narrower: the
        # missing scoring block is UNVERIFIABLE, never an error.
        assert not [f for f in findings
                    if f.check == "scoring" and f.severity == "error"]
        assert [f.severity for f in scoring] == [UNVERIFIABLE]
        assert "predates the score-function pin" in scoring[0].message

    def test_the_report_prints_the_scoring_line(self):
        from proofhouse.core.gate_run import run_gate
        from proofhouse.core.report import (bind_verdict, build_report,
                                            render_text)
        p = Project()
        entry = p.register("H-E", ["V-1"])
        result = p.eval(entry)
        run_gate(entry, result.run_id, p.ledger, p.manifests,
                 amendments=[], block_registry=p.blocks)

        verdict, results, provenance = bind_verdict(
            entry, p.ledger, p.manifests, block_registry=p.blocks)
        assert provenance["status"] == "bound"
        assert provenance["scoring"]["status"] == "pinned"
        assert p.pin["sha256"] in provenance["scoring"]["note"]

        text = render_text(build_report(
            entry, verdict, ledger=p.ledger, scored_results=results,
            provenance=provenance))
        assert "scoring: PINNED." in text
