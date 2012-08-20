from pylons import c, g

from alluratest.controller import setup_basic_test, setup_global_objects
from allura import model as M
from allura.lib import security
from allura.tests import decorators as td

def setUp():
    setup_basic_test()
    setup_with_tools()

@td.with_tracker
def setup_with_tools():
    setup_global_objects()
    g.set_app('bugs')

def test_role_assignments():
    admin = M.User.by_username('test-admin')
    user = M.User.by_username('test-user')
    anon = M.User.anonymous()
    def check_access(perm):
        pred = security.has_access(c.app, perm)
        return pred(user=admin), pred(user=user), pred(user=anon)
    assert check_access('configure') == (True, False, False)
    assert check_access('read') == (True, True, True)
    assert check_access('update') == (True, False, False)
    assert check_access('create') == (True, False, False)
    assert check_access('unmoderated_post') == (True, True, False)
    assert check_access('post') == (True, True, False)
    assert check_access('moderate') == (True, False, False)
    assert check_access('admin') == (True, False, False)
