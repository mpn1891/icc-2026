#!/usr/bin/env python3
"""Sparkplug B v3.0.0 payload encoding and edge-node session handling.

Why this is hand-written rather than a dependency: the two libraries on PyPI that speak
Sparkplug (`pysparkplug`, `mqtt-spb-wrapper`) both pin paho-mqtt to the 1.x line, and every
other service in this repo is on paho 2.x with the VERSION2 callback API. Pulling in a 1.x
pin here would make the Sparkplug valve structurally different from the plain-MQTT valve for
a reason that has nothing to do with Sparkplug -- which is exactly the confound the pattern
1 / pattern 2 comparison is trying to avoid.

So this file encodes `org.eclipse.tahu.protobuf.Payload` directly. That is a small, closed
schema (a Payload is a timestamp, a list of metrics and a sequence number) and the encoder is
about eighty lines of protobuf wire format. It is verified against Eclipse Tahu's own
generated protobuf code by `python selftest.py`, which decodes everything this file produces
and asserts it round-trips -- run that before trusting a change here.

Every MQTT-level constant below is quoted from the specification with its TCK identifier,
because the entire point of pattern 2 is that these are not the device's decisions:

  * Will Message (NDEATH):  QoS 1, retain false
        tck-id-message-flow-edge-node-birth-publish-will-message-qos
        tck-id-message-flow-edge-node-birth-publish-will-message-will-retained
  * NBIRTH/NDATA/NCMD/DBIRTH/DDATA/DDEATH/DCMD:  QoS 0, retain false
        tck-id-topics-nbirth-mqtt, tck-id-topics-ndata-mqtt, tck-id-topics-dbirth-mqtt,
        tck-id-topics-ddata-mqtt, tck-id-topics-ddeath-mqtt, ...
  * NBIRTH seq MUST be 0            tck-id-topics-nbirth-seq-num
  * NDEATH carries only bdSeq       tck-id-topics-ndeath-payload
  * NDEATH MUST NOT carry a seq     tck-id-topics-ndeath-seq
  * bdSeq starts at 0, +1 per CONNECT, and the NBIRTH's must match the will's
        tck-id-payloads-ndeath-will-message-bdseq / tck-id-topics-nbirth-bdseq
"""

from __future__ import annotations

import struct
import threading
import time

NAMESPACE = "spBv1.0"

# MQTT settings the spec fixes. Named, not inlined, so the config page can render the same
# constants it is refusing to let anyone edit.
DATA_QOS = 0
DATA_RETAIN = False
WILL_QOS = 1
WILL_RETAIN = False

SPEC_VERSION = "Sparkplug B v3.0.0"


class DataType:
    """org.eclipse.tahu.protobuf.DataType. Only what this device uses is listed."""

    Int8 = 1
    Int16 = 2
    Int32 = 3
    Int64 = 4
    UInt8 = 5
    UInt16 = 6
    UInt32 = 7
    UInt64 = 8
    Float = 9
    Double = 10
    Boolean = 11
    String = 12
    DateTime = 13
    Text = 14
    UUID = 15


DATATYPE_NAMES = {
    value: name for name, value in vars(DataType).items() if isinstance(value, int)
}


# -- protobuf wire format ----------------------------------------------------------------
#
# Four of the six wire types are enough for this schema: varint (0), fixed64 (1),
# length-delimited (2) and fixed32 (5).


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        chunk = value & 0x7F
        value >>= 7
        if value:
            out.append(chunk | 0x80)
        else:
            out.append(chunk)
            return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _uint(field: int, value: int) -> bytes:
    return _key(field, 0) + _varint(value)


def _bool(field: int, value: bool) -> bytes:
    return _key(field, 0) + _varint(1 if value else 0)


def _bytes(field: int, value: bytes) -> bytes:
    return _key(field, 2) + _varint(len(value)) + value


def _string(field: int, value: str) -> bytes:
    return _bytes(field, value.encode("utf-8"))


