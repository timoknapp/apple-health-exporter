from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.docker
def test_container_smoke() -> None:
    if os.environ.get("HEALTH_COACH_RUN_DOCKER_TESTS") != "1":
        pytest.skip("set HEALTH_COACH_RUN_DOCKER_TESTS=1 to run the Docker smoke test")
    backend = Path(__file__).resolve().parents[1]
    subprocess.run(
        [str(backend / "scripts" / "container_smoke.sh")],
        cwd=backend,
        check=True,
        timeout=600,
    )
