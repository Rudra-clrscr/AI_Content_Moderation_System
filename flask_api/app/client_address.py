"""Who sent this request, as an address an operator can read, record and refuse.

Two values, because they answer different questions. `ip` is the device - what you show in an
audit row and what you quote in a report. `network` is the block it belongs to, /24 for IPv4
and /64 for IPv6, which is the thing you actually refuse when one address stops working and
the next one along starts: a home connection re-dials into a new address in the same /24, and
an IPv6 host has 2^64 addresses of its own to rotate through, so blocking a single IPv6
address is very close to blocking nothing.

THE HEADER IS NOT EVIDENCE. X-Forwarded-For is an ordinary request header, so unless something
in front of this service overwrites it, the caller writes it themselves. Trusting it by default
would mean an author could keep their own address out of the audit log and, worse, put someone
else's into it - and with a block button on that row, forge a block against a stranger. So the
socket address is the default and the number of proxies is configuration, not a guess.
"""
from __future__ import annotations

import ipaddress

# What a /24 and a /64 are for is explained above; they are the sizes worth refusing. Anything
# larger starts taking in whole cities on carrier-grade NAT.
IPV4_PREFIX = 24
IPV6_PREFIX = 64


def network_of(ip: str) -> str:
    """The block an address sits in, e.g. "203.0.113.0/24", or "" if it is not an address."""
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return ""
    prefix = IPV4_PREFIX if addr.version == 4 else IPV6_PREFIX
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))


def _clean(value: str) -> str:
    """One X-Forwarded-For entry as a bare address.

    Entries arrive as "203.0.113.7", "203.0.113.7:51324" or "[2001:db8::1]:443" depending on
    what wrote them, and a port left on the end makes the value fail to parse, which would
    silently turn every forwarded address into "unknown".
    """
    v = str(value or "").strip()
    if not v:
        return ""
    if v.startswith("["):                      # [2001:db8::1]:443
        v = v[1:].split("]", 1)[0]
    elif v.count(":") == 1:                    # 203.0.113.7:51324 - one colon is never IPv6
        v = v.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(v))
    except ValueError:
        return ""


def resolve(remote_addr: str | None, forwarded_for: str | None, trust_proxy_hops: int = 0) -> dict:
    """{"ip", "network", "forwarded"} for one request.

    `trust_proxy_hops` is how many proxies are known to sit in front. Zero - the default -
    uses the socket address and ignores the header entirely. Above zero, that many entries are
    dropped from the RIGHT of the header and the next one is taken, because each proxy appends
    the address it heard from: the right-hand end is what your own infrastructure observed and
    the left-hand end is whatever the client typed.
    """
    socket_ip = _clean(remote_addr)
    chain = [c for c in (_clean(p) for p in str(forwarded_for or "").split(",")) if c]
    ip = socket_ip

    if trust_proxy_hops > 0 and chain:
        # hops=1 means one proxy in front, so its own view of the caller is the last entry.
        idx = len(chain) - trust_proxy_hops
        if 0 <= idx < len(chain):
            ip = chain[idx]
        elif chain:
            # Fewer entries than proxies claimed: the chain is short or the configuration is
            # wrong. The left-most is the closest to the caller that is actually present, and
            # it is still better than silently reporting the proxy's own address.
            ip = chain[0]

    return {
        "ip": ip or "",
        "network": network_of(ip) if ip else "",
        # Kept for the audit row so an operator can see what was claimed versus what was used.
        "forwarded": ", ".join(chain) if chain else "",
    }


def from_request(request, trust_proxy_hops: int = 0) -> dict:
    return resolve(request.remote_addr, request.headers.get("X-Forwarded-For"),
                   trust_proxy_hops)
