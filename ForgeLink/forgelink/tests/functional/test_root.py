import pyforge

from forgelink.tests import TestController

from pyforge.lib import helpers as h
from pyforge.ext.search import search_main
from ming.orm.ormsession import ThreadLocalORMSession

class TestRootController(TestController):
    def test_root_index_no_url(self):
        response = self.app.get('/link/index')
        assert 'Link is not configured' in response

    def test_root_index_with_url(self):
        response = self.app.get('/admin/link/options')
        response.form['url'] = 'http://www.google.com/'
        response.form.submit()
        redirected = self.app.get('/link/index').follow()
        assert redirected.request.url == 'http://www.google.com/'