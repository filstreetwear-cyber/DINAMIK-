"""
Capability — re-exported here (not redefined) from app.models.enums.

The refactor doc lists `app/tools/core/capability.py` as a new file, but
`Capability` also has to be a valid value for `ToolCall.capability`
(app/models/agent_run.py, Phase B) — one vocabulary, not two definitions
that can silently drift apart. app.models.enums is the lower layer (no
dependency on app.tools), so it holds the canonical definition; this
module is the "front door" the tool layer and the rest of app.tools are
expected to import from, per the file layout the refactor doc asks for.
"""
from __future__ import annotations

from app.models.enums import Capability

__all__ = ["Capability"]
