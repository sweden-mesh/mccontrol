import datetime

from mccontrol import directory

NOW = datetime.datetime(2026, 10, 7, 12, 0, 0)


def test_relative_times_are_short_and_say_never():
    assert directory.relative(None, NOW) == "-"
    assert directory.relative("2026-10-07 11:59:30", NOW) == "now"
    assert directory.relative("2026-10-07 11:15:00", NOW) == "45 min"
    assert directory.relative("2026-10-07 09:00:00", NOW) == "3 h"
    assert directory.relative("2026-10-04 12:00:00", NOW) == "3 d"
    assert directory.relative("garbage", NOW) == "?"


def _devs():
    return [{"name": "UGO", "node": "AB6B9285", "region": "JKG", "last_sent": "2026-10-07 11:00:00", "last_result": "ok"},
            {"name": "", "node": "4AC61A7F", "region": "JKG", "last_sent": None, "last_result": None},
            {"name": "Far", "node": "FD700369", "region": "GOT", "last_sent": "2026-10-06 11:00:00", "last_result": "refused: not-allowed"}]


def test_sorting_by_name_region_and_last_sent():
    assert [d["node"] for d in directory.sorted_devices(_devs(), "name")] == ["FD700369", "AB6B9285", "4AC61A7F"]
    assert [d["node"] for d in directory.sorted_devices(_devs(), "region")] == ["FD700369", "AB6B9285", "4AC61A7F"]
    assert [d["node"] for d in directory.sorted_devices(_devs(), "last sent")] == ["AB6B9285", "FD700369", "4AC61A7F"]


def test_a_row_lays_out_the_columns_and_fits_the_width():
    r = directory.row(_devs()[1], 38, NOW)
    assert r.startswith("(unnamed)          4AC61A7F JKG  -")
    assert len(r) == 37
    r = directory.row(_devs()[2], 70, NOW)
    assert "FD700369 GOT  1 d     refused: not-allowed" in r
    assert directory.header(38).startswith("Name")


def test_the_footer_counts_tags():
    assert directory.tag_footer(0, 0) == "no devices"
    assert directory.tag_footer(0, 1) == "1 device"
    assert directory.tag_footer(0, 3) == "3 devices"
    assert directory.tag_footer(2, 3) == "2 of 3 tagged"


def test_tagging_a_region():
    assert directory.by_region(_devs(), "jkg") == [0, 1]
    assert directory.by_region(_devs(), "GOT") == [2]
    assert directory.by_region(_devs(), "") == []
