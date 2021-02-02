import socket
import binascii
from hashlib import sha3_256
from ipaddress import ip_address
from typing import Optional, Tuple

from falcon import Request


_INVALID_STRING = "invalid_ip"
_IPv6_STRING = "ipv6"


def microcelling(ip: str, modulo=5) -> Optional[str]:
    """
    Turns an IPv4 or IPv6 address into an microcelled version,
    or `None` when invalid input is inputted.

    The microcelling works as follows:

    * Turn the `ip` into its byte representation
    * Turn the bytes into an integer (most significant byte last)
    * Round (down) to the next integer multiple of `modulo`.
    * Turn into bytes (prepend zero-bytes to receive a valid byte
      representation of an IPv4/6 address)
    * Turn the byte representation into an IP address
    """
    try:
        if ip.count(".") == 3:
            address_family = socket.AF_INET
            byte_len = 4
        elif ip.count(":") >= 2:
            address_family = socket.AF_INET6
            byte_len = 16
        else:
            return None
    except AttributeError:
        # Not a valid input that has "count" attribute
        return None

    try:
        # Turn IP into integer
        int_ip = int.from_bytes(
            bytes=socket.inet_pton(address_family, ip),
            byteorder="little"
        )

        # Perform microcelling to the integer
        microcelled_int_ip = int_ip - (int_ip % modulo)

        # Turn the integer into bytes (most significant byte last)
        microcelled_bytes = microcelled_int_ip.to_bytes(
            length=(microcelled_int_ip.bit_length() + 7) // 8,
            byteorder="little"
        )

        # Convert to correct byte length
        microcelled_byte_ip = b"\x00" * (byte_len - len(microcelled_bytes)) + microcelled_bytes

        # Turn the bytes back to an ip address
        microcelled_ip = socket.inet_ntop(address_family, microcelled_byte_ip)

        return microcelled_ip
    except (OSError, ValueError, TypeError):
        # Not a valid IP Address, maybe not even a string
        return None


def ip_collision_obfuscation(ip: str) -> str:
    """
    Turn an `ip` address into an obfuscated address by applying the two
    steps:

    * Apply `microcelling`.
    * Apply crc32 checksum.

    If no valid `ip` was entered, `_INVALID_STRING` will be returned.
    """
    microcelled_ip = microcelling(ip)

    if microcelled_ip:
        ip_hash = "%x" % binascii.crc32(bytes(microcelled_ip, "ascii"))
    else:
        ip_hash = _INVALID_STRING

    return ip_hash


def ip_token_obfuscation(ip: str, token: bytes) -> str:
    return sha3_256(token + ip.encode("utf-8")).hexdigest()


def geo_location_ip(ip_str: str) -> str:
    """
    Overwrite last octet of IPv4 address with 0

    :param ip_str: string representation of IP
    :return: modified IPv4 string, `invalid_ip`, or `IPv6`
    """
    try:
        ip_obj = ip_address(ip_str)
    except ValueError:
        return _INVALID_STRING

    if ip_obj.version == 6:
        return _IPv6_STRING
    else:
        return ip_str.rsplit(".", 1)[0] + ".0"


def obfuscated_ip_from_request(req: Request, token: bytes) -> Tuple[str, str, str]:
    """
    Fetch the ip address from Falcon's `Request` object and apply
    different hash methods to it. Returns the obfuscated ip (with
    intentional collisions), the pepper-hashed ip, and, if it is a IPv4
    address, the IP with the last octet set to zero as a triple.

    This method relies on Falcon'n `access_route` method [1], which
    first entry should contain the client's IP.
    Note that in cases where the client sits behind a proxy that does
    not forward the original ip, the proxy IP will be processed rather
    than the client IP. There is no way to find the actual client IP in
    that case.

    [1] https://github.com/falconry/falcon/blob/c3e91875f1030c68d17efa27cf8df3d18f337761/falcon/request.py#L891
    """
    try:
        ip = req.access_route[0]
    except IndexError:
        return (_INVALID_STRING,) * 3
    else:
        return (
            ip_collision_obfuscation(ip),
            ip_token_obfuscation(ip=ip, token=token),
            geo_location_ip(ip_str=ip)
        )
