#-*- python -*-
import logging
from pprint import pformat
from urllib import urlencode, unquote
from datetime import datetime

# Non-stdlib imports
from tg import expose, validate, redirect, response, flash
from tg.decorators import with_trailing_slash, without_trailing_slash
from tg.controllers import RestController
from pylons import g, c, request
from formencode import validators
from webob import exc
from ming.orm import session

# Pyforge-specific imports
from allura import model as M
from allura.lib import helpers as h
from allura.app import Application, SitemapEntry, DefaultAdminController
from allura.lib.search import search
from allura.lib.decorators import require_post, Property
from allura.lib.security import require_access, has_access
from allura.controllers import AppDiscussionController, BaseController
from allura.controllers import DispatchIndex
from allura.controllers import attachments as ac
from allura.lib import widgets as w
from allura.lib.widgets import form_fields as ffw
from allura.lib.widgets.subscriptions import SubscribeForm
from allura.lib.widgets.search import SearchResults

# Local imports
from forgewiki import model as WM
from forgewiki import version
from forgewiki.widgets.wiki import CreatePageWidget

log = logging.getLogger(__name__)

class W:
    thread=w.Thread(
        page=None, limit=None, page_size=None, count=None,
        style='linear')
    create_page_lightbox = CreatePageWidget(name='create_wiki_page', trigger='#sidebar a.add_wiki_page')
    markdown_editor = ffw.MarkdownEdit()
    label_edit = ffw.LabelEdit()
    attachment_add = ffw.AttachmentAdd()
    attachment_list = ffw.AttachmentList()
    subscribe_form = SubscribeForm()
    page_subscribe_form = SubscribeForm(thing='page')
    page_list = ffw.PageList()
    page_size = ffw.PageSize()
    search_results = SearchResults()


