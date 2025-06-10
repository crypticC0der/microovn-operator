import os
import time

import jubilant
import pytest

microovn_charm_path = "./" + os.environ.get("MICROOVN_CHARM_PATH")
if microovn_charm_path is None:
    raise EnvironmentError("MICROOVN_CHARM_PATH is not set")

token_distributor_charm_path = "./" + os.environ.get("TOKEN_DISTRIBUTOR_CHARM_PATH")
if token_distributor_charm_path is None:
    raise EnvironmentError("TOKEN_DISTRIBUTOR_CHARM_PATH is not set")

@pytest.fixture
def juju():
    # NOTE workaround for juju add-model leading to transaction aborted
    #      error when running in parallel (LP: #2053270).
    while (retry_count := 3):
        try:
            with jubilant.temp_model() as juju:
                yield juju
                break
        except (jubilant._juju.CLIError) as e:
            if retry_count and "transaction aborted" in e.stderr:
                retry_count -= 1
                time.sleep(0.1)
                continue
            raise e

def test_deploy(juju: jubilant.Juju):
    juju.deploy(microovn_charm_path)
    juju.wait(jubilant.all_active)
    juju.exec("microovn status", unit="microovn/0")
    
def test_integrate(juju: jubilant.Juju):
    juju.deploy(microovn_charm_path)
    juju.add_unit("microovn")
    juju.deploy(token_distributor_charm_path)
    juju.integrate("microovn","microcluster-token-distributor")
    juju.wait(jubilant.all_active)
    juju.exec("microovn status", unit="microovn/1")
    
def test_integrate_post_start(juju: jubilant.Juju):
    juju.deploy(microovn_charm_path)
    juju.deploy(token_distributor_charm_path)
    juju.wait(jubilant.all_active)
    juju.integrate("microovn","microcluster-token-distributor")
    juju.add_unit("microovn")
    juju.wait(jubilant.all_active)
    juju.exec("microovn status", unit="microovn/1")
    
def test_token_distributor_down(juju: jubilant.Juju):
    juju.deploy(microovn_charm_path)
    juju.deploy(token_distributor_charm_path)
    juju.integrate("microovn","microcluster-token-distributor")
    juju.add_unit("microovn")
    juju.wait(jubilant.all_active)
    juju.remove_unit("microcluster-token-distributor/0")
    juju.add_unit("microcluster-token-distributor")
    juju.add_unit("microovn")
    juju.wait(jubilant.all_active)
    juju.exec("microovn status", unit="microovn/2")
