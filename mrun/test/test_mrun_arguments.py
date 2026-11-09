import json
import os
import shlex
import subprocess
import sys
import time

import mslex
import pytest

from mrun.mrun import MRunTool


@pytest.fixture
def tool(monkeypatch, tmp_path):
    """Build command lines without launching or contacting MongoDB."""
    # Windows parsing tests also run on POSIX, where mslex is not imported.
    monkeypatch.setattr('mrun.mrun.mslex', mslex, raising=False)
    monkeypatch.setattr(MRunTool, 'getMongoDVersion', lambda *args: '8.0.0')
    monkeypatch.setattr('mrun.mrun.check_mongo_server_output',
                        lambda *args: b'  --setParameter arg\n  --port arg\n'
                        b'  --logappend\n  --quiet\n  --keyFile arg\n'
                        b'  --storageEngine arg\n  --logpath arg\n'
                        b'  -f [ --config ] arg\n')
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


@pytest.mark.parametrize('scope', ['shared', 'mongod', 'mongos'])
@pytest.mark.parametrize('option', [
    ['-f', 'server.conf'], ['-f=server.conf'],
    ['--config', 'server.conf'], ['--config=server.conf'],
])
def test_config_file_requires_auth_restart(tool, scope, option):
    tool.args = {
        'sharded': ['1'], 'auth': True, 'initial-user': True,
        'auth_db': 'admin', 'auth_role_docs': False,
        'auth_roles': ['clusterAdmin'],
    }
    tool.unknown_args = option if scope == 'shared' else []
    if scope != 'shared':
        tool.args['%s_arg' % scope] = [option]

    assert tool._requires_restart_for_auth(True)
    assert not tool._requires_restart_for_auth(False)
    tool.args['auth'] = False
    assert not tool._requires_restart_for_auth(True)
    tool.args['auth'] = True
    tool.args['sharded'] = False
    assert not tool._requires_restart_for_auth(True)


@pytest.mark.parametrize('option', ['--configsvr', '--configdb', '-foo'])
def test_unrelated_options_do_not_require_auth_restart(tool, option):
    tool.args = {
        'sharded': ['1'], 'auth': True, 'initial-user': True,
        'auth_db': 'admin', 'auth_role_docs': False,
        'auth_roles': ['clusterAdmin'], 'config': 1,
    }
    tool.unknown_args = [option, 'server.conf']
    assert not tool._requires_restart_for_auth(True)


@pytest.mark.parametrize('scope, option', [
    ('shared', '-f server.conf'), ('shared', '-f=server.conf'),
    ('mongod', '-f server.conf'), ('mongos', '-f server.conf'),
    ('mongod', '-f=server.conf'), ('mongos', '-f=server.conf'),
    ('mongod', '--config server.conf'), ('mongos', '--config server.conf'),
    ('mongod', '--config=server.conf'), ('mongos', '--config=server.conf'),
])
def test_parsed_config_file_requires_auth_restart(tool, scope, option):
    arguments = (shlex.split(option) if scope == 'shared' else
                 ['--%s-arg=%s' % (scope, option)])
    init(tool, '--auth', *arguments)
    assert tool._requires_restart_for_auth(True)
    if option.startswith('-f'):
        for command in commands(tool):
            applies = scope == 'shared' or command[0] == scope
            assert (shlex.split(option)[0] in command) == applies


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
        parameter = 'enableLocalhostAuthBypass=0'
        if command[0] == 'mongos':
            assert command[command.index(parameter) - 1] == '--setParameter'
        else:
            assert parameter not in command


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


