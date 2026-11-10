import unittest

from pyrogenesis_mcp.protocol import (
    MessageType, ProtocolError, build_authenticate, build_lan_authenticate, build_chat, build_handshake,
    decode_message, encode_message,
)


class ProtocolTests(unittest.TestCase):
    def test_known_handshake_fixture(self):
        # Independently laid out using NetMessages.h and CStr.cpp from v0.28.0.
        expected = bytes.fromhex(
            "02 0026 50630121 01010019 00000006 302e32382e30"
            "00000003 306164 00000006 302e32382e30"
        )
        self.assertEqual(build_handshake(mods=[("0ad", "0.28.0")]), expected)
        self.assertEqual(decode_message(expected).fields["mods"],
                         [{"name": "0ad", "version": "0.28.0"}])

    def test_utf16_authentication_fixture(self):
        # CStrW is UTF-16BE terminated by 0000, not UTF-8 or a length prefix.
        expected = bytes.fromhex("04 0011 865a 62df 0000 00000000 00000000")
        self.assertEqual(build_authenticate("\u865a\u62df"), expected)
        self.assertEqual(decode_message(expected).fields["name"], "\u865a\u62df")

    def test_signed_assignment_and_no_array_count(self):
        packet = bytes.fromhex("0b 000e 00000001 78 00410000 ff 02")
        item = decode_message(packet).fields["hosts"][0]
        self.assertEqual(item, {"guid": "x", "name": "A", "player_id": -1, "status": 2})

    def test_recipient_array_at_packet_end(self):
        msg = decode_message(build_chat("hello", ["a", "b"]))
        self.assertEqual(msg.fields["receivers"], [{"guid": "a"}, {"guid": "b"}])

    def test_lan_empty_password_is_not_hashed(self):
        # Hashing.cpp: if (string.empty()) return string; StartNetworkJoin does
        # not call SetGamePassword. Both the password and privilege stay empty.
        msg = decode_message(build_lan_authenticate("TestPlayer"))
        self.assertEqual(msg.fields["password"], "")
        self.assertEqual(msg.fields["controller_secret"], "")
        with self.assertRaises(ProtocolError):
            build_lan_authenticate("TestPlayer", "not-a-LAN-password")

    def test_reject_truncation_oversize_and_unknown_types(self):
        for packet in (b"", bytes.fromhex("ff0003"), bytes.fromhex("070008000000"),
                       bytes.fromhex("070007ffffffff"),
                       bytes.fromhex("04000c00410000000000000000")):
            with self.subTest(packet=packet.hex()), self.assertRaises(ProtocolError):
                decode_message(packet)
        with self.assertRaises(ProtocolError):
            build_chat("a" * 40000)

    def test_opaque_simulation_repr_does_not_expose_commands(self):
        msg = decode_message(encode_message(MessageType.SIMULATION_COMMAND,
                                             payload=b"hidden enemy target"))
        self.assertNotIn("enemy", repr(msg))
        self.assertEqual(msg.fields["payload"], b"hidden enemy target")


if __name__ == "__main__":
    unittest.main()
