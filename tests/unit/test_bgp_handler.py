# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the BgpHandler."""

import json
import socket
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import ops
import pytest
from ops import testing

from charm import MicroovnCharm
from constants import ROLE_ASSIGNMENT_RELATION
from role_handler import RoleResult
from snap_manager import SnapManager


@pytest.fixture()
def mock_microovn_snap():
    """Mock the microovn snap client for charm tests."""
    mock_snap = MagicMock(spec=SnapManager)
    mock_snap.name = "microovn"
    with patch.object(MicroovnCharm, "microovn_snap_client", mock_snap):
        yield mock_snap


@pytest.fixture()
def mock_ovn_exporter_snap():
    """Mock the ovn-exporter snap client for charm tests."""
    mock_snap = MagicMock(spec=SnapManager)
    mock_snap.name = "ovn-exporter"
    with patch.object(MicroovnCharm, "ovn_exporter_snap_client", mock_snap):
        yield mock_snap


def _make_role_assignment_relation(
    status: str = "assigned",
    roles: list[str] | None = None,
    workload_params: dict | None = None,
    unit_name: str = "microovn/0",
) -> testing.Relation:
    """Build a role-assignment relation with the given workload params."""
    assignment: dict = {"status": status}
    if roles is not None:
        assignment["roles"] = roles
    if workload_params is not None:
        assignment["workload-params"] = workload_params
    return testing.Relation(
        endpoint=ROLE_ASSIGNMENT_RELATION,
        remote_app_data={"assignments": json.dumps({unit_name: assignment})},
    )


class _FakeOvsdb:
    """Stateful fake of the microovn.ovn-nbctl / ovs-vsctl / ovn-sbctl commands."""

    def __init__(
        self,
        *,
        bridges: list[str] | None = None,
        system_id: str | None = None,
        sbctl_stderr: str | None = None,
        nbctl_remove_stderr: str | None = None,
    ) -> None:
        self.bridges = bridges
        self.sbctl_stderr = sbctl_stderr
        self.nbctl_remove_stderr = nbctl_remove_stderr
        self.lrp: dict[str, dict[str, str]] = {}
        self.ovs: dict[str, str] = {}
        if system_id is not None:
            self.ovs["system-id"] = system_id

    def __call__(self, cmd, *args, **kwargs):
        prog = cmd[0]
        if prog == "microovn.ovn-nbctl":
            return self._nbctl(cmd)
        if prog == "microovn.ovs-vsctl":
            return self._vsctl(cmd)
        if prog == "microovn.ovn-sbctl":
            return self._sbctl(cmd)
        return CompletedProcess(cmd, 0, "", "")

    def _nbctl(self, cmd):
        op = cmd[1]
        if op == "get":
            rec, key = cmd[3], cmd[4].split(":", 1)[1]
            row = self.lrp.get(rec)
            if row is None:
                return CompletedProcess(cmd, 1, "", f"no row {rec}")
            value = row.get(key)
            if value is None:
                return CompletedProcess(cmd, 1, "", f"no key {key}")
            return CompletedProcess(cmd, 0, f'"{value}"', "")
        if op == "set":
            rec = cmd[3]
            row = self.lrp.setdefault(rec, {})
            for kv in cmd[4:]:
                k, v = kv.split("=", 1)
                row[k.split(":", 1)[1]] = v
            return CompletedProcess(cmd, 0, "", "")
        if op == "remove":
            rec = cmd[3]
            row = self.lrp.get(rec)
            if row is None:
                return CompletedProcess(cmd, 1, "", f"no row {rec}")
            if self.nbctl_remove_stderr is not None:
                return CompletedProcess(cmd, 1, "", self.nbctl_remove_stderr)
            for key in cmd[5:]:
                row.pop(key, None)
            return CompletedProcess(cmd, 0, "", "")
        return CompletedProcess(cmd, 0, "", "")

    def _vsctl(self, cmd):
        op = cmd[1]
        if op == "get":
            key = cmd[4].split(":", 1)[1]
            value = self.ovs.get(key)
            if value is None:
                return CompletedProcess(cmd, 1, "", f"no key {key}")
            return CompletedProcess(cmd, 0, f'"{value}"', "")
        if op == "set":
            k, v = cmd[4].split("=", 1)
            self.ovs[k.split(":", 1)[1]] = v
            return CompletedProcess(cmd, 0, "", "")
        if op == "remove":
            self.ovs.pop(cmd[4], None)
            return CompletedProcess(cmd, 0, "", "")
        return CompletedProcess(cmd, 0, "", "")

    def _sbctl(self, cmd):
        if self.sbctl_stderr is not None:
            return CompletedProcess(cmd, 1, "", self.sbctl_stderr)
        if self.bridges is None:
            return CompletedProcess(cmd, 1, "", "no key")
        return CompletedProcess(cmd, 0, '"' + ",".join(self.bridges) + '"', "")


