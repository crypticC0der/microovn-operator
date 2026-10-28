# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Regression tests for the token-distributor library used by MicroOVN."""

import json
from dataclasses import replace
from subprocess import CompletedProcess
from unittest.mock import patch

import ops
import pytest
from charms.microcluster_token_distributor.v0 import token_distributor as library
from ops import testing


class ConsumerCharm(ops.CharmBase):
    def __init__(self, framework):
        super().__init__(framework)
        self.consumer = library.TokenConsumer(self, "cluster", ["microovn", "cluster"])


class DistributorCharm(ops.CharmBase):
    def __init__(self, framework):
        super().__init__(framework)
        self.distributor = library.TokenDistributorProvides(self, "cluster")


@pytest.fixture()
def consumer() -> testing.Context[ConsumerCharm]:
    return testing.Context(
        ConsumerCharm,
        meta={"name": "microovn", "requires": {"cluster": {"interface": "worker-cluster"}}},
    )


@pytest.fixture()
def distributor() -> testing.Context[DistributorCharm]:
    return testing.Context(
        DistributorCharm,
        meta={
            "name": "distributor",
            "provides": {"cluster": {"interface": "worker-cluster"}},
        },
    )


@pytest.fixture()
def hostname():
    with patch.object(library, "get_hostname", return_value="replacement"):
        yield


@pytest.mark.parametrize("cross_app", [False, True])
def test_replaced_hostname_gets_fresh_token(consumer, distributor, hostname, cross_app):
    """Exercise both sides with stale caches and two replacements of one hostname."""
    stale_data = {
        "mirror": "up",
        "mirror-a-voter": "bootstrap-token",
        "mirror-replacement": "consumed-token",
    }
    issued_tokens = []
    request_ids = []
    members = json.dumps([{"name": "a-voter", "role": "voter", "status": "ONLINE"}])

    def cluster_command(command, **kwargs):
        operation, *args = command[2:]
        if operation == "list":
            return CompletedProcess(command, 0, stdout=members, stderr="")
        if operation == "add":
            assert args == ["replacement"]  # The token must match the real hostname.
            token = f"fresh-token-{len(issued_tokens)}"
            issued_tokens.append(token)
            return CompletedProcess(command, 0, stdout=token + "\n", stderr="")
        assert operation == "join"
        assert issued_tokens, "A replacement must request a fresh token before joining"
        assert args == [issued_tokens[-1]]
        return CompletedProcess(command, 0, stdout="", stderr="")

    with patch.object(library.subprocess, "run", side_effect=cluster_command) as run:
        for _ in range(2):
            # A new unit initially sees the previous incarnation's cached token.
            joining_relation = testing.Relation(
                "cluster", remote_app_name="distributor", remote_units_data={0: stale_data}
            )
            joining_state = consumer.run(
                consumer.on.relation_changed(joining_relation, remote_unit=0),
                testing.State(relations=[joining_relation]),
            )
            run.assert_not_called()
            request_data = joining_state.get_relation(joining_relation.id).local_unit_data
            hostname = request_data["hostname"]
            request_id = hostname.partition(":")[2]
            assert request_id
            assert "token-request-id" not in request_data
            assert request_id not in request_ids
            request_ids.append(request_id)
            key = f"mirror-{hostname}"

            # Keep stale tokens on both the distributor and the communicator.
            provider_relation = testing.Relation(
                "cluster",
                remote_app_name="microovn",
                remote_units_data=(
                    {0: stale_data, 1: request_data} if not cross_app else {0: stale_data}
                ),
                local_unit_data=stale_data,
            )
            relations = [provider_relation]
            target_relation = provider_relation
            if cross_app:
                target_relation = testing.Relation(
                    "cluster",
                    remote_app_name="other-microovn",
                    remote_units_data={0: request_data},
                )
                relations.append(target_relation)
            provider_state = distributor.run(
                distributor.on.relation_changed(
                    target_relation, remote_unit=0 if cross_app else 1
                ),
                testing.State(leader=True, relations=relations),
            )
            mirrored = provider_state.get_relation(provider_relation.id).local_unit_data
            assert mirrored[key] == library.EMPTY_STRING

            # The elected communicator creates a token once for this request.
            communicator_relation = testing.Relation(
                "cluster", remote_units_data={0: mirrored}, local_unit_data=stale_data
            )
            with (
                patch.object(library, "get_hostname", return_value="a-voter"),
                consumer(
                    consumer.on.relation_changed(communicator_relation, remote_unit=0),
                    testing.State(
                        relations=[communicator_relation], unit_status=testing.ActiveStatus()
                    ),
                ) as manager,
            ):
                manager.charm.consumer._stored.in_cluster = True
                communicator_state = manager.run()
            generated = communicator_state.get_relation(communicator_relation.id).local_unit_data
            assert generated[key] == issued_tokens[-1]

            # A repeated event must not mint another token before mirroring catches up.
            token_count = len(issued_tokens)
            with patch.object(library, "get_hostname", return_value="a-voter"):
                consumer.run(
                    consumer.on.relation_changed(
                        communicator_state.get_relation(communicator_relation.id), remote_unit=0
                    ),
                    communicator_state,
                )
            assert len(issued_tokens) == token_count

            provider_relation = replace(
                provider_state.get_relation(provider_relation.id),
                remote_units_data={
                    **provider_relation.remote_units_data,
                    0: generated,
                },
            )
            provider_state = distributor.run(
                distributor.on.relation_changed(provider_relation, remote_unit=0),
                replace(
                    provider_state,
                    relations=[
                        provider_relation if rel.id == provider_relation.id else rel
                        for rel in provider_state.relations
                    ],
                ),
            )
            mirrored = provider_state.get_relation(target_relation.id).local_unit_data
            assert mirrored[key] == issued_tokens[-1]
            joining_relation = replace(
                joining_state.get_relation(joining_relation.id), remote_units_data={0: mirrored}
            )
            joined = consumer.run(
                consumer.on.relation_changed(joining_relation, remote_unit=0),
                replace(joining_state, relations=[joining_relation]),
            )
            assert joined.unit_status == ops.ActiveStatus("Joined cluster")
            # Removal/reprovisioning clears unit state, but leaves both mirrors stale.
            stale_data = mirrored
            run.reset_mock()

    assert len(set(issued_tokens)) == 2


