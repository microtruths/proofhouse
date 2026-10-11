# proofhouse.schema.versions — schema version and migration hook
"""
Schema versioning for proofhouse records.

Every record carries schema_version. This module defines the current version
and provides the hook for future migrations.

HISTORY — what each version requires of a hypothesis entry.

  1   The Phase 0 shape: V-1 to V-12 and E1 to E6. The later coherence
      checks (V-15 finite thresholds, V-16 clustering unit, V-17 executable
      feasibility) speak to fields v1 already had and apply to every version.
  2   Adds three required fields with no default: p_value_sidedness (V-13),
      fdr_method and fdr_method_justification (V-14). Each decides what a
      p-value is and what survives a screen, so neither can be filled in for
      a record registered before the choice existed.

V-13 and V-14 were required from 2026-08-17 while SCHEMA_VERSION stayed at
1, so every record from before that date was re-validated under rules it
was never registered under, and `verify` failed on registries nobody had
touched. The bump to 2 on 2026-10-11 is the repair. A record carrying 1 is
held to the v1 rules and reported by `verify` as legacy, never as an error
(core/verify.py, _check_schema). Every record this build writes carries 2.
"""

# The current schema version. Incremented when the record shape changes.
# All records written by this version of proofhouse will carry this value.
SCHEMA_VERSION = 2

# The first version at which V-13 and V-14 apply. A record with a valid
# version below this was registered before the fields existed and is not
# asked for them. A missing or invalid version earns no such exemption.
SIDEDNESS_AND_FDR_SINCE = 2


def is_valid_version(record_version):
    """A positive integer no newer than this build.

    A bool is not a version: isinstance(True, int) holds in Python, and a
    record saying `schema_version: true` must not read as version 1.
    """
    return (
        isinstance(record_version, int)
        and not isinstance(record_version, bool)
        and 1 <= record_version <= SCHEMA_VERSION
    )


def check_version(record_version):
    """Validate that a record's schema version is supported.

    Raises ValueError if the version is from the future (unsupported)
    or is not a positive integer.
    """
    if (isinstance(record_version, bool) or not isinstance(record_version, int)
            or record_version < 1):
        raise ValueError(
            "schema_version must be a positive integer, got: {!r}".format(
                record_version
            )
        )
    if record_version > SCHEMA_VERSION:
        raise ValueError(
            "schema_version {} is from a newer proofhouse; "
            "this build supports up to {}".format(record_version, SCHEMA_VERSION)
        )
