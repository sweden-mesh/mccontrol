"""Publish a signed envelope and wait for the node's answer.

One connection per command, opened and closed here, so that nothing holds the broker between
keystrokes. The result topic is subscribed before the command is published, or a fast node
could answer before anyone was listening.
"""

import os
import threading
import time

from . import envelope, token


class BrokerError(Exception):
    pass


def login(config, seed_hex=None):
    """`(username, password)` for the broker: the configured pair when a username is set (a
    region's own broker), else the tool's own key as a token the way a node signs in --
    nothing to hand out, the key is the identity."""
    if config.get("username"):
        return config["username"], config.get("password", "")
    if not seed_hex:
        return "", ""
    return token.username(seed_hex), token.make(seed_hex, config["broker"])


def _client(config, seed_hex=None):
    import paho.mqtt.client as mqtt
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if config.get("tls"):
        client.tls_set()
    return client


def _connect(client, config, timeout=10.0, seed_hex=None):
    """Sign in and connect. The credentials are set here, not in the factory, so a test's
    stand-in client sees the same login a real one gets."""
    user, password = login(config, seed_hex)
    client.username_pw_set(user, password)
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


FAKE_WAIT_SECS = float(os.environ.get("MCCONTROL_FAKE_WAIT", "0.6"))   # a faked send still waits a little, so a bar can be seen to move


def _fake(topic, command, counter, nodes):
    """For the interface's pseudo-terminal tests only: MCCONTROL_FAKE_RESULTS holds a JSON
    object `{node: [code, reply]}` and no broker is touched. Unset in normal use."""
    import json
    import os
    spec = os.environ.get("MCCONTROL_FAKE_RESULTS")
    if spec is None:
        return None
    scripted = json.loads(spec)
    answers = {}
    nodes = list(nodes) or [k.split("|")[0] for k in scripted]      # a broadcast: everyone scripted
    for node in nodes:
        hit = scripted.get(f"{node}|{command}", scripted.get(node))
        if hit is not None:
            code, reply = hit
            answers[node] = envelope.Result(node, counter, code, command, reply)
    return answers


TICK_SECS = 0.2


def _wait(total, done, answered, progress):
    """Wait up to `total` seconds, calling `progress(elapsed, total, answered())` every tick
    (a screen draws a bar from it); stop early when `done()` or when progress returns True
    (the person pressed Esc). The results so far are kept either way."""
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if progress and progress(min(elapsed, total), total, answered()):
            return
        if done() or elapsed >= total:
            return
        time.sleep(min(TICK_SECS, max(0.0, total - elapsed)))


def send(config, seed_hex, topic, command, nodes_to_hear=(), wait_secs=20.0, ttl_secs=None,
         counter=None, progress=None, keys=None):
    """Sign `command` for `topic`, publish it, and collect results from `nodes_to_hear`.

    Returns `(payload, {node: Result})`. A node missing from the dict did not answer within
    `wait_secs`; that is reported as silence, never as an empty result. `progress` is called
    through the wait as `_wait` describes.
    """
    counter = int(time.time()) if counter is None else counter
    payload = envelope.build(seed_hex, topic, counter, command,
                             ttl_secs=ttl_secs or config.get("ttl_secs", envelope.DEFAULT_TTL_SECS))
    faked = _fake(topic, command, counter, nodes_to_hear)
    if faked is not None:
        _wait(FAKE_WAIT_SECS, lambda: False, lambda: list(faked), progress)
        return payload, faked
    answers = {}
    keys = keys or {}                      # node id -> whole key where known (ar-11 topics)
    lock = threading.Lock()
    all_in = threading.Event()
    wanted = set(nodes_to_hear)

    def on_message(_c, _u, msg):
        leaf = envelope.node_from_result_topic(msg.topic)
        node = leaf[:envelope.NODE_HEX_LEN] if leaf else None
        result = envelope.parse_result(msg.payload, node)
        if result is None or result.counter != counter:
            return
        with lock:
            answers[node] = result
            if wanted and wanted <= set(answers):
                all_in.set()

    client = _client(config, seed_hex)
    client.on_message = on_message
    _connect(client, config, seed_hex=seed_hex)
    try:
        for node in wanted:
            client.subscribe(envelope.result_topic(config["region"], node, keys.get(node)), qos=1)
        info = client.publish(topic, payload, qos=1)
        info.wait_for_publish(timeout=10)
        if wanted:
            _wait(wait_secs, all_in.is_set, lambda: list(answers), progress)
    finally:
        client.loop_stop()
        client.disconnect()
    return payload, dict(answers)


# The read that discovery broadcasts. Harmless, on every node's allowlist, and answered by
# exactly the nodes that verified the signature: the firmware publishes no result for a
# command whose signature failed, so an answer of any kind is proof the node holds your key.
DISCOVERY_COMMAND = "get autoreply"


