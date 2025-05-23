# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Nominatim. (https://nominatim.org)
#
# Copyright (C) 2025 by the Nominatim developer community.
# For a full list of authors see the git log.
"""
Functions for importing, updating and otherwise maintaining the table
of artificial postcode centroids.
"""
from typing import Optional, Tuple, Dict, List, TextIO
from collections import defaultdict
from pathlib import Path
import csv
import gzip
import logging
from math import isfinite

from psycopg import sql as pysql

from ..db.connection import connect, Connection, table_exists
from ..utils.centroid import PointsCentroid
from ..data.postcode_format import PostcodeFormatter, CountryPostcodeMatcher
from ..tokenizer.base import AbstractAnalyzer, AbstractTokenizer

LOG = logging.getLogger()


def _to_float(numstr: str, max_value: float) -> float:
    """ Convert the number in string into a float. The number is expected
        to be in the range of [-max_value, max_value]. Otherwise rises a
        ValueError.
    """
    num = float(numstr)
    if not isfinite(num) or num <= -max_value or num >= max_value:
        raise ValueError()

    return num


class _PostcodeCollector:
    """ Collector for postcodes of a single country.
    """

    def __init__(self, country: str, matcher: Optional[CountryPostcodeMatcher]):
        self.country = country
        self.matcher = matcher
        self.collected: Dict[str, PointsCentroid] = defaultdict(PointsCentroid)
        self.normalization_cache: Optional[Tuple[str, Optional[str]]] = None

    def add(self, postcode: str, x: float, y: float) -> None:
        """ Add the given postcode to the collection cache. If the postcode
            already existed, it is overwritten with the new centroid.
        """
        if self.matcher is not None:
            normalized: Optional[str]
            if self.normalization_cache and self.normalization_cache[0] == postcode:
                normalized = self.normalization_cache[1]
            else:
                match = self.matcher.match(postcode)
                normalized = self.matcher.normalize(match) if match else None
                self.normalization_cache = (postcode, normalized)

            if normalized:
                self.collected[normalized] += (x, y)

    def commit(self, conn: Connection, analyzer: AbstractAnalyzer,
               project_dir: Optional[Path]) -> None:
        """ Update postcodes for the country from the postcodes selected so far.

            When 'project_dir' is set, then any postcode files found in this
            directory are taken into account as well.
        """
        if project_dir is not None:
            self._update_from_external(analyzer, project_dir)
        to_add, to_delete, to_update = self._compute_changes(conn)

        LOG.info("Processing country '%s' (%s added, %s deleted, %s updated).",
                 self.country, len(to_add), len(to_delete), len(to_update))

        with conn.cursor() as cur:
            if to_add:
                cur.executemany(pysql.SQL(
                    """INSERT INTO location_postcode
                         (place_id, indexed_status, country_code,
                          postcode, geometry)
                       VALUES (nextval('seq_place'), 1, {}, %s,
                               ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                    """).format(pysql.Literal(self.country)),
                    to_add)
            if to_delete:
                cur.execute("""DELETE FROM location_postcode
                               WHERE country_code = %s and postcode = any(%s)
                            """, (self.country, to_delete))
            if to_update:
                cur.executemany(
                    pysql.SQL("""UPDATE location_postcode
                                 SET indexed_status = 2,
                                     geometry = ST_SetSRID(ST_Point(%s, %s), 4326)
                                 WHERE country_code = {} and postcode = %s
                              """).format(pysql.Literal(self.country)),
                    to_update)

    def _compute_changes(
            self, conn: Connection
            ) -> Tuple[List[Tuple[str, float, float]], List[str], List[Tuple[float, float, str]]]:
        """ Compute which postcodes from the collected postcodes have to be
            added or modified and which from the location_postcode table
            have to be deleted.
        """
        to_update = []
        to_delete = []
        with conn.cursor() as cur:
            cur.execute("""SELECT postcode, ST_X(geometry), ST_Y(geometry)
                           FROM location_postcode
                           WHERE country_code = %s""",
                        (self.country, ))
            for postcode, x, y in cur:
                pcobj = self.collected.pop(postcode, None)
                if pcobj:
                    newx, newy = pcobj.centroid()
                    if (x - newx) > 0.0000001 or (y - newy) > 0.0000001:
                        to_update.append((newx, newy, postcode))
                else:
                    to_delete.append(postcode)

        to_add = [(k, *v.centroid()) for k, v in self.collected.items()]
        self.collected = defaultdict(PointsCentroid)

        return to_add, to_delete, to_update

    def _update_from_external(self, analyzer: AbstractAnalyzer, project_dir: Path) -> None:
        """ Look for an external postcode file for the active country in
            the project directory and add missing postcodes when found.
        """
        csvfile = self._open_external(project_dir)
        if csvfile is None:
            return

        try:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if 'postcode' not in row or 'lat' not in row or 'lon' not in row:
                    LOG.warning("Bad format for external postcode file for country '%s'."
                                " Ignored.", self.country)
                    return
                postcode = analyzer.normalize_postcode(row['postcode'])
                if postcode not in self.collected:
                    try:
                        # Do float conversation separately, it might throw
                        centroid = (_to_float(row['lon'], 180),
                                    _to_float(row['lat'], 90))
                        self.collected[postcode] += centroid
                    except ValueError:
                        LOG.warning("Bad coordinates %s, %s in %s country postcode file.",
                                    row['lat'], row['lon'], self.country)

        finally:
            csvfile.close()

    def _open_external(self, project_dir: Path) -> Optional[TextIO]:
        fname = project_dir / f'{self.country}_postcodes.csv'

        if fname.is_file():
            LOG.info("Using external postcode file '%s'.", fname)
            return open(fname, 'r', encoding='utf-8')

        fname = project_dir / f'{self.country}_postcodes.csv.gz'

        if fname.is_file():
            LOG.info("Using external postcode file '%s'.", fname)
            return gzip.open(fname, 'rt')

        return None


