import inspect
import json
import os
import shutil
import sys
from packaging import version

import pytest

from mrun.mrun import MRunTool

class TestMRun(object):

    # Setup & teardown functions

    def setup_method(self):
        self.base_dir = 'data_test_mrun'
        self.tool = MRunTool(test=True)
        self.tool.args = {'verbose': False}
        self.mongod_version = self.tool.getMongoDVersion()

    def teardown_method(self):
        self.tool = None
        if os.path.exists(self.base_dir):
            shutil.rmtree(self.base_dir)

    # Helper functions

    @pytest.mark.xfail(raises=SystemExit)
    def run_tool(self, arg_str):
        """Wrapper to call self.tool.run() with or without auth."""
        # name data directory according to test method name
        caller = inspect.stack()[1][3]
        self.data_dir = os.path.join(self.base_dir, caller)

        # add data directory to arguments for all commands
        arg_str += ' --dir %s' % self.data_dir

        self.tool.run(arg_str)

    def read_config(self):
        """Read the generated mrun startup file, get the command lines."""
        fp = open(self.data_dir + '/.mrun_startup', 'r')
        cfg = json.load(fp)
        cmd = [cfg['startup_info'][x] for x in cfg['startup_info'].keys()]
        return cfg, cmd

    def test_auth_client_adds_credentials_when_auth_enabled(self):
        calls = []
        self.tool.args = {
            'auth': True,
            'username': 'test-user',
            'password': 'test-password',
            'auth_db': 'test-auth-db',
        }
        self.tool.client = lambda host, **kwargs: calls.append((host, kwargs))

        self.tool.auth_client('localhost:27017', readPreference='primary')

        assert calls == [(
            'localhost:27017',
            {
                'username': 'test-user',
                'password': 'test-password',
                'authSource': 'test-auth-db',
                'readPreference': 'primary',
            },
        )]

    def test_auth_client_does_not_add_credentials_when_auth_disabled(self):
        calls = []
        self.tool.args = {'auth': False}
        self.tool.client = lambda host, **kwargs: calls.append((host, kwargs))

        self.tool.auth_client('localhost:27017', directConnection=True)

        assert calls == [('localhost:27017', {'directConnection': True})]

    def cmdlist_filter(self, cmdlist):
        """Filter command lines to contain only [mongod|mongos] --parameter."""
        # NOTE: Command "mongo was intentionally written with a leading quote
        res = map(lambda cmd: set([param for param in cmd.split()
                                   if param.startswith('"mongo') or
                                   param.startswith('--')]),
                  [cmd for cmd in cmdlist if cmd.startswith('"mongod') and
                   '--configsvr' in cmd] +
                  [cmd for cmd in cmdlist if cmd.startswith('"mongod') and
                   '--shardsvr' in cmd] +
                  [cmd for cmd in cmdlist if cmd.startswith('"mongod') and
                   '--configsvr' not in cmd and '--shardsvr' not in cmd] +
                  [cmd for cmd in cmdlist if cmd.startswith('"mongos')]
                  )
        return(res)

    def cmdlist_print(self):
        """Print the generated command lines to console."""
        cfg, cmdlist = self.read_config()
        print('\n')
        print(cmdlist)
        print('\n')
        cmdset = self.cmdlist_filter(cmdlist)
        for cmd in cmdset:
            print(cmd)

    def cmdlist_assert(self, cmdlisttest):
        """Assert helper for command lines."""
        cfg, cmdlist = self.read_config()
        cmdset = [set(x) for x in self.cmdlist_filter(cmdlist)]
        assert len(cmdlist) == len(cmdlisttest)
        for observed, expected in zip(cmdset, cmdlisttest):
            # if mongod, account for some extra observed parameter
            # (e.g. wiredTigerCacheSizeGB)
            if '"mongod"' in expected:
                assert expected.issubset(observed)
                assert expected.intersection(observed) == expected
            # if mongos, expected must match observed
            else:
                assert expected == observed

    @pytest.mark.xfail(raises=IOError)
    def raises_ioerror(self):
        """Check for IOError exceptions"""
        self.read_config()

    @pytest.fixture(autouse=True)
    def check_supported_version(self):
        """Skip unless minimum supported version"""
        if version.parse(self.mongod_version) <= version.parse('4.2.0'):
            raise pytest.skip('MongoDB version is older than 4.2.0')

    # Tests
    def test_single(self):
        """mrun should start 1 node."""
        self.run_tool('init --single')
        cmdlist = [
            set(['"mongod"', '--dbpath', '--logpath', '--port'])
            ]
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_single_storage(self):
        """mrun should start 1 node with specified storage."""
        self.run_tool('init --single --storageEngine wiredTiger')
        cmdlist = [
            set(['"mongod"', '--dbpath', '--logpath', '--port',
                 '--storageEngine'])
            ]
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_replicaset_3(self):
        """mrun should start 3 nodes replicaset."""
        self.run_tool('init --replicaset')
        cmdlist = (
            [set(['"mongod"', '--replSet', '--dbpath', '--logpath', '--port',
                  ])] * 3
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_replicaset_7(self):
        """mrun should start 7 nodes replicaset."""
        self.run_tool('init --replicaset --nodes 7')
        cmdlist = (
            [set(['"mongod"', '--replSet', '--dbpath', '--logpath', '--port',
                  ])] * 7
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_replicaset_6_1(self):
        """mrun should start 6 nodes + 1 arbiter replicaset."""
        self.run_tool('init --replicaset --nodes 6 --arbiter')
        cmdlist = (
            [set(['"mongod"', '--replSet', '--dbpath', '--logpath', '--port',
                  ])] * 7
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_sharded_replicaset_csrs_1(self):
        """
        mrun should start 1 replicaset config, 2 shards (3 nodes each),
        1 mongos (CSRS).
        """
        self.run_tool('init --sharded 2 --replicaset --config 1 --csrs')
        cmdlist = (
            [set(['"mongod"', '--replSet', '--dbpath', '--logpath', '--port',
                  '--configsvr'])] +
            [set(['"mongod"', '--replSet', '--dbpath', '--logpath', '--port',
                  '--shardsvr'])] * 6 +
            [set(['"mongos"', '--logpath', '--port', '--configdb'])]
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_sharded_replicaset_csrs_2(self):
        """
        mrun should start 2 replicaset config, 2 shards (3 nodes each),
        1 mongos (CSRS).
        """
        self.run_tool('init --sharded 2 --replicaset --config 2 --csrs')
        cmdlist = (
            [set(['"mongod"', '--replSet', '--dbpath', '--logpath', '--port',
                  '--configsvr'])] * 2 +
            [set(['"mongod"', '--replSet', '--dbpath', '--logpath', '--port',
                  '--shardsvr'])] * 6 +
            [set(['"mongos"', '--logpath', '--port', '--configdb'])]
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_sharded_replicaset_csrs_3(self):
        """
        mrun should start 3 replicaset config, 2 shards (3 nodes each),
        1 mongos (CSRS).
        """
        self.run_tool('init --sharded 2 --replicaset --config 3 --csrs')
        cmdlist = (
            [set(['"mongod"', '--replSet', '--dbpath', '--logpath', '--port',
                  '--configsvr'])] * 3 +
            [set(['"mongod"', '--replSet', '--dbpath', '--logpath', '--port',
                  '--shardsvr'])] * 6 +
            [set(['"mongos"', '--logpath', '--port', '--configdb'])]
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_sharded_replicaset_csrs_4(self):
        """
        mrun should start 4 replicaset config, 2 shards (3 nodes each),
        1 mongos (CSRS).
        """
        self.run_tool('init --sharded 2 --replicaset --config 4 --csrs')
        cmdlist = (
            [set(['"mongod"', '--replSet', '--dbpath', '--logpath', '--port',
                  '--configsvr'])] * 4 +
            [set(['"mongod"', '--replSet', '--dbpath', '--logpath', '--port',
                  '--shardsvr'])] * 6 +
            [set(['"mongos"', '--logpath', '--port', '--configdb'])]
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_sharded_embedded_csrs_1(self):
        """
        mrun should start 2 shards (1 node each), shard distribution should be 1 embedded CSRS,
        1 shard member, 1 mongos
        """
        if version.parse(self.mongod_version) < version.parse('8.0.0'):
            raise pytest.skip('MongoDB version is older than 8.0.0')
        self.run_tool('init --sharded 2 --replicaset --nodes 1 --config 1 --embeddedcsrs')
        cmdlist = (
            [set(['"mongod"', '--replSet', '--dbpath', '--logpath', '--port',
                  '--configsvr'])] * 1 +
            [set(['"mongod"', '--replSet', '--dbpath', '--logpath', '--port',
                  '--shardsvr'])] * 1 +
            [set(['"mongos"', '--logpath', '--port', '--configdb'])]
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_sharded_embedded_csrs_3(self):
        """
        mrun should start 2 shards (3 nodes each), shard distribution should be 1 embedded CSRS,
        1 shard member, 1 mongos
        """
        if version.parse(self.mongod_version) < version.parse('8.0.0'):
            raise pytest.skip('MongoDB version is older than 8.0.0')
        self.run_tool('init --sharded 2 --replicaset --nodes 3 --config 3 --embeddedcsrs')
        cmdlist = (
            [set(['"mongod"', '--replSet', '--dbpath', '--logpath', '--port',
                  '--configsvr'])] * 3 +
            [set(['"mongod"', '--replSet', '--dbpath', '--logpath', '--port',
                  '--shardsvr'])] * 3 +
            [set(['"mongos"', '--logpath', '--port', '--configdb'])]
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_sharded_oplogsize_csrs(self):
        """mrun should not pass --oplogSize to config server (CSRS)."""
        self.run_tool('init --sharded 1 --replicaset --nodes 1 '
                      '--oplogSize 19 --csrs')
        cmdlist = (
            [set(['"mongod"', '--port', '--logpath', '--dbpath', '--configsvr',
                  '--replSet'])] +
            [set(['"mongod"', '--port', '--replSet', '--shardsvr', '--logpath',
                  '--dbpath', '--oplogSize'])] +
            [set(['"mongos"', '--port', '--logpath', '--configdb'])]
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_sharded_three_mongos_csrs(self):
        """mrun should start 3 mongos (CSRS)."""
        self.run_tool('init --sharded 2 --replicaset --config 3 '
                      '--mongos 3 --csrs')
        cmdlist = (
            [set(['"mongod"', '--port', '--logpath', '--dbpath', '--configsvr',
                  '--replSet'])] * 3 +
            [set(['"mongod"', '--port', '--shardsvr', '--logpath', '--dbpath',
                  '--replSet'])] * 6 +
            [set(['"mongos"', '--port', '--logpath', '--configdb'])] * 3
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_default_replicaset_csrs(self):
        """
        mrun should create csrs by default -- replicaset shards
        """
        self.run_tool('init --sharded 2 --replicaset')
        cmdlist = (
            [set(['"mongod"', '--port', '--logpath', '--dbpath', '--configsvr',
                  '--replSet'])] +
            [set(['"mongod"', '--port', '--logpath', '--dbpath', '--shardsvr',
                  '--replSet'])] * 6 +
            [set(['"mongos"', '--port', '--logpath', '--configdb'])]
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_default_7_replicaset_csrs(self):
        """
        mrun should create csrs by default -- 7 node replicaset shards.
        """
        self.run_tool('init --sharded 2 --replicaset --nodes 7')
        cmdlist = (
            [set(['"mongod"', '--port', '--logpath', '--dbpath', '--configsvr',
                  '--replSet'])] +
            [set(['"mongod"', '--port', '--logpath', '--dbpath', '--shardsvr',
                  '--replSet'])] * 14 +
            [set(['"mongos"', '--port', '--logpath', '--configdb'])]
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_default_7_replicaset_5_config_csrs(self):
        """
        mrun should create csrs by default -- 7 node replicaset shards,
        5 nodes config servers.
        """
        self.run_tool('init --sharded 2 --replicaset --nodes 7 --config 5')
        cmdlist = (
            [set(['"mongod"', '--port', '--logpath', '--dbpath', '--configsvr',
                  '--replSet'])] * 5 +
            [set(['"mongod"', '--port', '--logpath', '--dbpath', '--shardsvr',
                  '--replSet'])] * 14 +
            [set(['"mongos"', '--port', '--logpath', '--configdb'])]
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_default_2_replicaset_arb_4_config_2_mongos_csrs(self):
        """
        mrun should create csrs by default -- 2 node replicaset shards +
        arbiter, 4 nodes config servers, 2 mongos.
        """
        self.run_tool('init --sharded 2 --replicaset --nodes 2 --arbiter '
                      '--config 4 --mongos 2')
        cmdlist = (
            [set(['"mongod"', '--port', '--logpath', '--dbpath', '--configsvr',
                  '--replSet'])] * 4 +
            [set(['"mongod"', '--port', '--logpath', '--dbpath', '--shardsvr',
                  '--replSet'])] * 4 +
            [set(['"mongod"', '--port', '--logpath', '--dbpath', '--replSet'
                  ])] * 2 +
            [set(['"mongos"', '--port', '--logpath', '--configdb'])] * 2
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    def test_storageengine_csrs(self):
        """
        mrun should not pass storageEngine option to config server.
        """
        self.run_tool('init --sharded 2 --replicaset --storageEngine wiredTiger')
        cmdlist = (
            [set(['"mongod"', '--port', '--logpath', '--dbpath', '--configsvr',
                  '--replSet'])] +
            [set(['"mongod"', '--port', '--logpath', '--dbpath', '--shardsvr',
                  '--storageEngine'])] * 6 +
            [set(['"mongos"', '--port', '--logpath', '--configdb'])]
            )
        if sys.platform == 'linux':
            for c in cmdlist:
                c.add('--fork')
        self.cmdlist_assert(cmdlist)

    @pytest.mark.xfail(raises=SystemExit)
    def test_invalid_hostname(self):
        """
        mrun should not start if hostname is specified but not bind_ip.
        """
        self.run_tool('init --replicaset --hostname this_host')
        self.read_config()

    def check_requires_restart_for_auth(self, first_init=True,
                                        unknown_args=None, **overrides):
        """Evaluate the auth restart decision with test arguments."""
        args = {
            'sharded': ['2'],
            'auth': True,
            'initial-user': True,
            'auth_db': 'admin',
            'auth_role_docs': False,
            'auth_roles': ['dbAdminAnyDatabase', 'readWriteAnyDatabase',
                           'userAdminAnyDatabase', 'clusterAdmin'],
        }
        args.update(overrides)
        self.tool.args = args
        self.tool.unknown_args = unknown_args or []
        return self.tool._requires_restart_for_auth(first_init)

    def test_sharded_auth_setup_does_not_require_restart(self):
        """Sharded auth setup should not restart for the default setup."""
        assert not self.check_requires_restart_for_auth()

    def test_sharded_auth_setup_requires_first_init(self):
        """The restart is only required on first init."""
        assert not self.check_requires_restart_for_auth(first_init=False)

    def test_non_sharded_auth_setup_does_not_require_restart(self):
        """Non-sharded auth setup never needs this restart."""
        assert not self.check_requires_restart_for_auth(sharded=False)

    def test_sharded_auth_setup_requires_initial_user(self):
        """Sharded auth setup without an initial user needs a restart."""
        assert self.check_requires_restart_for_auth(
            **{'initial-user': False})

    def test_sharded_auth_setup_requires_admin_auth_db(self):
        """The first user must be created in the admin database."""
        assert self.check_requires_restart_for_auth(auth_db='test')

    def test_sharded_auth_setup_requires_cluster_admin(self):
        """Adding shards requires the clusterAdmin role."""
        assert self.check_requires_restart_for_auth(
            auth_roles=['userAdminAnyDatabase'])

    def test_sharded_auth_setup_with_role_docs(self):
        """clusterAdmin role docs must be scoped to the admin database."""
        self.tool.args = {
            'sharded': ['2'],
            'auth': True,
            'initial-user': True,
            'auth_db': 'admin',
            'auth_role_docs': True,
            'auth_roles': ['{"role": "clusterAdmin", "db": "admin"}'],
        }
        self.tool.unknown_args = []
        assert not self.tool._requires_restart_for_auth(True)

        self.tool.args['auth_roles'] = [
            '{"role": "clusterAdmin", "db": "test"}']
        assert self.tool._requires_restart_for_auth(True)

    def test_sharded_auth_setup_with_localhost_bypass_disabled(self):
        """Disabling localhost auth bypass requires the restart fallback."""
        assert self.check_requires_restart_for_auth(
            unknown_args=['--setParameter', 'enableLocalhostAuthBypass=0'])

    def test_sharded_auth_setup_with_other_set_parameter(self):
        """Other setParameter arguments do not force the restart fallback."""
        assert not self.check_requires_restart_for_auth(
            unknown_args=['--setParameter', 'enableTestCommands=1'])
