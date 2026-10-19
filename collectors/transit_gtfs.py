#!/usr/bin/env python
"""Refresh the transit schedule database once a week.

WMATA and Ride On publish new GTFS a few times a year, at each service change,
and the planner reads ledger/raw/transit/gtfs.db rather than anything in
facts.db. Nothing is written as facts: a timetable is reference data, not
something that happened. A failed download keeps the previous database.
"""
import os, pathlib, shutil, sys

_EXT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_EXT / "lib"))
_root = os.environ.get("HERALD_ROOT")
if not _root and (_herald := shutil.which("herald")):
    _root = str(pathlib.Path(_herald).resolve().parents[1])
if _root:
    sys.path.insert(0, str(pathlib.Path(_root) / "lib"))

from herald import collector
from transit import gtfs

NAME = "transit_gtfs"
REQUIRES = ("wmata",)
CADENCE_MINUTES = 7 * 24 * 60


def collect(con) -> dict:
    return gtfs.build()


if __name__ == "__main__":
    collector.main(NAME, collect)