def update_postcodes(dsn: str, project_dir: Optional[Path], tokenizer: AbstractTokenizer) -> None:
    """ Update the table of artificial postcodes.

        Computes artificial postcode centroids from the placex table,
        potentially enhances it with external data and then updates the
        postcodes in the table 'location_postcode'.
    """
    matcher = PostcodeFormatter()
    with tokenizer.name_analyzer() as analyzer:
        with connect(dsn) as conn:
            # First get the list of countries that currently have postcodes.
            # (Doing this before starting to insert, so it is fast on import.)
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT country_code FROM location_postcode")
                todo_countries = set((row[0] for row in cur))

            # Recompute the list of valid postcodes from placex.
            with conn.cursor(name="placex_postcodes") as cur:
                cur.execute("""
                SELECT cc, pc, ST_X(centroid), ST_Y(centroid)
                FROM (SELECT
                        COALESCE(plx.country_code,
                                 get_country_code(ST_Centroid(pl.geometry))) as cc,
                        pl.address->'postcode' as pc,
                        COALESCE(plx.centroid, ST_Centroid(pl.geometry)) as centroid
                      FROM place AS pl LEFT OUTER JOIN placex AS plx
                             ON pl.osm_id = plx.osm_id AND pl.osm_type = plx.osm_type
                    WHERE pl.address ? 'postcode' AND pl.geometry IS NOT null) xx
                WHERE pc IS NOT null AND cc IS NOT null
                ORDER BY cc, pc""")

                collector = None

                for country, postcode, x, y in cur:
                    if collector is None or country != collector.country:
                        if collector is not None:
                            collector.commit(conn, analyzer, project_dir)
                        collector = _PostcodeCollector(country, matcher.get_matcher(country))
                        todo_countries.discard(country)
                    collector.add(postcode, x, y)

                if collector is not None:
                    collector.commit(conn, analyzer, project_dir)

            # Now handle any countries that are only in the postcode table.
            for country in todo_countries:
                fmt = matcher.get_matcher(country)
                _PostcodeCollector(country, fmt).commit(conn, analyzer, project_dir)

            conn.commit()

        analyzer.update_postcodes_from_db()


def can_compute(dsn: str) -> bool:
    """
        Check that the place table exists so that
        postcodes can be computed.
    """
    with connect(dsn) as conn:
        return table_exists(conn, 'place')
