import os
import sys
import logging
from multiprocessing import Process
from pkg_resources import iter_entry_points

import pylons
from paste.script import command
from paste.deploy import appconfig
from paste.registry import Registry

import activitystream
import ming
from allura.config.environment import load_environment

log = None

class EmptyClass(object): pass

class Command(command.Command):
    min_args = 1
    max_args = 1
    usage = '[<ini file>]'
    group_name = 'Allura'

    @ming.utils.LazyProperty
    def registry(self):
        return Registry()

    @ming.utils.LazyProperty
    def globals(self):
        import allura.lib.app_globals
        return allura.lib.app_globals.Globals()

    @ming.utils.LazyProperty
    def config(self):
        import tg
        return tg.config

    def basic_setup(self):
        global log, M
        if self.args[0]:
            # Probably being called from the command line - load the config file
            self.config = conf = appconfig('config:%s' % self.args[0],relative_to=os.getcwd())
            # ... logging does not understand section#subsection syntax
            logging_config = self.args[0].split('#')[0]
            logging.config.fileConfig(logging_config, disable_existing_loggers=False)
            log = logging.getLogger('allura.command')
            log.info('Initialize command with config %r', self.args[0])
            load_environment(conf.global_conf, conf.local_conf)
            self.setup_globals()
            from allura import model
            M=model
            ming.configure(**conf)
            activitystream.configure(**conf)
            pylons.c.user = M.User.anonymous()
        else:
            # Probably being called from another script (websetup, perhaps?)
            log = logging.getLogger('allura.command')
            conf = pylons.config
        self.tools = pylons.g.entry_points['tool'].values()
        for ep in iter_entry_points('allura.command_init'):
            log.info('Running command_init for %s', ep.name)
            ep.load()(conf)
        log.info('Loaded tools')

    def setup_globals(self):
        import allura.lib.app_globals
        self.registry.prepare()
        self.registry.register(pylons.c, EmptyClass())
        self.registry.register(pylons.g, self.globals)
        self.registry.register(allura.credentials, allura.lib.security.Credentials())
        pylons.c.queued_messages = None

    def teardown_globals(self):
        self.registry.cleanup()

