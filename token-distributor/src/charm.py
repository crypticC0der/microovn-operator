#!/usr/bin/env python3

import logging, ops, os

logger = logging.getLogger(__name__)

CONTROL_RELATION = "microovn-cluster"

def secretid(unitname):
    return "{0}-secret".format(unitname)

class MicroovnCharmCharm(ops.CharmBase):
    """Charm the application."""
    _stored = ops.StoredState()

    def update_tokens(self, relation_name):
        relation = self.model.get_relation(relation_name)
        if relation == None:
            return

        relation_data = relation.data

        newToken = False
        for unit in relation_data:
            # need hostname
            if not "hostname" in relation_data[unit]:
                continue

            # dont generate token for self or already generated
            if self.unit.name == unit.name or unit.name in relation_data[self.unit]:
                continue

            # get hostname
            hostname = relation_data[unit]["hostname"]
            token = os.popen(
                    "microovn cluster add {}".format(hostname)).read()
            token = token.strip()
            relation.data[self.unit][unit.name] = token
            logger.info("added token to {}".format(unit.name))
            newToken=True

        return newToken

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self._stored.set_default(in_cluster=False)
        framework.observe(self.on.start, self._on_start)
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.remove, self._on_remove)
        framework.observe(self.on.leader_elected , self._on_peers_changed)
        framework.observe(self.on[CONTROL_RELATION].relation_changed,
                            self._on_peers_changed)


    def add_hostname(self, relation_name):
        relation = self.model.get_relation(relation_name)
        relation.data[self.unit]["hostname"] = os.uname()[1]

    def find_token(self, relation_name):
        if not (relation := self.model.get_relation(relation_name)):
            return False

        tokens = {
            token
            for unit in relation.units | {self.unit}
            if (token := relation.data[unit].get(self.unit.name))
        }
        if len(tokens) != 1:
            return False
        (token,) = tokens
        return token

    def join_with_token(self, token):
        self.unit.status = ops.MaintenanceStatus("Joining cluster")
        os.system("microovn cluster join {}".format(token))
        self._stored.in_cluster=True
        self.unit.status = ops.ActiveStatus("Joined cluster")

    def _on_install(self, event: ops.InstallEvent):
        self.unit.status = ops.MaintenanceStatus("installing microovn snap")
        os.system("snap install microovn --channel latest/edge")
        self.unit.status = ops.MaintenanceStatus("waiting for microovn ready")
        os.system("microovn waitready")
        self.unit.status = ops.ActiveStatus("not leader so not joining cluster")

    def _on_start(self, event: ops.StartEvent):
        """Handle start event."""
        self.add_hostname(CONTROL_RELATION)

    def _on_remove(self, event: ops.RemoveEvent):
        if self._stored.in_cluster:
            os.system("microovn cluster remove {}".format(os.uname()[1]))

    def _handle_relation_joined(self, relation_name):
        if not self.unit.is_leader() or not self._stored.in_cluster:
            return False

        if self.update_tokens(relation_name):
            return True
        return False

    def _on_peers_changed(self, event: ops.RelationChangedEvent):
        if not self.unit.is_leader():
            return

        if not self._stored.in_cluster:
            if (token := self.find_token(CONTROL_RELATION)):
                self.join_with_token(token)
            else:
                self.unit.status = ops.ErrorStatus("leader not in cluster")
                return

        self._handle_relation_joined(CONTROL_RELATION)

if __name__ == "__main__":  # pragma: nocover
    ops.main(MicroovnCharmCharm)
