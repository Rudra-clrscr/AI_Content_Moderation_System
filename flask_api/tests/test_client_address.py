"""Where a submission came from, recorded in the audit row and refusable from it.

TWO VALUES, because they answer different questions. `ip` is the device, which is what an
operator quotes in a report. `network` is the /24 or /64 it sits in, which is what they
actually refuse when one address stops working and the next one along starts: a home
connection re-dials into a new address in the same /24, and an IPv6 host has 2^64 of its own.

THE HEADER IS NOT EVIDENCE. X-Forwarded-For is an ordinary request header, so with nothing in
front of this service overwriting it the caller writes it themselves. Trusting it by default
would let an author keep their own address out of the audit log and - with a Block button
sitting on that row - put a stranger's address in and have it refused. So the socket address
is the default, and how many proxies are in front is configuration rather than a guess.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.admin_store import AdminStore  # noqa: E402
from app.client_address import network_of, resolve  # noqa: E402


# ---- what an address belongs to ------------------------------------------------------

def test_an_ipv4_address_belongs_to_its_slash_24():
    assert network_of("203.0.113.77") == "203.0.113.0/24"


def test_an_ipv6_address_belongs_to_its_slash_64():
    """A /128 block is close to blocking nothing: the host owns the rest of its own /64."""
    assert network_of("2001:db8:1:2:3:4:5:6") == "2001:db8:1:2::/64"


def test_something_that_is_not_an_address_has_no_network():
    assert network_of("not-an-address") == ""
    assert network_of("") == ""


# ---- the header, and who is allowed to write it --------------------------------------

def test_without_a_proxy_the_socket_address_is_used():
    assert resolve("203.0.113.7", None, 0)["ip"] == "203.0.113.7"


def test_a_forged_header_is_ignored_when_nothing_is_in_front():
    """The whole reason this defaults to off. Honouring it here would let anyone hide their
    own address AND name a stranger's to be blocked."""
    got = resolve("203.0.113.7", "198.51.100.9", 0)
    assert got["ip"] == "203.0.113.7"
    assert got["network"] == "203.0.113.0/24"


def test_the_claimed_chain_is_still_recorded_for_the_operator():
    """Ignored for the verdict, kept for the audit: "they claimed to be elsewhere" is itself
    worth seeing."""
    assert resolve("203.0.113.7", "198.51.100.9", 0)["forwarded"] == "198.51.100.9"


def test_behind_one_proxy_the_proxys_own_view_is_used():
    assert resolve("10.0.0.5", "203.0.113.7", 1)["ip"] == "203.0.113.7"


def test_entries_the_client_prepended_are_not_believed():
    """Each proxy APPENDS what it heard, so only the right-hand end was observed by us. A
    caller who sends a header of their own invention pushes it to the left, where it stays."""
    assert resolve("10.0.0.5", "9.9.9.9, 203.0.113.7", 1)["ip"] == "203.0.113.7"


def test_two_proxies_step_back_two_hops():
    assert resolve("10.0.0.5", "203.0.113.7, 10.0.0.9", 2)["ip"] == "203.0.113.7"


def test_a_port_on_an_entry_does_not_lose_the_address():
    """Proxies write "203.0.113.7:51324" and "[2001:db8::1]:443". Left on, neither parses, and
    every forwarded address silently becomes nothing."""
    assert resolve("10.0.0.5", "203.0.113.7:51324", 1)["ip"] == "203.0.113.7"
    assert resolve("10.0.0.5", "[2001:db8::1]:443", 1)["ip"] == "2001:db8::1"


def test_claiming_more_proxies_than_the_chain_holds_falls_to_the_closest_present():
    """A misconfigured hop count must not report the proxy's own address as the author's."""
    assert resolve("10.0.0.5", "203.0.113.7", 3)["ip"] == "203.0.113.7"


# ---- through the service -------------------------------------------------------------

@pytest.fixture
def client(make_settings, tmp_path):
    settings = make_settings(result_sink="log")
    store = AdminStore(path=tmp_path / "admin.db")
    app = create_app(settings, admin_store=store)
    app.config["_store"] = store
    return app.test_client()


def post(client, text="hello there", ip="203.0.113.7", **headers):
    return client.post("/v1/moderate", json={"content": text, "content_type": "article"},
                       environ_base={"REMOTE_ADDR": ip}, headers=headers)


def test_an_ordinary_submission_is_recorded_with_its_address(client):
    assert post(client).status_code == 200
    rows = client.application.config["_store"].get_moderation_logs(limit=1)["logs"]
    assert rows and rows[0]["client_ip"] == "203.0.113.7"
    assert rows[0]["client_network"] == "203.0.113.0/24"


def test_the_address_is_not_added_to_the_public_response(client):
    """It is the caller's own address, so it tells them nothing - and putting it in the body
    would make an audit column part of the API's shape."""
    assert "client" not in post(client).get_json()


