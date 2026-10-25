"""Tests for the Mobius wire protocol (docs/01, docs/02)."""

import struct

import pytest

from custom_components.mobius import protocol as p


def test_crc16_ccitt_false_check_value():
    assert p.crc16(b"123456789") == 0x29B1


def test_crc16_table_entries():
    # The CRC of a single byte b with init 0 is entry b of the standard 0x1021 table.
    table = [0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7, 0x8108, 0x9129]
    for b, expected in enumerate(table):
        assert p.crc16(bytes([b]), 0) == expected


def test_frame_roundtrip():
    f = p.Frame(p.OpGroup.C2CI_REQUEST, p.Op.GET_ATTR_V2, 7, b"\x01\x02\x03")
    data = f.encode()
    assert data[0] == 0x02
    assert data[1:3] == b"\xde\x1c"
    assert data[3:5] == b"\x07\x00"
    assert data[7:9] == b"\x03\x00"
    assert p.Frame.decode(data) == f


def test_frame_bad_crc():
    data = bytearray(p.Frame(0xDE, 0x17, 1, b"\x00").encode())
    data[-1] ^= 0xFF
    with pytest.raises(p.ProtocolError):
        p.Frame.decode(bytes(data))


def test_set_group_bits():
    f = p.Frame(0xDE, 0x1D, 1)
    f.rsv0 = 0x01  # FSCI instance bits survive
    f.set_group(2)
    assert f.rsv0 == 0x09


def test_feed_mode_frame_matches_doc_example():
    payload = p.set_attrs_payload(
        [(p.Attr.CURRENT_SCENE, 0, [struct.pack("<HH", p.SceneId.FEED_MODE, 0)])], wide=True
    )
    assert payload.hex() == "910100000100040001000000"
    f = p.Frame(p.OpGroup.C2CI_REQUEST, p.Op.SET_ATTR_V2, 7, payload)
    f.set_group(2)
    assert f.encode()[:9].hex() == "02de1d070008000c00"


def test_get_payload_narrow_and_wide():
    assert p.get_attrs_payload([(1005, 0, 1)], wide=False).hex() == "ed030001"
    assert p.get_attrs_payload([(1005, 0, 1)], wide=True).hex() == "ed0300000100"
    # count 0xFFFF asks for every element
    assert p.get_attrs_payload([(400, 0, 0xFFFF)], wide=True).hex() == "90010000ffff"


def test_parse_get_response_wide():
    data = (
        b"\x00"
        + struct.pack("<HHHH", 401, 0, 1, 2) + b"\x01\x00"
        + struct.pack("<HHHH", 403, 0, 1, 2) + b"\x2c\x01"
        + struct.pack("<HHHH", 101, 4, 2, 4) + struct.pack("<ii", 12, -3)
    )
    status, values = p.parse_get_response(data, wide=True)
    assert status == 0
    assert p.u16(values[401].elements[0]) == p.SceneId.FEED_MODE
    assert p.u16(values[403].elements[0]) == 300
    assert values[101].index == 4
    assert [p.i32(e) for e in values[101].elements] == [12, -3]


def test_parse_physical_values_real_mp40():
    # Captured from a VorTech MP40w G3 QD (model 42) through the ESP32 proxy, 2026-10-17.
    frame = p.Frame.decode(bytes.fromhex(
        "02df170c0000003000006500010204290000002d0000006500040404050000007e05"
        "0000000000001f00000065000a0204000000001a090000917c"
    ))
    status, values = p.parse_get_response(frame.payload, wide=False)
    assert status == 0
    pv = {i: p.i32(b) for i, b in values[p.Attr.PHYSICAL_VALUES].items.items()}
    assert pv == {1: 41, 2: 45, 4: 5, 5: 1406, 6: 0, 7: 31, 10: 0, 11: 2330}


def test_parse_get_response_narrow():
    data = b"\x00" + struct.pack("<HBBB", 5, 0, 1, 4) + b"MP\x00\x00"
    status, values = p.parse_get_response(data, wide=False)
    assert status == 0
    assert p.text(values[5].elements[0]) == "MP"


def test_parse_get_response_error_status():
    assert p.parse_get_response(b"\x07", wide=True) == (7, {})


def test_parse_get_response_truncated():
    with pytest.raises(p.ProtocolError):
        p.parse_get_response(b"\x00" + struct.pack("<HHHH", 5, 0, 2, 4) + b"abcd", wide=True)


def test_parse_opcodes():
    assert p.parse_opcodes(b"\x00\x01\x00\x03\x00\x17\x1c\x1d") == b"\x17\x1c\x1d"


def test_coap_roundtrip():
    ip = bytes(range(16))
    inner = p.Frame(0xDE, 0x1C, 3, b"\x05\x00\x00\x00\x01\x00").encode()
    wrapped = p.coap_wrap(ip, 101, inner)
    assert wrapped[:16] == ip[::-1]
    assert struct.unpack_from("<iHHB", wrapped, 16) == (101, 1, len(inner), 0)
    assert wrapped[25:] == inner


def test_coap_unwrap():
    inner = p.Frame(0xDF, 0x1C, 3, b"\x00").encode()
    data = bytes(16) + struct.pack("<iHHB", 101, 205, len(inner), 2) + inner
    ind = p.coap_unwrap(data)
    assert (ind.token, ind.code, ind.ctype, ind.inner) == (101, 205, 2, inner)


def test_tank_group():
    assert p.tank_group(0b11) == 2  # group 2: bit 0 plus bit 1
    assert p.tank_group((1 << 4) | 1) == 5
    assert p.tank_group(1) == 0


def test_scene_record():
    rec = struct.pack("<HH", 1, 600) + b"Feed".ljust(16, b"\x00") + b"\xaa"
    s = p.SceneRecord.decode(rec)
    assert (s.id, s.timeout, s.name, s.data) == (1, 600, "Feed", b"\xaa")


def test_parse_advert_format2():
    raw = bytes([2, 0x02]) + struct.pack("<H", 162) + b"\x00" + struct.pack("<iH", 0, 0x1234)
    raw += b"ABCDEFGHIJKLMN" + b"\x00\x00\x00"  # 28 bytes total
    assert len(raw) == 28
    company = int.from_bytes(raw[:2], "little")
    adv = p.parse_advert({company: raw[2:]})
    assert adv is not None
    assert adv.model == "Radion XR15w G4 Pro"
    assert adv.serial == "ABCDEFGHIJKLMN"
    assert adv.pan_id == 0x1234
    assert adv.legacy is False


def test_parse_advert_unrelated():
    assert p.parse_advert({0x004C: b"\x02\x15" + bytes(21)}) is None


def test_chunk():
    assert p.chunk(bytes(45), 20) == [bytes(20), bytes(20), bytes(5)]


def mesh_entry(iid: bytes, model: int, serial: str) -> bytes:
    return iid + bytes.fromhex("b01f0000") + struct.pack("<H", model) + serial.encode()


def test_mesh_device_list():
    prefix = bytes.fromhex("fd00000000000001")
    value = p.AttrValue(p.Attr.NETWORKED_THREAD_DEVICES)
    value.items = {
        0: mesh_entry(bytes(range(8)), 42, "0000000TESTSN1"),
        1: mesh_entry(bytes(range(8, 16)), 149, "0000000TESTSN2"),
        2: bytes(28),  # unused slot
    }
    devices = p.parse_mesh_devices(value, prefix + bytes(8))
    assert devices == [
        p.MeshDevice(prefix + bytes(range(8)), 42, "0000000TESTSN1"),
        p.MeshDevice(prefix + bytes(range(8, 16)), 149, "0000000TESTSN2"),
    ]