@pytest.mark.parametrize('key_option', [
    ['--keyFile=/custom/key'],
    ['--keyFile=/custom/key file'],
    ['--keyFile', '/custom/key file'],
])
def test_bootstrap_removes_scoped_keyfiles(tool, monkeypatch, key_option):
    init(tool, '--auth', '--mongod-arg', shlex.join(key_option + ['--logappend']),
         '--mongos-arg', shlex.join(key_option + ['--quiet']))
    original = tool.startup_info.copy()
    launched = []
    monkeypatch.setattr('mrun.mrun.sys.platform', 'linux')
    monkeypatch.setattr('mrun.mrun.subprocess.check_output',
                        lambda command, **kwargs: launched.append(command[0]))
    waited = []
    monkeypatch.setattr(tool, 'wait_for', lambda ports: waited.append(ports))
    ports = list(map(int, tool.startup_info))

    tool._start_on_ports(ports, wait=True, override_auth=True)

    assert len(launched) == 3
    assert waited == [ports]
    assert tool.startup_info == original
    for command, original_command in zip(launched, original.values()):
        expected = shlex.split(original_command)
        if key_option[0] == '--keyFile':
            index = expected.index('--keyFile')
            del expected[index:index + 2]
        else:
            expected.remove(key_option[0])
        assert shlex.split(command) == expected


def test_bootstrap_removes_generated_keyfile(tool, monkeypatch):
    init(tool, '--auth')
    launched = []
    monkeypatch.setattr('mrun.mrun.sys.platform', 'linux')
    monkeypatch.setattr('mrun.mrun.subprocess.check_output',
                        lambda command, **kwargs: launched.append(command[0]))
    tool._start_on_ports(list(map(int, tool.startup_info)), override_auth=True)
    for command in launched:
        assert '--keyFile' not in shlex.split(command)
        assert tool.dir + '/keyfile' not in shlex.split(command)


def test_bootstrap_removes_all_keyfiles_and_preserves_other_arguments(tool):
    arguments = ['/path to/mongod', '--setParameter',
                 "message=don't remove --keyFile=/other",
                 '--keyFile', '/custom/key file', '--keyFile=/custom/key', '--fork']
    result = tool._without_keyfile(shlex.join(arguments))
    assert shlex.split(result) == arguments[:3] + ['--fork']


def test_bootstrap_preserves_windows_paths(tool, monkeypatch):
    command = (r'start /b "" "C:\Program Files\MongoDB\mongod.exe" '
               r'--dbpath "C:\data path\db" --keyFile "C:\key path\key" --fork')
    with monkeypatch.context() as patch:
        patch.setattr('mrun.mrun.os.name', 'nt')
        result = tool._without_keyfile(command)
    assert result == (r'start /b "" "C:\Program Files\MongoDB\mongod.exe" '
                      r'--dbpath "C:\data path\db" --fork')


@pytest.mark.parametrize('inline', [False, True])
@pytest.mark.parametrize('path', [r'C:\R&D\key', r'C:\R&D\key path',
                                  'C:\\R&D\\'])
def test_windows_filter_quotes_shell_metacharacters(tool, monkeypatch, inline, path):
    option = ['--keyFile=' + path] if inline else ['--keyFile', path]
    with monkeypatch.context() as patch:
        patch.setattr('mrun.mrun.os.name', 'nt')
        result = tool._filter_valid_arguments(option)
    assert mslex.split(result, ucrt=True) == option


@pytest.mark.parametrize('inline', [False, True])
@pytest.mark.parametrize('key_option', [[], ['--keyFile', r'C:\R&D\key'],
                                      ['--keyFile=C:\\R&D\\key'],
                                      ['--keyFile', 'C:\\key path\\'],
                                      ['--keyFile', 'first', '--keyFile=second']])
def test_windows_bootstrap_preserves_serialized_json(tool, monkeypatch, inline, key_option):
    value = 'logComponentVerbosity={"command": {"verbosity": 2}}'
    option = ['--setParameter=' + value] if inline else ['--setParameter', value]
    prefix = r'start /b "" "C:\Program Files\MongoDB\mongod.exe"  '
    parameter = subprocess.list2cmdline(option)
    command = prefix + parameter + ' ' + mslex.join(key_option) + ' --quiet'
    with monkeypatch.context() as patch:
        patch.setattr('mrun.mrun.os.name', 'nt')
        result = tool._without_keyfile(command)
    assert mslex.split(result, ucrt=True) == [
        'start', '/b', '', r'C:\Program Files\MongoDB\mongod.exe', *option, '--quiet']
    if not key_option:
        assert result == command


