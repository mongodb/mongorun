import json
import os
import shlex

import pytest

from mrun.mrun import MRunTool


@pytest.fixture
def tool(monkeypatch, tmp_path):
    """Build command lines without launching or contacting MongoDB."""
    monkeypatch.setattr(MRunTool, 'getMongoDVersion', lambda *args: '8.0.0')
    monkeypatch.setattr('mrun.mrun.check_mongo_server_output',
                        lambda *args: b'  --setParameter arg\n  --port arg\n'
                        b'  --logappend\n  --quiet\n  --keyFile arg\n'
                        b'  --storageEngine arg\n  --logpath arg\n')
    tool = MRunTool(test=True)
    tool.dir = str(tmp_path)
    monkeypatch.setattr(tool, 'discover', lambda: None)
    return tool


def init(tool, *arguments):
    args = ['init', '--replicaset', '--sharded', '1', '--nodes', '1',
            '--config', '1', '--dir', tool.dir, *arguments]
    tool.run(shlex.join(args))


def commands(tool):
    info = getattr(tool, 'started_info', tool.startup_info)
    return [shlex.split(command) for command in info.values()]


@pytest.mark.parametrize('argv', [False, True])
def test_scoped_bundles(tool, monkeypatch, argv):
    """Support quoted bundles in both CLI argv and string-based invocations."""
    arguments = ['init', '--replicaset', '--sharded', '1', '--nodes', '1',
                 '--config', '1', '--dir', tool.dir,
                 '--setParameter', 'enableTestCommands=1',
                 '--mongod-arg', '--setParameter shutdownTimeoutMillisForSignaledShutdown=1000 '
                 '--logappend',
                 '--mongos-arg',
                 '--setParameter mongosShutdownTimeoutMillisForSignaledShutdown=2000 '
                 '--quiet']
    if argv:
        monkeypatch.setattr('sys.argv', ['mrun', *arguments])
        tool.run()
    else:
        tool.run(shlex.join(arguments))

    assert len(commands(tool)) == 3
    for command in commands(tool):
        assert 'enableTestCommands=1' in command
        if command[0] == 'mongod':
            assert 'shutdownTimeoutMillisForSignaledShutdown=1000' in command
            assert '--logappend' in command
            assert 'mongosShutdownTimeoutMillisForSignaledShutdown=2000' not in command
            assert '--quiet' not in command
        else:
            assert command[0] == 'mongos'
            assert 'mongosShutdownTimeoutMillisForSignaledShutdown=2000' in command
            assert '--quiet' in command
            assert 'shutdownTimeoutMillisForSignaledShutdown=1000' not in command
            assert '--logappend' not in command


def test_repeated_bundles_and_shared_arguments(tool):
    init(tool, '--setParameter', 'shared=1',
         '--mongod-arg', '--setParameter first=1',
         '--mongod-arg=--setParameter=second=2',
         '--mongos-arg=--quiet')
    for command in commands(tool):
        assert 'shared=1' in command
        if command[0] == 'mongod':
            assert command.index('first=1') < command.index('--setParameter=second=2')
            assert '--quiet' not in command
        else:
            assert 'first=1' not in command
            assert '--setParameter=second=2' not in command
            assert '--quiet' in command


@pytest.mark.parametrize('topology', ['--single', '--replicaset'])
def test_non_sharded_bundles(tool, topology):
    args = ['init', topology, '--nodes', '1', '--dir', tool.dir,
            '--mongod-arg', '--setParameter serverOnly=1',
            '--mongos-arg', '--setParameter routerOnly=2']
    tool.run(shlex.join(args))
    assert len(commands(tool)) == 1
    command = commands(tool)[0]
    assert command[0] == 'mongod'
    assert 'serverOnly=1' in command
    assert 'routerOnly=2' not in command


@pytest.mark.parametrize('inline', [False, True])
def test_bundle_preserves_quoted_values(tool, inline):
    value = 'logComponentVerbosity={"command": {"verbosity": 2}}'
    option = ['--setParameter=' + value] if inline else ['--setParameter', value]
    init(tool, '--mongod-arg', shlex.join(option + ['--logpath', '/path with spaces/log']))
    for command in commands(tool):
        if command[0] == 'mongod':
            assert option[-1] in command
            assert '/path with spaces/log' in command
        else:
            assert option[-1] not in command
            assert '/path with spaces/log' not in command