def test_upgrade_advertises_request_once(consumer, hostname):
    relation = testing.Relation("cluster", local_unit_data={"hostname": "replacement"})
    state = consumer.run(consumer.on.upgrade_charm(), testing.State(relations=[relation]))
    request_data = state.get_relation(relation.id).local_unit_data
    assert request_data["hostname"].startswith("replacement:")

    state = consumer.run(consumer.on.upgrade_charm(), state)
    assert state.get_relation(relation.id).local_unit_data == request_data


def test_upgrade_preserves_joined_legacy_request(consumer, hostname):
    relation = testing.Relation("cluster", local_unit_data={"hostname": "replacement"})
    with (
        consumer(consumer.on.upgrade_charm(), testing.State(relations=[relation])) as manager,
        patch.object(library.TokenConsumer, "_handle_mirror"),
    ):
        manager.charm.consumer._stored.in_cluster = True
        state = manager.run()
    assert state.get_relation(relation.id).local_unit_data == relation.local_unit_data


def test_bootstrap_with_scoped_request(consumer, hostname):
    relation = testing.Relation(
        "cluster",
        remote_app_data={"bootstrap-app": "microovn"},
        remote_units_data={0: {"mirror": "up"}},
    )
    with patch.object(library.TokenConsumer, "_call_cluster_command") as command:
        command.side_effect = lambda operation, *args: (
            (0, "") if operation == "bootstrap" else (0, "[]")
        )
        state = consumer.run(
            consumer.on.relation_joined(relation, remote_unit=0),
            testing.State(leader=True, relations=[relation]),
        )

    assert state.unit_status == ops.ActiveStatus("Cluster bootstrapped")
    assert state.get_relation(relation.id).local_unit_data["hostname"].startswith("replacement:")
    command.assert_any_call("bootstrap")


