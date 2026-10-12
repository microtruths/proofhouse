"""Record the demo's evaluation run.

proofhouse has no `eval` command. An evaluation is a library call,
`run_eval`, because the tool cannot know how to load your data. This script
is the smallest honest version of that call: it loads the demo's returns
file and hands the adapter to the runner. The runner does the rest in a
fixed order: verify the pin, read the score function from the pinned
artifact, check block isolation, refuse a second measurement on a block this
entry already measured, consume the block, run the artifact, score it with
the trial count read from the ledger, write a manifest that records where
the score function came from, and append a chained ledger row carrying the
result's digest.

There is no score function in this file. It lives in toy_strategy.py, next
to the strategy, so the one pin covers both. Pass `score_fn=` to `run_eval`
only if you must, and expect `verify` to warn that the statistic's
computation was attested rather than verified.

Run it from the demo directory after `proofhouse register hypothesis.yaml`.
"""

import os
import sys

from proofhouse.adapters.csv_adapter import CSVAdapter
from proofhouse.core.blocks import BlockRegistry
from proofhouse.core.ledger import Ledger
from proofhouse.core.registry import Registry
from proofhouse.eval.runner import run_eval

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

    try:
        result = run_eval(
            entry=entry,
            adapter=adapter,
            block_registry=blocks,
            ledger=ledger,
            stage=2,
            manifests_dir=os.path.join(ph, "manifests"),
        )
    except ValueError as exc:
        # A refusal, not a crash: the runner checked something before
        # spending anything, and the message says what and what to do.
        print("REFUSED: {}".format(exc))
        return 1

    scored = result.scored
    print("{}  eval run recorded as {}".format(ENTRY_ID, result.run_id))
    print("  statistic   : {} = {:.4f}".format(scored.statistic, scored.value))
    print("  p_value     : {:.4f} (one-sided)".format(scored.p_value))
    print("  trial_count : {} (read from the ledger by the runner)".format(
        scored.trial_count))
    print("  scoring     : {} ({})".format(
        result.manifest["scoring"]["source"],
        "covered by the artifact pin"
        if result.manifest["scoring"]["source"] == "artifact"
        else "supplied by this script, not pinned"))
    print("  blocks spent: {}".format(entry.get("validation_blocks")))
    print("  manifest    : .proofhouse/manifests/{}.yaml".format(result.run_id))
    print("Next: proofhouse gate {}".format(ENTRY_ID))
    return 0


if __name__ == "__main__":
    sys.exit(main())
