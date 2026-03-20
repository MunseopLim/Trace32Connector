#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TRACE32 NETASSIST protocol diagnostic tool.

Performs each protocol step manually and prints raw bytes
to diagnose connection/communication issues.

Usage: python diag_connect.py [host] [port]
"""
from __future__ import print_function

import select
import socket
import struct
import sys
import binascii


MAGIC = b'TRACE32\x00'


def hexdump(data, prefix="  "):
    """Print hex dump of data."""
    data = bytearray(data)
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hexpart = ' '.join("{0:02X}".format(b) for b in chunk)
        ascpart = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        print("{0}{1:04X}: {2:<48s} {3}".format(prefix, i, hexpart, ascpart))


def udp_recv(sock, timeout=5.0):
    """Receive UDP packet with timeout."""
    readable, _, _ = select.select([sock], [], [], timeout)
    if not readable:
        return None, None
    data, addr = sock.recvfrom(4096)
    return bytearray(data), addr


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else 'localhost'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 20000

    print("=" * 60)
    print("TRACE32 NETASSIST Protocol Diagnostic")
    print("Target: {0}:{1}".format(host, port))
    print("=" * 60)

    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', 0))
    local_port = sock.getsockname()[1]
    target = (host, port)
    print("\nLocal UDP port: {0}".format(local_port))

    # ==========================================
    # STEP 1: Connection Handshake
    # ==========================================
    print("\n--- STEP 1: Connection Handshake ---")
    packet = bytearray(1024)
    packet[0] = 0x03  # T32_API_CONNECT
    packet[1] = 0
    struct.pack_into('<H', packet, 2, 1)        # transmit seq = 1
    struct.pack_into('<H', packet, 4, port)      # T32 port
    struct.pack_into('<H', packet, 6, local_port) # our port
    packet[8:16] = bytearray(MAGIC)

    print("SEND Connection Request ({0} bytes):".format(len(packet)))
    hexdump(packet[:32])

    sock.sendto(bytes(packet), target)

    resp, addr = udp_recv(sock, timeout=3.0)
    if resp is None:
        print("[FAIL] No response (timeout 3s)")
        print("  - Is TRACE32 running?")
        print("  - Is RCL=NETASSIST in config.t32?")
        print("  - Is PORT={0} in config.t32?".format(port))
        sock.close()
        return 1

    print("RECV ({0} bytes from {1}):".format(len(resp), addr))
    hexdump(resp[:32])
    print("  Type: 0x{0:02X} (expect 0x13 for OK)".format(resp[0]))

    if resp[0] != 0x13:
        print("[FAIL] Not a connection OK response")
        sock.close()
        return 1

    recv_seq = struct.unpack_from('<H', resp, 2)[0]
    packet_size = len(resp)
    print("[OK] Connected. Server seq={0}, PacketSize={1}".format(
        recv_seq, packet_size))

    # ==========================================
    # STEP 2: Sync
    # ==========================================
    print("\n--- STEP 2: Sync ---")
    tx_seq = 1
    sync_pkt = bytearray(16)
    sync_pkt[0] = 0x02  # SYNCREQUEST
    sync_pkt[1] = 0
    struct.pack_into('<H', sync_pkt, 2, tx_seq)
    sync_pkt[8:16] = bytearray(MAGIC)

    print("SEND SYNCREQUEST (16 bytes):")
    hexdump(sync_pkt)
    sock.sendto(bytes(sync_pkt), target)

    resp, _ = udp_recv(sock, timeout=3.0)
    if resp is None:
        print("[FAIL] No sync response")
        sock.close()
        return 1

    print("RECV SYNCACKN ({0} bytes):".format(len(resp)))
    hexdump(resp)
    print("  Type: 0x{0:02X} (expect 0x12)".format(resp[0]))

    if resp[0] != 0x12:
        print("[FAIL] Not SYNCACKN")
        sock.close()
        return 1

    recv_seq = struct.unpack_from('<H', resp, 2)[0]
    print("[OK] Sync ACK. Server recv_seq={0}".format(recv_seq))

    # Send SYNCBACK
    sync_pkt[0] = 0x22  # SYNCBACK
    sock.sendto(bytes(sync_pkt), target)
    print("SEND SYNCBACK [OK]")

    # ==========================================
    # STEP 3: ATTACH
    # ==========================================
    print("\n--- STEP 3: ATTACH ---")
    # Build ATTACH message: [LEN=2][CMD=0x71][DEV=0x01][MSGID=0]
    app_msg = bytearray([2, 0x71, 0x01, 0x00])
    # Prepend 5-byte internal header
    data = bytearray(5) + app_msg  # 9 bytes

    # Wrap in UDP data packet
    udp_pkt = bytearray(4 + len(data))
    udp_pkt[0] = 0x11  # T32_API_TRANSMIT
    udp_pkt[1] = 0     # no continuation
    struct.pack_into('<H', udp_pkt, 2, tx_seq)
    udp_pkt[4:] = data
    tx_seq += 1

    print("SEND ATTACH ({0} bytes):".format(len(udp_pkt)))
    hexdump(udp_pkt)

    sock.sendto(bytes(udp_pkt), target)

    resp, _ = udp_recv(sock, timeout=3.0)
    if resp is None:
        print("[FAIL] No ATTACH response")
        sock.close()
        return 1

    print("RECV ({0} bytes):".format(len(resp)))
    hexdump(resp)

    if len(resp) > 4:
        print("\n  UDP header: type=0x{0:02X} cont={1} seq={2}".format(
            resp[0], resp[1], struct.unpack_from('<H', resp, 2)[0]))
        payload = resp[4:]
        print("  Payload ({0} bytes):".format(len(payload)))
        for i, b in enumerate(payload):
            print("    [{0}] = 0x{1:02X}".format(i, b))

        # Try different status positions
        if len(payload) >= 4:
            print("\n  Possible status interpretations:")
            print("    payload[1] = 0x{0:02X} (current: status)".format(payload[1]))
            print("    payload[2] = 0x{0:02X}".format(payload[2]))
            print("    payload[3] = 0x{0:02X}".format(payload[3]))
            if len(payload) >= 5:
                print("    payload[4] = 0x{0:02X}".format(payload[4]))

    # ==========================================
    # STEP 4: PING
    # ==========================================
    print("\n--- STEP 4: PING ---")
    # Build PING: [LEN=2][CMD=0x73][SUBCMD=0x00][MSGID=1]
    app_msg = bytearray([2, 0x73, 0x00, 0x01])
    data = bytearray(5) + app_msg

    udp_pkt = bytearray(4 + len(data))
    udp_pkt[0] = 0x11
    udp_pkt[1] = 0
    struct.pack_into('<H', udp_pkt, 2, tx_seq)
    udp_pkt[4:] = data
    tx_seq += 1

    print("SEND PING ({0} bytes):".format(len(udp_pkt)))
    hexdump(udp_pkt)

    sock.sendto(bytes(udp_pkt), target)

    resp, _ = udp_recv(sock, timeout=3.0)
    if resp is None:
        print("[FAIL] No PING response")
        sock.close()
        return 1

    print("RECV ({0} bytes):".format(len(resp)))
    hexdump(resp)

    if len(resp) > 4:
        payload = resp[4:]
        print("\n  Payload bytes:")
        for i, b in enumerate(payload):
            print("    [{0}] = 0x{1:02X}".format(i, b))

    # ==========================================
    # STEP 5: CMD "PRINT VERSION.SOFTWARE()"
    # ==========================================
    print("\n--- STEP 5: CMD ---")
    cmd_str = b'PRINT VERSION.SOFTWARE()\x00'
    cmd_len = len(cmd_str) - 1  # strlen without null
    data_len = 2 + len(cmd_str)  # subcmd + msgid + payload

    app_msg = bytearray()
    app_msg.append(data_len & 0xFF)  # LEN
    app_msg.append(0x72)             # CMD_EXECUTE_PRACTICE
    app_msg.append(0x02)             # SUBCMD
    app_msg.append(0x02)             # MSGID
    app_msg.extend(cmd_str)

    # Pad to even
    if len(app_msg) % 2 != 0:
        app_msg.append(0)

    data = bytearray(5) + app_msg

    udp_pkt = bytearray(4 + len(data))
    udp_pkt[0] = 0x11
    udp_pkt[1] = 0
    struct.pack_into('<H', udp_pkt, 2, tx_seq)
    udp_pkt[4:] = data
    tx_seq += 1

    print("SEND CMD ({0} bytes):".format(len(udp_pkt)))
    hexdump(udp_pkt)

    sock.sendto(bytes(udp_pkt), target)

    resp, _ = udp_recv(sock, timeout=3.0)
    if resp is None:
        print("[FAIL] No CMD response")
        sock.close()
        return 1

    print("RECV ({0} bytes):".format(len(resp)))
    hexdump(resp)

    if len(resp) > 4:
        payload = resp[4:]
        print("\n  Payload bytes:")
        for i, b in enumerate(payload):
            print("    [{0}] = 0x{1:02X}".format(i, b))

    print("\n" + "=" * 60)
    print("Diagnostic complete")
    print("=" * 60)

    sock.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
