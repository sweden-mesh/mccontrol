"""The API under test: a live server on a free port over a store in a temp dir, a scripted
broker, and a validator for the subset of JSON Schema the OpenAPI document uses."""
import json
import pathlib
import socket

import pytest

from mccontrol import store as _store, transport
from mccontrol.api import serve, stop

SPEC = json.loads((pathlib.Path(__file__).parents[1] / "docs" / "api" / "openapi.json").read_text(encoding="utf-8"))
BEACON_FAKE = str(pathlib.Path(__file__).parent / "data" / "beacon-fake.json")
NODES = [("Gisebo", "4AC61A7F", "JKG"), ("", "65C1D12C", "JKG"), ("Far", "FD700369", "GOT")]
SCRIPT = {"4AC61A7F": [0, "> * F"], "65C1D12C": [14, ""],
          "4AC61A7F|ver": [0, "> esp32s3-v1.17.1-ar-11-observer-90e86c88"], "4AC61A7F|get name": [0, "> SE-JKG-Gisebo"],
          "65C1D12C|get name": [0, "> SE-JKG-Vaster-65c1"], "65C1D12C|ver": [0, "> esp32s3-v1.17.1-ar-10-observer-42f48ce9"],
          "AB6B9285": [0, "> on"], "AB6B9285|get name": [0, "> SE-JKG-UGORptr"]}


@pytest.fixture
def api(tmp_path, monkeypatch):
    """`(server, base_url, store)` with a key and three devices; the broker is SCRIPT."""
    monkeypatch.setattr(transport, "FAKE_WAIT_SECS", 0.05)
    monkeypatch.delenv("MCCONTROL_FAKE_RESULTS", raising=False)
    store = _store.Store(tmp_path / "home")
    store.create_key()
    port = closed_port()                # nothing in these tests may reach a real broker or Beacon
    store.save_config(broker="127.0.0.1", port=port, beacon_url=f"http://127.0.0.1:{port}")
    for name, node, region in NODES:
        store.add_device(name, node, region)
    server, url = serve(store, transport=dict(SCRIPT))
    yield server, url, store
    stop(server)


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def resolve(schema):
    while "$ref" in schema:
        node = SPEC
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        schema = node
    return schema


TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}


def validate(schema, value, where="$"):
    """Errors (a list of strings) for `value` against `schema`: type, nullable, required,
    properties, additionalProperties, items, enum, allOf and $ref -- what the document uses."""
    schema = resolve(schema)
    if value is None:
        return [] if schema.get("nullable") else [f"{where}: null where {schema.get('type')} is required"]
    errors = []
    for part in schema.get("allOf", ()):
        errors += validate(part, value, where)
    kind = schema.get("type")
    if kind:
        ok = isinstance(value, TYPES[kind]) and not (kind in ("integer", "number") and isinstance(value, bool))
        if not ok:
            return [f"{where}: {type(value).__name__} where {kind} is required"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} not in {schema['enum']}")
    if kind == "object":
        for key in schema.get("required", ()):
            if key not in value:
                errors.append(f"{where}: {key} is required")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        for key, item in value.items():
            if key in props:
                errors += validate(props[key], item, f"{where}.{key}")
            elif isinstance(extra, dict):
                errors += validate(extra, item, f"{where}.{key}")
    if kind == "array":
        for i, item in enumerate(value):
            errors += validate(schema["items"], item, f"{where}[{i}]")
    return errors
