"""The data pack's conversions that the sim's clock depends on."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from build_data import _local_to_utc  # noqa: E402


@pytest.mark.parametrize("local, tz, utc", [
    ("15-FEB-21 03:00:00", "CST-6", "2021-02-15T09:00Z"),   # Uri, in Central time
    ("09-OCT-20 11:00:00", "CST-6", "2020-10-09T17:00Z"),   # Delta's report for Evangeline Parish
    ("09-OCT-20 21:00:00", "CST-6", "2020-10-10T03:00Z"),   # into the next UTC day
    ("09-OCT-20 11:00:00", "EST-5", "2020-10-09T16:00Z"),
    ("09-OCT-20 11:00:00", "CDT-5", "2020-10-09T16:00Z"),
    ("09-OCT-20 11:00:00", "MST-7", "2020-10-09T18:00Z"),
])
def test_storm_event_times_become_utc(local, tz, utc):
    assert _local_to_utc(local, tz) == utc


def test_an_unknown_zone_is_an_error():
    with pytest.raises(ValueError, match="unknown Storm Events timezone"):
        _local_to_utc("09-OCT-20 11:00:00", "Central")