class ForgeWikiApp(Application):
    '''This is the Wiki app for PyForge'''
    __version__ = version.__version__
    permissions = [ 'configure', 'read', 'create', 'edit', 'delete',
                    'unmoderated_post', 'post', 'moderate', 'admin']
    searchable=True
    tool_label='Wiki'
    default_mount_label='Wiki'
    default_mount_point='wiki'
    ordinal=5
    default_root_page_name = u'Home'
    icons={
        24:'images/wiki_24.png',
        32:'images/wiki_32.png',
        48:'images/wiki_48.png'
    }

    def __init__(self, project, config):
        Application.__init__(self, project, config)
        self.root = RootController()
        self.api_root = RootRestController()
        self.admin = WikiAdminController(self)

    def has_access(self, user, topic):
        return has_access(c.app, 'post')(user=user)

    def handle_message(self, topic, message):
        log.info('Message from %s (%s)',
                 topic, self.config.options.mount_point)
        log.info('Headers are: %s', message['headers'])
        try:
            page = WM.Page.upsert(topic)
        except:
            log.exception('Error getting artifact %s', topic)
        self.handle_artifact_message(page, message)

    @Property
    def root_page_name():
        def fget(self):
            globals = WM.Globals.query.get(app_config_id=self.config._id)
            if globals is not None:
                page_name = globals.root
            else:
                page_name = self.default_root_page_name
            return page_name
        def fset(self, new_root_page_name):
            globals = WM.Globals.query.get(app_config_id=self.config._id)
            if globals is not None:
                globals.root = new_root_page_name
            elif new_root_page_name != self.default_root_page_name:
                globals = WM.Globals(app_config_id=self.config._id, root=new_root_page_name)
            if globals is not None:
                session(globals).flush()

    @Property
    def show_discussion():
        def fget(self):
            return self.config.options.get('show_discussion', True)
        def fset(self, show):
            self.config.options['show_discussion'] = bool(show)

    @Property
    def show_left_bar():
        def fget(self):
            return self.config.options.get('show_left_bar', True)
        def fset(self, show):
            self.config.options['show_left_bar'] = bool(show)

    @Property
    def show_right_bar():
        def fget(self):
            return self.config.options.get('show_right_bar', True)
        def fset(self, show):
            self.config.options['show_right_bar'] = bool(show)

    def main_menu(self):
        '''Apps should provide their entries to be added to the main nav
        :return: a list of :class:`SitemapEntries <allura.app.SitemapEntry>`
        '''
        return [ SitemapEntry(
                self.config.options.mount_label.title(),
                '.')]

    @property
    @h.exceptionless([], log)
    def sitemap(self):
        menu_id = self.config.options.mount_label.title()
        with h.push_config(c, app=self):
            pages = [
                SitemapEntry(p.title, p.url())
                for p in WM.Page.query.find(dict(
                        app_config_id=self.config._id,
                        deleted=False)) ]
            return [
                SitemapEntry(menu_id, '.')[SitemapEntry('Pages')[pages]] ]

    def create_common_wiki_menu(self,
                                has_create_access,
                                create_page_url,
                                create_page_class,
                                admin_menu=False):
        links = []
        if has_create_access:
            links += [SitemapEntry('Create Page', create_page_url,
                                    ui_icon=g.icons['plus'],
                                    className=create_page_class)]
        if not admin_menu:
            links += [SitemapEntry(''),
                SitemapEntry('Wiki Home', self.url, className='wiki_home')]
        links += [SitemapEntry('Browse Pages', self.url + 'browse_pages/'),
                  SitemapEntry('Browse Labels', self.url + 'browse_tags/')]
        discussion = c.app.config.discussion
        pending_mod_count = M.Post.query.find({'discussion_id':discussion._id, 'status':'pending'}).count() if discussion else 0
        if pending_mod_count and h.has_access(discussion, 'moderate')():
            links.append(SitemapEntry('Moderate', discussion.url() + 'moderate', ui_icon=g.icons['pencil'],
                small = pending_mod_count))
        if not admin_menu:
            links += [SitemapEntry(''),
                SitemapEntry('Formatting Help',self.url+'markdown_syntax/')]
        return links

    def admin_menu(self):
        admin_url = c.project.url() + \
                    'admin/' + \
                    self.config.options.mount_point + '/'
        links = [SitemapEntry('Set Home',
                              admin_url + 'home',
                              className='admin_modal')]

        if not self.show_left_bar:
            links += self.create_common_wiki_menu(True,
                        admin_url + 'create_wiki_page',
                        'admin_modal', admin_menu=True)
        links += super(ForgeWikiApp, self).admin_menu(force_options=True)

        return links

    @h.exceptionless([], log)
    def sidebar_menu(self):
        try:
            page = request.path_info.split(self.url)[-1].split('/')[-2]
            page = h.really_unicode(page)
            page = WM.Page.query.find(dict(app_config_id=self.config._id, title=page, deleted=False)).first()
        except:
            page = None
        return self.create_common_wiki_menu(has_access(self, 'create'),c.app.url,'add_wiki_page')

    def install(self, project):
        'Set up any default permissions and roles here'
        self.config.options['project_name'] = project.name
        super(ForgeWikiApp, self).install(project)
        # Setup permissions
        role_admin = M.ProjectRole.by_name('Admin')._id
        role_developer = M.ProjectRole.by_name('Developer')._id
        role_member = M.ProjectRole.by_name('Member')._id
        role_auth = M.ProjectRole.by_name('*authenticated')._id
        role_anon = M.ProjectRole.by_name('*anonymous')._id
        self.config.acl = [
            M.ACE.allow(role_anon, 'read'),
            M.ACE.allow(role_auth, 'post'),
            M.ACE.allow(role_auth, 'unmoderated_post'),
            M.ACE.allow(role_member, 'create'),
            M.ACE.allow(role_member, 'edit'),
            M.ACE.allow(role_developer, 'delete'),
            M.ACE.allow(role_developer, 'moderate'),
            M.ACE.allow(role_admin, 'configure'),
            M.ACE.allow(role_admin, 'admin'),
            ]
        root_page_name = self.default_root_page_name
        WM.Globals(app_config_id=c.app.config._id, root=root_page_name)
        self.upsert_root(root_page_name)

    def upsert_root(self, new_root):
        p = WM.Page.query.get(app_config_id=self.config._id, title=new_root, deleted=False)
        if p is None:
            with h.push_config(c, app=self):
                p = WM.Page.upsert(new_root)
                p.viewable_by = ['all']
                url = c.app.url + 'markdown_syntax' + '/'
                p.text = """Welcome to your wiki!

This is the default page, edit it as you see fit. To add a new page simply reference it within brackets, e.g.: [SamplePage].

The wiki uses [Markdown](%s) syntax.

[[project_admins]]
[[download_button]]
""" % url
                p.commit()


    def uninstall(self, project):
        "Remove all the tool's artifacts from the database"
        WM.WikiAttachment.query.remove(dict(app_config_id=self.config._id))
        WM.Page.query.remove(dict(app_config_id=self.config._id))
        WM.Globals.query.remove(dict(app_config_id=self.config._id))
        super(ForgeWikiApp, self).uninstall(project)

