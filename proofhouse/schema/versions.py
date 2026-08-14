# proofhouse.schema.versions — schema version and migration hook
"""
Schema versioning for proofhouse records.

Every record carries schema_version. This module defines the current version
and provides the hook for future migrations.
"""

# The current schema version. Incremented when the record shape changes.
# All records written by this version of proofhouse will carry this value.
SCHEMA_VERSION = 1


def check_version(record_version):
    """Validate that a record's schema version is supported.

    Raises ValueError if the version is from the future (unsupported)
    or is not a positive integer.
    """
    if not isinstance(record_version, int) or record_version < 1:
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
