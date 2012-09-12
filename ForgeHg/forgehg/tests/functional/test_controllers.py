import json

import pkg_resources
import pylons
pylons.c = pylons.tmpl_context
pylons.g = pylons.app_globals
from pylons import c
from ming.orm import ThreadLocalORMSession
from datadiff.tools import assert_equal

from allura.lib import helpers as h
from allura.tests import decorators as td
from allura import model as M
from alluratest.controller import TestController


class TestRootController(TestController):

    def setUp(self):
        TestController.setUp(self)
        self.setup_with_tools()

    @td.with_hg
    def setup_with_tools(self):
        h.set_context('test', 'src-hg', neighborhood='Projects')
        repo_dir = pkg_resources.resource_filename(
            'forgehg', 'tests/data')
        c.app.repo.fs_path = repo_dir
        c.app.repo.status = 'ready'
        c.app.repo.name = 'testrepo.hg'
        c.app.repo.refresh()
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        h.set_context('test', 'src-hg', neighborhood='Projects')
        c.app.repo.refresh()

    def test_fork(self):
        to_project = M.Project.query.get(shortname='test2', neighborhood_id=c.project.neighborhood_id)
        r = self.app.post('/src-hg/fork', params=dict(
            project_id=str(to_project._id),
            mount_point='code'))
        assert "{status: 'error'}" not in str(r.follow())
        cloned_from = c.app.repo
        with h.push_context('test2', 'code', neighborhood='Projects'):
            c.app.repo.init_as_clone(
                    cloned_from.full_fs_path,
                    cloned_from.app.config.script_name(),
                    cloned_from.full_fs_path)
        r = self.app.get('/p/test2/code').follow().follow().follow()
        assert 'Clone of' in r
        r = self.app.get('/src-hg/').follow().follow()
        assert 'Forks' in r

    def test_merge_request(self):
        to_project = M.Project.query.get(shortname='test2', neighborhood_id=c.project.neighborhood_id)
        r = self.app.post('/src-hg/fork', params=dict(
            project_id=str(to_project._id),
            mount_point='code'))
        assert "{status: 'error'}" not in str(r.follow())
        cloned_from = c.app.repo
        with h.push_context('test2', 'code', neighborhood='Projects'):
            c.app.repo.init_as_clone(
                    cloned_from.full_fs_path,
                    cloned_from.app.config.script_name(),
                    cloned_from.full_fs_path)
        r = self.app.get('/p/test2/code/').follow().follow()
        assert 'Request Merge' in r
        # Request Merge button only visible to repo admins
        kw = dict(extra_environ=dict(username='test-user'))
        r = self.app.get('/p/test2/code/', **kw).follow(**kw).follow(**kw)
        assert 'Request Merge' not in r, r
        # Request merge controller action only permitted for repo admins
        r = self.app.get('/p/test2/code/request_merge', status=403, **kw)
        r = self.app.get('/p/test2/code/request_merge')
        assert 'Request merge' in r
        # Merge request detail view
        r = r.forms[0].submit().follow()
        assert 'would like you to merge' in r
        mr_num = r.request.url.split('/')[-2]
        # Merge request list view
        r = self.app.get('/p/test/src-hg/merge-requests/')
        assert 'href="%s/"' % mr_num in r
        # Merge request status update
        r = self.app.post('/p/test/src-hg/merge-requests/%s/save' % mr_num,
                          params=dict(status='rejected')).follow()
        assert 'Merge Request #%s:  (rejected)' % mr_num in r, r

    def test_status(self):
        resp = self.app.get('/src-hg/status')
        d = json.loads(resp.body)
        assert d == dict(status='ready')

    def test_status_html(self):
        resp = self.app.get('/src-hg/').follow().follow()
        # repo status not displayed if 'ready'
        assert None == resp.html.find('div', dict(id='repo_status'))
        h.set_context('test', 'src-hg', neighborhood='Projects')
        c.app.repo.status = 'analyzing'
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        # repo status displayed if not 'ready'
        resp = self.app.get('/src-hg/').follow().follow()
        div = resp.html.find('div', dict(id='repo_status'))
        assert div.span.text == 'analyzing'

    def test_index(self):
        resp = self.app.get('/src-hg/').follow().follow()
        assert 'hg clone http://' in resp, resp

    def test_index_empty(self):
        self.app.get('/test-app-hg/')

    def test_commit_browser(self):
        resp = self.app.get('/src-hg/commit_browser')

    def test_commit_browser_data(self):
        resp = self.app.get('/src-hg/commit_browser_data')
        data = json.loads(resp.body);
        assert data['max_row'] == 4
        assert data['next_column'] == 1
        assert_equal(data['built_tree']['e5a0b44437be783c41084e7bf0740f9b58b96ecf'],
                {u'url': u'/p/test/src-hg/ci/e5a0b44437be783c41084e7bf0740f9b58b96ecf/',
                 u'oid': u'e5a0b44437be783c41084e7bf0740f9b58b96ecf',
                 u'column': 0,
                 u'parents': [u'773d2f8e3a94d0d5872988b16533d67e1a7f5462'],
                 u'message': u'Modify README', u'row': 3})

    def _get_ci(self):
        resp = self.app.get('/src-hg/').follow().follow()
        for tag in resp.html.findAll('a'):
            if tag['href'].startswith('/p/test/src-hg/ci/'):
                return tag['href']
        return None

    def test_commit(self):
        ci = self._get_ci()
        resp = self.app.get(ci)
        assert 'Rick Copeland' in resp, resp.showbrowser()

    def test_tree(self):
        ci = self._get_ci()
        resp = self.app.get(ci + 'tree/')
        assert len(resp.html.findAll('tr')) == 3, resp.showbrowser()
        assert 'README' in resp, resp.showbrowser()

    def test_file(self):
        ci = self._get_ci()
        resp = self.app.get(ci + 'tree/README')
        assert 'README' in resp.html.find('h2', {'class':'dark title'}).contents[2]
        content = str(resp.html.find('div', {'class':'clip grid-19'}))
        assert 'This is readme' in content, content
        assert '<span id="l1" class="code_block">' in resp
        assert 'var hash = window.location.hash.substring(1);' in resp
        resp = self.app.get(ci + 'tree/test.jpg')

    def test_invalid_file(self):
        ci = self._get_ci()
        resp = self.app.get(ci + 'tree/READMEz', status=404)

    def test_diff(self):
        ci = '/p/test/src-hg/ci/e5a0b44437be783c41084e7bf0740f9b58b96ecf/'
        parent = '773d2f8e3a94d0d5872988b16533d67e1a7f5462'
        resp = self.app.get(ci + 'tree/README?barediff=' + parent,
                validate_chunk=True)
        assert 'readme' in resp, resp.showbrowser()
        assert '+++' in resp, resp.showbrowser()
        assert '+Another line' in resp, resp.showbrowser()

    def test_binary_diff(self):
        ci = '/p/test/src-hg/ci/4a7f7ec0dcf5f005eb5d177b3d8c00bfc8159843/'
        parent = '1c7eb55bbd66ff45906b4a25d4b403899e0ffff1'
        resp = self.app.get(ci + 'tree/test.jpg?barediff=' + parent,
        validate_chunk=True)
        assert 'Cannot display: file marked as a binary type.' in resp


