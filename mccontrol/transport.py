"""Publish a signed envelope and wait for the node's answer.

One connection per command, opened and closed here, so that nothing holds the broker between
keystrokes. The result topic is subscribed before the command is published, or a fast node
could answer before anyone was listening.
"""

import threading
import time

from . import envelope


class BrokerError(Exception):
    pass


def _client(config):
    import paho.mqtt.client as mqtt
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(config["username"], config["password"])
    return client


def _connect(client, config, timeout=10.0):
    done = threading.Event()
    verdict = {}

    def on_connect(_c, _u, _f, reason_code, _p=None):
        verdict["ok"] = not reason_code.is_failure if hasattr(reason_code, "is_failure") else reason_code == 0
        verdict["why"] = str(reason_code)
        done.set()

    client.on_connect = on_connect
    try:
        client.connect(config["broker"], int(config["port"]), keepalive=30)
    except OSError as exc:
        raise BrokerError(f"cannot reach {config['broker']}:{config['port']}: {exc}") from None
    client.loop_start()
    if not done.wait(timeout):
        client.loop_stop()
        raise BrokerError(f"{config['broker']} did not answer the connection in {timeout:.0f} s")
    if not verdict.get("ok"):
        client.loop_stop()
        raise BrokerError(f"{config['broker']} refused the connection: {verdict.get('why')}")


def send(config, seed_hex, topic, command, nodes_to_hear=(), wait_secs=20.0, ttl_secs=None,
         counter=None):
    """Sign `command` for `topic`, publish it, and collect results from `nodes_to_hear`.

    Returns `(payload, {node: Result})`. A node missing from the dict did not answer within
    `wait_secs`; that is reported as silence, never as an empty result.
    """
    counter = int(time.time()) if counter is None else counter
    payload = envelope.build(seed_hex, topic, counter, command,
                             ttl_secs=ttl_secs or config.get("ttl_secs", envelope.DEFAULT_TTL_SECS))
    answers = {}
    lock = threading.Lock()
    all_in = threading.Event()
    wanted = set(nodes_to_hear)

    def on_message(_c, _u, msg):
        node = envelope.node_from_result_topic(msg.topic)
        result = envelope.parse_result(msg.payload, node)
        if result is None or result.counter != counter:
            return
        with lock:
            answers[node] = result
            if wanted and wanted <= set(answers):
                all_in.set()

    client = _client(config)
    client.on_message = on_message
    _connect(client, config)
    try:
        for node in wanted:
            client.subscribe(envelope.result_topic(config["region"], node), qos=1)
        info = client.publish(topic, payload, qos=1)
        info.wait_for_publish(timeout=10)
        if wanted:
            all_in.wait(wait_secs)
    finally:
        client.loop_stop()
        client.disconnect()
    return payload, dict(answers)


def discover(config, seconds=5.0, client_factory=None):
    """The nodes publishing a status in the region: `[{node, name, model, firmware, repeat, at}]`.

    Observer nodes publish `meshcore/{IATA}/{NODE}/status`, retained, so the broker hands over
    the last one from every node the moment we subscribe: a few seconds is enough. A retained
    status may be old -- the node may be long gone -- so `at` is shown and nothing here claims
    the node is up. Whether it accepts *your* key is learnt by sending it something.
    """
    import json

    found = {}
    lock = threading.Lock()

    def on_message(_c, _u, msg):
        parts = msg.topic.split("/")
        if len(parts) < 4 or parts[-1] != "status":
            return
        node = parts[-2].upper()
        if len(node) != 64:
            return
        try:
            data = json.loads(msg.payload.decode("utf-8", "replace"))
        except ValueError:
            return
        with lock:
            found[node] = {
                "node": node,
                "name": str(data.get("origin") or "")[:40],
                "model": str(data.get("model") or ""),
                "firmware": str(data.get("firmware_version") or ""),
                "repeat": str(data.get("repeat") or ""),
                "at": str(data.get("timestamp") or "")[:16].replace("T", " "),
                "retained": bool(getattr(msg, "retain", False)),
            }

    # Statuses are on the Observer feed, which is a different broker from the control one.
    feed = dict(config, broker=config.get("discovery_broker") or config["broker"])
    client = (client_factory or _client)(feed)
    client.on_message = on_message
    _connect(client, feed)
    try:
        client.subscribe(f"{envelope.TOPIC_PREFIX}{config['region'].upper()}/+/status", qos=0)
        time.sleep(seconds)
    finally:
        client.loop_stop()
        client.disconnect()
    return sorted(found.values(), key=lambda d: (d["name"].lower(), d["node"]))


def test_connection(config, client_factory=None, timeout=10.0):
    """Can we reach the control broker, sign in, subscribe to a result topic and publish?

    Returns a list of `(step, ok, detail)` in the order tried, stopping at the first failure.
    The publish goes to a test topic under the region, not to any node's command topic, so
    nothing a repeater listens on is touched.
    """
    steps = []
    client = (client_factory or _client)(config)
    try:
        _connect(client, config, timeout=timeout)
    except BrokerError as exc:
        steps.append(("connect and sign in", False, str(exc)))
        return steps
    steps.append(("connect and sign in", True,
                  f"{config['broker']}:{config['port']} as {config.get('username') or '(no username)'}"))
    region = (config.get("region") or "TEST").upper()
    try:
        topic = f"{envelope.TOPIC_PREFIX}{region}/mccontrol-test/res"
        got = threading.Event()
        client.on_message = lambda _c, _u, msg: got.set() if msg.topic == topic else None
        info = client.subscribe(topic, qos=1)
        steps.append(("subscribe to a result topic", True, topic))
        info = client.publish(topic, "mccontrol connection test", qos=1)
        info.wait_for_publish(timeout=timeout)
        steps.append(("publish", True, "accepted by the broker"))
        if got.wait(timeout):
            steps.append(("receive what we published", True, "the broker relays this topic"))
        else:
            steps.append(("receive what we published", False,
                          f"nothing came back in {timeout:.0f} s: the broker accepts publishes "
                          f"but does not deliver them to subscribers, or drops this topic"))
    except Exception as exc:        # paho raises several types; the step name says where
        steps.append(("subscribe or publish", False, str(exc)))
    finally:
        client.loop_stop()
        client.disconnect()
    return steps
