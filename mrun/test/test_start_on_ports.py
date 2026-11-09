import subprocess
import threading

import pytest

from mrun.mrun import MRunTool


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setattr(MRunTool, 'getMongoDVersion', lambda *args: '8.0.0')
    tool = MRunTool()
    tool.args = {'verbose': False}
    tool.startup_info = {
        str(port): f'mongod --port {port} --logpath /logs/{port}'
        for port in (27017, 27018, 27019)
    }
    return tool


@pytest.mark.parametrize('platform', ['linux', 'darwin', 'windows'])
def test_starts_concurrently_and_waits_after_all_launches(tool, monkeypatch, platform):
    barrier = threading.Barrier(3, timeout=5)
    launched = []
    waited = []

    def launch(command, **kwargs):
        barrier.wait()
        launched.append(command)

    class Process:
        def poll(self):
            return 0

    def popen(command, **kwargs):
        assert kwargs['start_new_session'] is True
        assert 'preexec_fn' not in kwargs
        launch(command, **kwargs)
        return Process()

    def wait_for(ports):
        assert len(launched) == 3
        waited.append(ports)

    monkeypatch.setattr('mrun.mrun.time.sleep', lambda seconds: None)
    monkeypatch.setattr('mrun.mrun.subprocess.check_output', launch)
    monkeypatch.setattr('mrun.mrun.subprocess.check_call', launch)
    monkeypatch.setattr('mrun.mrun.subprocess.Popen', popen)
    monkeypatch.setattr(tool, 'wait_for', wait_for)
    ports = [27017, 27018, 27019]
    with monkeypatch.context() as patch:
        patch.setattr('mrun.mrun.os.name', 'nt' if platform == 'windows' else 'posix')
        patch.setattr('mrun.mrun.sys.platform', platform)
        tool._start_on_ports(ports, wait=True)
    assert waited == [ports]


def test_start_failure_propagates_without_waiting(tool, monkeypatch, capsys):
    def launch(command, **kwargs):
        if '--port 27018' in command[0]:
            raise subprocess.CalledProcessError(1, command, output=b'launch failed')

    monkeypatch.setattr('mrun.mrun.sys.platform', 'linux')
    monkeypatch.setattr('mrun.mrun.subprocess.check_output', launch)
    monkeypatch.setattr(tool, '_get_last_error_log', lambda command: 'server log')
    monkeypatch.setattr(tool, 'wait_for', lambda ports: pytest.fail('unexpected wait'))
    with pytest.raises(SystemExit, match="can't start process, return code 1"):
        tool._start_on_ports([27017, 27018, 27019], wait=True)
    output = capsys.readouterr()
    assert 'launch failed' in output.out
    assert 'server log' in output.err


def test_darwin_start_failure_propagates_without_waiting(tool, monkeypatch):
    class Process:
        def poll(self):
            return 2

    monkeypatch.setattr('mrun.mrun.sys.platform', 'darwin')
    monkeypatch.setattr('mrun.mrun.subprocess.Popen', lambda *args, **kwargs: Process())
    monkeypatch.setattr('mrun.mrun.time.sleep', lambda seconds: None)
    monkeypatch.setattr(tool, 'wait_for', lambda ports: pytest.fail('unexpected wait'))
    with pytest.raises(SystemExit, match='Process failed to start, return code 2'):
        tool._start_on_ports([27017], wait=True)


def test_wait_false_does_not_wait(tool, monkeypatch):
    monkeypatch.setattr('mrun.mrun.sys.platform', 'linux')
    monkeypatch.setattr('mrun.mrun.subprocess.check_output', lambda *args, **kwargs: None)
    monkeypatch.setattr(tool, 'wait_for', lambda ports: pytest.fail('unexpected wait'))
    tool._start_on_ports([27017], wait=False)


def test_empty_ports_still_waits_when_requested(tool, monkeypatch):
    waited = []
    monkeypatch.setattr(tool, 'wait_for', lambda ports: waited.append(ports))
    tool._start_on_ports([], wait=True)
    assert waited == [[]]
