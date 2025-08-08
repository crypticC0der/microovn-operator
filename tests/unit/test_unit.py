#!/usr/bin/env python3

import ops
from ops import testing
from unittest.mock import patch
import subprocess

import charm
import charms.tls_certificates_interface.v4.tls_certificates as tls_certs


@patch.object(charm.subprocess, "run")
def test_call_microovn_command(mock_run):
        charm.call_microovn_command("status")
        args, kwargs = mock_run.call_args
        assert args[0] == ["microovn", "status"]

        addresses = ["192.168.0.16", "8.8.8.8", "4.13.6.12"]
        charm.call_microovn_command("config", "set", "ovn.central-ips", ",".join(addresses))
        args, kwargs = mock_run.call_args
        assert args[0] == [
            "microovn",
            "config",
            "set",
            "ovn.central-ips",
            "192.168.0.16,8.8.8.8,4.13.6.12",
        ]


@patch.object(charm.subprocess, "run")
def test_install(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    ctx = testing.Context(charm.MicroovnCharm)
    state_in = testing.State()
    ctx.run(ctx.on.install(), state_in)
    mock_run.assert_any_call(
        ["snap", "wait", "system", "seed.loaded"], check=True
    )
    mock_run.assert_any_call(
        ["snap", "install", "microovn", "--channel", "latest/edge"], check=True
    )
    mock_run.assert_any_call(
        ["microovn", "waitready"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, input=None, text=True,
    )

@patch.object(charm, "call_microovn_command")
def test_central_exists(mock_run):
    status_output = """
MicroOVN deployment summary:
- a (10.155.18.254)
    Services: central, chassis, switch
OVN Database summary:
OVN Northbound: OK (7.3.0)
OVN Southbound: OK (20.33.0)
"""
    mock_run.return_value = (0, status_output)
    ctx = testing.Context(charm.MicroovnCharm)
    with ctx(ctx.on.start(), testing.State()) as manager:
        manager.charm._stored.in_cluster=True
        output = manager.charm._microovn_central_exists()
        mock_run.assert_any_call("status")
        assert output

@patch.object(charm, "call_microovn_command")
def test_dataplane_mode(microovn_command):
    ctx = testing.Context(charm.MicroovnCharm)
    ovsdb_relation = testing.Relation("ovsdb-external", "ovsdb-cms", remote_units_data={0: {"bound-address":"192.168.0.16"}})
    microovn_command.return_value = (0, "")
    with ctx(ctx.on.start(), testing.State(relations=[ovsdb_relation],leader=True)) as manager:
        manager.charm._stored.in_cluster=True
        assert manager.charm._dataplane_mode()
        assert manager.charm.ovsdbcms_requires.bound_addresses()
        microovn_command.assert_any_call("disable", "central", "--allow-disable-last-central")
        microovn_command.assert_any_call("config", "set", "ovn.central-ips", "192.168.0.16")

@patch.object(charm, "call_microovn_command")
def test_external_ovs_broken(microovn_command):
    ctx = testing.Context(charm.MicroovnCharm)
    microovn_command.return_value = (0, "")
    with ctx(ctx.on.start(), testing.State(leader=True)) as manager, \
        patch.object(manager.charm,"_microovn_central_exists") as central_exists:
        manager.charm._stored.in_cluster=True
        central_exists.return_value = False
        manager.charm._on_ovsdbcms_broken(None)
        microovn_command.assert_any_call("config", "delete", "ovn.central-ips")
        expected_status = ops.BlockedStatus(
            "microovn has no central nodes, this could either be due to a "
            + "recently broken ovsdb-cms relation or a configuration issue"
        )
        assert manager.charm.unit.status == expected_status


@patch.object(charm, "call_microovn_command")
def test_certs_available(microovn_command):
    fake_ca = "ca cert 413"
    fake_given_cert = "given cert 612"
    fake_priv_key = "priv key 1111"
    ctx = testing.Context(charm.MicroovnCharm)
    with ctx(ctx.on.start(), testing.State()) as manager, \
        patch.object(manager.charm.certificates,"get_assigned_certificate") as get_certs:
        priv_key = tls_certs.PrivateKey(raw=fake_priv_key)
        provider_cert = tls_certs.ProviderCertificate(relation_id=0, ca=fake_ca,certificate=fake_given_cert,chain = [fake_given_cert, fake_ca], certificate_signing_request=None)
        get_certs.return_value = (provider_cert, priv_key)
        microovn_command.return_value = (0, "New CA certificate: Issued")

        manager.charm._on_certificates_available(None)

        get_certs.assert_any_call(certificate_request=charm.CSR_ATTRIBUTES)
        microovn_command.assert_any_call("certificates", "set-ca", "--combined", stdin="\n".join([fake_given_cert, fake_ca, fake_priv_key]))