class TestLogPagination(TestController):

    def setUp(self):
        TestController.setUp(self)
        self.setup_with_tools()

    @td.with_hg
    def setup_with_tools(self):
        h.set_context('test', 'src-hg', neighborhood='Projects')
        repo_dir = pkg_resources.resource_filename(
            'forgehg', 'tests/data')
        c.app.repo.fs_path = repo_dir
        c.app.repo.status = 'ready'
        c.app.repo.name = 'paginationtest.hg'
        c.app.repo.refresh()
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        h.set_context('test', 'src-hg', neighborhood='Projects')
        c.app.repo.refresh()

    def _get_ci(self):
        resp = self.app.get('/src-hg/').follow().follow()
        for tag in resp.html.findAll('a'):
            if tag['href'].startswith('/p/test/src-hg/ci/'):
                return tag['href']
        return None

    def test_show_pagination(self):
        resp = self.app.get(self._get_ci() + 'log/')
        assert "pager_curpage" in resp
        resp = self.app.get(self._get_ci() + 'log/?limit=50')
        assert "pager_curpage" not in resp
        resp = self.app.get(self._get_ci() + 'log/?page=2')
        assert "pager_curpage" not in resp

    def test_log_messages(self):
        resp = self.app.get(self._get_ci() + 'log/')
        # first commit is on the first page
        assert "[0debe4]" in resp
        # 25th commit is on the first page too
        assert "[ab7517]" in resp
        # 26th commit is not on the first page
        assert "[dc406e]" not in resp
        resp = self.app.get(self._get_ci() + 'log/?page=1')
        assert "[0debe4]" not in resp
        # 26th commit is on the second page
        assert "[dc406e]" in resp

        # test with greater limit
        resp = self.app.get(self._get_ci() + 'log/?limit=50')
        assert "[0debe4]" in resp
        assert "[dc406e]" in resp
