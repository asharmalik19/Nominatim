# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Nominatim. (https://nominatim.org)
#
# Copyright (C) 2024 by the Nominatim developer community.
# For a full list of authors see the git log.
"""
Version information for Nominatim.
"""
from typing import NamedTuple, Optional


class NominatimVersion(NamedTuple):
    """ Version information for Nominatim. We follow semantic versioning.

        Major, minor and patch_level refer to the last released version.
        The database patch level tracks important changes between releases
        and must always be increased when there is a change to the database or code
        that requires a migration.

        When adding a migration on the development branch, raise the patch level
        to 99 to make sure that the migration is applied when updating from a
        patch release to the next minor version. Patch releases usually shouldn't
        have migrations in them. When they are needed, then make sure that the
        migration can be reapplied and set the migration version to the appropriate
        patch level when cherry-picking the commit with the migration.
    """

    major: int
    minor: int
    patch_level: int
    db_patch_level: int

    def __str__(self) -> str:
        if self.db_patch_level is None:
            return f"{self.major}.{self.minor}.{self.patch_level}"

        return f"{self.major}.{self.minor}.{self.patch_level}-{self.db_patch_level}"

    def release_version(self) -> str:
        """ Return the release version in semantic versioning format.

            The release version does not include the database patch version.
        """
        return f"{self.major}.{self.minor}.{self.patch_level}"


def parse_version(version: str) -> NominatimVersion:
    """ Parse a version string into a version consisting of a tuple of
        four ints: major, minor, patch level, database patch level

        This is the reverse operation of `version_str()`.
    """
    parts = version.split('.')
    return NominatimVersion(*[int(x) for x in parts[:2] + parts[2].split('-')])


NOMINATIM_VERSION = parse_version('5.1.0-0')

POSTGRESQL_REQUIRED_VERSION = (12, 0)
POSTGIS_REQUIRED_VERSION = (3, 0)
OSM2PGSQL_REQUIRED_VERSION = (1, 8)

# Cmake sets a variable @GIT_HASH@ by executing 'git --log'. It is not run
# on every execution of 'make'.
# cmake/tool-installed.tmpl is used to build the binary 'nominatim'. Inside
# there is a call to set the variable value below.
GIT_COMMIT_HASH: Optional[str] = None