def _writes(calls):
    """Return only set/remove mutation calls (drops read-only get calls)."""
    return [
        c
        for c in calls
        if c.args[0][0] in ("microovn.ovn-nbctl", "microovn.ovs-vsctl")
        and c.args[0][1] in ("set", "remove")
    ]


def _nbctl_calls(run_mock, op):
    return [
        c.args[0]
        for c in run_mock.call_args_list
        if c.args[0][0] == "microovn.ovn-nbctl" and c.args[0][1] == op
    ]


def _vsctl_calls(run_mock, op):
    return [
        c.args[0]
        for c in run_mock.call_args_list
        if c.args[0][0] == "microovn.ovs-vsctl" and c.args[0][1] == op
    ]


def _lrp(chassis, bridge):
    return f"bgp-lrp-bgp-lr-{chassis}-to-bgp-ls-{chassis}-{bridge}"


# --- no-intent tests ---


def test_no_role_assignment_is_noop(mock_microovn_snap, mock_ovn_exporter_snap):
    """No relation: enforce is a no-op with no mutation calls."""
    with patch("bgp_handler.subprocess.run", side_effect=_FakeOvsdb()) as run:
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State()) as manager:
            result = manager.charm.bgp_handler.enforce()
            assert result is RoleResult.OK

    assert not _writes(run.call_args_list)


def test_roles_without_bgp_params_is_noop(mock_microovn_snap, mock_ovn_exporter_snap):
    """An assignment without BGP workload params is not BGP intent."""
    rel = _make_role_assignment_relation(roles=["chassis", "central"])
    with patch("bgp_handler.subprocess.run", side_effect=_FakeOvsdb()) as run:
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State(relations=[rel])) as manager:
            result = manager.charm.bgp_handler.enforce()
            assert result is RoleResult.OK

    assert not _writes(run.call_args_list)


# --- intent validation ---


@pytest.mark.parametrize(
    "peer",
    [
        "not-a-peer",
        "65101",
        "65101@",
        "@fe80::1",
        "0@fe80::1",
        "4294967296@10.0.0.1",
        "65101@not-an-ip",
    ],
)
def test_invalid_peer_blocks(mock_microovn_snap, mock_ovn_exporter_snap, peer):
    """A malformed peer must block before any subprocess call."""
    rel = _make_role_assignment_relation(
        roles=["chassis"], workload_params={"rack": "rack-a", "peer": peer}
    )
    with patch("bgp_handler.subprocess.run") as run:
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State(relations=[rel])) as manager:
            result = manager.charm.bgp_handler.enforce()
            assert result is RoleResult.FAILED
            assert isinstance(manager.charm.unit.status, ops.BlockedStatus)
        run.assert_not_called()


def test_missing_rack_blocks(mock_microovn_snap, mock_ovn_exporter_snap):
    """A missing rack must block before any subprocess call."""
    rel = _make_role_assignment_relation(
        roles=["chassis"], workload_params={"peer": "65101@fe80::1"}
    )
    with patch("bgp_handler.subprocess.run") as run:
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State(relations=[rel])) as manager:
            result = manager.charm.bgp_handler.enforce()
            assert result is RoleResult.FAILED
            assert isinstance(manager.charm.unit.status, ops.BlockedStatus)
        run.assert_not_called()


def test_invalid_local_asn_blocks(mock_microovn_snap, mock_ovn_exporter_snap):
    """An invalid local-asn must block before any subprocess call."""
    rel = _make_role_assignment_relation(
        roles=["chassis"],
        workload_params={"rack": "rack-a", "peer": "65101@fe80::1", "local-asn": "bogus"},
    )
    with patch("bgp_handler.subprocess.run") as run:
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State(relations=[rel])) as manager:
            result = manager.charm.bgp_handler.enforce()
            assert result is RoleResult.FAILED
            assert isinstance(manager.charm.unit.status, ops.BlockedStatus)
        run.assert_not_called()


# --- reconcile tests ---


def test_valid_intent_stamps_markers(mock_microovn_snap, mock_ovn_exporter_snap):
    """Valid intent stamps attach + peers on each derived uplink LRP."""
    rel = _make_role_assignment_relation(
        roles=["chassis"], workload_params={"rack": "rack-a", "peer": "65101@fe80::1"}
    )
    with patch("bgp_handler.subprocess.run", side_effect=_FakeOvsdb(bridges=["br-bgp"])) as run:
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State(relations=[rel])) as manager:
            result = manager.charm.bgp_handler.enforce()
            assert result is RoleResult.OK

    set_calls = _nbctl_calls(run, "set")
    assert set_calls
    flat = " ".join(a for args in set_calls for a in args)
    assert _lrp(socket.gethostname(), "br-bgp") in flat
    assert "external_ids:microovn-bgp-attach=true" in flat
    assert "external_ids:microovn-bgp-peers=65101@fe80::1" in flat


