"""A wedged BlueZ must not stall periodic checks or provide stale evidence."""
from types import SimpleNamespace

import dbus
import pytest

from blueferry.bluez_reads import BluezReadCache


@pytest.fixture
def reads():
    calls = []
    now = [0.0]

    def call_async(*args, **kwargs):
        assert kwargs == {"timeout": 5.0}
        calls.append(args)

    cache = BluezReadCache(lambda: SimpleNamespace(call_async=call_async), lambda: now[0])
    return cache, calls, now


def pending(read):
    with pytest.raises(dbus.exceptions.DBusException) as raised:
        read()
    assert raised.value.get_dbus_name() == "org.freedesktop.DBus.Error.NoReply"


def test_unanswered_reads_return_immediately_and_coalesce(reads):
    cache, calls, now = reads
    def read():
        return cache.property("/device", "org.bluez.Bearer.LE1", "Connected")
    for now[0] in range(20):
        pending(read)
    assert len(calls) == 1
    assert calls[0][:6] == ("org.bluez", "/device", "org.freedesktop.DBus.Properties",
                           "Get", "ss", ("org.bluez.Bearer.LE1", "Connected"))
    calls[0][-2](True)
    assert read() is True
    now[0] += 5
    assert read() is True
    assert len(calls) == 2
    now[0] += 6
    pending(read)  # Cached Connected=true has expired even without a callback.
    assert len(calls) == 2


def test_error_replaces_cached_success_and_retries_are_bounded(reads):
    cache, calls, now = reads
    def read():
        return cache.property("/device", "org.bluez.Bearer.LE1", "Connected")
    pending(read)
    calls[0][-2](True)
    now[0] = 5
    assert read() is True
    calls[-1][-1](dbus.exceptions.DBusException("timeout", name="org.freedesktop.DBus.Error.NoReply"))
    pending(read)
    assert len(calls) == 2
    now[0] = 10
    pending(read)
    assert len(calls) == 3
    calls[-1][-2](False)
    assert read() is False


def test_property_signal_beats_old_reply_and_invalidations_expire_value(reads):
    cache, calls, _now = reads
    def read():
        return cache.property("/device", "org.bluez.Bearer.LE1", "Connected")
    pending(read)
    cache.properties_changed("/device", "org.bluez.Bearer.LE1", {"Connected": False}, [])
    calls[0][-2](True)
    assert read() is False
    cache.properties_changed("/device", "org.bluez.Bearer.LE1", {}, ["Connected"])
    pending(read)
    calls[-1][-2](True)
    assert read() is True


@pytest.mark.parametrize("invalidation", ["invalidate", "invalidate_managed_objects"])
def test_late_snapshot_cannot_survive_owner_or_topology_change(reads, invalidation):
    cache, calls, _now = reads
    def read():
        return cache.read("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", "", (), owner=":1.2")
    pending(read)
    getattr(cache, invalidation)()
    pending(read)
    calls[0][-2]({"stale": True})
    pending(read)
    calls[-1][-2]({"fresh": True})
    assert read() == {"fresh": True}