def test_a_blocked_address_cannot_submit(client):
    client.application.config["_store"].block_client("203.0.113.7", "ip", reason="repeat spam")
    r = post(client)
    assert r.status_code == 403
    assert r.get_json()["error"]["code"] == "client_blocked"


def test_the_refusal_does_not_say_how_wide_the_block_is(client):
    """Naming the address or the scope tells someone probing it exactly which address to come
    back from."""
    client.application.config["_store"].block_client("203.0.113.0/24", "network")
    msg = post(client).get_json()["error"]["message"]
    assert "203.0.113" not in msg and "network" not in msg


def test_blocking_a_network_catches_the_address_next_door(client):
    """The case the scope exists for: the connection reconnects onto a neighbouring address."""
    client.application.config["_store"].block_client("203.0.113.0/24", "network")
    assert post(client, ip="203.0.113.188").status_code == 403


def test_blocking_one_device_does_not_catch_its_neighbours(client):
    client.application.config["_store"].block_client("203.0.113.7", "ip")
    assert post(client, ip="203.0.113.188").status_code == 200


def test_a_blocked_caller_cannot_slip_past_with_a_forged_header(client):
    """The attack the default exists to stop, from the other direction."""
    client.application.config["_store"].block_client("203.0.113.7", "ip")
    assert post(client, **{"X-Forwarded-For": "198.51.100.1"}).status_code == 403


def test_unblocking_lets_them_back_in(client):
    store = client.application.config["_store"]
    store.block_client("203.0.113.7", "ip")
    assert post(client).status_code == 403
    store.unblock_client("203.0.113.7")
    assert post(client).status_code == 200


def test_a_blocklist_that_cannot_be_read_does_not_take_the_service_down(client, monkeypatch):
    """It is a moderation convenience, not an authentication boundary. Failing closed would
    mean the whole service stops when the admin database does."""
    store = client.application.config["_store"]
    monkeypatch.setattr(store, "blocked_entry",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    assert post(client).status_code == 200


# ---- noticing the one configuration mistake that matters -----------------------------
# A proxy goes in front and trust_proxy_hops is left at 0. Nothing fails: every audit row
# just records the proxy instead of the author, the log fills with one address, and it is
# noticed when somebody tries to block a member and refuses the whole site.

def test_a_forwarded_request_with_no_configured_proxy_is_reported(client, caplog):
    import app.api as api
    api._FORWARDED_WARNED = False
    with caplog.at_level("WARNING"):
        post(client, **{"X-Forwarded-For": "203.0.113.9"})
    said = [r for r in caplog.records if "trust_proxy_hops" in str(r.msg)]
    assert said, "a proxy in front with hops at 0 must be reported"
    # The message has to name the header it saw and the address it is recording instead,
    # or it is a warning the reader cannot act on.
    assert "203.0.113.9" in said[0].getMessage()
    assert "203.0.113.7" in said[0].getMessage()


def test_it_is_said_once_and_not_on_every_request(client, caplog):
    """This runs on every request; an operator who has read it does not need it again."""
    import app.api as api
    api._FORWARDED_WARNED = False
    with caplog.at_level("WARNING"):
        for _ in range(4):
            post(client, **{"X-Forwarded-For": "203.0.113.9"})
    said = [r for r in caplog.records if "trust_proxy_hops" in str(r.msg)]
    assert len(said) == 1


def test_nothing_is_said_when_no_proxy_is_involved(client, caplog):
    """The ordinary case. A warning on every direct request is noise that trains people to
    ignore the one that matters."""
    import app.api as api
    api._FORWARDED_WARNED = False
    with caplog.at_level("WARNING"):
        post(client)
    assert not [r for r in caplog.records if "trust_proxy_hops" in str(r.msg)]


def test_nothing_is_said_when_the_proxy_is_configured(make_settings, tmp_path, caplog):
    """hops set and a header arriving is the normal deployment, not a mistake."""
    import app.api as api
    from app.admin_store import AdminStore

    settings = make_settings(result_sink="log", trust_proxy_hops=1)
    store = AdminStore(path=tmp_path / "proxy.db")
    c = create_app(settings, admin_store=store).test_client()
    api._FORWARDED_WARNED = False
    with caplog.at_level("WARNING"):
        c.post("/v1/moderate", json={"content": "hello there", "content_type": "article"},
               environ_base={"REMOTE_ADDR": "10.0.0.5"},
               headers={"X-Forwarded-For": "203.0.113.9"})
    assert not [r for r in caplog.records if "trust_proxy_hops" in str(r.msg)]
    rows = store.get_moderation_logs(limit=1)["logs"]
    assert rows[0]["client_ip"] == "203.0.113.9", "the author, not the proxy"