def discover(config, seed_hex, region, wait_secs=10.0, client_factory=None, counter=None, progress=None):
    """The nodes in `region` that accept your signing key: `[{node, region, code, reply}]`.

    One signed broadcast of a read to `meshcore/{IATA}/all/cmd` on the control broker, then
    every `meshcore/{IATA}/+/res` that answers it. A node that answers -- even with a refusal
    such as rate-limited -- verified the signature and is yours. A node with another key, or
    none, says nothing, and nothing here claims anything about it.
    """
    counter = int(time.time()) if counter is None else counter
    topic = envelope.command_topic(region)
    payload = envelope.build(seed_hex, topic, counter, DISCOVERY_COMMAND,
                             ttl_secs=config.get("ttl_secs", envelope.DEFAULT_TTL_SECS))
    faked = _fake(topic, DISCOVERY_COMMAND, counter, ())
    if faked is not None:
        _wait(FAKE_WAIT_SECS, lambda: False, lambda: list(faked), progress)
        return [{"node": n[:envelope.NODE_HEX_LEN], "region": region.upper(), "code": r.code, "reply": r.reply,
                 "key": n if len(n) == envelope.KEY_HEX_LEN else None}
                for n, r in sorted(faked.items())]
    found = {}
    lock = threading.Lock()

    def on_message(_c, _u, msg):
        leaf = envelope.node_from_result_topic(msg.topic)
        if not leaf:
            return
        node = leaf[:envelope.NODE_HEX_LEN]
        result = envelope.parse_result(msg.payload, node)
        if result is None or result.counter != counter:
            return
        with lock:
            # the leaf says which firmware the node runs: a whole key is kept for its topics
            found[node] = {"node": node, "region": region.upper(), "code": result.code,
                           "reply": result.reply, "key": leaf if len(leaf) == envelope.KEY_HEX_LEN else None}

    client = (client_factory or _client)(config, seed_hex)
    client.on_message = on_message
    _connect(client, config, seed_hex=seed_hex)
    try:
        client.subscribe(f"{envelope.TOPIC_PREFIX}{region.upper()}/+/res", qos=1)
        info = client.publish(topic, payload, qos=1)
        info.wait_for_publish(timeout=10)
        _wait(wait_secs, lambda: False, lambda: list(found), progress)
    finally:
        client.loop_stop()
        client.disconnect()
    return sorted(found.values(), key=lambda d: d["node"])


def register(config, seed_hex, wait_secs=10.0, client_factory=None, now=None):
    """Publish the signed registration line and wait for the broker's acknowledgement:
    `(ok, detail)`. `ok` only on an explicit `ok`; silence is "no acknowledgement", which
    may mean a broker without the hook (a region's own) -- said, never assumed."""
    public = envelope.public_key_hex(seed_hex)
    payload = envelope.register_payload(seed_hex, now=now)
    fake = os.environ.get("MCCONTROL_FAKE_REGISTER")
    if fake is not None:
        return fake == "ok", fake
    got = threading.Event()
    answer = {}

    def on_message(_c, _u, msg):
        if msg.topic == envelope.register_ack_topic(public):
            answer["text"] = msg.payload.decode("utf-8", "replace").strip()
            got.set()

    client = (client_factory or _client)(config, seed_hex)
    client.on_message = on_message
    _connect(client, config, seed_hex=seed_hex)
    try:
        client.subscribe(envelope.register_ack_topic(public), qos=1)
        info = client.publish(envelope.REGISTER_TOPIC, payload, qos=1)
        info.wait_for_publish(timeout=10)
        got.wait(wait_secs)
    finally:
        client.loop_stop()
        client.disconnect()
    text = answer.get("text")
    if text is None:
        return False, "no acknowledgement: the broker may not register keys (a region's own without the hook)"
    return text == "ok", text


def test_connection(config, client_factory=None, timeout=10.0, seed_hex=None):
    """Can we reach the control broker, sign in, subscribe to a result topic and publish?

    Returns a list of `(step, ok, detail)` in the order tried, stopping at the first failure.
    The publish goes to a test topic under the region, not to any node's command topic, so
    nothing a repeater listens on is touched.
    """
    steps = []
    client = (client_factory or _client)(config, seed_hex)
    user, _pw = login(config, seed_hex)
    how = (f"as {user}" if config.get("username") else
           (f"token login as {user[:12]}... (your key is the identity)" if user else "with no credentials"))
    try:
        _connect(client, config, timeout=timeout, seed_hex=seed_hex)
    except BrokerError as exc:
        steps.append(("connect and sign in", False, str(exc)))
        return steps
    steps.append(("connect and sign in", True, f"{config['broker']}:{config['port']} {how}"))
    try:
        topic = f"{envelope.TOPIC_PREFIX}TEST/mccontrol-test/res"
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
