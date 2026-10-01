import pytest
from backend.engine import Engine
from backend.network import Network


@pytest.fixture(scope='session')
def network():
    return Network()


@pytest.fixture
def engine(network):
    return Engine(network)
