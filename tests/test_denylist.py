"""The tool's copy of the firmware's deny list (MqttControlLogic.h, 2026-10-08). Each
group here has a twin in the firmware's test_mqtt_control; the lists must agree."""
import pytest

from mccontrol import actions, denylist, envelope


def travels(command):
    return denylist.refusal(command) is None


def test_the_tuning_writes_and_every_harmless_read_travel():
    for c in ("set txdelay 0.5", "set rxdelay 0", "set af 9", "set cad on", "set int.thresh 1",
              "set flood.max 8", "set flood.max.unscoped 2", "set autoreply.delay 60",
              "get radio", "get radio.rxgain", "get af.anything", "get name", "get public.key",
              "get mqtt1.server", "get mqtt.cmd", "region", "region put se-jkp *",
              "trigger test", "trigger private " + "AB" * 32, "ver", "reboot", "clkreboot",
              "neighbors", "neighbor.remove 4AC61A7F", "board", "memory", "clear stats",
              "ota check", "clock", "clock sync", "gps on", "powersaving off", "sensor get gps",
              "set name Foo", "set mqtt1.preset meshat.se", "set mqtt2.server x", "set mqtt2.password y",
              "set mqtt.presets 2", "set mqtt.iata GOT", "set mqtt.interval 10"):
        assert travels(c), c


def test_each_denied_group_is_refused_with_a_reason():
    cases = {
        "set prv.key 00": "identity", "set public.key 00": "identity",
        "set mqtt.cmd off": "control plane", "set mqtt.cmd.slot 2": "control plane",
        "set mqtt.cmd.pubkey 00": "control plane", "get mqtt.cmd.pubkey": "command key",
        "set wifi.pwd x": "credential", "get wifi.ssid": "network", "password x": "credential",
        "get guest.password": "credential", "set alert.psk 00": "credential",
        "get bridge.secret": "credential", "get snmp.community": "credential",
        "get mqtt.owner": "ownership", "set owner.info x": "ownership", "get tls.x": "TLS",
        "get prv.key": "private key", "get prv.keyx": "secret", "set passwordx y": "secret",
        "get mqtt1.password": "slot", "get mqtt6.token": "slot", "get mqtt3.username x": "slot",
        "erase": "destructive", "start webconfig": "SoftAP", "start ota ap": "SoftAP",
        "ota update": "signed image", "poweroff": "no way back", "shutdown": "no way back",
        "set bootloader.ver 2": "destructive",
        "set lat 57.78": "position", "get lon": "position", "set role repeater": "role",
        "set repeat off": "kill switch", "set repeat on": "kill switch",
        "set freq 869.6": "radio", "set radio.rxgain 1": "radio", "set tx 20": "radio",
        "set extra.sf 1": "radio", "set dutycycle 10": "radio", "tempradio 869 62.5 8 8": "radio",
        "advert": "transmission", "advert.zerohop": "transmission", "alert test": "transmission",
        "set advert.interval 1": "airtime", "set flood.advert.interval 1": "airtime",
        "time 1900000000": "clock", "log": "serial", "stats-core": "serial", "stats-radio-diag": "serial",
    }
    for command, word in cases.items():
        why = denylist.refusal(command)
        assert why and word in why, (command, why)


def test_the_boundary_rule_and_the_verbs():
    assert travels("set txdelay 1") and not travels("set tx 1")            # tx vs txdelay
    assert travels("set latency 1") and not travels("set lat.x 1")
    assert not travels("reboot now") and not travels("ota check x") and not travels("ota")
    assert not travels("clear") and travels("clear stats")
    assert not travels("floodscan") and not travels("setx autoreply on") and not travels("regionx put se")
    assert denylist.refusal("set") == "names no setting" and denylist.refusal("get") == "names no setting"
    assert "|" in denylist.refusal("region def a|* b|*")


def test_a_refused_command_is_not_signed_and_the_reason_is_in_the_error(tmp_path):
    from mccontrol import store as _store
    store = _store.Store(tmp_path / "home")
    store.create_key()
    store.add_device("LOUTEST", "4AC61A7F", "JKG")
    with pytest.raises(envelope.EnvelopeError, match="would refuse 'set repeat off': denied: the remote kill switch"):
        actions.send(store, "set repeat off", device=store.device("loutest"), dry_run=True)


def test_cautions_cover_what_the_deny_list_newly_admits():
    assert "restarts" in actions.caution("reboot")
    assert "slot 1" in actions.caution("set mqtt1.server mqtt.example")
    assert "region" in actions.caution("set mqtt.iata GOT")
    assert "renames" in actions.caution("set name Foo")
    assert actions.caution("set txdelay 0.5") is None
    assert actions.caution("set repeat off") is None          # denied outright; nothing to ask
