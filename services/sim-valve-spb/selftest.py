#!/usr/bin/env python3
"""Proof that sparkplug.py encodes what the Sparkplug B specification says it does.

`python selftest.py` -- no Docker, no broker, no network, nothing installed.

sparkplug.py writes protobuf by hand (see its docstring for why), so it needs a check that
does not simply ask the encoder to confirm itself. Two independent ones are here:

  1. **Golden byte vectors.** The exact bytes below were produced by this encoder and then
     parsed by Eclipse Tahu's own generated protobuf code, field by field, on 2026-08-17.
     If a change to the encoder alters them, it has changed the wire format, and the
     assertions in `check_against_tahu()` are what must be re-run to justify it.

  2. **A cross-check against Tahu itself**, skipped unless the generated module is
     importable. To run it:

         pip install grpcio-tools
         curl -O https://raw.githubusercontent.com/eclipse/tahu/master/sparkplug_b/sparkplug_b.proto
         python -m grpc_tools.protoc --proto_path=. --python_out=. sparkplug_b.proto
         python selftest.py

     `sparkplug_b_pb2.py` is deliberately NOT committed: it is generated code with a
     protobuf-runtime version guard, and the service does not need protobuf at runtime.

Run this before trusting any change to sparkplug.py.
"""

from __future__ import annotations

import sys

import sparkplug
from sparkplug import DataType as DT
from sparkplug import Metric, encode_payload

TS = 1755460000000  # a fixed instant, so the golden vectors are reproducible

# Verified against org.eclipse.tahu.protobuf.Payload -- see the module docstring.
GOLDEN_NDEATH = bytes.fromhex("0880b2c7cc8b33120b0a05626453657120045803")
GOLDEN_DDATA = bytes.fromhex(
    "0880b2c7cc8b33121310011880b2c7cc8b33200c7a066c6f636b65641807"
)


def sample_metrics():
    """One metric per datatype the encoder must handle.

    Not a mirror of the registry in app.py. `Synthetic/Double` and the two `Neg/*` entries
    exercise encodings the device never publishes -- the registry has no Double, and no
    counter goes negative -- and are named so nobody mistakes them for real metrics.
    """
    return [
        Metric("Valve/State", DT.String, "open", alias=1, timestamp_ms=TS),
        Metric("Valve/IsOpen", DT.Boolean, True, alias=2, timestamp_ms=TS),
        Metric("Valve/PositionPct", DT.Float, 100.0, alias=3, timestamp_ms=TS,
               properties={"engUnit": (DT.String, "%")}),
        Metric("Sample/CycleCount", DT.Int64, 42, alias=4, timestamp_ms=TS),
        Metric("Badge/LastScanTime", DT.DateTime, TS, alias=5, timestamp_ms=TS),
        Metric("Badge/LastDenyReason", DT.String, None, alias=6, timestamp_ms=TS),
        Metric("Synthetic/Double", DT.Double, 36.75, alias=7, timestamp_ms=TS),
        Metric("Neg/Int32", DT.Int32, -7, alias=8, timestamp_ms=TS),
        Metric("Neg/Int64", DT.Int64, -9, alias=9, timestamp_ms=TS),
    ]


def ndeath_bytes() -> bytes:
    # tck-id-topics-ndeath-payload: bdSeq and nothing else.
    # tck-id-topics-ndeath-seq: no sequence number.
    return encode_payload([Metric("bdSeq", DT.Int64, 3)], timestamp_ms=TS, seq=None)


def ddata_bytes() -> bytes:
    # Alias-only DDATA -- what actually goes on the wire once the birth certificate has
    # taught the consumer what alias 1 means.
    return encode_payload(
        [Metric("Valve/State", DT.String, "locked", alias=1, timestamp_ms=TS)],
        timestamp_ms=TS, seq=7, include_names=False, include_properties=False)


def check_golden() -> None:
    assert ndeath_bytes() == GOLDEN_NDEATH, "NDEATH encoding changed"
    assert ddata_bytes() == GOLDEN_DDATA, "alias-only DDATA encoding changed"
    print("golden vectors            OK")


