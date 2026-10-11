"""Set up the proofhouse demo in the current directory.

Copies the toy strategy, its data, a block file and run_eval.py here, then
pins the strategy by hash into hypothesis.yaml. After this, every step is a
plain command you can read in the README.

Run it from an empty directory (not from inside examples/quickstart):

    mkdir demo && cd demo
    python /path/to/proofhouse/examples/quickstart/start_demo.py
"""

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COPIED = ["toy_strategy.py", "returns.csv", "block.yaml", "run_eval.py"]
WRITTEN = "hypothesis.yaml"
TEMPLATE = "hypothesis.template.yaml"


def main():
    dest = os.getcwd()
    if os.path.abspath(dest) == HERE:
        print("Run this from an empty demo directory, not from inside "
              "examples/quickstart.")
        return 1

    existing = [f for f in COPIED + [WRITTEN]
                if os.path.exists(os.path.join(dest, f))]
    if existing:
        print("Refusing to overwrite files already here: {}".format(
            ", ".join(existing)))
        print("Start the demo in an empty directory.")
        return 1

    for name in COPIED:
        shutil.copyfile(os.path.join(HERE, name), os.path.join(dest, name))

    # The pin: path plus sha256 of the artifact as it is right now. The
    # runner refuses to execute any file whose hash differs from this.
    from proofhouse.core.artifact_pin import create_implementation_pin
    pin = create_implementation_pin("toy_strategy.py")

    with open(os.path.join(HERE, TEMPLATE)) as f:
        text = f.read()
    text = text.replace("PIN_PATH", pin["path"]).replace(
        "PIN_SHA256", pin["sha256"])
    with open(os.path.join(dest, WRITTEN), "w") as f:
        f.write(text)

    print("Demo files written to {}".format(dest))
    for name in COPIED + [WRITTEN]:
        print("  {}".format(name))
    print("Pinned toy_strategy.py as {}".format(pin["sha256"]))
    print("\nNext (after `proofhouse init`, if you have not run it here yet):")
    print("  proofhouse blocks add block.yaml")
    print("  proofhouse explore --universe demo")
    print("  proofhouse register hypothesis.yaml")
    print("  proofhouse fill H-2026-DEMO power_check")
    print("  python run_eval.py")
    print("  proofhouse gate H-2026-DEMO")
    return 0


if __name__ == "__main__":
    sys.exit(main())
