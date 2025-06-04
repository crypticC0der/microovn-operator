#!/usr/bin/env python3
import logging, ops, os

logger = logging.getLogger(__name__)
WORKER_RELATION = "cluster"
MIRROR_PREFIX = "mirror-"

def get_hostname():
    return os.uname().nodename

def mirror_id(hostname):
    return MIRROR_PREFIX + hostname

class MicroovnCharm(ops.CharmBase):
    _stored = ops.StoredState()

    def update_mirror_state(self, relation_data):
        if self.unit.is_leader():
            relation_data[self.unit]["mirror"]="up"
        elif relation_data[self.unit].get("mirror"):
            relation_data[self.unit]["mirror"]="down"

    def update_tokens(self, relation):
        relation_data = relation.data
        distributor_mirrors = [
            unit
            for unit in relation.units
            if (mirror := relation_data[unit].get("mirror")) and mirror == "up"
        ]

        if len(distributor_mirrors) != 1:
            return False

        distributor_mirror = relation_data[distributor_mirrors[0]]
        new_token = False

        # found token distributor leader
        for mirror_key in distributor_mirror.keys():
            if not mirror_key.startswith(MIRROR_PREFIX):
                continue

            hostname = mirror_key[len(MIRROR_PREFIX):]

            # skip if token generated
            if distributor_mirror[mirror_key] != "empty" or \
               mirror_key in relation_data[self.unit]:
                continue

            #generate token and add to this side of mirror
            token = os.popen(
                    "microovn cluster add {}".format(hostname)).read()
            token = token.strip()
            relation_data[self.unit][mirror_key] = token
            logger.info("added token for {}".format(hostname))
            new_token=True

        return new_token

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self._stored.set_default(in_cluster=False)
        framework.observe(self.on.start, self._on_start)
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.remove, self._on_remove)
        framework.observe(self.on.leader_elected , self._on_leader_elected)
        framework.observe(self.on[WORKER_RELATION].relation_changed,
                            self._on_cluster_changed)
        framework.observe(self.on[WORKER_RELATION].relation_created,
                            self._handle_relation_created)


    def add_hostname(self, relation_name):
        relation = self.model.get_relation(relation_name)
        relation.data[self.unit]["hostname"] = get_hostname()

    def find_token(self, relation):
        tokens = [
            token
            for unit in relation.units
            if (token := relation.data[unit].get(mirror_id(get_hostname()))) and \
            token != "empty"
        ]
        if len(tokens) > 1:
            self.unit.status = ops.MaintenanceStatus("Too many tokens")
            return False
        if len(tokens) == 0:
            self.unit.status = ops.MaintenanceStatus("No token yet")
            return False

        logger.info("found token")
        return tokens[0]

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
        self.unit.status = ops.MaintenanceStatus("waiting for cluster setup")

    def _on_start(self, event: ops.StartEvent):
        if self.unit.is_leader():
            # only bootstrap if leader
            # TODO: fix cluster bootstrapped, leader changes to new node not in
            #       cluster, bootstrapps new cluster can be fixed via having some
            #       other characteristic determine who manages this side of the
            #       mirror ie: microcluster leader
            os.system(f"microovn cluster bootstrap")
            self._stored.in_cluster = True
            self.unit.status = ops.ActiveStatus("cluster bootstrapped")

    def _handle_relation_created(self, event: ops.RelationCreatedEvent):
        self.add_hostname(WORKER_RELATION)
        self.update_mirror_state(event.relation.data)
        if self.unit.is_leader():
            self.update_tokens(event.relation)

    def _on_remove(self, event: ops.RemoveEvent):
        if self._stored.in_cluster:
            os.system("microovn cluster remove {}".format(get_hostname()))

    def _on_leader_elected(self, _: ops.RelationChangedEvent):
        if relation := self.model.get_relation(WORKER_RELATION):
            self.update_mirror_state(relation.data)

    def _on_cluster_changed(self, event: ops.RelationChangedEvent):
        if not self._stored.in_cluster:
            if (token := self.find_token(event.relation)):
                self.join_with_token(token)

        if self.unit.is_leader() and self._stored.in_cluster:
            return self.update_tokens(event.relation)

if __name__ == "__main__":  # pragma: nocover
    ops.main(MicroovnCharm)
