# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""BGP attach marker producer for the MicroOVN charm.

This module implements the charm-half of the MicroOVN BGP v2 design
(``~/plans/specs/microovn-bgp-v2.org``): the charm consumes intent from the
``role-assignment`` relation's ``workload-params`` (``rack``, ``peer`` and,
optionally, ``local-asn``) and stamps the attach markers the snap reads from
OVN Northbound. Everything after the markers --- redirect, BIRD, the
interconnect bridge --- is owned by the snap.

The names derive from Neutron Core OVN BGP (``neutron/services/bgp/``):

- the per-chassis router is ``bgp-lr-<chassis>``
  (``helpers.get_chassis_router_name``);
- its uplink (peer) logical router port is
  ``bgp-lrp-bgp-lr-<chassis>-to-bgp-ls-<chassis>-<net>``
  (``helpers.get_lrp_name`` + ``helpers.get_chassis_peer_switch_name``), one
  per ``<net>`` entry in ``Chassis_Private.external_ids:neutron-bgp-bridges``.

Only the peer logical router port carries the markers
(``microovn-bgp-attach`` + ``microovn-bgp-peers``); the
``...-to-bgp-lr-main`` port is the internal chassis<->main-router link, not an
uplink.

Intent is signalled purely by the marker params (``rack``/``peer``/``local-asn``);
the ``roles`` list is orthogonal by design (the spec's enablement is derived
from the markers, not the role list).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import subprocess
from typing import Any

import ops

from role_handler import RoleResult

logger = logging.getLogger(__name__)

# Attach markers, read by the snap from the uplink Logical_Router_Port (NB).
ATTACH_MARKER = "microovn-bgp-attach"
PEERS_KEY = "microovn-bgp-peers"

# Chassis-wide knobs, read by the snap from Open_vSwitch external_ids.
ASN_KEY = "microovn-bgp-asn"
RACK_KEY = "microovn-bgp-rack"

# The source of the per-chassis uplink (peer) bridge list: present on the SB
# Chassis_Private table, published there by Sunbeam's network-agents snap.
BGP_BRIDGES_KEY = "neutron-bgp-bridges"

# The local chassis name in OVN == Open_vSwitch.external_ids:system-id.
SYSTEM_ID_KEY = "system-id"