def _float(field: int, value: float) -> bytes:
    return _key(field, 5) + struct.pack("<f", value)


def _double(field: int, value: float) -> bytes:
    return _key(field, 1) + struct.pack("<d", value)


# -- payload -----------------------------------------------------------------------------


def _encode_value(field_base_owner: str, datatype: int, value) -> bytes:
    """The value half of a Metric or a PropertyValue.

    Metric and PropertyValue use different field numbers for the same set of value types,
    which is the only reason this takes an owner argument.
    """
    if field_base_owner == "metric":
        int_f, long_f, float_f, double_f, bool_f, string_f = 10, 11, 12, 13, 14, 15
    else:  # PropertyValue
        int_f, long_f, float_f, double_f, bool_f, string_f = 3, 4, 5, 6, 7, 8

    if datatype in (DataType.Int8, DataType.Int16, DataType.Int32,
                    DataType.UInt8, DataType.UInt16, DataType.UInt32):
        # Signed values ride in a uint32 field as two's complement, per the spec's
        # "Signed Integers" note -- the receiver casts back using the declared datatype.
        return _uint(int_f, int(value) & 0xFFFFFFFF)
    if datatype in (DataType.Int64, DataType.UInt64, DataType.DateTime):
        return _uint(long_f, int(value) & 0xFFFFFFFFFFFFFFFF)
    if datatype == DataType.Float:
        return _float(float_f, float(value))
    if datatype == DataType.Double:
        return _double(double_f, float(value))
    if datatype == DataType.Boolean:
        return _bool(bool_f, bool(value))
    if datatype in (DataType.String, DataType.Text, DataType.UUID):
        return _string(string_f, str(value))
    raise ValueError("unsupported datatype %r" % datatype)


class Metric:
    """One named, typed, timestamped value.

    The `datatype` is not decoration: it is transmitted, so the consumer is told what this
    is rather than inferring it from how the number happened to render. `properties` carries
    engineering units the same way -- both of which a JSON payload has to agree on out of
    band, or guess.
    """

    __slots__ = ("name", "datatype", "value", "alias", "timestamp_ms", "is_null", "properties")

    def __init__(self, name, datatype, value, alias=None, timestamp_ms=None,
                 properties=None):
        self.name = name
        self.datatype = datatype
        self.value = value
        self.alias = alias
        self.timestamp_ms = timestamp_ms
        self.is_null = value is None
        self.properties = properties or {}

    def encode(self, include_name: bool = True, include_properties: bool = True) -> bytes:
        out = bytearray()
        if include_name and self.name is not None:
            out += _string(1, self.name)
        if self.alias is not None:
            out += _uint(2, self.alias)
        if self.timestamp_ms is not None:
            out += _uint(3, self.timestamp_ms)
        out += _uint(4, self.datatype)
        if self.is_null:
            # tck-id-payloads-metric-datatype-value-null: a null metric sets is_null and
            # carries no value at all.
            out += _bool(7, True)
        if include_properties and self.properties:
            out += _bytes(9, _encode_property_set(self.properties))
        if not self.is_null:
            out += _encode_value("metric", self.datatype, self.value)
        return bytes(out)


def _encode_property_set(properties: dict) -> bytes:
    """PropertySet: parallel `keys` and `values` arrays, matched by position."""
    keys = bytearray()
    values = bytearray()
    for key, (datatype, value) in properties.items():
        keys += _string(1, key)
        body = _uint(1, datatype) + _encode_value("property", datatype, value)
        values += _bytes(2, body)
    return bytes(keys + values)


def encode_payload(metrics, timestamp_ms=None, seq=None, include_names=True,
                   include_properties=True) -> bytes:
    """Serialize one Sparkplug B payload.

    `seq` is omitted for NDEATH only (tck-id-topics-ndeath-seq); every other message type
    carries it, and a consumer that sees a gap knows it missed something -- which is the
    thing plain MQTT cannot tell you at all.
    """
    out = bytearray()
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    out += _uint(1, timestamp_ms)
    for metric in metrics:
        out += _bytes(2, metric.encode(include_name=include_names,
                                       include_properties=include_properties))
    if seq is not None:
        out += _uint(3, seq)
    return bytes(out)


