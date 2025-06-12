import time

import jubilant
import pytest


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
