# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Nominatim. (https://nominatim.org)
#
# Copyright (C) 2025 by the Nominatim developer community.
# For a full list of authors see the git log.
"""
Functions for creating a tokenizer or initialising the right one for an
existing database.

A tokenizer is something that is bound to the lifetime of a database. It
can be chosen and configured before the initial import but then needs to
be used consistently when querying and updating the database.

This module provides the functions to create and configure a new tokenizer
as well as instantiating the appropriate tokenizer for updating an existing
database.
"""
from typing import Optional
import logging
import importlib
from pathlib import Path

from ..errors import UsageError
from ..db import properties
from ..db.connection import connect
from ..config import Configuration
from ..tokenizer.base import AbstractTokenizer, TokenizerModule

LOG = logging.getLogger()


def _import_tokenizer(name: str) -> TokenizerModule:
    """ Load the tokenizer.py module from project directory.
    """
    src_file = Path(__file__).parent / (name + '_tokenizer.py')
    if not src_file.is_file():
        LOG.fatal("No tokenizer named '%s' available. "
                  "Check the setting of NOMINATIM_TOKENIZER.", name)
        raise UsageError('Tokenizer not found')

    return importlib.import_module('nominatim_db.tokenizer.' + name + '_tokenizer')


def create_tokenizer(config: Configuration, init_db: bool = True,
                     module_name: Optional[str] = None) -> AbstractTokenizer:
    """ Create a new tokenizer as defined by the given configuration.

        The tokenizer data and code is copied into the 'tokenizer' directory
        of the project directory and the tokenizer loaded from its new location.
    """
    if module_name is None:
        module_name = config.TOKENIZER

    # Import and initialize the tokenizer.
    tokenizer_module = _import_tokenizer(module_name)

    tokenizer = tokenizer_module.create(config.get_libpq_dsn())
    tokenizer.init_new_db(config, init_db=init_db)

    with connect(config.get_libpq_dsn()) as conn:
        properties.set_property(conn, 'tokenizer', module_name)

    return tokenizer


def get_tokenizer_for_db(config: Configuration) -> AbstractTokenizer:
    """ Instantiate a tokenizer for an existing database.

        The function looks up the appropriate tokenizer in the database
        and initialises it.
    """
    with connect(config.get_libpq_dsn()) as conn:
        name = properties.get_property(conn, 'tokenizer')

    if name is None:
        LOG.fatal("Tokenizer was not set up properly. Database property missing.")
        raise UsageError('Cannot initialize tokenizer.')

    tokenizer_module = _import_tokenizer(name)

    tokenizer = tokenizer_module.create(config.get_libpq_dsn())
    tokenizer.init_from_project(config)

    return tokenizer
