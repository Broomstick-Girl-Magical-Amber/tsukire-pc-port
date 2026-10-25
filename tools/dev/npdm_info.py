#!/usr/bin/env python3
"""npdm_info.py - Reads from main.npdm what games/<game>/game.cpp needs.

Usage: python npdm_info.py exefs/main.npdm
Prints: PROGRAM_ID=<hex>  SYSTEM_RESOURCE_SIZE=<hex>  NAME=<name>
"""
import struct
import sys


def main():
    data = open(sys.argv[1], "rb").read()
    if data[:4] != b"META":
        sys.exit("not an NPDM (META is missing)")
    system_resource = struct.unpack_from("<I", data, 0x14)[0]
    name = data[0x20:0x30].split(b"\0")[0].decode("ascii", "replace")
    aci0 = struct.unpack_from("<I", data, 0x70)[0]
    if data[aci0:aci0 + 4] != b"ACI0":
        sys.exit("ACI0 not found")
    program_id = struct.unpack_from("<Q", data, aci0 + 0x10)[0]
    print(f"PROGRAM_ID={program_id:016X}")
    print(f"SYSTEM_RESOURCE_SIZE={system_resource:X}")
    print(f"NAME={name}")


if __name__ == "__main__":
    main()
