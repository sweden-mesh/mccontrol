import datetime
import re

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
    r = directory.row(_devs()[2], 90, NOW)
    assert re.search(r"FD700369 GOT  1 d\s{20,}refused: not-allowed", r)    # blank Batt, Seen and Ver between
    assert directory.header(38).startswith("Name")


def test_the_footer_counts_tags():
    assert directory.tag_footer(0, 0) == "no devices"
    assert directory.tag_footer(0, 1) == "1 device"
    assert directory.tag_footer(0, 3) == "3 devices"
    assert directory.tag_footer(2, 3) == "2 of 3 tagged"


def test_the_fleet_columns_show_battery_in_volts_and_the_age_of_the_last_status():
    d = dict(_devs()[1], fleet={"battery_mv": 4261, "seen": (NOW - datetime.timedelta(minutes=12)).isoformat(), "firmware": "x"})
    r = directory.row(d, 100, NOW)
    assert "4AC61A7F JKG  -       4.26  12 m" in r
    assert directory.header(100).split()[4:6] == ["Batt", "Seen"]
    assert "   " in directory.row(_devs()[1], 100, NOW)         # no fleet data: blank, never a guess


def test_the_version_column_is_short_and_sorts():
    assert directory.version_short("esp32s3-v1.17.1-ar-10-observer-42f48ce9") == "ar-10 1.17.1"
    assert directory.version_short("MeshCore v1.16.0.37-observer-9670035") == "1.16.0.37"
    assert directory.version_short(None) == ""
    devs = [dict(_devs()[0], ver="v1.17.1-ar-10"), dict(_devs()[1], ver="v1.16.0"), _devs()[2]]
    assert [d.get("ver") for d in directory.sorted_devices(devs, "version")] == ["v1.16.0", "v1.17.1-ar-10", None]
    assert "ar-10 1.17.1" in directory.row(devs[0], 100, NOW)


def test_a_device_not_heard_from_in_seven_days_is_marked_and_one_never_asked_is_not():
    fresh = dict(_devs()[0], last_sent=(NOW - datetime.timedelta(days=10)).isoformat(),
                 last_heard=(NOW - datetime.timedelta(days=2)).isoformat())
    old = dict(_devs()[0], last_sent=(NOW - datetime.timedelta(days=10)).isoformat(),
               last_heard=(NOW - datetime.timedelta(days=8)).isoformat())
    never_heard = dict(_devs()[0], last_sent=(NOW - datetime.timedelta(hours=1)).isoformat(), last_heard=None)
    never_asked = dict(_devs()[0], last_sent=None, last_heard=None)
    beacon_saw = dict(old, fleet={"battery_mv": 4000, "seen": (NOW - datetime.timedelta(days=1)).isoformat()})
    assert not directory.stale(fresh, NOW) and directory.stale(old, NOW)
    assert directory.stale(never_heard, NOW) and not directory.stale(never_asked, NOW)
    assert not directory.stale(beacon_saw, NOW)
    assert directory.row(old, 60, NOW).startswith(old["name"][:16] + " ?")
    assert " ?" not in directory.row(fresh, 60, NOW)[:18]


def test_the_chosen_columns_decide_what_a_narrow_row_shows():
    d = dict(_devs()[0], ver="v1.17.1-ar-10", last_result="ok")
    assert directory.chosen(["ver", "sent"]) == ["sent", "ver"]            # COLUMNS order, not the asking order
    assert directory.chosen(["bogus"]) == list(directory.COLUMNS)           # nothing valid: all
    narrow = directory.row(d, 38, NOW, columns=["ver"])
    assert "ar-10 1.1" in narrow and "GOT" not in narrow       # 37 columns: the revision first, then what fits
    assert directory.header(38, ["ver"]).split() == ["Name", "Node", "Ver"]
    wide = directory.row(d, 100, NOW)
    assert "JKG" in wide and "ar-10 1.17.1" in wide and wide.rstrip().endswith("ok")


def test_two_nodes_on_ar_10_and_ar_11_differ_in_the_ver_column_at_every_width():
    """Wide enough, Ver is `ar-10 1.17.1`; narrower than that (MeshcoreCommander's directory at
    120 columns leaves it three), it is the AR column: the revision number alone."""
    old = {"name": "Old", "node": "AAAA0001", "region": "JKG", "ver": "esp32s3-v1.17.1-ar-10-observer-42f48ce9"}
    new = dict(old, name="New", ver="esp32s3-v1.17.1-ar-11-observer-90e86c88")
    plain = dict(old, ver="MeshCore v1.16.0.37-observer-9670035")
    for width, columns, cell in ((111, None, "ar-1"), (58, None, " 1"), (57, None, " 1"), (38, ["ver"], "ar-1"),
                                 (38, ["ver", "result"], "ar-1"), (44, ["ver"], "ar-1")):
        a, b = directory.row(old, width, columns=columns), directory.row(new, width, columns=columns)
        assert a[19:] != b[19:], (width, a, b)                      # the names aside, the rows differ
        assert (cell + "0") in a and (cell + "1") in b, (width, a, b)
    assert directory.header(57).rstrip().endswith("AR") and "Ver" in directory.header(111) and "Ver" in directory.header(38, ["ver"])
    assert directory.row(new, 57).rstrip().endswith("11") and directory.row(plain, 57).rstrip().endswith("-")
    assert directory.cells(old)["ver"] == "ar-10 1.17.1"
