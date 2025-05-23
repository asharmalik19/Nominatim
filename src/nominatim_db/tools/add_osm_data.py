# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Nominatim. (https://nominatim.org)
#
# Copyright (C) 2024 by the Nominatim developer community.
# For a full list of authors see the git log.
"""
Function to add additional OSM data from a file or the API into the database.
"""
from typing import Any, MutableMapping
from pathlib import Path
import logging
import urllib

from ..db.connection import connect
from ..utils.url_utils import get_url
from .exec_utils import run_osm2pgsql

LOG = logging.getLogger()


def _run_osm2pgsql(dsn: str, options: MutableMapping[str, Any]) -> None:
    run_osm2pgsql(options)

    # Handle deletions
    with connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT flush_deleted_places()')
        conn.commit()


def add_data_from_file(dsn: str, fname: str, options: MutableMapping[str, Any]) -> int:
    """ Adds data from a OSM file to the database. The file may be a normal
        OSM file or a diff file in all formats supported by libosmium.
    """
    options['import_file'] = Path(fname)
    options['append'] = True
    _run_osm2pgsql(dsn, options)

    # No status update. We don't know where the file came from.
    return 0


def add_osm_object(dsn: str, osm_type: str, osm_id: int, use_main_api: bool,
                   options: MutableMapping[str, Any]) -> int:
    """ Add or update a single OSM object from the latest version of the
        API.
    """
    if use_main_api:
        base_url = f'https://www.openstreetmap.org/api/0.6/{osm_type}/{osm_id}'
        if osm_type in ('way', 'relation'):
            base_url += '/full'
    else:
        # use Overpass API
        if osm_type == 'node':
            data = f'node({osm_id});out meta;'
        elif osm_type == 'way':
            data = f'(way({osm_id});>;);out meta;'
        else:
            data = f'(rel(id:{osm_id});>;);out meta;'
        base_url = 'https://overpass-api.de/api/interpreter?' \
                   + urllib.parse.urlencode({'data': data})

    options['append'] = True
    options['import_data'] = get_url(base_url).encode('utf-8')

    _run_osm2pgsql(dsn, options)

    return 0