def test_multiple_bridges_stamp_each_lrp(mock_microovn_snap, mock_ovn_exporter_snap):
    """One LRP is stamped per neutron-bgp-bridges entry."""
    rel = _make_role_assignment_relation(
        roles=["chassis"], workload_params={"rack": "rack-a", "peer": "65101@fe80::1"}
    )
    with patch(
        "bgp_handler.subprocess.run", side_effect=_FakeOvsdb(bridges=["br-bgp-0", "br-bgp-1"])
    ) as run:
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State(relations=[rel])) as manager:
            result = manager.charm.bgp_handler.enforce()
            assert result is RoleResult.OK

    chassis = socket.gethostname()
    set_calls = _nbctl_calls(run, "set")
    assert len(set_calls) == 2
    assert {args[3] for args in set_calls} == {
        _lrp(chassis, "br-bgp-0"),
        _lrp(chassis, "br-bgp-1"),
    }


def test_missing_bridges_waits(mock_microovn_snap, mock_ovn_exporter_snap):
    """Bridges not yet published should yield WaitingStatus and a retry."""
    rel = _make_role_assignment_relation(
        roles=["chassis"], workload_params={"rack": "rack-a", "peer": "65101@fe80::1"}
    )
    with patch("bgp_handler.subprocess.run", side_effect=_FakeOvsdb()) as run:
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State(relations=[rel])) as manager:
            result = manager.charm.bgp_handler.enforce()
            assert result is RoleResult.RETRY
            assert isinstance(manager.charm.unit.status, ops.WaitingStatus)

    assert not _writes(run.call_args_list)


def test_sbctl_error_blocks(mock_microovn_snap, mock_ovn_exporter_snap):
    """A real SB read error should block."""
    rel = _make_role_assignment_relation(
        roles=["chassis"], workload_params={"rack": "rack-a", "peer": "65101@fe80::1"}
    )
    fake = _FakeOvsdb(sbctl_stderr="database connection failed")
    with patch("bgp_handler.subprocess.run", side_effect=fake) as run:
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State(relations=[rel])) as manager:
            result = manager.charm.bgp_handler.enforce()
            assert result is RoleResult.FAILED
            assert isinstance(manager.charm.unit.status, ops.BlockedStatus)

    assert not _writes(run.call_args_list)


def test_idempotent_second_enforce_no_extra_writes(mock_microovn_snap, mock_ovn_exporter_snap):
    """A second enforce with unchanged intent must not re-write markers."""
    rel = _make_role_assignment_relation(
        roles=["chassis"], workload_params={"rack": "rack-a", "peer": "65101@fe80::1"}
    )
    with patch("bgp_handler.subprocess.run", side_effect=_FakeOvsdb(bridges=["br-bgp"])) as run:
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State(relations=[rel])) as manager:
            assert manager.charm.bgp_handler.enforce() is RoleResult.OK
            run.reset_mock()
            assert manager.charm.bgp_handler.enforce() is RoleResult.OK
            assert not _writes(run.call_args_list)


def test_out_of_band_marker_removal_is_reasserted(mock_microovn_snap, mock_ovn_exporter_snap):
    """A marker removed out-of-band must be re-stamped on the next tick."""
    rel = _make_role_assignment_relation(
        roles=["chassis"], workload_params={"rack": "rack-a", "peer": "65101@fe80::1"}
    )
    fake = _FakeOvsdb(bridges=["br-bgp"])
    with patch("bgp_handler.subprocess.run", side_effect=fake) as run:
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State(relations=[rel])) as manager:
            assert manager.charm.bgp_handler.enforce() is RoleResult.OK
            # Simulate out-of-band removal of the markers.
            fake.lrp[_lrp(socket.gethostname(), "br-bgp")] = {}
            run.reset_mock()
            result = manager.charm.bgp_handler.enforce()

    assert result is RoleResult.OK
    set_calls = _nbctl_calls(run, "set")
    assert set_calls
    assert any("microovn-bgp-attach=true" in a for args in set_calls for a in args)


def test_local_asn_writes_asn_knob(mock_microovn_snap, mock_ovn_exporter_snap):
    """A local-asn override must stamp the microovn-bgp-asn chassis knob."""
    rel = _make_role_assignment_relation(
        roles=["chassis"],
        workload_params={"rack": "rack-a", "peer": "65101@fe80::1", "local-asn": 64123},
    )
    with patch("bgp_handler.subprocess.run", side_effect=_FakeOvsdb(bridges=["br-bgp"])) as run:
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State(relations=[rel])) as manager:
            assert manager.charm.bgp_handler.enforce() is RoleResult.OK

    vsctl_set = _vsctl_calls(run, "set")
    assert any("external_ids:microovn-bgp-asn=64123" in a for args in vsctl_set for a in args)