class RootController(BaseController, DispatchIndex):

    def __init__(self):
        setattr(self, 'feed.atom', self.feed)
        setattr(self, 'feed.rss', self.feed)
        c.create_page_lightbox = W.create_page_lightbox
        self._discuss = AppDiscussionController()

    def _check_security(self):
        require_access(c.app, 'read')

    @with_trailing_slash
    @expose()
    def index(self, **kw):
        redirect(h.really_unicode(c.app.root_page_name).encode('utf-8')+'/')

    @expose()
    def _lookup(self, pname, *remainder):
        """Instantiate a Page object, and continue dispatch there."""
        # HACK: The TG request extension machinery will strip off the end of
        # a dotted wiki page name if it matches a known file extension. Here,
        # we reassemble the original page name.
        if request.response_ext and not remainder:
            pname += request.response_ext
        return PageController(pname), remainder

    @expose()
    def new_page(self, title):
        redirect(h.really_unicode(title).encode('utf-8') + '/')

    @with_trailing_slash
    @expose('jinja:forgewiki:templates/wiki/search.html')
    @validate(dict(q=validators.UnicodeString(if_empty=None),
                   history=validators.StringBool(if_empty=False),
                   project=validators.StringBool(if_empty=False)))
    def search(self, q=None, history=None, project=None, limit=None, page=0, **kw):
        'local wiki search'
        if project:
            redirect(c.project.url() + 'search?' + urlencode(dict(q=q, history=history)))
        results = []
        count=0
        limit, page, start = g.handle_paging(limit, page, default=25)
        if not q:
            q = ''
        else:
            results = search(
                q, rows=limit, start=start,
                fq=[
                    'is_history_b:%s' % history,
                    'project_id_s:%s' % c.project._id,
                    'mount_point_s:%s'% c.app.config.options.mount_point,
                    '-deleted_b:true'])
            if results: count=results.hits
        c.search_results = W.search_results
        return dict(q=q, history=history, results=results or [],
                    count=count, limit=limit, page=page)

    @with_trailing_slash
    @expose('jinja:forgewiki:templates/wiki/browse.html')
    @validate(dict(sort=validators.UnicodeString(if_empty='alpha'),
                   show_deleted=validators.StringBool(if_empty=False),
                   page=validators.Int(if_empty=0),
                   limit=validators.Int(if_empty=None)))
    def browse_pages(self, sort='alpha', show_deleted=False, page=0, limit=None, **kw):
        'list of all pages in the wiki'
        c.page_list = W.page_list
        c.page_size = W.page_size
        limit, pagenum, start = g.handle_paging(limit, page, default=25)
        count = 0
        pages = []
        uv_pages = []
        criteria = dict(app_config_id=c.app.config._id)
        can_delete = has_access(c.app, 'delete')()
        show_deleted = show_deleted and can_delete
        if not can_delete:
            criteria['deleted'] = False
        q = WM.Page.query.find(criteria)
        if sort == 'alpha':
            q = q.sort('title')
        count = q.count()
        q = q.skip(start).limit(int(limit))
        for page in q:
            recent_edit = page.history().first()
            p = dict(title=page.title, url=page.url(), deleted=page.deleted)
            if recent_edit:
                p['updated'] = recent_edit.timestamp
                p['user_label'] = recent_edit.author.display_name
                p['user_name'] = recent_edit.author.username
                pages.append(p)
            else:
                if sort == 'recent':
                    uv_pages.append(p)
                else:
                    pages.append(p)
        if sort == 'recent':
            pages.sort(reverse=True, key=lambda x:(x['updated']))
            pages = pages + uv_pages
        return dict(pages=pages, can_delete=can_delete, show_deleted=show_deleted,
                    limit=limit, count=count, page=pagenum)

    @with_trailing_slash
    @expose('jinja:forgewiki:templates/wiki/browse_tags.html')
    @validate(dict(sort=validators.UnicodeString(if_empty='alpha'),
                   page=validators.Int(if_empty=0),
                   limit=validators.Int(if_empty=None)))
    def browse_tags(self, sort='alpha', page=0, limit=None):
        'list of all labels in the wiki'
        c.page_list = W.page_list
        c.page_size = W.page_size
        limit, pagenum, start = g.handle_paging(limit, page, default=25)
        count = 0
        page_tags = {}
        q = WM.Page.query.find(dict(app_config_id=c.app.config._id, deleted=False))
        count = q.count()
        q = q.skip(start).limit(int(limit))
        for page in q:
            if page.labels:
                for label in page.labels:
                    if label not in page_tags:
                        page_tags[label] = []
                    page_tags[label].append(page)
        return dict(labels=page_tags, limit=limit, count=count, page=pagenum)

    @with_trailing_slash
    @expose('jinja:allura:templates/markdown_syntax.html')
    def markdown_syntax(self):
        'Display a page about how to use markdown.'
        return dict(example=MARKDOWN_EXAMPLE)

    @with_trailing_slash
    @expose('jinja:allura:templates/markdown_syntax_dialog.html')
    def markdown_syntax_dialog(self):
        'Display a page about how to use markdown.'
        return dict(example=MARKDOWN_EXAMPLE)

    @without_trailing_slash
    @expose()
    @validate(dict(
            since=h.DateTimeConverter(if_empty=None, if_invalid=None),
            until=h.DateTimeConverter(if_empty=None, if_invalid=None),
            offset=validators.Int(if_empty=None),
            limit=validators.Int(if_empty=None)))
    def feed(self, since=None, until=None, offset=None, limit=None):
        if request.environ['PATH_INFO'].endswith('.atom'):
            feed_type = 'atom'
        else:
            feed_type = 'rss'
        title = 'Recent changes to %s' % c.app.config.options.mount_point
        feed = M.Feed.feed(
            dict(project_id=c.project._id,app_config_id=c.app.config._id),
            feed_type,
            title,
            c.app.url,
            title,
            since, until, offset, limit)
        response.headers['Content-Type'] = ''
        response.content_type = 'application/xml'
        return feed.writeString('utf-8')

