# Quick-start demo

The files behind the quick start in the main README. Run the demo from an
empty directory, never from here: `start_demo.py` copies what it needs into
your directory and refuses to overwrite anything.

| File | What it is |
|---|---|
| `start_demo.py` | Copies the files below into the current directory and pins the artifact into `hypothesis.yaml` |
| `toy_strategy.py` | The artifact: one-day momentum and its score function, both under the one pin; reads its parameters from the entry |
| `returns.csv` | Sixty days of synthetic daily returns, generated once and committed |
| `block.yaml` | One validation block covering the series |
| `hypothesis.template.yaml` | The entry, with two placeholders for the pin |
| `run_eval.py` | The eval step. There is no `proofhouse eval` command; this is the library call. No score function here: it lives in the artifact |

The verdict the demo records is stated in the main README. If you change
the threshold in `hypothesis.yaml` before registering, you will record a
different one, and the registered words will say so.
