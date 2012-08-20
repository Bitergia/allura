# -*- coding: utf-8 -*-
"""Setup the allura application"""
import os
import sys
import logging
import shutil
from collections import defaultdict
from datetime import datetime

import tg
from pylons import c, g
from paste.deploy.converters import asbool

from ming import Session, mim
from ming.orm import state, session
from ming.orm.ormsession import ThreadLocalORMSession

import allura
from allura.lib import plugin
from allura import model as M
from allura.websetup import schema
from allura.command import EnsureIndexCommand
from allura.command import CreateTroveCategoriesCommand

log = logging.getLogger(__name__)

def cache_test_data():
    log.info('Saving data to cache in .test-data')
    if os.path.exists('.test-data'):
        shutil.rmtree('.test-data')
    os.system('mongodump -h 127.0.0.1:27018 -o .test-data > mongodump.log 2>&1')

def restore_test_data():
    if os.path.exists('.test-data'):
        log.info('Restoring data from cache in .test-data')
        rc = os.system('mongorestore -h 127.0.0.1:27018 --dir .test-data > mongorestore.log 2>&1')
        return rc == 0
    else:
        return False

def bootstrap(command, conf, vars):
    """Place any commands to setup allura here"""
    # are we being called by the test suite?
    test_run = conf.get('__file__', '').endswith('test.ini')

    # if this is a test_run, skip user project creation to save time
    make_user_projects = not test_run
    def make_user(*args, **kw):
        kw.update(make_project=make_user_projects)
        return create_user(*args, **kw)

    # Our bootstrap doesn't play nicely with SFX project and user APIs
    tg.config['auth.method'] = tg.config['registration.method'] = 'local'
    assert tg.config['auth.method'] == 'local'
    conf['auth.method'] = conf['registration.method'] = 'local'

    # Clean up all old stuff
    ThreadLocalORMSession.close_all()
    c.queued_messages = defaultdict(list)
    c.user = c.project = c.app = None
    database=conf.get('db_prefix', '') + 'project:test'
    wipe_database()
    try:
        g.solr.delete(q='*:*')
    except: # pragma no cover
        log.error('SOLR server is %s', g.solr_server)
        log.error('Error clearing solr index')
    if asbool(conf.get('cache_test_data')):
        if restore_test_data():
            from allura.lib import helpers as h
            h.set_context('test', neighborhood='Projects')
            return
    log.info('Initializing search')

    log.info('Registering root user & default neighborhoods')
    anonymous = M.User(
        _id=None,
        username='*anonymous',
        display_name='Anonymous')

    # never make a user project for the root user
    root = create_user('Root', make_project=False)

    n_projects = M.Neighborhood(name='Projects', url_prefix='/p/',
                                features=dict(private_projects = True,
                                              max_projects = None,
                                              css = 'none',
                                              google_analytics = False))
    n_users = M.Neighborhood(name='Users', url_prefix='/u/',
                             shortname_prefix='u/',
                             features=dict(private_projects = True,
                                           max_projects = None,
                                           css = 'none',
                                           google_analytics = False))
    n_adobe = M.Neighborhood(name='Adobe', url_prefix='/adobe/', project_list_url='/adobe/',
                             features=dict(private_projects = True,
                                           max_projects = None,
                                           css = 'custom',
                                           google_analytics = True))
    assert tg.config['auth.method'] == 'local'
    project_reg = plugin.ProjectRegistrationProvider.get()
    p_projects = project_reg.register_neighborhood_project(n_projects, [root], allow_register=True)
    p_users = project_reg.register_neighborhood_project(n_users, [root])
    p_adobe = project_reg.register_neighborhood_project(n_adobe, [root])
    ThreadLocalORMSession.flush_all()
    ThreadLocalORMSession.close_all()

    # add the adobe icon
    file_name = 'adobe_icon.png'
    file_path = os.path.join(allura.__path__[0],'public','nf','images',file_name)
    M.NeighborhoodFile.from_path(file_path, neighborhood_id=n_adobe._id)

    # Add some test users
    for unum in range(10):
        make_user('Test User %d' % unum)

    log.info('Creating basic project categories')
    cat1 = M.ProjectCategory(name='clustering', label='Clustering')

    cat2 = M.ProjectCategory(name='communications', label='Communications')
    cat2_1 = M.ProjectCategory(name='synchronization', label='Synchronization', parent_id=cat2._id)
    cat2_2 = M.ProjectCategory(name='streaming', label='Streaming', parent_id=cat2._id)
    cat2_3 = M.ProjectCategory(name='fax', label='Fax', parent_id=cat2._id)
    cat2_4 = M.ProjectCategory(name='bbs', label='BBS', parent_id=cat2._id)

    cat3 = M.ProjectCategory(name='database', label='Database')
    cat3_1 = M.ProjectCategory(name='front_ends', label='Front-Ends', parent_id=cat3._id)
    cat3_2 = M.ProjectCategory(name='engines_servers', label='Engines/Servers', parent_id=cat3._id)

    log.info('Registering "regular users" (non-root) and default projects')
    # since this runs a lot for tests, separate test and default users and
    # do the minimal needed
    if asbool(conf.get('load_test_data')):
        u_admin = make_user('Test Admin')
        u_admin.preferences = dict(email_address='test-admin@users.localhost')
        u_admin.email_addresses = ['test-admin@users.localhost']
        u_admin.set_password('foo')
        u_admin.claim_address('test-admin@users.localhost')
    else:
        u_admin = make_user('Admin 1', username='admin1')
        # Admin1 is almost root, with admin access for Users and Projects neighborhoods
        p_projects.add_user(u_admin, ['Admin'])
        p_users.add_user(u_admin, ['Admin'])

        p_allura = n_projects.register_project('allura', u_admin)
    u1 = make_user('Test User')
    p_adobe1 = n_adobe.register_project('adobe-1', u_admin)
    p_adobe.add_user(u_admin, ['Admin'])
    p0 = n_projects.register_project('test', u_admin)
    p1 = n_projects.register_project('test2', u_admin)
    p0._extra_tool_status = [ 'alpha', 'beta' ]

    sess = session(M.Neighborhood) # all the sessions are the same
    for x in (n_adobe, n_projects, n_users, p_projects, p_users, p_adobe):
        # Ming doesn't detect substructural changes in newly created objects (vs loaded from DB)
        state(x).status = 'dirty'
        # TODO: Hope that Ming can be improved to at least avoid stuff below
        sess.flush(x)

    c.project = p0
    c.user = u_admin
    p1 = p0.new_subproject('sub1')
    ThreadLocalORMSession.flush_all()
    if asbool(conf.get('load_test_data')):
        if asbool(conf.get('cache_test_data')):
            cache_test_data()
    else: # pragma no cover
        # regular first-time setup
        p0.add_user(u_admin, ['Admin'])
        log.info('Registering initial apps')
        for ep_name, app in g.entry_points['tool'].iteritems():
            if not app.installable:
                continue
            p0.install_app(ep_name)
    ThreadLocalORMSession.flush_all()
    ThreadLocalORMSession.close_all()

