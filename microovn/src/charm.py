#!/usr/bin/env python3
import logging
import time
import subprocess
from pathlib import Path

import ops

from charms.microovn.v0.ovsdb import OVSDBProvides
from charms.microcluster_token_distributor.v0.token_distributor import TokenConsumer
from typing import Optional

from charms.tls_certificates_interface.v4.tls_certificates import (
    Certificate,
    CertificateRequestAttributes,
    Mode,
    PrivateKey,
    TLSCertificatesRequiresV4,
)

logger = logging.getLogger(__name__)
OVSDB_RELATION = "ovsdb"
WORKER_RELATION = "cluster"
CERTIFICATES_RELATION = "certificates"

CSR_ATTRIBUTES = CertificateRequestAttributes(
    common_name="Charmed MicroOVN",
    is_ca=True,
)

def call_microovn_command(*args, stdin=None):
    result = subprocess.run(
        ["microovn", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        input=stdin,
        text=True
    )
    return result.returncode, result.stdout


class MicroovnCharm(ops.CharmBase):
    _stored = ops.StoredState()
    ovsdb_provides = None

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self._stored.set_default(in_cluster=False)

        self.certificates = TLSCertificatesRequiresV4(
            charm=self,
            relationship_name=CERTIFICATES_RELATION,
            certificate_requests=[CSR_ATTRIBUTES],
            mode=Mode.APP,
        )

        self.ovsdb_provides = OVSDBProvides(
            charm=self,
            relation_name=OVSDB_RELATION,
        )

        self.token_consumer = TokenConsumer(
            charm=self,
            relation_name=WORKER_RELATION,
            command_name=["microovn", "cluster"]
        )

        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on[WORKER_RELATION].relation_changed,
                            self._on_cluster_changed)
        framework.observe(
            self.certificates.on.certificate_available, self._on_certificates_available
        )

    def _on_certificates_available(self, _: ops.EventBase):
        """Check if the certificate or private key needs an update and perform the update.

        This method retrieves the currently assigned certificate and private key associated with
        the charm's TLS relation. It checks whether the certificate or private key has changed
        or needs to be updated. If an update is necessary, the new certificate or private key is
        stored.
        """
        provider_certificate, private_key = self.certificates.get_assigned_certificate(
            certificate_request=CSR_ATTRIBUTES
        )
        if not provider_certificate or not private_key:
            logger.debug("Certificate or private key is not available")
            return
        combined_cert = str(provider_certificate.certificate) + "\n" + str(provider_certificate.ca)
        combined_input = combined_cert + "\n" + str(private_key)
        err, output = call_microovn_command("certificates", "set-ca",
                                "--combined",stdin=combined_input)
        if err:
            logger.error(
                "microovn certificates set-ca failed with error code {0}".format(code))
            raise RuntimeError(
                "Updating certificates failed with error code {0}".format(code))
        if "New CA certificate: Issued" in output:
            logger.info("CA certificate updated, new certificates issued")
            return True

        return False

    def _on_install(self, event: ops.InstallEvent):
        self.unit.status = ops.MaintenanceStatus("Installing microovn snap")
        while retries := 3:
            try:
                subprocess.run(
                    ["snap", "wait", "system", "seed.loaded"],
                    check=True)
                subprocess.run(
                    ["snap", "install", "microovn", "--channel", "latest/edge"],
                    check=True)
                break
            except subprocess.CalledProcessError as e:
                if retries:
                    retries -= 1
                    self.unit.status = ops.MaintenanceStatus(
                        f"Snap install failed, {retries} retries left")
                    time.sleep(1)
                    continue
                raise e

        self.unit.status = ops.MaintenanceStatus("Waiting for microovn ready")
        retries = 0
        while (code := call_microovn_command("waitready")[0]):
            retries+=1
            if retries>3:
                logger.error(
                    "microovn waitready failed with error code {0}".format(code))
                raise RuntimeError("microovn waitready failed 3 times")
            self.unit.status = ops.MaintenanceStatus(
                "Microovn waitready failed, retry {0}".format(retries))
            time.sleep(1)

    def _on_cluster_changed(self, event: ops.RelationChangedEvent):
        if self._stored.in_cluster:
            self.ovsdb_provides.update_relation_data()

if __name__ == "__main__":  # pragma: nocover
    ops.main(MicroovnCharm)