def test_remove_uses_real_hostname(consumer, hostname):
    with (
        patch.object(
            library.TokenConsumer, "_call_cluster_command", return_value=(0, "")
        ) as command,
        consumer(consumer.on.remove(), testing.State()) as manager,
    ):
        manager.charm.consumer._stored.in_cluster = True
        manager.charm.consumer._stored.token_request_id = "request-id"
        manager.run()

    command.assert_called_once_with("remove", "replacement")


def test_distributor_supports_legacy_requests(distributor):
    relation = testing.Relation("cluster", remote_units_data={0: {"hostname": "legacy"}})
    state = distributor.run(
        distributor.on.relation_changed(relation, remote_unit=0),
        testing.State(leader=True, relations=[relation]),
    )
    assert state.get_relation(relation.id).local_unit_data["mirror-legacy"] == library.EMPTY_STRING


@pytest.mark.parametrize("leader", [True, False])
def test_distributor_upgrade_reconciles_requests(distributor, leader):
    relation = testing.Relation(
        "cluster",
        local_unit_data={"mirror-replacement": "consumed-token"},
        remote_units_data={0: {"hostname": "replacement", "token-request-id": "new-request"}},
    )
    state = distributor.run(
        distributor.on.upgrade_charm(), testing.State(leader=leader, relations=[relation])
    )
    data = state.get_relation(relation.id).local_unit_data
    if leader:
        assert data["mirror-replacement:new-request"] == library.EMPTY_STRING
    else:
        assert data == relation.local_unit_data


def test_distributor_upgrade_without_relations(distributor):
    distributor.run(distributor.on.upgrade_charm(), testing.State(leader=True))


def test_mirror_handover_with_unpublished_request(consumer):
    relation = testing.Relation(
        "cluster",
        local_unit_data={"mirror": "up"},
        remote_units_data={0: {"mirror": "up", "mirror-replacement:new": library.EMPTY_STRING}},
    )
    with consumer(consumer.on.start(), testing.State(relations=[relation])) as manager:
        cluster_relation = manager.charm.model.get_relation("cluster")
        assert cluster_relation is not None
        manager.charm.consumer._safely_down_mirror(cluster_relation)
        state = manager.run()
    assert state.get_relation(relation.id).local_unit_data["mirror"] == "down"


@pytest.mark.parametrize("operation", ["join", "add", "remove", "list", "bootstrap"])
def test_cluster_command_logs_stderr_without_token(consumer, caplog, operation):
    token = "private-join-token"
    stderr = f"Join rejected: {token}" if operation == "join" else "Cluster unavailable"
    args = (operation, token) if operation == "join" else (operation,)
    with (
        patch.object(library.subprocess, "run") as run,
        consumer(consumer.on.start(), testing.State()) as manager,
    ):
        run.return_value = CompletedProcess([], 1, stdout=token, stderr=stderr)
        assert manager.charm.consumer._call_cluster_command(*args) == (1, token)

    assert f"microovn cluster {operation}" in caplog.text
    assert "exit code 1" in caplog.text
    assert ("Join rejected" if operation == "join" else stderr) in caplog.text
    assert token not in caplog.text
    assert run.call_args.kwargs["stderr"] == library.subprocess.PIPE


def test_successful_command_does_not_log_token(consumer, caplog):
    token = "private-join-token"
    with (
        patch.object(library.subprocess, "run") as run,
        consumer(consumer.on.start(), testing.State()) as manager,
    ):
        run.return_value = CompletedProcess([], 0, stdout=token, stderr="")
        assert manager.charm.consumer._call_cluster_command("add", "node") == (0, token)

    assert token not in caplog.text
