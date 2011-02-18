import logging
import shutil
from urllib import quote

from pylons import c, g
from tg import expose, redirect, url
from tg.decorators import with_trailing_slash, without_trailing_slash
from bson import ObjectId

from allura import version
from allura.lib import helpers as h
from allura import model as M
from allura.lib import security
from allura.lib.decorators import audit, require_post
from allura.app import Application, SitemapEntry, DefaultAdminController, ConfigOption

log = logging.getLogger(__name__)


class RepositoryApp(Application):
    END_OF_REF_ESCAPE='~'
    __version__ = version.__version__
    permissions = [
        'read', 'write', 'create',
        'unmoderated_post', 'post', 'moderate', 'admin',
        'configure']
    config_options = Application.config_options + [
        ConfigOption('cloned_from_project_id', ObjectId, None),
        ConfigOption('cloned_from_repo_id', ObjectId, None),
        ConfigOption('init_from_url', str, None)
        ]
    tool_label='Repository'
    default_mount_label='Code'
    default_mount_point='code'
    ordinal=2
    forkable=False
    default_branch_name=None # master or default or some such
    repo=None # override with a property in child class
    icons={
        24:'allura/images/code_24.png',
        32:'allura/images/code_32.png',
        48:'allura/images/code_48.png'
    }

    def __init__(self, project, config):
        Application.__init__(self, project, config)
        self.admin = RepoAdminController(self)

    @property
    @h.exceptionless([], log)
    def sitemap(self):
        menu_id = self.config.options.mount_label.title()
        with h.push_config(c, app=self):
            return [
                SitemapEntry(menu_id, '.')[self.sidebar_menu()] ]

    def admin_menu(self):
        admin_url = c.project.url()+'admin/'+self.config.options.mount_point+'/'
        links = [SitemapEntry('Viewable Files', admin_url + 'extensions', className='admin_modal')]
        links.append(SitemapEntry('Refresh Repository',
                                  c.project.url() + self.config.options.mount_point + '/refresh',
                                  className='nav_child'))
        if self.permissions and security.has_artifact_access('configure', app=self)():
            links.append(SitemapEntry('Permissions', admin_url + 'permissions', className='nav_child'))
        return links

    @h.exceptionless([], log)
    def sidebar_menu(self):
        if not self.repo or self.repo.status != 'ready':
            return [
                SitemapEntry(self.repo.status) ]
        if self.default_branch_name:
            default_branch_url = (
                c.app.url
                + url(quote(self.default_branch_name + self.END_OF_REF_ESCAPE))
                + '/')
        else:
            default_branch_url = c.app.url
        links = []
        if self.forkable and self.repo.status == 'ready':
            links.append(SitemapEntry('Fork', c.app.url + 'fork', ui_icon=g.icons['fork']))
        merge_request_count = self.repo.merge_requests_by_statuses('open').count()
        if merge_request_count:
            links += [
                SitemapEntry(
                    'Merge Requests', c.app.url + 'merge-requests/',
                    className='nav_child',
                    small=merge_request_count) ]
        if self.repo.upstream_repo.name:
            links += [
                SitemapEntry('Clone of'),
                SitemapEntry(self.repo.upstream_repo.name, self.repo.upstream_repo.url,
                             className='nav_child')
                ]
            if len(c.app.repo.branches):
                links.append(SitemapEntry('Request Merge', c.app.url + 'request_merge',
                             ui_icon=g.icons['merge'],
                             className='nav_child'))
            pending_upstream_merges = self.repo.pending_upstream_merges()
            if pending_upstream_merges:
                links.append(SitemapEntry(
                        'Pending Merges',
                        self.repo.upstream_repo.url + 'merge-requests/',
                        className='nav_child',
                        small=pending_upstream_merges))
        if self.repo.branches:
            links.append(SitemapEntry('Branches'))
            for b in self.repo.branches:
                links.append(SitemapEntry(
                        b.name, url(c.app.url, dict(branch='ref/' + b.name)),
                        className='nav_child',
                        small=b.count))
        if self.repo.repo_tags:
            links.append(SitemapEntry('Tags'))
            max_tags = 10
            for i, b in enumerate(self.repo.repo_tags):
                if i < max_tags:
                    links.append(SitemapEntry(
                            b.name, url(c.app.url, dict(branch='ref/' + b.name)),
                            className='nav_child',
                            small=b.count))
                elif i == max_tags:
                    links.append(
                        SitemapEntry(
                            'More Tags',
                            default_branch_url+'tags/',
                            className='nav_child'))
                    break
        return links

    def install(self, project):
        self.config.options['project_name'] = project.name
        super(RepositoryApp, self).install(project)
        role_developer = M.ProjectRole.by_name('Developer')._id
        role_auth = M.ProjectRole.authenticated()._id
        role_anon = M.ProjectRole.anonymous()._id
        self.config.acl.update(
            configure=c.project.roleids_with_permission('tool'),
            read=c.project.roleids_with_permission('read'),
            create=[role_developer],
            write=[role_developer],
            unmoderated_post=[role_auth],
            post=[role_anon],
            moderate=[role_developer],
            admin=c.project.roleids_with_permission('tool'))

    def uninstall(self, project):
        g.publish('audit', 'repo.uninstall', dict(project_id=project._id))

    @classmethod
    @audit('repo.init')
    def _init(cls, routing_key, data):
        c.app.repo.init()
        M.Notification.post_user(
            c.user, c.app.repo, 'created',
            text='Repository %s/%s created' % (
                c.project.shortname, c.app.config.options.mount_point))

    @classmethod
    @audit('repo.clone')
    def _clone(cls, routing_key, data):
        if not c.app.forkable:
            return cls._init(cls, routing_key, data)
        c.app.repo.init_as_clone(
            data['cloned_from_path'],
            data['cloned_from_name'],
            data['cloned_from_url'])
        M.Notification.post_user(
            c.user, c.app.repo, 'created',
            text='Repository %s/%s created' % (
                c.project.shortname, c.app.config.options.mount_point))

    @classmethod
    @audit('repo.refresh')
    def _refresh(cls, routing_key, data):
        c.app.repo.refresh()

    @classmethod
    @audit('repo.uninstall')
    def _uninstall(cls, routing_key, data):
        "Remove all the tool's artifacts and the physical repository"
        repo = c.app.repo
        if repo is not None:
            shutil.rmtree(repo.full_fs_path, ignore_errors=True)
            repo.delete()
        M.MergeRequest.query.remove(dict(
                app_config_id=c.app.config._id))
        super(RepositoryApp, c.app).uninstall(project=c.project)

class RepoAdminController(DefaultAdminController):

    def __init__(self, app):
        self.app = app
        self.repo = app.repo

    def _check_security(self):
        security.require(security.has_artifact_access('configure', app=self.app))

    @with_trailing_slash
    @expose()
    def index(self, **kw):
        redirect('extensions')

    @without_trailing_slash
    @expose('jinja:repo/admin_extensions.html')
    def extensions(self, **kw):
        return dict(app=self.app,
                    allow_config=True,
                    additional_viewable_extensions=getattr(self.repo, 'additional_viewable_extensions', ''))

    @without_trailing_slash
    @expose()
    @require_post()
    def set_extensions(self, **post_data):
        self.repo.additional_viewable_extensions = post_data['additional_viewable_extensions']
