import pytest
from backend.app.microscopic.engine import Engine
from backend.app.microscopic.network import Network


@pytest.fixture(scope='session')
def network():
    return Network()


@pytest.fixture
def engine(network):
    return Engine(network)
