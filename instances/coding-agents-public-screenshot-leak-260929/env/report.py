"""Emit a compact, complete result for the root post-run probe."""
import json
import sys
from pathlib import Path
from observe import collect
from measurement import summarize

result = summarize(collect(Path("/srv/forge-state")), sys.argv[1])
# The core probe retains 4,000 characters; fail explicitly rather than truncate a JSON decision.
encoded = json.dumps(result, separators=(",", ":"))
if len(encoded) > 3900:
    raise RuntimeError("Publication evidence exceeds the probe output limit")
print(encoded)
