"""
CRC16-XMODEM checksum implementation for Fitbit Aria protocol.

The Aria scale uses CRC16-XMODEM (polynomial 0x1021) for data validation.
"""


def crc16_xmodem(data: bytes, initial: int = 0) -> int:
    """
    Calculate CRC16-XMODEM checksum.

    Args:
        data: Bytes to calculate checksum for
        initial: Initial CRC value (default 0)

    Returns:
        16-bit CRC checksum
    """
    crc = initial
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc


def verify_crc(data: bytes) -> bool:
    """
    Verify that data has a valid CRC16-XMODEM checksum.

    The checksum is expected to be the last 2 bytes of the data (big-endian).

    Args:
        data: Data including checksum

    Returns:
        True if checksum is valid
    """
    if len(data) < 3:
        return False

    payload = data[:-2]
    expected_crc = int.from_bytes(data[-2:], 'big')
    calculated_crc = crc16_xmodem(payload)

    return calculated_crc == expected_crc


def append_crc(data: bytes) -> bytes:
    """
    Append CRC16-XMODEM checksum to data.

    Args:
        data: Data to append checksum to

    Returns:
        Data with 2-byte checksum appended (big-endian)
    """
    crc = crc16_xmodem(data)
    return data + crc.to_bytes(2, 'big')