class BgpHandler(ops.Object):
    """Writes BGP attach markers from the role-assignment workload params."""

    _stored = ops.StoredState()

    def __init__(self, charm, relation_name: str, role_handler) -> None:
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name
        self._role_handler = role_handler
        self._stored.set_default(applied="")

    # --- Public API -----------------------------------------------------

    def enforce(self) -> RoleResult:
        """Reconcile the BGP markers with the current assignment.

        Returns ``OK`` when the markers match the intent (including the
        no-intent case), ``RETRY`` when the intent is present but something is
        still transient (bridges/LRPs not yet published), and ``FAILED`` when
        the intent is invalid or a required write failed. The unit status is
        set accordingly, so callers must honour a non-``OK`` result.
        """
        valid, params = self._resolve_params()
        if not valid:
            return RoleResult.FAILED

        if params is None:
            return self._teardown()

        chassis = self._chassis_name()
        bridges = self._read_peer_bridges(chassis)
        if bridges is None:
            self._charm.unit.status = ops.BlockedStatus("Failed to read BGP peer bridges")
            return RoleResult.FAILED
        if not bridges:
            self._charm.unit.status = ops.WaitingStatus("Waiting for BGP peer bridges")
            return RoleResult.RETRY

        desired = self._desired_state(params, bridges, chassis)
        return self._reconcile(desired)

    def _chassis_name(self) -> str:
        """Return the local OVN chassis name.

        The authoritative value is ``Open_vSwitch.external_ids:system-id``
        (set by the snap to the cluster member name at bootstrap/join), which
        also covers a cluster bootstrapped with a custom ``--name``. Fall back
        to the short hostname when the key is not present.
        """
        res = self._run_vsctl("get", "open_vswitch", ".", f"external_ids:{SYSTEM_ID_KEY}")
        if res.returncode == 0:
            name = res.stdout.strip().strip('"')
            if name:
                return name
        return socket.gethostname()

    # --- Intent ---------------------------------------------------------

    def _resolve_params(self) -> tuple[bool, dict[str, Any] | None]:
        assignment = self._role_handler.get_assignment()
        if assignment is None:
            return True, None
        workload = assignment.workload_params or {}
        rack = workload.get("rack")
        peer = workload.get("peer")
        local_asn = workload.get("local-asn")

        # BGP is only signalled when one of the BGP keys is present.
        if rack is None and peer is None and local_asn is None:
            return True, None
        if not isinstance(peer, str) or not peer:
            self._charm.unit.status = ops.BlockedStatus("BGP peer is required")
            return False, None
        if not self._validate_peer(peer):
            self._charm.unit.status = ops.BlockedStatus(f"Invalid BGP peer: {peer!r}")
            return False, None
        if not isinstance(rack, str) or not rack.strip():
            self._charm.unit.status = ops.BlockedStatus(f"Invalid BGP rack: {rack!r}")
            return False, None
        if local_asn is not None and not self._validate_asn(local_asn):
            self._charm.unit.status = ops.BlockedStatus(f"Invalid BGP local-asn: {local_asn!r}")
            return False, None

        return True, {"rack": rack.strip(), "peer": peer, "local_asn": local_asn}

    @staticmethod
    def _validate_peer(peer: str) -> bool:
        """Validate a ``<asn>@<address>`` peer string."""
        if "@" not in peer:
            return False
        asn_s, addr_s = peer.split("@", 1)
        if not asn_s.isdigit():
            return False
        asn = int(asn_s)
        if not 1 <= asn <= 0xFFFFFFFF:
            return False
        try:
            ipaddress.ip_address(addr_s)
        except ValueError:
            return False
        return True

    @staticmethod
    def _validate_asn(asn: Any) -> bool:
        """Validate a local ASN supplied as ``int`` or digit ``str``."""
        if isinstance(asn, bool):
            return False
        if isinstance(asn, int):
            return 1 <= asn <= 0xFFFFFFFF
        if isinstance(asn, str) and asn.isdigit():
            return 1 <= int(asn) <= 0xFFFFFFFF
        return False

    # --- Name / state derivation ---------------------------------------

    @staticmethod
    def _uplink_lrp(chassis: str, bridge: str) -> str:
        """Derive the uplink (peer) LRP name for a chassis and BGP bridge."""
        return f"bgp-lrp-bgp-lr-{chassis}-to-bgp-ls-{chassis}-{bridge}"

    def _desired_state(self, params: dict, bridges: list[str], chassis: str) -> dict:
        """Compute the desired marker state for the given params + bridges."""
        targets = {self._uplink_lrp(chassis, b): params["peer"] for b in bridges}
        asn = params["local_asn"]
        return {
            "targets": targets,
            "asn": str(asn) if asn is not None else None,
            "rack": params["rack"],
        }

    # --- Applied-state bookkeeping -------------------------------------

    def _get_applied(self) -> dict | None:
        raw = str(self._stored.applied)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Stored applied BGP state is not valid JSON: %r", raw)
            return None

    def _save_applied(self, state: dict | None) -> None:
        self._stored.applied = "" if state is None else json.dumps(state, sort_keys=True)

    # --- Reconcile ------------------------------------------------------

    # Note on idempotency: the desired LRP markers are reconciled against a
    # *read-back* of the live NB/OVS state (not only the cached StoredState
    # snapshot), so a marker removed out-of-band is re-asserted on the next
    # tick. LRPs no longer in the desired set are cleared from the cached
    # snapshot; the asn/rack knobs are also synced by read-back.

    def _reconcile(self, desired: dict) -> RoleResult:
        applied = self._get_applied()
        stale = {
            lrp: peer
            for lrp, peer in (applied or {}).get("targets", {}).items()
            if lrp not in desired["targets"]
        }
        if not self._clear_targets(stale):
            return RoleResult.FAILED

        for lrp, peer in desired["targets"].items():
            attach, current_peers = self._read_lrp_markers(lrp)
            if attach == "true" and current_peers == peer:
                continue
            if not self._stamp_target(lrp, peer):
                return RoleResult.FAILED

        for key, want in ((ASN_KEY, desired["asn"]), (RACK_KEY, desired["rack"])):
            if not self._sync_knob(key, want):
                return RoleResult.FAILED

        self._save_applied(desired)
        return RoleResult.OK

    def _teardown(self) -> RoleResult:
        # Fast path: nothing was ever applied, so there is nothing to tear down.
        # (Known limitation: a StoredState reset after a re-deploy on the same
        # machine with surviving NB/OVS markers is not re-detected here; the
        # markers are inert until the intent returns, at which point reconcile
        # re-asserts them from live read-back.)
        applied = self._get_applied()
        if applied is None:
            return RoleResult.OK
        if not self._clear_targets(applied.get("targets", {})):
            return RoleResult.FAILED
        for key in (ASN_KEY, RACK_KEY):
            if not self._sync_knob(key, None):
                return RoleResult.FAILED
        self._save_applied(None)
        return RoleResult.OK

    def _read_lrp_markers(self, lrp: str) -> tuple[str | None, str | None]:
        """Read the current attach + peers markers on an LRP (None if absent)."""
        return self._nbctl_get(lrp, ATTACH_MARKER), self._nbctl_get(lrp, PEERS_KEY)

    def _stamp_target(self, lrp: str, peer: str) -> bool:
        """Set the attach markers on one LRP, treating a missing row as wait."""
        res = self._run_nbctl(
            "set",
            "Logical_Router_Port",
            lrp,
            f"external_ids:{ATTACH_MARKER}=true",
            f"external_ids:{PEERS_KEY}={peer}",
        )
        if res.returncode == 0:
            return True
        if self._is_missing_row(res.stderr):
            self._charm.unit.status = ops.WaitingStatus(f"Waiting for BGP router port {lrp}")
        else:
            logger.error("ovn-nbctl set failed on %s: %s", lrp, res.stderr)
            self._charm.unit.status = ops.BlockedStatus(f"Failed to set BGP markers on {lrp}")
        return False

    def _clear_targets(self, targets: dict[str, str]) -> bool:
        """Remove the attach markers from each LRP.

        A missing row is benign (already gone); any other failure aborts so the
        applied snapshot is not advanced past a marker that is still present.
        """
        for lrp in targets:
            res = self._run_nbctl(
                "remove",
                "Logical_Router_Port",
                lrp,
                "external_ids",
                ATTACH_MARKER,
                PEERS_KEY,
            )
            if res.returncode != 0 and not self._is_missing_row(res.stderr):
                logger.error("Failed to clear BGP markers on %s: %s", lrp, res.stderr.strip())
                self._charm.unit.status = ops.BlockedStatus(
                    f"Failed to clear BGP markers on {lrp}"
                )
                return False
        return True

    def _sync_knob(self, key: str, want: str | None) -> bool:
        """Set or clear an Open_vSwitch external-id key, reading back first."""
        res = self._run_vsctl("get", "open_vswitch", ".", f"external_ids:{key}")
        have = res.stdout.strip().strip('"') if res.returncode == 0 else None
        if have == want:
            return True
        if want is None:
            res = self._run_vsctl("remove", "open_vswitch", ".", "external_ids", key)
        else:
            res = self._run_vsctl("set", "open_vswitch", ".", f"external_ids:{key}={want}")
        if res.returncode != 0:
            logger.error("ovs-vsctl %s failed: %s", "set" if want else "remove", res.stderr)
            self._charm.unit.status = ops.BlockedStatus("Failed to update BGP chassis knobs")
            return False
        return True

    # --- Commands -------------------------------------------------------

    def _read_peer_bridges(self, chassis: str) -> list[str] | None:
        """Read ``Chassis_Private.external_ids:neutron-bgp-bridges``.

        Returns a list of bridge names, ``[]`` when the key/row is not present
        yet (Sunbeam has not published bridges), or ``None`` on a real error.
        """
        res = self._run_sbctl("get", "Chassis_Private", chassis, f"external_ids:{BGP_BRIDGES_KEY}")
        if res.returncode != 0:
            if self._is_missing_row(res.stderr) or "no key" in res.stderr:
                return []
            logger.error("ovn-sbctl get failed: %s", res.stderr)
            return None
        raw = res.stdout.strip().strip('"').strip("'")
        if not raw:
            return []
        return [b.strip() for b in raw.split(",") if b.strip()]

    def _nbctl_get(self, lrp: str, key: str) -> str | None:
        res = self._run_nbctl("get", "Logical_Router_Port", lrp, f"external_ids:{key}")
        if res.returncode != 0:
            return None
        return res.stdout.strip().strip('"')

    @staticmethod
    def _is_missing_row(stderr: str) -> bool:
        return "no row" in stderr or "does not exist" in stderr or "not found" in stderr

    def _run_nbctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["microovn.ovn-nbctl", *args], capture_output=True, text=True)

    def _run_sbctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["microovn.ovn-sbctl", *args], capture_output=True, text=True)

    def _run_vsctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["microovn.ovs-vsctl", *args], capture_output=True, text=True)