def test_scoped_arguments_still_use_binary_validation(tool):
    init(tool, '--mongod-arg', '--storageEngine wiredTiger --unknown-option ignored '
         '--setParameter retained=1')
    for command in commands(tool):
        assert '--unknown-option' not in command
        assert 'ignored' not in command
        if command[0] == 'mongod':
            assert 'retained=1' in command
            assert ('--storageEngine' in command) == ('--configsvr' not in command)
        else:
            assert 'retained=1' not in command
            assert '--storageEngine' not in command


def test_unscoped_parameters_remain_shared(tool):
    init(tool, '--setParameter', 'shutdownTimeoutMillisForSignaledShutdown=1000',
         '--setParameter=mongosShutdownTimeoutMillisForSignaledShutdown=2000')
    for command in commands(tool):
        assert 'shutdownTimeoutMillisForSignaledShutdown=1000' in command
        assert '--setParameter=mongosShutdownTimeoutMillisForSignaledShutdown=2000' in command


def prepare_start(tool, monkeypatch):
    """Exercise real start/restart logic with process operations stubbed out."""
    def discover():
        if tool.args['command'] != 'init':
            tool._load_parameters()

    def start_on_ports(*args, **kwargs):
        tool.started_info = tool.startup_info.copy()

    monkeypatch.setattr(tool, 'discover', discover)
    monkeypatch.setattr(tool, 'get_tagged', lambda tags: {27017, 27018, 27019})
    monkeypatch.setattr(tool, '_get_ports_from_args', lambda *args: {27017, 27018, 27019})
    monkeypatch.setattr(tool, '_start_on_ports', start_on_ports)
    monkeypatch.setattr(tool, 'wait_for', lambda *args, **kwargs: None)
    monkeypatch.setattr(tool, '_get_processes', lambda: {})
    monkeypatch.setattr(tool, 'stop', lambda: None)


@pytest.mark.parametrize('command', ['start', 'restart'])
@pytest.mark.parametrize('override', [None, '--setParameter replacement=3', ''])
def test_saved_bundles_and_temporary_overrides(tool, monkeypatch, command, override):
    init(tool, '--setParameter', 'shared=1',
         '--mongod-arg', '--setParameter original=1',
         '--mongos-arg', '--setParameter router=2')
    startup_file = tool.dir + '/.mrun_startup'
    with open(startup_file) as stream:
        saved = json.load(stream)
    assert saved['parsed_args']['mongod_arg'] == [['--setParameter', 'original=1']]
    prepare_start(tool, monkeypatch)
    arguments = [command, '--dir', tool.dir]
    if override is not None:
        arguments += ['--mongod-arg', override]
    tool.run(shlex.join(arguments))

    for line in commands(tool):
        assert 'shared=1' in line
        if line[0] == 'mongod':
            assert ('original=1' in line) == (override is None)
            assert ('replacement=3' in line) == bool(override)
            assert 'router=2' not in line
        else:
            assert 'router=2' in line
            assert 'original=1' not in line
            assert 'replacement=3' not in line
    with open(startup_file) as stream:
        assert json.load(stream) == saved


def test_legacy_metadata_and_binarypath_rebuild(tool, monkeypatch):
    init(tool, '--setParameter', 'shared=1')
    startup_file = tool.dir + '/.mrun_startup'
    with open(startup_file) as stream:
        saved = json.load(stream)
    del saved['parsed_args']['mongod_arg']
    del saved['parsed_args']['mongos_arg']
    with open(startup_file, 'w') as stream:
        json.dump(saved, stream)
    prepare_start(tool, monkeypatch)
    tool.run(shlex.join(['start', '--dir', tool.dir, '--binarypath', '/custom/bin']))
    for command in commands(tool):
        assert command[0] in ['/custom/bin/mongod', '/custom/bin/mongos']
        assert 'shared=1' in command


@pytest.mark.parametrize('inline', [False, True])
def test_scoped_authentication_options(tool, inline):
    key_option = (['--keyFile=/custom/key file'] if inline else
                  ['--keyFile', '/custom/key file'])
    init(tool, '--auth', '--mongod-arg', shlex.join(key_option),
         '--mongos-arg', shlex.join(key_option +
                                    ['--setParameter', 'enableLocalhostAuthBypass=0']))
    for command in commands(tool):
        assert sum(arg.split('=', 1)[0] == '--keyFile' for arg in command) == 1
        assert key_option[-1] in command
    assert tool._localhost_auth_bypass_disabled()


