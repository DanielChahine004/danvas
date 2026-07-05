"""Run every code generator, in dependency order — the one command to
remember after editing anything generated:

    python scripts/gen_all.py

- ``gen_protocol.py``  — danvas/_protocol.py -> frontend protocol.generated.js
  (guarded by tests/test_protocol_sync.py)
- ``gen_component_templates.py`` — component classes (JSX + CONTRACT) ->
  danvas/templates/components.json (guarded by tests/test_component_templates.py
  and tests/test_component_contracts.py)

Remember the consumers: the Rust SDK embeds components.json at compile time
(cargo build after regenerating), and danvasd embeds the frontend dist AND
components.json (npm run build in danvas/frontend when src changed, then
cargo build --release --manifest-path broker/Cargo.toml).
"""

import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    for script in ("gen_protocol.py", "gen_component_templates.py"):
        print(f"== {script}")
        subprocess.run([sys.executable, os.path.join(_HERE, script)],
                       check=True)
    print("done -- rebuild embedders if needed (see this script's docstring)")


if __name__ == "__main__":
    main()