class PageController(BaseController):

    def __init__(self, title):
        self.title = h.really_unicode(unquote(title))
        self.page = WM.Page.query.get(
            app_config_id=c.app.config._id, title=self.title)
        if self.page is not None:
            self.attachment = WikiAttachmentsController(self.page)
        c.create_page_lightbox = W.create_page_lightbox
        setattr(self, 'feed.atom', self.feed)
        setattr(self, 'feed.rss', self.feed)

    def _check_security(self):
        if self.page:
            require_access(self.page, 'read')
            if self.page.deleted:
                require_access(self.page, 'delete')
        else:
            require_access(c.app, 'create')

    def fake_page(self):
        return dict(
            title=self.title,
            text='',
            labels=[],
            viewable_by=['all'],
            attachments=[])

    def get_version(self, version):
        if not version: return self.page
        try:
            return self.page.get_version(version)
        except ValueError:
            return None

    @expose()
    def _lookup(self, pname, *remainder):
        page = WM.Page.query.get(
            app_config_id=c.app.config._id, title=pname)
        if page:
            redirect(page.url())
        else:
            raise exc.HTTPNotFound

    @with_trailing_slash
    @expose('jinja:forgewiki:templates/wiki/page_view.html')
    @validate(dict(version=validators.Int(if_empty=None),
                   page=validators.Int(if_empty=0),
                   limit=validators.Int(if_empty=25)))
    def index(self, version=None, page=0, limit=25, **kw):
        if not self.page:
            redirect(c.app.url+h.urlquote(self.title)+'/edit')
        c.thread = W.thread
        c.attachment_list = W.attachment_list
        c.subscribe_form = W.page_subscribe_form
        post_count = self.page.discussion_thread.post_count
        limit, pagenum = h.paging_sanitizer(limit, page, post_count)
        page = self.get_version(version)
        if page is None:
            if version: redirect('.?version=%d' % (version-1))
            else: redirect('.')
        elif 'all' not in page.viewable_by and c.user.username not in page.viewable_by:
            raise exc.HTTPForbidden(detail="You may not view this page.")
        cur = page.version
        if cur > 1: prev = cur-1
        else: prev = None
        next = cur+1
        hide_left_bar = not (c.app.show_left_bar)
        return dict(
            page=page,
            cur=cur, prev=prev, next=next,
            subscribed=M.Mailbox.subscribed(artifact=self.page),
            hide_left_bar=hide_left_bar, show_meta=c.app.show_right_bar,
            pagenum=pagenum, limit=limit, count=post_count)

    @without_trailing_slash
    @expose('jinja:forgewiki:templates/wiki/page_edit.html')
    def edit(self):
        page_exists = self.page
        if self.page:
            require_access(self.page, 'edit')
            page = self.page
        else:
            page = self.fake_page()
        c.markdown_editor = W.markdown_editor
        c.user_select = ffw.ProjectUserSelect()
        c.attachment_add = W.attachment_add
        c.attachment_list = W.attachment_list
        c.label_edit = W.label_edit
        hide_left_bar = not c.app.show_left_bar
        return dict(page=page, page_exists=page_exists,
            hide_left_bar=hide_left_bar)

    @without_trailing_slash
    @expose('json')
    @require_post()
    def delete(self):
        require_access(self.page, 'delete')
        M.Shortlink.query.remove(dict(ref_id=self.page.index_id()))
        self.page.deleted = True
        suffix = " {dt.hour}:{dt.minute}:{dt.second} {dt.day}-{dt.month}-{dt.year}".format(dt=datetime.utcnow())
        self.page.title += suffix
        return dict(location='../'+self.page.title+'/?deleted=True')

    @without_trailing_slash
    @expose('json')
    @require_post()
    def undelete(self):
        require_access(self.page, 'delete')
        self.page.deleted = False
        M.Shortlink.from_artifact(self.page)
        return dict(location='./edit')

    @without_trailing_slash
    @expose('jinja:forgewiki:templates/wiki/page_history.html')
    @validate(dict(page=validators.Int(if_empty=0),
                   limit=validators.Int(if_empty=None)))
    def history(self, page=0, limit=None):
        if not self.page:
            raise exc.HTTPNotFound
        c.page_list = W.page_list
        c.page_size = W.page_size
        limit, pagenum, start = g.handle_paging(limit, page, default=25)
        count = 0
        pages = self.page.history()
        count = pages.count()
        pages = pages.skip(start).limit(int(limit))
        return dict(title=self.title, pages=pages,
                    limit=limit, count=count, page=pagenum)

    @without_trailing_slash
    @expose('jinja:forgewiki:templates/wiki/page_diff.html')
    @validate(dict(
            v1=validators.Int(),
            v2=validators.Int()))
    def diff(self, v1, v2, **kw):
        if not self.page:
            raise exc.HTTPNotFound
        p1 = self.get_version(v1)
        p2 = self.get_version(v2)
        result = h.diff_text(p1.text, p2.text)
        return dict(p1=p1, p2=p2, edits=result)

    @without_trailing_slash
    @expose(content_type='text/plain')
    def raw(self):
        if not self.page:
            raise exc.HTTPNotFound
        return pformat(self.page)

    @without_trailing_slash
    @expose()
    @validate(dict(
            since=h.DateTimeConverter(if_empty=None, if_invalid=None),
            until=h.DateTimeConverter(if_empty=None, if_invalid=None),
            offset=validators.Int(if_empty=None),
            limit=validators.Int(if_empty=None)))
    def feed(self, since=None, until=None, offset=None, limit=None):
        if not self.page:
            raise exc.HTTPNotFound
        if request.environ['PATH_INFO'].endswith('.atom'):
            feed_type = 'atom'
        else:
            feed_type = 'rss'
        feed = M.Feed.feed(
            {'ref_id':self.page.index_id()},
            feed_type,
            'Recent changes to %s' % self.page.title,
            self.page.url(),
            'Recent changes to %s' % self.page.title,
            since, until, offset, limit)
        response.headers['Content-Type'] = ''
        response.content_type = 'application/xml'
        return feed.writeString('utf-8')

    @without_trailing_slash
    @expose('json')
    @require_post()
    @validate(dict(version=validators.Int(if_empty=1)))
    def revert(self, version):
        if not self.page:
            raise exc.HTTPNotFound
        require_access(self.page, 'edit')
        orig = self.get_version(version)
        if orig:
            self.page.text = orig.text
        self.page.commit()
        return dict(location='.')

    @without_trailing_slash
    @h.vardec
    @expose()
    @require_post()
    def update(self, title=None, text=None,
               labels=None, labels_old=None,
               viewable_by=None,
               new_viewable_by=None,**kw):
        activity_verb = 'created'
        if not title:
            flash('You must provide a title for the page.','error')
            redirect('edit')
        title = title.replace('/', '-')
        if not self.page:
            # the page doesn't exist yet, so create it
            self.page = WM.Page.upsert(self.title)
            self.page.viewable_by = ['all']
        else:
            require_access(self.page, 'edit')
            activity_verb = 'modified'
        name_conflict = None
        if self.page.title != title:
            name_conflict = WM.Page.query.find(dict(app_config_id=c.app.config._id, title=title, deleted=False)).first()
            if name_conflict:
                flash('There is already a page named "%s".' % title, 'error')
            else:
                if self.page.title == c.app.root_page_name:
                    WM.Globals.query.get(app_config_id=c.app.config._id).root = title
                self.page.title = title
                activity_verb = 'renamed'
        self.page.text = text
        if labels:
            self.page.labels = labels.split(',')
        else:
            self.page.labels = []
        self.page.commit()
        g.director.create_activity(c.user, activity_verb, self.page,
                target=c.project)
        if new_viewable_by:
            if new_viewable_by == 'all':
                self.page.viewable_by.append('all')
            else:
                user = c.project.user_in_project(str(new_viewable_by))
                if user:
                    self.page.viewable_by.append(user.username)
        if viewable_by:
            for u in viewable_by:
                if u.get('delete'):
                    if u['id'] == 'all':
                        self.page.viewable_by.remove('all')
                    else:
                        user = M.User.by_username(str(u['id']))
                        if user:
                            self.page.viewable_by.remove(user.username)
        redirect('../' + h.really_unicode(self.page.title).encode('utf-8') + ('/' if not name_conflict else '/edit'))

    @without_trailing_slash
    @expose()
    @require_post()
    def attach(self, file_info=None):
        if not self.page:
            raise exc.HTTPNotFound
        require_access(self.page, 'edit')
        if hasattr(file_info, 'file'):
            self.page.attach(file_info.filename, file_info.file, content_type=file_info.type)
        redirect(request.referer)

    @expose()
    @validate(W.subscribe_form)
    def subscribe(self, subscribe=None, unsubscribe=None):
        if not self.page:
            raise exc.HTTPNotFound
        if subscribe:
            self.page.subscribe(type='direct')
        elif unsubscribe:
            self.page.unsubscribe()
        redirect(request.referer)