def check_topics() -> None:
    assert sparkplug.topic("G", "NBIRTH", "E") == "spBv1.0/G/NBIRTH/E"
    assert sparkplug.topic("G", "DDATA", "E", "D") == "spBv1.0/G/DDATA/E/D"
    # The spec fixes these; the config page renders them as disabled controls.
    assert (sparkplug.DATA_QOS, sparkplug.DATA_RETAIN) == (0, False)
    assert (sparkplug.WILL_QOS, sparkplug.WILL_RETAIN) == (1, False)
    print("topics and MQTT constants OK")


def check_sequence() -> None:
    seq = sparkplug.SequenceCounter()
    assert seq.reset() == 0                      # tck-id-topics-nbirth-seq-num
    assert [seq.next() for _ in range(3)] == [1, 2, 3]
    for _ in range(252):
        seq.next()
    assert seq.next() == 0, "seq must roll over at 256"
    print("sequence counter          OK")


def check_roundtrip() -> None:
    """The decoder is only used for NCMD, but it must agree with the encoder on names."""
    decoded = sparkplug.decode_payload(encode_payload(sample_metrics(),
                                                      timestamp_ms=TS, seq=0))
    assert decoded["timestamp"] == TS and decoded["seq"] == 0
    metrics = {metric["name"]: metric for metric in decoded["metrics"]}
    assert metrics["Valve/State"]["value"] == "open"
    assert metrics["Valve/IsOpen"]["value"] is True
    assert metrics["Sample/CycleCount"]["value"] == 42
    assert metrics["Badge/LastDenyReason"]["is_null"] is True
    assert abs(metrics["Synthetic/Double"]["value"] - 36.75) < 1e-12
    assert metrics["Neg/Int32"]["value"] == (-7 & 0xFFFFFFFF)

    rebirth = encode_payload([Metric("Node Control/Rebirth", DT.Boolean, True)])
    assert sparkplug.decode_payload(rebirth)["metrics"][0]["value"] is True
    print("decode round trip         OK")


def check_against_tahu() -> bool:
    """Authoritative check. Returns False if the generated module is not present."""
    try:
        import sparkplug_b_pb2 as tahu
    except ImportError:
        return False

    raw = encode_payload(sample_metrics(), timestamp_ms=TS, seq=0)
    payload = tahu.Payload()
    consumed = payload.ParseFromString(raw)
    assert consumed == len(raw), "trailing bytes -- the payload is not valid protobuf"
    assert payload.timestamp == TS and payload.seq == 0

    m = {metric.name: metric for metric in payload.metrics}
    assert m["Valve/State"].string_value == "open"
    assert m["Valve/State"].datatype == DT.String
    assert m["Valve/State"].alias == 1
    assert m["Valve/State"].timestamp == TS
    assert m["Valve/IsOpen"].boolean_value is True
    assert abs(m["Valve/PositionPct"].float_value - 100.0) < 1e-6
    assert list(m["Valve/PositionPct"].properties.keys) == ["engUnit"]
    assert m["Valve/PositionPct"].properties.values[0].string_value == "%"
    assert m["Sample/CycleCount"].long_value == 42
    assert m["Badge/LastScanTime"].datatype == DT.DateTime
    assert m["Badge/LastScanTime"].long_value == TS
    # A null metric sets is_null and carries no value at all, rather than an empty string.
    assert m["Badge/LastDenyReason"].is_null is True
    assert m["Badge/LastDenyReason"].HasField("string_value") is False
    assert abs(m["Synthetic/Double"].double_value - 36.75) < 1e-12
    assert m["Neg/Int32"].int_value == (-7 & 0xFFFFFFFF)
    assert m["Neg/Int64"].long_value == (-9 & 0xFFFFFFFFFFFFFFFF)

    death = tahu.Payload()
    death.ParseFromString(ndeath_bytes())
    assert not death.HasField("seq"), "NDEATH must not carry a sequence number"
    assert len(death.metrics) == 1 and death.metrics[0].name == "bdSeq"

    data = tahu.Payload()
    data.ParseFromString(ddata_bytes())
    assert data.metrics[0].name == "" and data.metrics[0].alias == 1
    assert data.metrics[0].string_value == "locked" and data.seq == 7
    return True


def main() -> int:
    check_golden()
    check_topics()
    check_sequence()
    check_roundtrip()
    if check_against_tahu():
        print("Eclipse Tahu cross-check  OK")
    else:
        print("Eclipse Tahu cross-check  SKIPPED (sparkplug_b_pb2 not importable -- see "
              "the module docstring)")
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