@pytest.mark.parametrize('binary', ['mongod', 'mongos'])
@pytest.mark.parametrize('inline', [False, True])
def test_one_sided_keyfile_is_rejected(tool, binary, inline):
    key_option = (['--keyFile=/custom/key file'] if inline else
                  ['--keyFile', '/custom/key file'])
    with pytest.raises(SystemExit, match='must use the same --keyFile path'):
        init(tool, '--auth', '--%s-arg' % binary, shlex.join(key_option))
    assert not os.path.exists(os.path.join(tool.dir, '.mrun_startup'))


@pytest.mark.parametrize('binary', ['mongod', 'mongos'])
def test_shared_and_matching_scoped_keyfile(tool, binary):
    init(tool, '--auth', '--keyFile', '/custom/key',
         '--%s-arg=--keyFile=/custom/key' % binary)
    assert tool._get_key_file_from_args() == '/custom/key'


@pytest.mark.parametrize('arguments', [
    ('--mongod-arg=--keyFile=/first', '--mongos-arg=--keyFile=/second'),
    ('--keyFile=/first', '--mongod-arg=--keyFile=/second'),
    ('--keyFile=/first', '--mongos-arg=--keyFile=/second'),
])
def test_conflicting_keyfiles_are_rejected(tool, arguments):
    with pytest.raises(SystemExit, match='must use the same --keyFile path'):
        init(tool, *arguments)


@pytest.mark.parametrize('arguments', [
    ('--keyFile',),
    ('--keyFile=',),
    ('--mongod-arg=--keyFile',),
    ('--mongos-arg=--keyFile=',),
])
def test_keyfile_requires_a_path(tool, arguments):
    with pytest.raises(SystemExit, match='--keyFile requires a path'):
        init(tool, *arguments)


def test_generated_keyfile_is_shared(tool):
    init(tool, '--auth')
    for command in commands(tool):
        assert command[command.index('--keyFile') + 1] == tool.dir + '/keyfile'


@pytest.mark.parametrize('topology', ['--single', '--replicaset'])
def test_one_sided_keyfile_is_rejected_for_non_sharded(tool, topology):
    with pytest.raises(SystemExit, match='must use the same --keyFile path'):
        tool.run(shlex.join(['init', topology, '--nodes', '1', '--dir', tool.dir,
                             '--mongod-arg=--keyFile=/custom/key']))


@pytest.mark.parametrize('command', ['start', 'restart'])
def test_invalid_keyfile_override_is_rejected_before_stopping(tool, monkeypatch, command):
    init(tool, '--auth', '--keyFile=/shared/key')
    prepare_start(tool, monkeypatch)
    stopped = []
    monkeypatch.setattr(tool, 'stop', lambda: stopped.append(True))
    with pytest.raises(SystemExit, match='must use the same --keyFile path'):
        tool.run(shlex.join([command, '--dir', tool.dir,
                             '--mongod-arg=--keyFile=/different/key']))
    assert not stopped
    assert not hasattr(tool, 'started_info')


@pytest.mark.parametrize('command', ['start', 'restart'])
def test_matching_keyfile_overrides_are_accepted(tool, monkeypatch, command):
    init(tool, '--auth')
    prepare_start(tool, monkeypatch)
    tool.run(shlex.join([command, '--dir', tool.dir,
                         '--mongod-arg=--keyFile=/custom/key',
                         '--mongos-arg=--keyFile=/custom/key']))
    for line in commands(tool):
        assert '--keyFile=/custom/key' in line
        assert tool.dir + '/keyfile' not in line


@pytest.mark.parametrize('command', ['start', 'restart'])
def test_invalid_saved_keyfile_is_rejected(tool, monkeypatch, command):
    init(tool, '--auth')
    startup_file = os.path.join(tool.dir, '.mrun_startup')
    with open(startup_file) as stream:
        saved = json.load(stream)
    saved['parsed_args']['mongod_arg'] = [['--keyFile=/custom/key']]
    with open(startup_file, 'w') as stream:
        json.dump(saved, stream)
    prepare_start(tool, monkeypatch)
    stopped = []
    monkeypatch.setattr(tool, 'stop', lambda: stopped.append(True))
    with pytest.raises(SystemExit, match='must use the same --keyFile path'):
        tool.run(shlex.join([command, '--dir', tool.dir]))
    assert not stopped


def test_malformed_bundle_is_rejected(tool, capsys):
    with pytest.raises(SystemExit):
        init(tool, '--mongod-arg', '--setParameter "unterminated')
    assert '--mongod-arg' in capsys.readouterr().err
