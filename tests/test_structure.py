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
