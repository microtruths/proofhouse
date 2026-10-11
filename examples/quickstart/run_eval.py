"""Record the demo's evaluation run.

proofhouse has no `eval` command. An evaluation is a library call,
`run_eval`, because the tool cannot know how to load your data or score your
strategy. This script is the smallest honest version of that call: it loads
the demo's returns file, hands the pinned artifact and a score function to
the runner, and the runner does the rest in a fixed order: verify the pin,
check block isolation, consume the block, run the artifact, score it with the
trial count read from the ledger, write a manifest, and append a chained
ledger row carrying the result's digest.

Run it from the demo directory after `proofhouse register hypothesis.yaml`.
"""

import os
import sys

from proofhouse.adapters.csv_adapter import CSVAdapter
from proofhouse.core.blocks import BlockRegistry
from proofhouse.core.ledger import Ledger
from proofhouse.core.registry import Registry
from proofhouse.eval.runner import run_eval
from proofhouse.eval.stats import p_from_t, panel_ic, spearman_t
from proofhouse.eval.types import Diagnostics, ScoredResult

ENTRY_ID = "H-2026-DEMO"


def find_root(start):
    """Walk up from `start` to the directory holding .proofhouse/."""
    path = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(path, ".proofhouse")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def score_fn(predictions, outcomes, trial_count):
    """Turn the artifact's output into a gate-evaluable result.

    `trial_count` arrives from the runner, which read it from the ledger.
    A score function must use that number and never compute its own.
    """
    n = len(predictions)
    ic = panel_ic(predictions, outcomes)
    t = spearman_t(ic, n)
    p = p_from_t(t, n - 2, "one-sided")
    scored = ScoredResult(
        statistic="panel_ic",
        value=ic,
        trial_count=trial_count,
        p_value=p,
        n_effective=n,
        n_effective_basis="one trading day; the series is synthetic and "
                          "its days are not clustered",
        metadata={"n": n, "t": t},
    )
    diagnostics = Diagnostics()
    diagnostics.add("n_pairs", n)
    diagnostics.add("first_five_predictions", predictions[:5])
    return scored, diagnostics


def main():
    root = find_root(os.getcwd())
    if root is None:
        print("No .proofhouse/ found here or above. Run `proofhouse init` "
              "in the demo directory first.")
        return 1
    os.chdir(root)  # the artifact pin is a path relative to the project root
    ph = os.path.join(root, ".proofhouse")

    blocks = BlockRegistry(path=os.path.join(ph, "blocks", "blocks.yaml"))
    registry = Registry(os.path.join(ph, "registry"), block_registry=blocks)
    try:
        entry = registry.get_entry(ENTRY_ID)
    except KeyError:
        print("{} is not registered. Run `proofhouse register "
              "hypothesis.yaml` first.".format(ENTRY_ID))
        return 1

    adapter = CSVAdapter(os.path.join(root, "returns.csv"))
    ledger = Ledger(os.path.join(ph, "ledger", "ledger.jsonl"))

    result = run_eval(
        entry=entry,
        adapter=adapter,
        block_registry=blocks,
        ledger=ledger,
        stage=2,
        score_fn=score_fn,
        manifests_dir=os.path.join(ph, "manifests"),
    )

    scored = result.scored
    print("{}  eval run recorded as {}".format(ENTRY_ID, result.run_id))
    print("  statistic   : {} = {:.4f}".format(scored.statistic, scored.value))
    print("  p_value     : {:.4f} (one-sided)".format(scored.p_value))
    print("  trial_count : {} (read from the ledger by the runner)".format(
        scored.trial_count))
    print("  blocks spent: {}".format(entry.get("validation_blocks")))
    print("  manifest    : .proofhouse/manifests/{}.yaml".format(result.run_id))
    print("Next: proofhouse gate {}".format(ENTRY_ID))
    return 0


if __name__ == "__main__":
    sys.exit(main())
