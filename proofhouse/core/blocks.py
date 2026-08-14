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
    V (Validation) — one block per hypothesis, tokened, single-use
    L (Live) — forward-collected, licenses deployment, never gates
"""

import copy


# Segment promotion order: higher index = more restricted.
# A block may be promoted up this ladder but never demoted.
_SEGMENT_ORDER = {"E": 0, "S1": 1, "S2": 2, "R": 3, "V": 4, "L": 5}

# Segments where consumption is single-use (one entry per block)
_SINGLE_USE_SEGMENTS = frozenset({"V"})

# Segments where seam acknowledgment is required
_SEAM_CHECK_SEGMENTS = frozenset({"V"})


class BlockRegistry:
    """In-memory block registry with consumption tracking.

    Blocks are stored by ID. Consumption appends to a block's consumed_by
    list. V-blocks enforce single-use: once consumed by one entry, they
    refuse consumption by another.
    """

    def __init__(self):
        self._blocks = {}  # id -> block dict

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

        # E4: V-blocks are single-use
        segment = block.get("segment", "")
        if segment in _SINGLE_USE_SEGMENTS:
            existing = block.get("consumed_by", [])
            for consumption in existing:
                if consumption["entry_id"] != entry_id:
                    raise ValueError(
                        "Block {} (segment {}) is already consumed by entry {}. "
                        "V-blocks are single-use per hypothesis "
                        "(A7).".format(block_id, segment, consumption["entry_id"])
                    )
            # Idempotent: if same entry already consumed, just return
            if any(c["entry_id"] == entry_id for c in existing):
                return

        # §2.4.2 / failure catalog #12: seam check on V-blocks
        seams = block.get("seams", [])
        if seams and segment in _SEAM_CHECK_SEGMENTS and not seam_acknowledged:
            raise ValueError(
                "Block {} has data-source seams {} and segment {}. "
                "Consumption requires seam_acknowledged=True. "
                "Failure catalog #12: 'splices need overlap + reconciliation; "
                "never let a seam sit unvalidated inside a test period.'"
                .format(block_id, seams, segment)
            )

        # Record the consumption
        consumption_record = {
            "entry_id": entry_id,
        }
        if seam_acknowledged:
            consumption_record["seam_acknowledged"] = True

        block.setdefault("consumed_by", []).append(consumption_record)

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

    def promote_block(self, block_id, new_segment):
        """Promote a block to a higher segment.

        Blocks may be promoted up the segment ladder (e.g., R→V) but
        never demoted (V→R is refused — blocks do not un-spend).

        Args:
            block_id: The block ID.
            new_segment: The target segment.

        Raises:
            KeyError: If block not found.
            ValueError: If new_segment is invalid or is a demotion.
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
        if _SEGMENT_ORDER[new_segment] < _SEGMENT_ORDER[current]:
            raise ValueError(
                "Block {} cannot demote from {} to {} — "
                "blocks do not un-spend.".format(block_id, current, new_segment)
            )

        block["segment"] = new_segment