# --- teardown tests ---


def test_no_intent_after_apply_clears_markers(mock_microovn_snap, mock_ovn_exporter_snap):
    """A later no-intent assignment must clear previously applied markers."""
    no_bgp_rel = _make_role_assignment_relation(roles=["chassis"])
    chassis = socket.gethostname()
    lrp = _lrp(chassis, "br-bgp")
    fake = _FakeOvsdb(bridges=["br-bgp"])
    fake.lrp[lrp] = {"microovn-bgp-attach": "true", "microovn-bgp-peers": "65101@fe80::1"}
    fake.ovs["microovn-bgp-asn"] = "64000"
    fake.ovs["microovn-bgp-rack"] = "rack-a"
    applied = {"targets": {lrp: "65101@fe80::1"}, "asn": "64000", "rack": "rack-a"}

    with patch("bgp_handler.subprocess.run", side_effect=fake) as run:
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State(relations=[no_bgp_rel])) as manager:
            manager.charm.bgp_handler._save_applied(applied)
            result = manager.charm.bgp_handler.enforce()
            assert result is RoleResult.OK

    assert _nbctl_calls(run, "remove")
    vsctl_remove = _vsctl_calls(run, "remove")
    assert any("microovn-bgp-rack" in a for args in vsctl_remove for a in args)
    assert any("microovn-bgp-asn" in a for args in vsctl_remove for a in args)


def test_peer_change_restamps_markers(mock_microovn_snap, mock_ovn_exporter_snap):
    """A changed peer must re-stamp the LRP with the new peer value."""
    rel = _make_role_assignment_relation(
        roles=["chassis"], workload_params={"rack": "rack-a", "peer": "65102@fe80::2"}
    )
    chassis = socket.gethostname()
    lrp = _lrp(chassis, "br-bgp")
    fake = _FakeOvsdb(bridges=["br-bgp"])
    fake.lrp[lrp] = {"microovn-bgp-attach": "true", "microovn-bgp-peers": "65101@fe80::1"}
    applied = {"targets": {lrp: "65101@fe80::1"}, "asn": None, "rack": "rack-a"}

    with patch("bgp_handler.subprocess.run", side_effect=fake) as run:
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State(relations=[rel])) as manager:
            manager.charm.bgp_handler._save_applied(applied)
            result = manager.charm.bgp_handler.enforce()
            assert result is RoleResult.OK

    set_calls = _nbctl_calls(run, "set")
    assert any("microovn-bgp-peers=65102@fe80::2" in a for args in set_calls for a in args)


def test_clear_failure_blocks_and_does_not_advance(mock_microovn_snap, mock_ovn_exporter_snap):
    """A hard remove failure must abort reconcile without advancing the snapshot."""
    rel = _make_role_assignment_relation(
        roles=["chassis"], workload_params={"rack": "rack-a", "peer": "65102@fe80::2"}
    )
    chassis = socket.gethostname()
    stale_lrp = _lrp(chassis, "br-bgp-old")
    applied = {"targets": {stale_lrp: "65101@fe80::1"}, "asn": None, "rack": "rack-a"}
    fake = _FakeOvsdb(bridges=["br-bgp"], nbctl_remove_stderr="transaction error")
    fake.lrp[stale_lrp] = {"microovn-bgp-attach": "true", "microovn-bgp-peers": "65101@fe80::1"}

    with patch("bgp_handler.subprocess.run", side_effect=fake) as run:
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State(relations=[rel])) as manager:
            manager.charm.bgp_handler._save_applied(applied)
            result = manager.charm.bgp_handler.enforce()
            assert result is RoleResult.FAILED
            assert isinstance(manager.charm.unit.status, ops.BlockedStatus)
            assert manager.charm.bgp_handler._get_applied() == applied

    assert not _nbctl_calls(run, "set")


# --- chassis name tests ---


def test_chassis_name_reads_system_id(mock_microovn_snap, mock_ovn_exporter_snap):
    """_chassis_name must prefer Open_vSwitch system-id over the hostname."""
    with patch("bgp_handler.subprocess.run", side_effect=_FakeOvsdb(system_id="custom-chassis")):
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State()) as manager:
            assert manager.charm.bgp_handler._chassis_name() == "custom-chassis"


def test_chassis_name_falls_back_to_hostname(mock_microovn_snap, mock_ovn_exporter_snap):
    """_chassis_name falls back to the hostname when system-id is absent."""
    with patch("bgp_handler.subprocess.run", side_effect=_FakeOvsdb()):
        ctx = testing.Context(MicroovnCharm)
        with ctx(ctx.on.start(), testing.State()) as manager:
            assert manager.charm.bgp_handler._chassis_name() == socket.gethostname()
