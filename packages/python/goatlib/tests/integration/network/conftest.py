from pathlib import Path

import pytest
from goatlib.analysis.network.network_processor import InMemoryNetworkProcessor


@pytest.fixture
def processor(network_file: Path) -> InMemoryNetworkProcessor:
    """A pytest fixture that yields a processor within a context manager."""
    with InMemoryNetworkProcessor(str(network_file)) as proc:
        yield proc
    # Cleanup is handled automatically as the 'with' block exits
