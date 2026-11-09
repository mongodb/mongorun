import threading

import pytest

from mrun.mrun import MRunTool


class MongosStart(Exception):
    """Stop the test when initialization reaches mongos startup."""


@pytest.fixture
def sharded_tool(monkeypatch, tmp_path):
    monkeypatch.setattr(MRunTool, 'getMongoDVersion', lambda *args: '8.0.0')
    tool = MRunTool()
    tool.dir = str(tmp_path)
    tool.args = {
        'config': 1, 'sharded': ['2'], 'embeddedcsrs': False,
        'csrs': True, 'replicaset': True, 'single': False,
        'verbose': False, 'auth': False,
    }
    monkeypatch.setattr(tool, '_load_parameters', lambda: False)
    monkeypatch.setattr(tool, '_get_ssl_pymongo_options', lambda args: {})
    monkeypatch.setattr(tool, '_get_tls_pymongo_options', lambda args: {})
    monkeypatch.setattr(tool, '_get_ssl_server_args', lambda: '')
    monkeypatch.setattr(tool, '_get_tls_server_args', lambda: '')
    monkeypatch.setattr(tool, '_construct_cmdlines', lambda: None)
    monkeypatch.setattr(tool, '_store_parameters', lambda: None)
    monkeypatch.setattr(tool, '_get_shard_names', lambda args: ['shard01', 'shard02'])
    tagged = {
        ('all',): [27017, 27018, 27019, 27020],
        ('mongod', 'down'): [27018, 27019, 27020],
        ('config',): [27020],
        ('shard01',): [27018],
        ('shard02',): [27019],
        ('mongos', 'down'): [27017],
    }
    monkeypatch.setattr(tool, 'get_tagged', lambda tags: tagged[tuple(tags)])
    monkeypatch.setattr(tool, 'wait_for', lambda ports, *args, **kwargs:
                        [(port, True) for port in ports])
    return tool


def test_sharded_replsets_initialize_concurrently_before_mongos(sharded_tool, monkeypatch):
    tool = sharded_tool
    barrier = threading.Barrier(3, timeout=5)
    completed = []

    def initiate(port, name):
        # All three calls must be running at once, not merely queued.
        barrier.wait()
        completed.append((port, name))

    def start(ports, **kwargs):
        if ports == [27017]:
            assert set(completed) == {(27020, 'configRepl'),
                                      (27018, 'shard01'), (27019, 'shard02')}
            raise MongosStart()
        assert ports == [27018, 27019, 27020]

    monkeypatch.setattr(tool, '_initiate_replset', initiate)
    monkeypatch.setattr(tool, '_start_on_ports', start)
    with pytest.raises(MongosStart):
        tool.init()


@pytest.mark.parametrize('error_type', [SystemExit, ValueError])
def test_initialization_failure_prevents_mongos_start(sharded_tool, monkeypatch, error_type):
    tool = sharded_tool
    started = []

    def initiate(port, name):
        if name == 'shard01':
            raise error_type('initialization failed')

    monkeypatch.setattr(tool, '_initiate_replset', initiate)
    monkeypatch.setattr(tool, '_start_on_ports', lambda ports, **kwargs: started.append(ports))
    with pytest.raises(error_type, match='initialization failed'):
        tool.init()
    assert started == [[27018, 27019, 27020]]


@pytest.mark.parametrize('csrs', [False, True])
def test_sharded_without_shards(sharded_tool, monkeypatch, csrs):
    tool = sharded_tool
    # init enables CSRS by default on modern versions.
    tool.current_version = '3.2.0'
    tool.args['csrs'] = csrs
    monkeypatch.setattr(tool, '_get_shard_names', lambda args: [])
    initiated = []
    monkeypatch.setattr(tool, '_initiate_replset',
                        lambda port, name: initiated.append((port, name)))

    def start(ports, **kwargs):
        if ports == [27017]:
            raise MongosStart()

    monkeypatch.setattr(tool, '_start_on_ports', start)
    with pytest.raises(MongosStart):
        tool.init()
    assert initiated == ([(27020, 'configRepl')] if csrs else [])


def test_existing_environment_does_not_reinitialize(sharded_tool, monkeypatch):
    tool = sharded_tool
    tool.loaded_args = tool.args
    monkeypatch.setattr(tool, '_load_parameters', lambda: True)
    monkeypatch.setattr(tool, '_initiate_replset',
                        lambda *args: pytest.fail('unexpected initialization'))

    def start(ports, **kwargs):
        if ports == [27017]:
            raise MongosStart()

    monkeypatch.setattr(tool, '_start_on_ports', start)
    with pytest.raises(MongosStart):
        tool.init()
