import threading
from types import SimpleNamespace

import pytest

from mrun.mrun import MRunTool


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setattr(MRunTool, 'getMongoDVersion', lambda *args: '8.0.0')
    tool = MRunTool(test=True)
    tool.unknown_args = []
    tool.args = {
        'command': 'init',
        'sharded': ['2'],
        'replicaset': True,
        'single': False,
        'nodes': 2,
        'arbiter': False,
        'config': 1,
        'mongos': 1,
        'port': 27017,
    }
    # Replica-set role discovery and the separate config-server ping should not
    # contact a real server in these tests.
    client = SimpleNamespace(primary=None, secondaries=set(), arbiters=set(),
                             admin=SimpleNamespace(command=lambda *args: None))
    monkeypatch.setattr(tool, 'client', lambda *args, **kwargs: client)
    return tool


def test_discover_probes_concurrently_and_preserves_tags(tool, monkeypatch):
    ports = list(range(27017, 27023))
    barrier = threading.Barrier(len(ports), timeout=5)
    running_ports = {27017, 27019, 27022}

    def probe(port):
        # A sequential implementation cannot reach the barrier.
        barrier.wait()
        return port in running_ports

    monkeypatch.setattr(tool, 'is_running', probe)
    tool.discover()

    assert tool.cluster_running == {port: port in running_ports for port in ports}
    assert tool.cluster_tags['all'] == ports
    assert tool.cluster_tags['running'] == [27017, 27019, 27022]
    assert tool.cluster_tags['down'] == [27018, 27020, 27021]
    assert tool.get_tagged(['mongod', 'down']) == {27018, 27020, 27021}
    assert tool.get_tagged(['shard01']) == {27018, 27019}
    assert tool.get_tagged(['shard02']) == {27020, 27021}
    assert tool.get_tagged(['config']) == {27022}
    assert all(tool.cluster_tags[str(port)] == [port] for port in ports)


def test_discover_propagates_probe_errors(tool, monkeypatch):
    def probe(port):
        if port == 27019:
            raise RuntimeError('probe failed')
        return False

    monkeypatch.setattr(tool, 'is_running', probe)
    with pytest.raises(RuntimeError, match='probe failed'):
        tool.discover()


def test_discover_uses_saved_topology_for_other_commands(tool, monkeypatch):
    saved_args = tool.args.copy()
    tool.args = {'command': 'list'}

    def load_parameters():
        tool.loaded_args = saved_args
        return True

    monkeypatch.setattr(tool, '_load_parameters', load_parameters)
    monkeypatch.setattr(tool, 'is_running', lambda port: port == 27018)
    tool.discover()

    assert tool.get_tagged(['running', 'shard01']) == {27018}
    assert tool.get_tagged(['down']) == {27017, 27019, 27020, 27021, 27022}