class WikiAttachmentController(ac.AttachmentController):
    AttachmentClass = WM.WikiAttachment
    edit_perm = 'edit'

class WikiAttachmentsController(ac.AttachmentsController):
    AttachmentControllerClass = WikiAttachmentController

MARKDOWN_EXAMPLE='''
# First-level heading

Some *emphasized* and **strong** text

#### Fourth-level heading

'''

class RootRestController(RestController):

    @expose('json:')
    def get_all(self, **kw):
        page_titles = []
        pages = WM.Page.query.find(dict(app_config_id=c.app.config._id, deleted=False))
        for page in pages:
            if has_access(page, 'read')():
                page_titles.append(page.title)
        return dict(pages=page_titles)

    @expose('json:')
    def get_one(self, title, **kw):
        page = WM.Page.query.get(app_config_id=c.app.config._id, title=h.really_unicode(title), deleted=False)
        if page is None:
            raise exc.HTTPNotFound, title
        require_access(page, 'read')
        return dict(title=page.title, text=page.text, labels=page.labels)

    @h.vardec
    @expose()
    @require_post()
    def post(self, title, **post_data):
        page = WM.Page.query.get(
            app_config_id=c.app.config._id,
            title=title,
            deleted=False)
        if not page:
            require_access(c.app, 'create')
            page = WM.Page.upsert(title)
            page.viewable_by = ['all']
        else:
            require_access(page, 'edit')
        page.text = post_data['text']
        if 'labels' in post_data:
            page.labels = post_data['labels'].split(',')
        page.commit()


