# -*- coding: utf-8 -*-
import os
import Image, StringIO
import allura

from nose.tools import assert_true

from ming.orm.ormsession import ThreadLocalORMSession
from mock import patch

from allura import model as M
from allura.lib import helpers as h
from allura.tests import decorators as td
from alluratest.controller import TestController

from forgewiki import model

#---------x---------x---------x---------x---------x---------x---------x
# RootController methods exposed:
#     index, new_page, search
# PageController methods exposed:
#     index, edit, history, diff, raw, revert, update
# CommentController methods exposed:
#     reply, delete

class TestRootController(TestController):
    def setUp(self):
        super(TestRootController, self).setUp()
        self.setup_with_tools()

    @td.with_wiki
    def setup_with_tools(self):
        pass

    def test_root_index(self):
        r = self.app.get('/wiki/tést/').follow()
        assert 'tést' in r
        assert 'Create Page' in r
        # No 'Create Page' button if user doesn't have 'create' perm
        r = self.app.get('/wiki/tést/',
                extra_environ=dict(username='*anonymous')).follow()
        assert 'Create Page' not in r

    def test_root_markdown_syntax(self):
        response = self.app.get('/wiki/markdown_syntax/')
        assert 'Markdown Syntax' in response

    def test_root_browse_tags(self):
        response = self.app.get('/wiki/browse_tags/')
        assert 'Browse Labels' in response

    def test_root_browse_pages(self):
        response = self.app.get('/wiki/browse_pages/')
        assert 'Browse Pages' in response

    def test_root_new_page(self):
        response = self.app.get('/wiki/new_page?title=tést')
        assert 'tést' in response

    def test_root_new_search(self):
        self.app.get('/wiki/tést/')
        response = self.app.get('/wiki/search?q=tést')
        assert 'Search wiki: tést' in response

    def test_page_index(self):
        response = self.app.get('/wiki/tést/')
        assert 'tést' in response.follow()

    def test_page_edit(self):
        self.app.get('/wiki/tést/index')
        response = self.app.post('/wiki/tést/edit')
        assert 'tést' in response

    @patch('forgewiki.wiki_main.g.director.create_activity')
    def test_activity(self, create_activity):
        d = dict(title='foo', text='footext')
        self.app.post('/wiki/foo/update', params=d)
        assert create_activity.call_count == 1
        assert create_activity.call_args[0][1] == 'created'
        create_activity.reset_mock()
        d = dict(title='foo', text='new footext')
        self.app.post('/wiki/foo/update', params=d)
        assert create_activity.call_count == 1
        assert create_activity.call_args[0][1] == 'modified'
        create_activity.reset_mock()
        d = dict(title='new foo', text='footext')
        self.app.post('/wiki/foo/update', params=d)
        assert create_activity.call_count == 1
        assert create_activity.call_args[0][1] == 'renamed'

    def test_title_slashes(self):
        # forward slash not allowed in wiki page title - converted to dash
        response = self.app.post(
            '/wiki/foo-bar/update',
            params={
                'title':'foo/bar',
                'text':'sometext',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'}).follow()
        assert 'foo-bar' in response
        assert 'foo-bar' in response.request.url

    def test_dotted_page_name(self):
        r = self.app.post(
            '/wiki/page.dot/update',
            params={
                'title':'page.dot',
                'text':'text1',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'}).follow()
        assert 'page.dot' in r

    def test_subpage_attempt(self):
        self.app.get('/wiki/tést/')
        self.app.post(
            '/wiki/tést/update',
            params={
                'title':'tést',
                'text':'text1',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        assert '/p/test/wiki/Home/' in self.app.get('/wiki/tést/Home/')
        self.app.get('/wiki/tést/notthere/', status=404)

    def test_page_history(self):
        self.app.get('/wiki/tést/')
        self.app.post(
            '/wiki/tést/update',
            params={
                'title':'tést',
                'text':'text1',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        self.app.post(
            '/wiki/tést/update',
            params={
                'title':'tést',
                'text':'text2',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        response = self.app.get('/wiki/tést/history')
        assert 'tést' in response
        # two revisions are shown
        assert '2 by Test Admin' in response
        assert '1 by Test Admin' in response
        # you can revert to an old revison, but not the current one
        assert response.html.find('a',{'href':'./revert?version=1'})
        assert not response.html.find('a',{'href':'./revert?version=2'})
        response = self.app.get('/wiki/tést/history', extra_environ=dict(username='*anonymous'))
        # two revisions are shown
        assert '2 by Test Admin' in response
        assert '1 by Test Admin' in response
        # you cannot revert to any revision
        assert not response.html.find('a',{'href':'./revert?version=1'})
        assert not response.html.find('a',{'href':'./revert?version=2'})

    def test_page_diff(self):
        self.app.post(
            '/wiki/tést/update',
            params={
                'title':'tést',
                'text':'sometext',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        self.app.post('/wiki/tést/revert', params=dict(version='1'))
        response = self.app.get('/wiki/tést/')
        assert 'Subscribe' in response
        response = self.app.get('/wiki/tést/diff?v1=0&v2=0')
        assert 'tést' in response

    def test_page_raw(self):
        self.app.post(
            '/wiki/TEST/update',
            params={
                'title':'TEST',
                'text':'sometext',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        response = self.app.get('/wiki/TEST/raw')
        assert 'TEST' in response

    def test_page_revert_no_text(self):
        self.app.post(
            '/wiki/tést/update',
            params={
                'title':'tést',
                'text':'',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        response = self.app.post('/wiki/tést/revert', params=dict(version='1'))
        assert '.' in response.json['location']
        response = self.app.get('/wiki/tést/')
        assert 'tést' in response

    def test_page_revert_with_text(self):
        self.app.get('/wiki/tést/')
        self.app.post(
            '/wiki/tést/update',
            params={
                'title':'tést',
                'text':'sometext',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        response = self.app.post('/wiki/tést/revert', params=dict(version='1'))
        assert '.' in response.json['location']
        response = self.app.get('/wiki/tést/')
        assert 'tést' in response

    def test_page_update(self):
        self.app.get('/wiki/tést/')
        response = self.app.post(
            '/wiki/tést/update',
            params={
                'title':'tést',
                'text':'sometext',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        assert 'tést' in response

    def test_page_label_unlabel(self):
        self.app.get('/wiki/tést/')
        response = self.app.post(
            '/wiki/tést/update',
            params={
                'title':'tést',
                'text':'sometext',
                'labels':'yellow,green',
                'labels_old':'yellow,green',
                'viewable_by-0.id':'all'})
        assert 'tést' in response
        response = self.app.post(
            '/wiki/tést/update',
            params={
                'title':'tést',
                'text':'sometext',
                'labels':'yellow',
                'labels_old':'yellow',
                'viewable_by-0.id':'all'})
        assert 'tést' in response

    def test_new_attachment(self):
        self.app.post(
            '/wiki/tést/update',
            params={
                'title':'tést',
                'text':'sometext',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        content = file(__file__).read()
        self.app.post('/wiki/tést/attach', upload_files=[('file_info', 'test_root.py', content)])
        response = self.app.get('/wiki/tést/')
        assert 'test_root.py' in response

    def test_new_text_attachment_content(self):
        self.app.post(
            '/wiki/tést/update',
            params={
                'title':'tést',
                'text':'sometext',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        file_name = 'test_root.py'
        file_data = file(__file__).read()
        upload = ('file_info', file_name, file_data)
        self.app.post('/wiki/tést/attach', upload_files=[upload])
        page_editor = self.app.get('/wiki/tést/edit')
        download = page_editor.click(description=file_name)
        assert_true(download.body == file_data)

    def test_new_image_attachment_content(self):
        self.app.post('/wiki/TEST/update', params={
                'title':'TEST',
                'text':'sometext',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        file_name = 'neo-icon-set-454545-256x350.png'
        file_path = os.path.join(allura.__path__[0],'nf','allura','images',file_name)
        file_data = file(file_path).read()
        upload = ('file_info', file_name, file_data)
        self.app.post('/wiki/TEST/attach', upload_files=[upload])
        h.set_context('test', 'wiki', neighborhood='Projects')
        page = model.Page.query.find(dict(title='TEST')).first()
        filename = page.attachments.first().filename

        uploaded = Image.open(file_path)
        r = self.app.get('/wiki/TEST/attachment/'+filename)
        downloaded = Image.open(StringIO.StringIO(r.body))
        assert uploaded.size == downloaded.size
        r = self.app.get('/wiki/TEST/attachment/'+filename+'/thumb')

        thumbnail = Image.open(StringIO.StringIO(r.body))
        assert thumbnail.size == (255,255)

        # Make sure thumbnail is present
        r = self.app.get('/wiki/TEST/')
        img_srcs = [ i['src'] for i in r.html.findAll('img') ]
        assert ('/p/test/wiki/TEST/attachment/' + filename + '/thumb') in img_srcs, img_srcs
        # Update the page to embed the image, make sure the thumbnail is absent
        self.app.post('/wiki/TEST/update', params=dict(
                title='TEST',
                text='sometext\n[[img src=%s alt=]]' % file_name))
        r = self.app.get('/wiki/TEST/')
        img_srcs = [ i['src'] for i in r.html.findAll('img') ]
        assert ('/p/test/wiki/TEST/attachment/' + filename) not in img_srcs, img_srcs
        assert ('./attachment/' + file_name) in img_srcs, img_srcs

    def test_sidebar_static_page(self):
        response = self.app.get('/wiki/tést/')
        assert 'Edit this page' not in response
        assert 'Related Pages' not in response

    def test_related_links(self):
        response = self.app.get('/wiki/TEST/').follow()
        assert 'Edit TEST' in response
        assert 'Related' not in response
        self.app.post('/wiki/TEST/update', params={
                'title':'TEST',
                'text':'sometext',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        self.app.post('/wiki/aaa/update', params={
                'title':'aaa',
                'text':'',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        self.app.post('/wiki/bbb/update', params={
                'title':'bbb',
                'text':'',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})

        h.set_context('test', 'wiki', neighborhood='Projects')
        a = model.Page.query.find(dict(title='aaa')).first()
        a.text = '\n[TEST]\n'
        b = model.Page.query.find(dict(title='TEST')).first()
        b.text = '\n[bbb]\n'
        ThreadLocalORMSession.flush_all()
        M.MonQTask.run_ready()
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()

        response = self.app.get('/wiki/TEST/')
        assert 'Related' in response
        assert 'aaa' in response
        assert 'bbb' in response

    def test_show_discussion(self):
        self.app.post('/wiki/tést/update', params={
                'title':'tést',
                'text':'sometext',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        wiki_page = self.app.get('/wiki/tést/')
        assert wiki_page.html.find('div',{'id':'new_post_holder'})
        options_admin = self.app.get('/admin/wiki/options', validate_chunk=True)
        assert options_admin.form['show_discussion'].checked
        options_admin.form['show_discussion'].checked = False
        options_admin.form.submit()
        options_admin2 = self.app.get('/admin/wiki/options', validate_chunk=True)
        assert not options_admin2.form['show_discussion'].checked
        wiki_page2 = self.app.get('/wiki/tést/')
        assert not wiki_page2.html.find('div',{'id':'new_post_holder'})

    def test_show_left_bar(self):
        self.app.post('/wiki/tést/update', params={
                'title':'tést',
                'text':'sometext',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        wiki_page = self.app.get('/wiki/tést/')
        assert wiki_page.html.find('ul',{'class':'sidebarmenu'})
        options_admin = self.app.get('/admin/wiki/options', validate_chunk=True)
        assert options_admin.form['show_left_bar'].checked
        options_admin.form['show_left_bar'].checked = False
        options_admin.form.submit()
        options_admin2 = self.app.get('/admin/wiki/options', validate_chunk=True)
        assert not options_admin2.form['show_left_bar'].checked
        wiki_page2 = self.app.get('/wiki/tést/',extra_environ=dict(username='*anonymous'))
        assert not wiki_page2.html.find('ul',{'class':'sidebarmenu'})
        wiki_page3 = self.app.get('/wiki/tést/')
        assert not wiki_page3.html.find('ul',{'class':'sidebarmenu'})

    def test_show_metadata(self):
        self.app.post('/wiki/tést/update', params={
                'title':'tést',
                'text':'sometext',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        wiki_page = self.app.get('/wiki/tést/')
        assert wiki_page.html.find('div',{'class':'editbox'})
        options_admin = self.app.get('/admin/wiki/options', validate_chunk=True)
        assert options_admin.form['show_right_bar'].checked
        options_admin.form['show_right_bar'].checked = False
        options_admin.form.submit()
        options_admin2 = self.app.get('/admin/wiki/options', validate_chunk=True)
        assert not options_admin2.form['show_right_bar'].checked
        wiki_page2 = self.app.get('/wiki/tést/')
        assert not wiki_page2.html.find('div',{'class':'editbox'})

    def test_edit_mount_label(self):
        r = self.app.get('/admin/wiki/edit_label', validate_chunk=True)
        assert r.form['mount_label'].value == 'Wiki'
        r = self.app.post('/admin/wiki/update_label', params=dict(
            mount_label='Tricky Wiki'))
        r = self.app.get('/admin/wiki/edit_label', validate_chunk=True)
        assert r.form['mount_label'].value == 'Tricky Wiki'

    def test_page_links_are_colored(self):
        self.app.get('/wiki/space%20page/')
        params = {
            'title':'space page',
            'text':'''There is a space in the title!''',
            'labels':'',
            'labels_old':'',
            'viewable_by-0.id':'all'}
        self.app.post('/wiki/space%20page/update', params=params)
        self.app.get('/wiki/TEST/')
        params = {
            'title':'TEST',
            'text':'''
* Here is a link to [this page](TEST)
* Here is a link to [another page](Some page which does not exist)
* Here is a link to [space page space](space page)
* Here is a link to [space page escape](space%20page)
* Here is a link to [TEST]
* Here is a link to [Some page which does not exist]
* Here is a link to [space page]
* Here is a link to [space%20page]
''',
            'labels':'',
            'labels_old':'',
            'viewable_by-0.id':'all'}
        self.app.post('/wiki/TEST/update', params=params)
        r = self.app.get('/wiki/TEST/')
        found_links = 0
        for link in r.html.findAll('a'):
            if link.contents == ['this page']:
                assert 'notfound' not in link.get('class', '')
                found_links +=1
            if link.contents == ['another page']:
                assert 'notfound' in link.get('class', '')
                found_links +=1
            if link.contents == ['space page space']:
                assert 'notfound' not in link.get('class', '')
                found_links +=1
            if link.contents == ['space page escape']:
                assert 'notfound' not in link.get('class', '')
                found_links +=1
            if link.contents == ['[TEST]']:
                assert 'notfound' not in link.get('class', '')
                found_links +=1
            if link.contents == ['[Some page which does not exist]']:
                assert 'notfound' in link.get('class', '')
                found_links +=1
            if link.contents == ['[space page]']:
                assert 'notfound' not in link.get('class', '')
                found_links +=1
            if link.contents == ['[space%20page]']:
                assert 'notfound' not in link.get('class', '')
                found_links +=1
        assert found_links == 8, 'Wrong number of links found'

    def test_home_rename(self):
        assert 'The resource was found at http://localhost/p/test/wiki/Home/;' in self.app.get('/p/test/wiki/')
        req = self.app.get('/p/test/wiki/Home/edit')
        req.forms[1]['title'].value = 'new_title'
        req.forms[1].submit()
        assert 'The resource was found at http://localhost/p/test/wiki/new_title/;' in self.app.get('/p/test/wiki/')

    def test_page_delete(self):
        self.app.post('/wiki/aaa/update', params={
                'title':'aaa',
                'text':'',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        self.app.post('/wiki/bbb/update', params={
                'title':'bbb',
                'text':'',
                'labels':'',
                'labels_old':'',
                'viewable_by-0.id':'all'})
        response = self.app.get('/wiki/browse_pages/')
        assert 'aaa' in response
        assert 'bbb' in response
        self.app.post('/wiki/bbb/delete')
        response = self.app.get('/wiki/browse_pages/')
        assert 'aaa' in response
        assert '?deleted=True">bbb' in response

    def test_mailto_links(self):
        self.app.get('/wiki/test_mailto/')
        params = {
            'title':'test_mailto',
            'text':'''
* Automatic mailto #1 <darth.vader@deathstar.org>
* Automatic mailto #2 <mailto:luke.skywalker@tatooine.org>
* Handmaid mailto <a href="mailto:yoda@jedi.org">Email Yoda</a>
''',
            'labels':'',
            'labels_old':'',
            'viewable_by-0.id':'all'}
        self.app.post('/wiki/test_mailto/update', params=params)
        r = self.app.get('/wiki/test_mailto/')
        mailto_links = 0
        for link in r.html.findAll('a'):
            if link.get('href') == 'mailto:darth.vader@deathstar.org':
                assert 'notfound' not in link.get('class', '')
                mailto_links +=1
            if link.get('href') == 'mailto:luke.skywalker@tatooine.org':
                assert 'notfound' not in link.get('class', '')
                mailto_links += 1
            if link.get('href') == 'mailto:yoda@jedi.org':
                assert link.contents == ['Email Yoda']
                assert 'notfound' not in link.get('class', '')
                mailto_links += 1
        assert mailto_links == 3, 'Wrong number of mailto links'
