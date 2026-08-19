# proofhouse.core.blocks — block registry and consumption
"""
Block registry for proofhouse.

Phase 0 Build Plan §4.1:
    The atomic unit is one 2-month block. Segments are labels over a
    block sequence, so what gets spent is a block.

    "data_complete is flipped by an explicit completion event, never
     inferred from end being in the past." (A6)

    "seams records data-source boundaries inside the block. A block with
     non-empty seams cannot be assigned segment V unless the consuming
     entry carries seam_acknowledged: true." (failure catalog #12)

Segment hierarchy (from least to most restricted):
    E (Exploration) — unlimited reuse, never validates
    S1 (Screen A) — reusable within a cycle
    S2 (Screen B) — reusable within a cycle
    R (Reserve) — untouched, promoted to V on demand
    V (Validation) — one block per hypothesis, tokened, single-use (TERMINAL)
    L (Live) — forward-collected, licenses deployment, never gates
"""

import copy
import os

import yaml


# Segment promotion order: higher index = more restricted.
# A block may be promoted up this ladder but never demoted.
# V is terminal: spent blocks don't become live data.
_SEGMENT_ORDER = {"E": 0, "S1": 1, "S2": 2, "R": 3, "V": 4, "L": 5}

# V is terminal — once a block is spent for validation, it cannot be
# promoted to L. V→L would imply that validation data is also used for
# live deployment, which violates the separation.
_TERMINAL_SEGMENTS = frozenset({"V"})

# Segments where consumption is single-use (one entry per block)
_SINGLE_USE_SEGMENTS = frozenset({"V"})

# Segments where seam acknowledgment is required
_SEAM_CHECK_SEGMENTS = frozenset({"V"})

# Segments whose LABEL IS A CLAIM ABOUT THE PAST rather than a permission.
# E, S1, S2 and L say what a block may be used for, and reading them is the
# whole point — consumption there is unremarkable. R says nothing has been
# read from this block; V says it is unexamined validation data. Only those
# two can be contradicted by the consumption record sitting beside them.
#
# Getting this set wrong in the permissive direction hides the one case that
# matters. Getting it wrong in the strict direction is worse: a first cut
# flagged every consumed E and S1 block, which on the live registry meant six
# false alarms burying the single real one. A check that cries wolf teaches
# you to skip its output.
_UNTOUCHED_CLAIM_SEGMENTS = frozenset({"R", "V"})


