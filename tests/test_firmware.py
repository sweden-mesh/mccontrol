from mccontrol import firmware

AR10 = "esp32s3-v1.17.1-ar-10-observer-42f48ce9"
AR11 = "esp32s3-v1.17.1-ar-11-observer-2c079ed7"


def test_the_revision_is_read_from_the_tag():
    assert firmware.revision(AR10) == 10 and firmware.revision(AR11) == 11
    assert firmware.revision("MeshCore v1.16.0.37-observer-9670035") is None
    assert firmware.revision("") is None and firmware.revision(None) is None
    assert firmware.revision("x-ar-123-y") == 123


def test_ar10_admits_the_old_allowlist_and_nothing_newer():
    for c in ("set autoreply on", "set autoreply.hops 8", "get autoreply", "get autoreply.channel",
              "trigger test", "trigger test a1b2c3d4 S", "trigger private " + "AB" * 32,
              "get txdelay", "get af", "get radio", "get repeat", "get flood.max.unscoped"):
        assert firmware.admits(AR10, c), c
    for c in ("region", "region put se-jkp *", "ver", "get name", "get stats", "set txdelay 0.5",
              "reboot", "neighbors", "get radio.rxgain", "get radio foo", "set autoreplyx 1"):
        assert not firmware.admits(AR10, c), c


def test_ar11_is_the_deny_list_and_the_boundary_is_at_eleven():
    assert firmware.admits(AR11, "region put se-jkp *") and firmware.admits(AR11, "ver") and firmware.admits(AR11, "get stats")
    assert not firmware.admits(AR11, "set repeat off") and not firmware.admits(AR11, "erase")
    assert firmware.entry_for(AR10)[0] == "ar-10" and firmware.entry_for(AR11)[0] == "ar-11"
    assert firmware.entry_for("v1.17.1-lou-15-arrc-x")[0] == "lou-14"   # newer than the table: the newest row
    assert firmware.entry_for("v1.17.1-ar-9-x")[0] == "ar-10"           # older: the oldest row


def test_an_unknown_version_is_treated_as_the_oldest():
    assert firmware.admits(None, "get autoreply") and not firmware.admits(None, "ver")
    assert firmware.admits("MeshCore v1.16.0.37-observer", "trigger test")
    assert not firmware.admits("", "region")


def test_needs_names_the_oldest_build_that_admits():
    assert firmware.needs("get autoreply") == "ar-10"
    assert firmware.needs("ver") == "ar-11" and firmware.needs("region") == "ar-11" and firmware.needs("get stats") == "ar-11"
    assert firmware.needs("erase") is None


LOU_AR = "esp32s3-v1.17.1-lou-14-ar-observer-1a2b3c4d"
LOU_RC = "esp32s3-v1.17.1-lou-14-rc-observer-1a2b3c4d"
LOU_ARRC = "esp32s3-v1.17.1-lou-14-arrc-observer-1a2b3c4d"
AR13 = "esp32s3-v1.17.1-ar-13-observer-16a05810"


def test_lou_is_read_before_ar():
    # An ar-only image carries -ar- after its revision; read first, the hash would be it.
    assert firmware.revision(LOU_AR) == 14 and firmware.revision("x-v1.17.1-lou-14-ar-9f04c21b") == 14
    assert firmware.revision(LOU_ARRC) == 14 and firmware.revision(AR13) == 13
    assert firmware.revision("x-lou-x-ar-13-y") is None
    assert firmware.entry_for(LOU_AR)[0] == "lou-14" and firmware.entry_for(AR13)[0] == "ar-13"
    assert firmware.speaks_v2(LOU_RC)


def test_the_features_come_from_ver():
    assert firmware.features(LOU_AR) == "ar" and firmware.features(LOU_RC) == "rc"
    assert firmware.features(LOU_ARRC) == "arrc"
    assert firmware.features("nrf52840-v1.17.1-lou-14-ar-1a2b3c4d") == "ar"
    assert firmware.features(AR13) == "arrc" and firmware.features(None) == "arrc"   # before lou-14: both
    assert firmware.features("x-v1.17.1-lou-14-rcar-1a2b") == ""        # not a combination
    assert firmware.features("x-v1.17.1-lou-14-1a2b3c4d") == ""         # no token
    assert firmware.has(LOU_ARRC, "ar") and firmware.has(LOU_ARRC, "rc") and not firmware.has(LOU_RC, "ar")
    assert not firmware.has("x-lou-14-asar-1", "rc") and firmware.has("x-lou-14-asar-1", "as")


def test_admits_by_features():
    for c in ("trigger test", "trigger test a1b2c3d4 S", "trigger private " + "AB" * 32,
              "set autoreply on", "set autoreply.region se-jkp", "get autoreply", "get autoreply.legacy"):
        assert firmware.admits(LOU_ARRC, c), c
        assert not firmware.admits(LOU_RC, c), c          # rc without ar: refused, so hidden
    for c in ("get stats", "region", "ver", "mqtt.cmd ping", "set ota.auto on", "get autoreplyx"):
        assert firmware.admits(LOU_RC, c) == firmware.admits(LOU_ARRC, c), c
    assert firmware.admits(LOU_RC, "get stats") and firmware.admits(LOU_RC, "region put se-jkp *")
    # An ar-only image has no plane: nothing travels to it.
    assert not any(firmware.admits(LOU_AR, c) for c in ("ver", "get stats", "trigger test"))
