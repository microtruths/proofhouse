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

        # ------------------------------------------------------------------
        # Exemptions are declared BY MODULE, not by name.
        #
        # Phase 1 Completion Plan §5. A hand-listed set of names is a
        # ratchet: when S-2 fires, the one-line fix is to add the name,
        # and that always feels justified. Listing names derived from
        # whatever happened to be unwired institutionalizes the very
        # pattern this test exists to catch — which is how
        # screen_stage1a/1b came to be formally blessed while the
        # screening funnel had no orchestrator at all.
        #
        # A module is exempt only if it is a RESEARCHER-FACING TOOLKIT:
        # functions called by the researcher's score_fn callback, at
        # registration/planning time, or from analysis scripts — never by
        # proofhouse internals. Orchestration modules get no exemption.
        #
        # Adding a module here requires a stated reason in the same commit.
        # ------------------------------------------------------------------
        exempt_modules = {
            # Statistical toolkit: called from the researcher's score_fn.
            "proofhouse/eval/stats.py",
            # CV toolkit: called from the researcher's evaluation code.
            "proofhouse/eval/cv.py",
            # Cost/feasibility toolkit: called at feasibility analysis time.
            "proofhouse/eval/costs.py",
            # Power toolkit: called at registration/planning time.
            "proofhouse/eval/power.py",
        }
        # NOTE: proofhouse/core/screen.py is deliberately NOT exempt. It is
        # orchestration, not a toolkit. Its functions must have real callers.

        # Name-level exemptions are limited to dunders, generic interface
        # methods dispatched dynamically, and the single user-facing entry
        # point. Nothing may be added here to silence a specific failure.
        exempt = {
            "__init__", "__repr__", "__str__", "__eq__", "__hash__",
            "to_dict", "add",
            # The top-level entry point: called by the user, by definition
            # not by another proofhouse module.
            "run_eval",
            # Registration-time helpers invoked when setting up entries.
            "create_implementation_pin", "declare_overlap", "create_manifest",
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
                rel_mod = os.path.relpath(fpath, base).replace(os.sep, "/")
                if rel_mod in exempt_modules:
                    continue
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
                    # A same-module caller IS a caller. Skipping the whole
                    # defining file was too blunt: it meant a function called
                    # only from a sibling in its own module (run_screen ->
                    # screen_stage1a, benjamini_yekutieli -> harmonic_number)
                    # read as unwired. Strip only the `def` line, then search
                    # what remains.
                    haystack = "\n".join(
                        line for line in code.splitlines()
                        if not re.match(
                            r'\s*(async\s+)?def\s+' + re.escape(name) + r'\b',
                            line,
                        )
                    )
                else:
                    haystack = code
                if re.search(r'\b' + re.escape(name) + r'\b', haystack):
                    found = True
                    break
            if not found:
                unwired.append("{} (defined in {})".format(
                    name, ", ".join(def_files)
                ))

        # ------------------------------------------------------------------
        # Known-unwired snapshot.
        #
        # Not an exemption. Each entry is a DECLARED GAP with a reason and
        # the phase that closes it. The assertion is equality, so this
        # fails in both directions:
        #   - a NEW unwired function appears  -> fails (the defect we want)
        #   - a listed one gets wired         -> fails, telling you to
        #                                        delete the line
        #
        # That second direction is what an exemption list can never do:
        # exemptions go stale silently, a snapshot cannot.
        # ------------------------------------------------------------------
        known_unwired = {
            # Part C's run_screen wired the previous five (screen_stage1a,
            # screen_stage1b, benjamini_hochberg, benjamini_yekutieli,
            # harmonic_number). An entry here is a debt with a named payer,
            # not a dispensation — if this dict grows, say who wires it and when.
            #
            # 2b wired evaluate_gate: core/gate_run.run_gate now calls it
            # behind the re-run lock, so it left this dict. The gap promptly
            # reappeared ONE LEVEL UP, on run_gate itself — which is the
            # phenomenon this test is named for and worth stating plainly
            # rather than treating as a surprise each time. The regress
            # terminates only at a call site with a human at it, i.e. the CLI.
            #
            # PAYER: Phase 2c, `proofhouse gate <entry-id>`.
            # BLOCKED ON: a decision, not effort. The CLI must obtain the
            # ScoredResults for an entry, and nothing persists them today —
            # run_eval returns them in memory and writes only provenance
            # hashes to the manifest. That also means Phase 1's stated exit
            # criterion, "reproduce a past result from a manifest", cannot
            # currently be checked. Resolve where scored results live before
            # wiring the command, rather than inventing a second store here.
            "run_gate": "wired by Phase 2c's `gate` CLI; needs scored-result persistence",
            "from_dict": "wired by Phase 2c's `gate` CLI, which reads the result back",
            "foreclosure_advisory": "wired by Phase 2c's `gate` CLI (advisory line in output)",
        }

        unwired_names = sorted(
            u.split(" (")[0] for u in unwired
        )
        expected_names = sorted(known_unwired)

        assert unwired_names == expected_names, (
            "Unwired-function snapshot mismatch "
            "(the recurring '#26 one level up' defect).\n"
            "  Currently unwired: {}\n"
            "  Declared unwired:  {}\n"
            "If a NEW name appears, wire it or declare it with a reason.\n"
            "If a declared name disappeared, it is now wired — delete its "
            "line from known_unwired.".format(unwired_names, expected_names)
        )

