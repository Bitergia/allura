from mock import patch

from allura.tests import TestController
from allura import model as M


class TestDiscuss(TestController):

    def test_subscribe_unsubscribe(self):
        home = self.app.get('/wiki/_discuss/')
        subscribed = [ i for i in home.html.findAll('input')
                       if i.get('type') == 'checkbox'][0]
        assert 'checked' not in subscribed.attrMap
        link = [ a for a in home.html.findAll('a')
                 if 'thread' in a['href'] ][0]
        params = {
            'threads-0._id':link['href'][len('/p/test/wiki/_discuss/thread/'):-1],
            'threads-0.subscription':'on' }
        r = self.app.post('/wiki/_discuss/subscribe',
                          params=params,
                          headers={'Referer':'/wiki/_discuss/'})
        r = r.follow()
        subscribed = [ i for i in r.html.findAll('input')
                       if i.get('type') == 'checkbox'][0]
        assert 'checked' in subscribed.attrMap
        params = {
            'threads-0._id':link['href'][len('/p/test/wiki/_discuss/thread/'):-1]
            }
        r = self.app.post('/wiki/_discuss/subscribe',
                          params=params,
                          headers={'Referer':'/wiki/_discuss/'})
        r = r.follow()
        subscribed = [ i for i in r.html.findAll('input')
                       if i.get('type') == 'checkbox'][0]
        assert 'checked' not in subscribed.attrMap

    def _make_post(self, text):
        home = self.app.get('/wiki/_discuss/')
        thread_link = [ a for a in home.html.findAll('a')
                 if 'thread' in a['href'] ][0]['href']
        thread = self.app.get(thread_link)
        for f in thread.html.findAll('form'):
            if f.get('action', '').endswith('/post'):
                break
        params = dict()
        inputs = f.findAll('input')
        for field in inputs:
            if field.has_key('name'):
                params[field['name']] = field.has_key('value') and field['value'] or ''
        params[f.find('textarea')['name']] = text
        r = self.app.post(f['action'].encode('utf-8'), params=params,
                          headers={'Referer':thread_link.encode("utf-8")},
                          extra_environ=dict(username='root'))
        r = r.follow()
        return r

    def test_post(self):
        home = self.app.get('/wiki/_discuss/')
        thread_link = [ a for a in home.html.findAll('a')
                 if 'thread' in a['href'] ][0]['href']
        r = self._make_post('This is a post')
        assert 'This is a post' in r, r
        post_link = str(r.html.find('div',{'class':'edit_post_form reply'}).find('form')['action'])
        r = self.app.get(post_link[:-2], status=302)
        r = self.app.get(post_link)
        post_form = r.html.find('form',{'action':post_link})
        params = dict()
        inputs = post_form.findAll('input')
        for field in inputs:
            if field.has_key('name'):
                params[field['name']] = field.has_key('value') and field['value'] or ''
        params[post_form.find('textarea')['name']] = 'This is a new post'
        r = self.app.post(post_link,
                          params=params,
                          headers={'Referer':thread_link.encode("utf-8")})
        r = r.follow()
        assert 'This is a new post' in r, r
        r = self.app.get(post_link)
        assert str(r).count('This is a new post') == 3
        post_form = r.html.find('form',{'action':post_link + 'reply'})
        params = dict()
        inputs = post_form.findAll('input')
        for field in inputs:
            if field.has_key('name'):
                params[field['name']] = field.has_key('value') and field['value'] or ''
        params[post_form.find('textarea')['name']] = 'Tis a reply'
        r = self.app.post(post_link + 'reply',
                          params=params,
                          headers={'Referer':post_link.encode("utf-8")})
        r = self.app.get(thread_link)
        assert 'Tis a reply' in r, r
        permalinks = [post.find('form')['action'].encode('utf-8') for post in r.html.findAll('div',{'class':'edit_post_form reply'})]
        self.app.post(permalinks[1]+'flag')
        self.app.post(permalinks[1]+'moderate', params=dict(delete='delete'))
        self.app.post(permalinks[0]+'moderate', params=dict(spam='spam'))

    def test_post_paging(self):
        home = self.app.get('/wiki/_discuss/')
        thread_link = [ a for a in home.html.findAll('a')
                 if 'thread' in a['href'] ][0]['href']
        # just make sure it doesn't 500
        r = self.app.get('%s?limit=50&page=0' % thread_link)

    @patch('allura.controllers.discuss.g.director.create_activity')
    def test_edit_post(self, create_activity):
        r = self._make_post('This is a post')
        assert create_activity.call_count == 1, create_activity.call_count
        assert create_activity.call_args[0][1] == 'posted'
        create_activity.reset_mock()
        thread_url = r.request.url
        reply_form = r.html.find('div',{'class':'edit_post_form reply'}).find('form')
        post_link = str(reply_form['action'])
        assert 'This is a post' in str(r.html.find('div',{'class':'display_post'}))
        assert 'Last edit:' not in str(r.html.find('div',{'class':'display_post'}))
        params = dict()
        inputs = reply_form.findAll('input')
        for field in inputs:
            if field.has_key('name'):
                params[field['name']] = field.has_key('value') and field['value'] or ''
        params[reply_form.find('textarea')['name']] = 'zzz'
        self.app.post(post_link, params)
        assert create_activity.call_count == 1, create_activity.call_count
        assert create_activity.call_args[0][1] == 'modified'
        r = self.app.get(thread_url)
        assert 'zzz' in str(r.html.find('div',{'class':'display_post'}))
        assert 'Last edit: Test Admin less than 1 minute ago' in str(r.html.find('div',{'class':'display_post'}))

class TestAttachment(TestController):

    def setUp(self):
        super(TestAttachment, self).setUp()
        home = self.app.get('/wiki/_discuss/')
        self.thread_link = [ a['href'].encode("utf-8")
                             for a in home.html.findAll('a')
                             if 'thread' in a['href'] ][0]
        thread = self.app.get(self.thread_link)
        for f in thread.html.findAll('form'):
            if f.get('action', '').endswith('/post'):
                break
        self.post_form_link = f['action'].encode('utf-8')
        params = dict()
        inputs = f.findAll('input')
        for field in inputs:
            if field.has_key('name'):
                params[field['name']] = field.has_key('value') and field['value'] or ''
        params[f.find('textarea')['name']] = 'Test Post'
        r = self.app.post(f['action'].encode('utf-8'), params=params,
                          headers={'Referer':self.thread_link})
        r = r.follow()
        self.post_link = str(r.html.find('div',{'class':'edit_post_form reply'}).find('form')['action'])

    def test_attach(self):
        r = self.app.post(self.post_link + 'attach',
                          upload_files=[('file_info', 'test.txt', 'HiThere!')])
        r = self.app.get(self.thread_link)
        for alink in r.html.findAll('a'):
            if 'attachment' in alink['href']:
                alink = str(alink['href'])
                break
        else:
            assert False, 'attachment link not found'
        r = self.app.get(alink)
        assert r.content_disposition == 'attachment;filename="test.txt"', 'Attachments should force download'
        r = self.app.post(self.post_link + 'attach',
                          upload_files=[('file_info', 'test.o12', 'HiThere!')])
        r = self.app.post(alink, params=dict(delete='on'))
