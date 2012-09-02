# -*- coding: utf-8 -*-
"""
Functional test suite for the root controller.

This is an example of how functional tests can be written for controllers.

As opposed to a unit-test, which test a small unit of functionality,
functional tests exercise the whole application and its WSGI stack.

Please read http://pythonpaste.org/webtest/ for more information.

"""
from tg import config
from nose.tools import assert_equal
from ming.orm.ormsession import ThreadLocalORMSession

from allura.tests import decorators as td
from allura.tests import TestController
from allura import model as M
from allura.lib.helpers import push_config


class TestRootController(TestController):

    def setUp(self):
        super(TestRootController, self).setUp()
        n_adobe = M.Neighborhood.query.get(name='Adobe')
        assert n_adobe
        u_admin = M.User.query.get(username='test-admin')
        assert u_admin
        p_adobe2 = n_adobe.register_project('adobe-2', u_admin)

    def test_index(self):
        response = self.app.get('/')
        assert_equal(response.html.find('h2',{'class':'dark title'}).contents[0].strip(), 'All Projects')
        projects = response.html.findAll('div',{'class':'border card'})
        assert projects[0].find('a').get('href') == '/adobe/adobe-1/'
        cat_links = response.html.find('div',{'id':'sidebar'}).findAll('li')
        assert len(cat_links) == 4
        assert cat_links[0].find('a').get('href') == '/browse/clustering'
        assert cat_links[0].find('a').find('span').string == 'Clustering'

    def test_sidebar_escaping(self):
        # use this as a convenient way to get something in the sidebar
        M.ProjectCategory(name='test-xss', label='<script>alert(1)</script>')
        ThreadLocalORMSession.flush_all()

        response = self.app.get('/')
        # inject it into the sidebar data
        content = str(response.html.find('div',{'id':'content_base'}))
        assert '<script>' not in content
        assert '&lt;script&gt;' in content

    def test_strange_accept_headers(self):
        hdrs = [
            'text/plain;text/html;text/*',
            'text/html,application/xhtml+xml,application/xml;q=0.9;text/plain;q=0.8,image/png,*/*;q=0.5' ]
        for hdr in hdrs:
            # malformed headers used to return 500, just make sure they don't now
            self.app.get('/', headers=dict(Accept=hdr), validate_skip=True)

    def test_project_browse(self):
        com_cat = M.ProjectCategory.query.find(dict(label='Communications')).first()
        fax_cat = M.ProjectCategory.query.find(dict(label='Fax')).first()
        M.Project.query.find(dict(name='adobe-1')).first().category_id = com_cat._id
        response = self.app.get('/browse')
        assert len(response.html.findAll('a',{'href':'/adobe/adobe-1/'})) == 1
        assert len(response.html.findAll('a',{'href':'/adobe/adobe-2/'})) == 1
        response = self.app.get('/browse/communications')
        assert len(response.html.findAll('a',{'href':'/adobe/adobe-1/'})) == 1
        assert len(response.html.findAll('a',{'href':'/adobe/adobe-2/'})) == 0
        response = self.app.get('/browse/communications/fax')
        assert len(response.html.findAll('a',{'href':'/adobe/adobe-1/'})) == 0
        assert len(response.html.findAll('a',{'href':'/adobe/adobe-2/'})) == 0

    def test_project_redirect(self):
        with push_config(config, **{'activitystream.enabled': 'false'}):
            resp = self.app.get('/p/test2/')
            assert_equal(resp.status_int, 302)
            assert_equal(resp.location, 'http://localhost/p/test2/admin/')

        with push_config(config, **{'activitystream.enabled': 'true'}):
            resp = self.app.get('/p/test2/')
            assert_equal(resp.status_int, 302)
            assert_equal(resp.location, 'http://localhost/p/test2/activity/')

        with push_config(config, **{'activitystream.enabled': 'false'}):
            self.app.cookies['activitystream.enabled'] = 'true'
            resp = self.app.get('/p/test2/')
            assert_equal(resp.status_int, 302)
            assert_equal(resp.location, 'http://localhost/p/test2/activity/')

    def test_neighborhood_home(self):
        # Install home app
        nb = M.Neighborhood.query.get(name='Adobe')
        p = nb.neighborhood_project
        p.install_app('home', 'home', 'Home', ordinal=0)

        response = self.app.get('/adobe/')
        projects = response.html.findAll('div',{'class':'border card'})
        assert len(projects) == 2
        cat_links = response.html.find('div',{'id':'sidebar'}).findAll('ul')[1].findAll('li')
        assert len(cat_links) == 3, cat_links
        assert cat_links[0].find('a').get('href') == '/adobe/browse/clustering'
        assert cat_links[0].find('a').find('span').string == 'Clustering'

    def test_neighborhood_project_browse(self):
        com_cat = M.ProjectCategory.query.find(dict(label='Communications')).first()
        fax_cat = M.ProjectCategory.query.find(dict(label='Fax')).first()
        M.Project.query.find(dict(name='adobe-1')).first().category_id = com_cat._id
        M.Project.query.find(dict(name='adobe-2')).first().category_id = fax_cat._id
        response = self.app.get('/adobe/browse')
        assert len(response.html.findAll('a',{'href':'/adobe/adobe-1/'})) == 1
        assert len(response.html.findAll('a',{'href':'/adobe/adobe-2/'})) == 1
        response = self.app.get('/adobe/browse/communications')
        assert len(response.html.findAll('a',{'href':'/adobe/adobe-1/'})) == 1
        assert len(response.html.findAll('a',{'href':'/adobe/adobe-2/'})) == 1
        response = self.app.get('/adobe/browse/communications/fax')
        assert len(response.html.findAll('a',{'href':'/adobe/adobe-1/'})) == 0
        assert len(response.html.findAll('a',{'href':'/adobe/adobe-2/'})) == 1

    @td.with_wiki
    def test_markdown_to_html(self):
        n = M.Neighborhood.query.get(name='Projects')
        r = self.app.get('/nf/markdown_to_html?markdown=*aaa*bb[wiki:Home]&project=test&app=bugs&neighborhood=%s' % n._id, validate_chunk=True)
        assert '<p><em>aaa</em>bb<a href="/p/test/wiki/Home/">[wiki:Home]</a></p>' in r, r

    def test_slash_redirect(self):
        r = self.app.get('/p',status=301)
        r = self.app.get('/p/',status=302)
