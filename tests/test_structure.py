# tests/test_structure.py — Import direction enforcement
"""
Phase 0 Build Plan §6, test S-1:
    "No module under schema/ or chain/ imports from core/.
     Enforced by AST scan, so the future package split stays cheap."

This ensures the schema/chain layer can be extracted as a standalone
verifier package without dragging in the core layer.
"""

import ast
import os


class TestStructure:
    """S-1: Import direction enforcement."""

    def test_s1_no_upward_imports(self):
        """No module under schema/ or chain/ imports from core/.

        The import direction is one-way: core/ may import from schema/
        and chain/, but not vice versa.
        """
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pkg = os.path.join(base, "proofhouse")

        restricted_dirs = [
            os.path.join(pkg, "schema"),
            os.path.join(pkg, "chain"),
        ]
        forbidden_import = "proofhouse.core"

        violations = []

        for restricted_dir in restricted_dirs:
            if not os.path.exists(restricted_dir):
                continue
            for fname in os.listdir(restricted_dir):
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(restricted_dir, fname)
                with open(fpath) as f:
                    try:
                        tree = ast.parse(f.read(), filename=fpath)
                    except SyntaxError:
                        continue

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.startswith(forbidden_import):
                                violations.append(
                                    "{}: imports {}".format(
                                        os.path.relpath(fpath, base),
                                        alias.name,
                                    )
                                )
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and node.module.startswith(forbidden_import):
                            violations.append(
                                "{}: imports from {}".format(
                                    os.path.relpath(fpath, base),
                                    node.module,
                                )
                            )

        assert violations == [], (
            "Import direction violation — schema/ and chain/ must not "
            "import from core/:\n" + "\n".join("  " + v for v in violations)
        )

    def test_s2_no_unwired_public_functions(self):
        """Every public function/method in core/ and eval/ has a non-test caller.

        The recurring defect in this project is never a wrong component,
        it's an unconnected one. Three rounds running, the bug has been
        'correct code, no caller'. This test catches it structurally.

        A public function (no leading underscore) defined in core/ or eval/
        must appear as an import or attribute access in at least one
        non-test, non-__init__ Python file within the proofhouse package.

        Exempt:
        - __init__, __repr__, __str__, __eq__, __hash__: dunder methods
        - to_dict, add: generic interface methods called dynamically
        - Class definitions (we check functions/methods, not classes)
        - Functions starting with _ (explicitly private)
        """
        import re

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pkg = os.path.join(base, "proofhouse")

        # Exempt names: dunders, generic interface methods, and user-facing
        # toolkit functions that are public API for researchers (called by
        # score_fn callbacks, at registration time, or in analysis scripts).
        # These are intentionally not called by proofhouse internals.
        exempt = {
            # Python dunders
            "__init__", "__repr__", "__str__", "__eq__", "__hash__",
            # Generic interface methods
            "to_dict", "add",
            # Top-level entry point (called by user, not by other modules)
            "run_eval",
            # Stats toolkit (used by researcher's score_fn callback)
            "deflated_sharpe", "panel_ic", "hit_rate",
            "alpha_vs_benchmark", "benchmark_sensitivity_sweep",
            # Costs toolkit (used at feasibility analysis time)
            "check_feasibility", "min_viable_sleeve_usd",
            "implied_min_rebalance_pct",
            # CV toolkit (used by researcher's evaluation code)
            "purge_indices", "embargo_test_indices", "make_cv_splits",
            # Power toolkit (used at registration/planning time)
            "affordable_n", "compute_power_check",
            # Screening toolkit (called at screening stage, not per-eval)
            "screen_stage1a", "screen_stage1b",
            # Registration-time functions (called when setting up entries)
            "create_implementation_pin", "declare_overlap",
            "create_manifest",
        }

        # Phase 1: collect all public function/method names from core/ and eval/
        scan_dirs = [
            os.path.join(pkg, "core"),
            os.path.join(pkg, "eval"),
        ]

        public_names = {}  # {name: [defining_file, ...]}
        for scan_dir in scan_dirs:
            if not os.path.exists(scan_dir):
                continue
            for fname in os.listdir(scan_dir):
                if not fname.endswith(".py") or fname == "__init__.py":
                    continue
                fpath = os.path.join(scan_dir, fname)
                with open(fpath) as f:
                    try:
                        tree = ast.parse(f.read(), filename=fpath)
                    except SyntaxError:
                        continue
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        name = node.name
                        if name.startswith("_") or name in exempt:
                            continue
                        rel = os.path.relpath(fpath, base)
                        if name not in public_names:
                            public_names[name] = []
                        public_names[name].append(rel)

        # Phase 2: scan all non-test proofhouse .py files for references
        all_proofhouse_code = []
        for dirpath, dirnames, filenames in os.walk(pkg):
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(dirpath, fname)
                rel = os.path.relpath(fpath, base)
                # Skip test files
                if "test" in rel.lower():
                    continue
                with open(fpath) as f:
                    all_proofhouse_code.append((rel, f.read()))

        # Phase 3: check each public name has at least one reference
        # in a non-test file OTHER than its own definition file
        unwired = []
        for name, def_files in sorted(public_names.items()):
            found = False
            for rel, code in all_proofhouse_code:
                if rel in def_files:
                    continue  # skip the file where it's defined
                if re.search(r'\b' + re.escape(name) + r'\b', code):
                    found = True
                    break
            if not found:
                unwired.append("{} (defined in {})".format(
                    name, ", ".join(def_files)
                ))

        assert unwired == [], (
            "Public functions with zero non-test callers "
            "(the recurring '#26 one level up' defect):\n"
            + "\n".join("  " + u for u in unwired)
        )

