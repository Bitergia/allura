import json

import pkg_resources
import pylons
pylons.c = pylons.tmpl_context
pylons.g = pylons.app_globals
from pylons import c
from ming.orm import ThreadLocalORMSession

from allura.lib import helpers as h
from allura.tests import decorators as td
from alluratest.controller import TestController


class SVNTestController(TestController):
    def setUp(self):
        TestController.setUp(self)
        self.setup_with_tools()

    @td.with_svn
    def setup_with_tools(self):
        h.set_context('test', 'src', neighborhood='Projects')
        repo_dir = pkg_resources.resource_filename(
            'forgesvn', 'tests/data/')
        c.app.repo.fs_path = repo_dir
        c.app.repo.status = 'ready'
        c.app.repo.name = 'testsvn'
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        h.set_context('test', 'src', neighborhood='Projects')
        c.app.repo.refresh()
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        h.set_context('test', 'src', neighborhood='Projects')


class TestRootController(SVNTestController):
    def test_status(self):
        resp = self.app.get('/src/status')
        d = json.loads(resp.body)
        assert d == dict(status='ready')

    def test_status_html(self):
        resp = self.app.get('/src/').follow()
        # repo status not displayed if 'ready'
        assert None == resp.html.find('div', dict(id='repo_status'))
        h.set_context('test', 'src', neighborhood='Projects')
        c.app.repo.status = 'analyzing'
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        # repo status displayed if not 'ready'
        resp = self.app.get('/src/').follow()
        div = resp.html.find('div', dict(id='repo_status'))
        assert div.span.text == 'analyzing'

    def test_index(self):
        resp = self.app.get('/src/').follow()
        assert 'svn checkout' in resp
        assert '[r5]' in resp, resp.showbrowser()

    def test_index_empty(self):
        self.app.get('/svn/')

    def test_commit_browser(self):
        resp = self.app.get('/src/commit_browser')

    def test_commit_browser_data(self):
        resp = self.app.get('/src/commit_browser_data')
        data = json.loads(resp.body);
        assert data['max_row'] == 4
        assert data['next_column'] == 1
        for val in data['built_tree'].values():
            if val['url'] == '/p/test/src/1/':
                assert val['column'] == 0
                assert val['row'] == 4
                assert val['message'] == 'Create readme'

    def test_feed(self):
        r = self.app.get('/src/feed.rss')
        assert 'Remove hello.txt' in str(r), r

    def test_commit(self):
        resp = self.app.get('/src/3/tree/')
        assert len(resp.html.findAll('tr')) == 3, resp.showbrowser()

    def test_tree(self):
        resp = self.app.get('/src/1/tree/')
        assert len(resp.html.findAll('tr')) == 2, resp.showbrowser()
        resp = self.app.get('/src/3/tree/a/')
        assert len(resp.html.findAll('tr')) == 2, resp.showbrowser()

    def test_file(self):
        resp = self.app.get('/src/1/tree/README')
        assert 'README' in resp.html.find('h2', {'class':'dark title'}).contents[2]
        content = str(resp.html.find('div', {'class':'clip grid-19'}))
        assert 'This is readme' in content, content
        assert '<span id="l1" class="code_block">' in resp
        assert 'var hash = window.location.hash.substring(1);' in resp

    def test_invalid_file(self):
        resp = self.app.get('/src/1/tree/READMEz', status=404)

    def test_diff(self):
        resp = self.app.get('/src/3/tree/README?diff=2')
        assert 'This is readme' in resp, resp.showbrowser()
        assert '+++' in resp, resp.showbrowser()

    def test_checkout_svn(self):
        self.app.post('/p/test/admin/src/set_checkout_url',
                      {"checkout_url": "badurl"})
        r = self.app.get('/p/test/admin/src/checkout_url')
        assert 'value="trunk"' in r
        self.app.post('/p/test/admin/src/set_checkout_url',
                      {"checkout_url": ""})
        r = self.app.get('/p/test/admin/src/checkout_url')
        assert 'value="trunk"' not in r
        self.app.post('/p/test/admin/src/set_checkout_url',
                      {"checkout_url": "a"})
        r = self.app.get('/p/test/admin/src/checkout_url')
        assert 'value="a"' in r


class TestImportController(SVNTestController):
    def test_index(self):
        self.app.get('/p/test/admin/src/importer').follow(status=200)
