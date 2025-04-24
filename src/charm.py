#!/usr/bin/env python3
# Copyright 2025 Ubuntu
# See LICENSE file for licensing details.

"""Charm the application."""

import logging

import ops
import os

logger = logging.getLogger(__name__)


class MicroovnCharmCharm(ops.CharmBase):
    """Charm the application."""
    _stored = ops.StoredState()

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self._stored.set_default(in_cluster=False)
        framework.observe(self.on.start, self._on_start)
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on["microovn-peers"].relation_changed,
                          self._on_peers_changed)
        framework.observe(self.on.remove, self._on_remove)

    def _on_install(self, event: ops.InstallEvent):
        self.unit.status = ops.MaintenanceStatus("installing microovn snap")
        os.system("snap install microovn --channel latest/edge")
        self.unit.status = ops.MaintenanceStatus("waiting for microovn ready")
        os.system("microovn waitready")
        self.unit.status = ops.MaintenanceStatus("waiting for cluster setup")

    def update_tokens(self, relation_data):
        if not self.unit.is_leader():
            return

        logger.info("updating tokens")
        logger.info(self.unit.name)
        logger.info(relation_data)
        for unit in relation_data:
            if unit.name == "microovn" or not "hostname" in relation_data[unit]:
                continue
            if self.unit == unit or unit.name in relation_data[self.app]:
                continue

            hostname = relation_data[unit]["hostname"]
            token = os.popen(
                    "microovn cluster add {}".format(hostname)).read()
            token = token.strip()
            relation_data[self.app][unit.name] = token
            logger.info("added token to {}".format(self.unit.name))

    def _on_start(self, event: ops.StartEvent):
        """Handle start event."""
        # add hostname to data
        relation = self.model.get_relation("microovn-peers")
        relation.data[self.unit]["hostname"] = os.uname()[1]

        if not self.unit.is_leader():
            self.unit.status = ops.WaitingStatus("waiting for token")
            return

        # only bootstrap if leader
        os.system(f"microovn cluster bootstrap")
        self._stored.in_cluster = True
        self.unit.status = ops.ActiveStatus("cluster bootstrapped")
        relation = self.model.get_relation("microovn-peers")
        if relation is None:
            event.defer()
            return
        self.update_tokens(relation.data)

    def _on_peers_changed(self, event: ops.RelationChangedEvent):
        logger.info("peers changed")
        relation = self.model.get_relation("microovn-peers")

        # if leader ensure leader value is correct and update tokens
        if self.unit.is_leader():
            self.update_tokens(relation.data)
            return 

        if not self.unit.name in relation.data[self.app]:
            self.unit.status = ops.WaitingStatus("no token yet")
            event.defer()
            return

        logger.info(self._stored.in_cluster)
        if self._stored.in_cluster:
            return

        # if token exists grab and use
        token = relation.data[self.app][self.unit.name]
        self.unit.status = ops.MaintenanceStatus("Joining cluster")
        os.system("microovn cluster join {}".format(token))
        self._stored.in_cluster=True
        logger.info(self._stored.in_cluster)
        self.unit.status = ops.ActiveStatus("Joined cluster")

    def _on_remove(self, event: ops.RemoveEvent):
        relation_data = self.model.get_relation("microovn-peers").data
        hostname = relation_data[self.unit]["hostname"]
        os.system("microovn cluster remove {}".format(hostname))


if __name__ == "__main__":  # pragma: nocover
    ops.main(MicroovnCharmCharm)