class BlockRegistry:
    """Block registry with consumption tracking and optional disk persistence.

    Blocks are stored by ID. Consumption appends to a block's consumed_by
    list. V-blocks enforce single-use: once consumed by one entry, they
    refuse consumption by another.

    When initialized with a path, all mutations are automatically persisted.
    """

    def __init__(self, path=None):
        """Initialize the block registry.

        Args:
            path: Optional path to the block registry YAML file.
                  If set, the registry auto-saves after every mutation
                  and loads from disk on init if the file exists.
        """
        self._blocks = {}  # id -> block dict
        self._path = path

        if path and os.path.exists(path):
            self._load()

    def _load(self):
        """Load blocks from disk."""
        with open(self._path) as f:
            data = yaml.safe_load(f)
        if data and isinstance(data, dict):
            blocks = data.get("blocks", [])
            for block in blocks:
                if isinstance(block, dict) and "id" in block:
                    block.setdefault("consumed_by", [])
                    self._blocks[block["id"]] = block

    def _save(self):
        """Save blocks to disk if a path is configured."""
        if not self._path:
            return
        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        data = {
            "schema_version": 1,
            "blocks": list(self._blocks.values()),
        }
        with open(self._path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=True,
                      allow_unicode=True)

    def add_block(self, block):
        """Add a block to the registry.

        Args:
            block: Dict with required keys: id, start, end, segment,
                   data_complete, completed_at, sources, seams.

        Raises:
            ValueError: If block ID already exists or segment is invalid.
        """
        block_id = block["id"]
        if block_id in self._blocks:
            raise ValueError("Block {} already exists".format(block_id))

        segment = block.get("segment", "")
        if segment not in _SEGMENT_ORDER:
            raise ValueError(
                "Invalid segment '{}'. Must be one of: {}".format(
                    segment, ", ".join(sorted(_SEGMENT_ORDER.keys()))
                )
            )

        stored = copy.deepcopy(block)
        stored.setdefault("consumed_by", [])
        self._blocks[block_id] = stored
        self._save()

    def get_block(self, block_id):
        """Get a block by ID. Returns a copy.

        Raises:
            KeyError: If block not found.
        """
        if block_id not in self._blocks:
            raise KeyError("Block not found: {}".format(block_id))
        return copy.deepcopy(self._blocks[block_id])

    def list_blocks(self):
        """Return a list of all blocks (copies)."""
        return [copy.deepcopy(b) for b in self._blocks.values()]

    def consume_block(self, block_id, entry_id, seam_acknowledged=False):
        """Record that an entry has consumed (touched) a block.

        Enforces:
        - E6: Block must have data_complete=True.
        - E4: V-blocks are single-use per entry. A V-block already consumed
               by a different entry is refused.
        - §2.4.2 / failure catalog #12: Non-empty seams on segment V
               require seam_acknowledged=True.

        Same-entry consumption is idempotent (not an error).

        Args:
            block_id: The block ID to consume.
            entry_id: The consuming entry's ID.
            seam_acknowledged: Whether the entry acknowledges the seam.

        Raises:
            KeyError: If block not found.
            ValueError: If consumption is refused.
        """
        if block_id not in self._blocks:
            raise KeyError("Block not found: {}".format(block_id))

        block = self._blocks[block_id]

        # E6: data_complete must be True
        if not block.get("data_complete", False):
            raise ValueError(
                "Block {} has data_complete=False and cannot be consumed. "
                "Availability is keyed to data completion, not calendar dates "
                "(A6).".format(block_id)
            )

        segment = block.get("segment", "")

        # §2.4.2 / failure catalog #12: seam check on V-blocks.
        #
        # THIS MUST PRECEDE THE E4 BLOCK BELOW. It used to follow it, and the
        # E4 idempotent early return therefore skipped it entirely:
        #     consume as R   -> no seam gate applies to R
        #     promote R -> V
        #     consume again, same entry -> early return, seam NEVER checked
        # A check that exists, is tested, and does not run on the path that
        # matters is failure catalog #27's shape. Ordering is the whole fix.
        seams = block.get("seams", [])
        if seams and segment in _SEAM_CHECK_SEGMENTS and not seam_acknowledged:
            raise ValueError(
                "Block {} has data-source seams {} and segment {}. "
                "Consumption requires seam_acknowledged=True. "
                "Failure catalog #12: 'splices need overlap + reconciliation; "
                "never let a seam sit unvalidated inside a test period.'"
                .format(block_id, seams, segment)
            )

        # E4: V-blocks are single-use
        if segment in _SINGLE_USE_SEGMENTS:
            existing = block.get("consumed_by", [])
            for consumption in existing:
                if consumption["entry_id"] != entry_id:
                    raise ValueError(
                        "Block {} (segment {}) is already consumed by entry {}. "
                        "V-blocks are single-use per hypothesis "
                        "(A7).".format(block_id, segment, consumption["entry_id"])
                    )
            # Idempotent: if same entry already consumed, just return. An
            # acknowledgment arriving late is recorded rather than dropped —
            # silently discarding it left the caller no way to tell that what
            # they passed had been ignored.
            for consumption in existing:
                if consumption["entry_id"] == entry_id:
                    if seam_acknowledged and not consumption.get(
                            "seam_acknowledged"):
                        consumption["seam_acknowledged"] = True
                        self._save()
                    return

        # Record the consumption
        consumption_record = {
            "entry_id": entry_id,
        }
        if seam_acknowledged:
            consumption_record["seam_acknowledged"] = True

        block.setdefault("consumed_by", []).append(consumption_record)
        self._save()

    def complete_block(self, block_id, completed_at):
        """Record the data-completion event for a block.

        A6: data_complete is flipped by an explicit completion event,
        never inferred from end being in the past.

        Args:
            block_id: The block ID.
            completed_at: RFC3339 UTC timestamp of completion.

        Raises:
            KeyError: If block not found.
            ValueError: If block is already complete.
        """
        if block_id not in self._blocks:
            raise KeyError("Block not found: {}".format(block_id))

        block = self._blocks[block_id]
        if block.get("data_complete", False):
            raise ValueError(
                "Block {} is already marked data_complete".format(block_id)
            )

        block["data_complete"] = True
        block["completed_at"] = completed_at
        self._save()

    def promote_block(self, block_id, new_segment,
                      acknowledge_prior_consumption=False, rationale=None):
        """Promote a block to a higher segment.

        Blocks may be promoted up the segment ladder (e.g., R→V) but
        never demoted (V→R is refused — blocks do not un-spend).
        V is terminal: a spent validation block cannot be promoted to L.

        PROMOTION TO V CONSULTS consumed_by. Promoting to V is the act of
        claiming a block is unexamined validation data, and making that claim
        about a block something has already read is false on its face. This
        used to be permitted silently, which left one entry free to train on a
        reserve block, promote it, and score itself on the same window — a
        finished record indistinguishable from a block held clean.

        The escape hatch is modeled on the seam precedent (failure catalog
        #12): known contamination may be crossed, but only explicitly and only
        with a written reason. An absolute refusal would be routed around, the
        same reasoning already recorded for the re-run lock.

        Args:
            block_id: The block ID.
            new_segment: The target segment.
            acknowledge_prior_consumption: Cross a known-read block into V.
            rationale: Why that is legitimate here. Mandatory when
                acknowledging — acknowledging without saying why is not
                acknowledging.

        Raises:
            KeyError: If block not found.
            ValueError: If new_segment is invalid, is a demotion, the current
                segment is terminal, or the block has been read and the
                promotion is not acknowledged.
        """
        if block_id not in self._blocks:
            raise KeyError("Block not found: {}".format(block_id))

        if new_segment not in _SEGMENT_ORDER:
            raise ValueError(
                "Invalid segment '{}'. Must be one of: {}".format(
                    new_segment, ", ".join(sorted(_SEGMENT_ORDER.keys()))
                )
            )

        block = self._blocks[block_id]
        current = block["segment"]

        # V is terminal — spent blocks don't become live data
        if current in _TERMINAL_SEGMENTS and new_segment != current:
            raise ValueError(
                "Block {} has terminal segment {} and cannot be promoted "
                "further.".format(block_id, current)
            )

        if _SEGMENT_ORDER[new_segment] < _SEGMENT_ORDER[current]:
            raise ValueError(
                "Block {} cannot demote from {} to {} — "
                "blocks do not un-spend.".format(block_id, current, new_segment)
            )

        readers = [c["entry_id"] for c in block.get("consumed_by", [])]
        if new_segment in _UNTOUCHED_CLAIM_SEGMENTS and readers:
            if not acknowledge_prior_consumption:
                raise ValueError(
                    "Block {} has already been read by {} and cannot be "
                    "promoted to {} as unexamined validation data.{} Pass "
                    "acknowledge_prior_consumption=True with a rationale if "
                    "crossing it is genuinely legitimate.".format(
                        block_id, ", ".join(sorted(readers)), new_segment,
                        self._notes_summary(block))
                )
            if not (rationale or "").strip():
                raise ValueError(
                    "Acknowledging prior consumption of block {} requires a "
                    "rationale. An acknowledgment with no reason recorded is "
                    "not an acknowledgment.".format(block_id)
                )
            block["promotion_acknowledgment"] = {
                "prior_consumption": sorted(readers),
                "rationale": rationale.strip(),
                "from_segment": current,
            }

        block["segment"] = new_segment
        self._save()

    def _notes_summary(self, block):
        """Read any consumption note back at the point of refusal.

        The return on recording a note at all: an explanation filed in August
        stops nothing in November, but a refusal that quotes it does.
        """
        notes = block.get("consumption_notes") or []
        if not notes:
            return ""
        return " Recorded: {}".format("; ".join(
            "{} — {}".format(n.get("entry_id"), n.get("rationale"))
            for n in notes))

    def acknowledge_consumption(self, block_id, entry_id, rationale,
                                acknowledged_at):
        """Record why a block carries consumption its segment does not imply.

        Segment R claims a block is untouched. When that turns out false, the
        options are to rewrite the label (which needs a demotion path, and
        demotion is what makes V single-use meaningless) or to leave the label
        and attach an explanation. This is the second.

        Prose, not a flag, because contamination is not global: data read by a
        wallet-selection study is genuinely spent for the next wallet-selection
        study and holdout-grade at best for an unrelated family. Only a
        sentence can say which, and a boolean would over-refuse one case while
        under-informing the other.

        A NOTE NAMES THE ENTRY IT COVERS. A blanket "acknowledged" would
        pre-clear every future reading of the block too, so the note is scoped
        to consumption that has actually happened.

        Args:
            block_id: The block ID.
            entry_id: The entry whose consumption is being explained. Must
                already appear in consumed_by.
            rationale: What was read, and what the block is now unfit for.
            acknowledged_at: RFC3339 UTC timestamp. Passed in rather than read
                from a clock, matching complete_block — this module stays
                clock-free.

        Raises:
            KeyError: If block not found.
            ValueError: If the entry has not consumed the block, or no
                rationale was given.
        """
        if block_id not in self._blocks:
            raise KeyError("Block not found: {}".format(block_id))

        block = self._blocks[block_id]
        readers = [c["entry_id"] for c in block.get("consumed_by", [])]
        if entry_id not in readers:
            raise ValueError(
                "Entry {} has not consumed block {}, so there is nothing to "
                "acknowledge. A note filed ahead of consumption would "
                "pre-clear every later reading of the block.".format(
                    entry_id, block_id)
            )

        if not (rationale or "").strip():
            raise ValueError(
                "Acknowledging consumption of block {} requires a rationale — "
                "what was read, and what the block is now unfit for.".format(
                    block_id)
            )

        notes = block.setdefault("consumption_notes", [])
        for note in notes:
            if note.get("entry_id") == entry_id:
                note["rationale"] = rationale.strip()
                note["acknowledged_at"] = acknowledged_at
                self._save()
                return

        notes.append({
            "entry_id": entry_id,
            "rationale": rationale.strip(),
            "acknowledged_at": acknowledged_at,
        })
        self._save()

    def unexplained_consumption(self, block_id):
        """Entries that read this block while its label claims none did.

        Only segment R. E, S1, S2 and L are permissions to read, so
        consumption there is expected rather than contradictory. V is excluded
        because consuming validation data is precisely what V is for — a
        V-block that was read BEFORE its promotion is caught by
        promotion_acknowledgment instead, which is the record of that specific
        act.
        """
        block = self._blocks[block_id]
        if block.get("segment") != "R":
            return []
        explained = {n.get("entry_id")
                     for n in block.get("consumption_notes") or []}
        return sorted({c["entry_id"] for c in block.get("consumed_by", [])}
                      - explained)

    def enforce_block_isolation(self, block_ids, stage):
        """Enforce block segment constraints for a given stage.

        Parent plan §5.2: A stage-1 row touching segment V is a HARD ERROR.
        Stage-1 runs may only touch E, S1, S2 blocks.
        Stage-2 runs may touch V blocks.

        Args:
            block_ids: List of block IDs to check.
            stage: Stage label (1 or 2).

        Raises:
            ValueError: If any block violates the stage constraint.
        """
        # Segments allowed at each stage
        _STAGE_ALLOWED = {
            1: frozenset({"E", "S1", "S2"}),
            2: frozenset({"E", "S1", "S2", "V", "R", "L"}),
        }

        if stage not in _STAGE_ALLOWED:
            raise ValueError("Invalid stage {}. Must be 1 or 2.".format(stage))

        allowed = _STAGE_ALLOWED[stage]

        for block_id in block_ids:
            if block_id not in self._blocks:
                raise KeyError("Block not found: {}".format(block_id))

            block = self._blocks[block_id]
            segment = block.get("segment", "")

            if segment not in allowed:
                raise ValueError(
                    "Block {} (segment {}) cannot be touched at stage {}. "
                    "Stage-{} runs may only touch segments: {}. "
                    "This is a hard error, not a warning (§5.2).".format(
                        block_id, segment, stage, stage,
                        ", ".join(sorted(allowed))
                    )
                )