class WikiAdminController(DefaultAdminController):

    def __init__(self, app):
        self.app = app

    def _check_security(self):
        require_access(self.app, 'configure')

    @with_trailing_slash
    def index(self, **kw):
        redirect('home')

    @without_trailing_slash
    @expose('jinja:forgewiki:templates/wiki/admin_home.html')
    def home(self):
        return dict(app=self.app,
                    home=self.app.root_page_name,
                    allow_config=has_access(self.app, 'configure')())

    @expose('jinja:forgewiki:templates/wiki/admin_add_page.html')
    def create_wiki_page(self):
        return dict(app=self.app,
                    allow_config=has_access(self.app, 'configure')())

    @without_trailing_slash
    @expose('jinja:forgewiki:templates/wiki/admin_options.html')
    def options(self):
        return dict(app=self.app,
                    allow_config=has_access(self.app, 'configure')())

    @without_trailing_slash
    @expose()
    @require_post()
    def set_home(self, new_home):
        self.app.root_page_name = new_home
        self.app.upsert_root(new_home)
        flash('Home updated')
        mount_base = c.project.url()+self.app.config.options.mount_point+'/'
        url = h.really_unicode(mount_base).encode('utf-8') + h.really_unicode(new_home).encode('utf-8')+'/'
        redirect(url)

    @without_trailing_slash
    @expose()
    @require_post()
    def set_options(self, show_discussion=False, show_left_bar=False, show_right_bar=False):
        self.app.show_discussion = show_discussion
        self.app.show_left_bar = show_left_bar
        self.app.show_right_bar = show_right_bar
        flash('Wiki options updated')
        redirect(c.project.url()+'admin/tools')