# -- topics ------------------------------------------------------------------------------


def topic(group_id: str, message_type: str, edge_node_id: str, device_id: str = None) -> str:
    """`spBv1.0/{group_id}/{message_type}/{edge_node_id}[/{device_id}]`.

    Four or five tokens, in that order, always. There is no configuration knob here and
    that is the entire point of pattern 2 -- see services/sim-valve-spb/page.html.
    """
    parts = [NAMESPACE, group_id, message_type, edge_node_id]
    if device_id:
        parts.append(device_id)
    return "/".join(parts)


class SequenceCounter:
    """The Sparkplug `seq`: 0-255, rolling, and reset to 0 by every NBIRTH.

    A hand-rolled protocol has to invent this, agree it with every consumer, and then
    remember to check it. Here it is simply part of being on the wire.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = 0

    def reset(self) -> int:
        with self._lock:
            self._value = 0
            return 0

    def next(self) -> int:
        with self._lock:
            self._value = (self._value + 1) % 256
            return self._value


# -- decoding ----------------------------------------------------------------------------
#
# Only NCMD needs decoding, and only for one metric: `Node Control/Rebirth`. Ignition's MQTT
# Engine issues that command by itself whenever it sees DATA for a device it has no birth
# certificate for, so an edge node that ignores it can get permanently stuck as "unknown" in
# the tag tree. Answering it is not optional politeness -- it is what makes the integration
# self-heal, and there is no equivalent anywhere in plain MQTT.


def _read_varint(data: bytes, index: int):
    result = 0
    shift = 0
    while True:
        byte = data[index]
        index += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, index
        shift += 7


def _fields(data: bytes):
    """Yield (field_number, wire_type, payload) for one protobuf message."""
    index = 0
    end = len(data)
    while index < end:
        key, index = _read_varint(data, index)
        field, wire = key >> 3, key & 0x07
        if wire == 0:
            value, index = _read_varint(data, index)
            yield field, wire, value
        elif wire == 1:
            yield field, wire, data[index:index + 8]
            index += 8
        elif wire == 2:
            length, index = _read_varint(data, index)
            yield field, wire, data[index:index + length]
            index += length
        elif wire == 5:
            yield field, wire, data[index:index + 4]
            index += 4
        else:
            raise ValueError("unsupported wire type %s" % wire)


def _decode_metric(data: bytes) -> dict:
    metric = {"name": None, "alias": None, "datatype": None, "value": None, "is_null": False}
    for field, wire, payload in _fields(data):
        if field == 1 and wire == 2:
            metric["name"] = payload.decode("utf-8", "replace")
        elif field == 2:
            metric["alias"] = payload
        elif field == 4:
            metric["datatype"] = payload
        elif field == 7:
            metric["is_null"] = bool(payload)
        elif field == 10:
            metric["value"] = payload
        elif field == 11:
            metric["value"] = payload
        elif field == 12 and wire == 5:
            metric["value"] = struct.unpack("<f", payload)[0]
        elif field == 13 and wire == 1:
            metric["value"] = struct.unpack("<d", payload)[0]
        elif field == 14:
            metric["value"] = bool(payload)
        elif field == 15 and wire == 2:
            metric["value"] = payload.decode("utf-8", "replace")
    return metric


def decode_payload(data: bytes) -> dict:
    result = {"timestamp": None, "seq": None, "metrics": []}
    for field, wire, payload in _fields(data):
        if field == 1:
            result["timestamp"] = payload
        elif field == 2 and wire == 2:
            result["metrics"].append(_decode_metric(payload))
        elif field == 3:
            result["seq"] = payload
    return result