def test_windows_bootstrap_does_not_remove_keyfile_text_in_values(tool, monkeypatch):
    arguments = ['--setParameter', 'message={"text": "keep --keyFile=/other"}',
                 '--keyFile', r'C:\key path\key', '--quiet']
    command = subprocess.list2cmdline(arguments)
    with monkeypatch.context() as patch:
        patch.setattr('mrun.mrun.os.name', 'nt')
        result = tool._without_keyfile(command)
    assert mslex.split(result, ucrt=True) == arguments[:2] + ['--quiet']


def test_windows_bootstrap_preserves_shell_escaped_values(tool, monkeypatch):
    arguments = ['start', '/b', '', r'C:\Program Files\MongoDB\mongod.exe',
                 '--logpath', r'C:\R&D\log', '--setParameter',
                 'message={"text": "R&D %PATH% !PATH!"}',
                 '--keyFile', r'C:\R&D\key', '--keyFile=C:\\other key\\', '--quiet']
    command = mslex.join(arguments)
    with monkeypatch.context() as patch:
        patch.setattr('mrun.mrun.os.name', 'nt')
        result = tool._without_keyfile(command)
    assert mslex.split(result, ucrt=True) == arguments[:8] + ['--quiet']


@pytest.mark.parametrize('auth', [False, True])
def test_windows_bootstrap_launch_preserves_arguments(tool, monkeypatch, auth):
    tool.args = {'verbose': False}
    value = 'logComponentVerbosity={"command": {"verbosity": 2}}'
    options = ['--setParameter', value]
    if auth:
        options += ['--keyFile', r'C:\R&D\key']
    prefix = r'start /b "" "C:\Program Files\MongoDB\mongod.exe" '
    tool.startup_info = {'27017': prefix + mslex.join(options)}
    original = tool.startup_info.copy()
    launched = []
    monkeypatch.setattr('mrun.mrun.subprocess.check_call',
                        lambda command, **kwargs: launched.append((command, kwargs)))
    monkeypatch.setattr('mrun.mrun.time.sleep', lambda seconds: None)
    with monkeypatch.context() as patch:
        patch.setattr('mrun.mrun.os.name', 'nt')
        tool._start_on_ports([27017], override_auth=True)
    command, kwargs = launched[0]
    assert kwargs == {'shell': True}
    assert mslex.split(command, ucrt=True) == [
        'start', '/b', '', r'C:\Program Files\MongoDB\mongod.exe', *options[:2]]
    assert tool.startup_info == original


@pytest.mark.parametrize('metacharacter', list('&|<>^()%!'))
def test_windows_json_shell_metacharacters_are_escaped(tool, monkeypatch, metacharacter):
    argument = 'message={"text":"R%sD"}' % metacharacter
    with monkeypatch.context() as patch:
        patch.setattr('mrun.mrun.os.name', 'nt')
        result = tool._filter_valid_arguments(['--setParameter', argument])
    assert mslex.split(result, ucrt=True) == ['--setParameter', argument]


@pytest.mark.skipif(os.name != 'nt', reason='Requires cmd.exe and Windows argument parsing')
@pytest.mark.parametrize('bootstrap', [False, True])
def test_windows_shell_argument_roundtrip(tmp_path, bootstrap):
    """Check the actual start /b shell and C-runtime parsing, without MongoDB."""
    output = tmp_path / 'argv.json'
    arguments = ['--keyFile', r'C:\R&D\key', '--keyFile=C:\\R&D\\key',
                 '--setParameter', 'logComponentVerbosity={"command": {"verbosity": 2}}',
                 'message={"text":"R&D|<>()^"}', 'C:\\R&D\\', '', '%PATH%', '!PATH!']
    script = ('import json, pathlib, sys; '
              'pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))')
    command = 'start /b "" ' + mslex.join(
        [sys.executable, '-c', script, str(output), *arguments])
    if bootstrap:
        command = MRunTool(test=True)._without_keyfile(command)
        arguments = arguments[3:]
    subprocess.check_call(command, shell=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            received = json.loads(output.read_text())
            break
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.05)
    else:
        pytest.fail('Windows argument capture process did not write its result')
    assert received == arguments


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