def wipe_database():
    conn = M.main_doc_session.bind.conn
    create_trove_categories = CreateTroveCategoriesCommand('create_trove_categories')
    index = EnsureIndexCommand('ensure_index')
    if isinstance(conn, mim.Connection):
        clear_all_database_tables()
        for db in conn.database_names():
            db = conn[db]
    else:
        for database in conn.database_names():
            if database not in ( 'allura', 'pyforge', 'project-data'): continue
            log.info('Wiping database %s', database)
            db = conn[database]
            for coll in db.collection_names():
                if coll.startswith('system.'): continue
                log.info('Dropping collection %s:%s', database, coll)
                try:
                    db.drop_collection(coll)
                except:
                    pass
    create_trove_categories.run([''])
    index.run([''])



def clear_all_database_tables():
    conn = M.main_doc_session.bind.conn
    for db in conn.database_names():
        db = conn[db]
        for coll in db.collection_names():
            if coll == 'system.indexes':
                continue
            db.drop_collection(coll)


def create_user(display_name, username=None, password='foo', make_project=False):
    if not username:
        username = display_name.lower().replace(' ', '-')
    user = M.User.register(dict(username=username,
                                display_name=display_name),
                           make_project=make_project)
    user.set_password(password)
    return user


class DBSession(Session):
    '''Simple session that takes a pymongo connection and a database name'''

    def __init__(self, db):
        self._db = db

    @property
    def db(self):
        return self._db

    def _impl(self, cls):
        return self.db[cls.__mongometa__.name]

def pm(etype, value, tb): # pragma no cover
    import pdb, traceback
    try:
        from IPython.ipapi import make_session; make_session()
        from IPython.Debugger import Pdb
        sys.stderr.write('Entering post-mortem IPDB shell\n')
        p = Pdb(color_scheme='Linux')
        p.reset()
        p.setup(None, tb)
        p.print_stack_trace()
        sys.stderr.write('%s: %s\n' % ( etype, value))
        p.cmdloop()
        p.forget()
        # p.interaction(None, tb)
    except ImportError:
        sys.stderr.write('Entering post-mortem PDB shell\n')
        traceback.print_exception(etype, value, tb)
        pdb.post_mortem(tb)

sys.excepthook = pm
