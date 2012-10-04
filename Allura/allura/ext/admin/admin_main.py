import logging
from collections import defaultdict
from datetime import datetime

import pkg_resources
from pylons import c, g, request
from paste.deploy.converters import asbool
from tg import expose, redirect, flash, validate, config
from tg.decorators import with_trailing_slash, without_trailing_slash
from webob import exc
from bson import ObjectId

from allura.app import Application, DefaultAdminController, SitemapEntry
from allura.lib import helpers as h
from allura import version
from allura import model as M
from allura.lib.security import has_access, require_access
from allura.lib.widgets import form_fields as ffw
from allura.lib import exceptions as forge_exc
from allura.lib import plugin
from allura.controllers import BaseController
from allura.lib.decorators import require_post

from . import widgets as aw
from allura.lib.widgets.project_list import ProjectScreenshots

log = logging.getLogger(__name__)

class W:
    markdown_editor = ffw.MarkdownEdit()
    label_edit = ffw.LabelEdit()
    mount_delete = ffw.Lightbox(name='mount_delete',trigger='a.mount_delete')
    admin_modal = ffw.Lightbox(name='admin_modal',trigger='a.admin_modal')
    install_modal = ffw.Lightbox(name='install_modal',trigger='a.install_trig')
    explain_export_modal = ffw.Lightbox(name='explain_export',trigger='#why_export')
    group_card = aw.GroupCard()
    permission_card = aw.PermissionCard()
    group_settings = aw.GroupSettings()
    new_group_settings = aw.NewGroupSettings()
    screenshot_admin = aw.ScreenshotAdmin()
    screenshot_list = ProjectScreenshots()
    metadata_admin = aw.MetadataAdmin()
    audit = aw.AuditLog()
    page_list=ffw.PageList()



class AdminApp(Application):
    '''This is the admin app.  It is pretty much required for
    a functioning allura project.
    '''
    __version__ = version.__version__
    installable=False
    _installable_tools = None
    tool_label = 'admin'
    icons={
        24:'images/admin_24.png',
        32:'images/admin_32.png',
        48:'images/admin_48.png'
    }

    def __init__(self, project, config):
        Application.__init__(self, project, config)
        self.root = ProjectAdminController()
        self.admin = AdminAppAdminController(self)
        self.templates = pkg_resources.resource_filename('allura.ext.admin', 'templates')
        self.sitemap = [ SitemapEntry('Admin','.')]

    def is_visible_to(self, user):
        '''Whether the user can view the app.'''
        return has_access(c.project, 'create')(user=user)

    @staticmethod
    def installable_tools_for(project):
        cls = AdminApp
        if cls._installable_tools is None:
            tools = [dict(name=k, app=v) for k,v in g.entry_points['tool'].iteritems()]
            tools.sort(key=lambda t:(t['app'].status_int(), t['app'].ordinal))
            cls._installable_tools = [ t for t in tools if t['app'].installable ]
        return [ t for t in cls._installable_tools
            if t['app'].status in project.allowed_tool_status ]

    def main_menu(self):
        '''Apps should provide their entries to be added to the main nav
        :return: a list of :class:`SitemapEntries <allura.app.SitemapEntry>`
        '''
        return [SitemapEntry('Admin', '.')]

    @h.exceptionless([], log)
    def sidebar_menu(self):
        links = []
        admin_url = c.project.url()+'admin/'


        if c.project.is_nbhd_project:
            links.append(SitemapEntry('Add Project', c.project.url()+'add_project', ui_icon=g.icons['plus']))
            nbhd_admin_url = c.project.neighborhood.url()+'_admin/'
            links = links + [
                     SitemapEntry('Neighborhood'),
                     SitemapEntry('Overview', nbhd_admin_url+'overview'),
                     SitemapEntry('Awards', nbhd_admin_url+'accolades')]
        else:
            links += [SitemapEntry('Metadata', admin_url+'overview'),]
            if c.project.neighborhood.name != "Users":
                links += [
                    SitemapEntry('Screenshots', admin_url+'screenshots'),
                    SitemapEntry('Categorization', admin_url+'trove')
                ]
        links.append(SitemapEntry('Tools', admin_url+'tools'))
        if c.project.is_root and has_access(c.project, 'admin')():
            links.append(SitemapEntry('User Permissions', admin_url+'groups/'))
        if not c.project.is_root and has_access(c.project, 'admin')():
            links.append(SitemapEntry('Permissions', admin_url+'permissions/'))
        if len(c.project.neighborhood_invitations):
            links.append(SitemapEntry('Invitation(s)', admin_url+'invitations'))
        links.append(SitemapEntry('Audit Trail', admin_url+ 'audit/'))
        if c.project.is_nbhd_project:
            links.append(SitemapEntry('Statistics', nbhd_admin_url+ 'stats/'))
            links.append(None)
            links.append(SitemapEntry('Help', nbhd_admin_url+ 'help/'))
        return links

    def admin_menu(self):
        return []

    def install(self, project):
        pass

class ProjectAdminController(BaseController):

    def _check_security(self):
        require_access(c.project, 'admin')

    def __init__(self):
        self.permissions = PermissionsController()
        self.groups = GroupsController()
        self.audit = AuditController()

    @with_trailing_slash
    @expose('jinja:allura.ext.admin:templates/project_admin.html')
    def index(self, **kw):
        return dict()

    @without_trailing_slash
    @expose('jinja:allura.ext.admin:templates/project_invitations.html')
    def invitations(self):
        return dict()

    @without_trailing_slash
    @expose('jinja:allura.ext.admin:templates/project_overview.html')
    def overview(self, **kw):
        c.markdown_editor = W.markdown_editor
        c.metadata_admin = W.metadata_admin
        c.explain_export_modal = W.explain_export_modal
        show_export_control = asbool(config.get('show_export_control', False))
        allow_project_delete = asbool(config.get('allow_project_delete', True))
        explain_export_text = '''The purpose of this section is to determine whether your project is subject to the provisions of the
        US Export Administration Regulations. You should consult section 734.4 and Supplement 2 to Part 734 for information on such items
        and the calculation of U.S. controlled content.
        <a href="http://www.bis.doc.gov/encryption/default.htm" target="_blank">http://www.bis.doc.gov/encryption/default.htm</a>'''
        if 'us_export_contact' in config:
            explain_export_text += 'If you have additional questions, please contact <a href="mailto:{contact}">{contact}</a>.'.format(
                contact=config['us_export_contact']
            )
        return dict(show_export_control=show_export_control,
                    allow_project_delete=allow_project_delete,
                    explain_export_text=explain_export_text)

    @without_trailing_slash
    @expose('jinja:allura.ext.admin:templates/project_screenshots.html')
    def screenshots(self, **kw):
        c.screenshot_admin = W.screenshot_admin
        c.screenshot_list = W.screenshot_list
        return dict()

    @without_trailing_slash
    @expose('jinja:allura.ext.admin:templates/project_trove.html')
    def trove(self):
        c.label_edit = W.label_edit
        base_troves = M.TroveCategory.query.find(dict(trove_parent_id=0)).sort('fullname').all()
        topic_trove = M.TroveCategory.query.get(trove_parent_id=0,shortname='topic')
        license_trove = M.TroveCategory.query.get(trove_parent_id=0,shortname='license')
        return dict(base_troves=base_troves,license_trove=license_trove,topic_trove=topic_trove)

    @without_trailing_slash
    @expose('jinja:allura.ext.admin:templates/project_tools.html')
    def tools(self, **kw):
        c.markdown_editor = W.markdown_editor
        c.label_edit = W.label_edit
        c.mount_delete = W.mount_delete
        c.admin_modal = W.admin_modal
        c.install_modal = W.install_modal
        mounts = c.project.ordered_mounts()
        return dict(
            mounts=mounts,
            installable_tools=AdminApp.installable_tools_for(c.project),
            roles=M.ProjectRole.query.find(dict(project_id=c.project.root_project._id)).sort('_id').all(),
            categories=M.ProjectCategory.query.find(dict(parent_id=None)).sort('label').all())

    @expose()
    @require_post()
    def update_labels(self, labels=None, labels_old=None, **kw):
        require_access(c.project, 'admin')
        c.project.labels = labels.split(',')
        M.AuditLog.log('updated labels')
        redirect('trove')

    @without_trailing_slash
    @expose()
    def clone(self,
              repo_type=None, source_url=None,
              mount_point=None, mount_label=None,
              **kw):
        require_access(c.project, 'admin')
        if repo_type is None:
            return (
                '<form method="get">'
                '<input name="repo_type" value="Git">'
                '<input name="source_url">'
                '<input type="submit">'
                '</form>')
        for ep in pkg_resources.iter_entry_points('allura', repo_type):
            break
        if ep is None or source_url is None:
            raise exc.HTTPNotFound
        h.log_action(log, 'install tool').info(
            'clone repo from %s', source_url,
            meta=dict(tool_type=repo_type, mount_point=mount_point, mount_label=mount_label))
        c.project.install_app(
            repo_type,
            mount_point=mount_point,
            mount_label=mount_label,
            init_from_url=source_url)
        M.AuditLog.log('Create repo as clone')
        redirect('tools')

    @without_trailing_slash
    @expose('jinja:allura.ext.admin:templates/project_permissions.html')
    def groups(self, **kw):
        return dict()

    @expose()
    def _lookup(self, name, *remainder):
        app = c.project.app_instance(name)
        if app is None:
            raise exc.HTTPNotFound, name
        return app.admin, remainder

    @expose()
    @require_post()
    @validate(W.metadata_admin, error_handler=overview)
    def update(self, name=None,
               short_description=None,
               summary='',
               icon=None,
               category=None,
               external_homepage='',
               support_page='',
               support_page_url='',
               removal='',
               moved_to_url='',
               export_controlled=False,
               export_control_type=None,
               tracking_id='',
               **kw):
        require_access(c.project, 'update')

        if removal != c.project.removal:
            M.AuditLog.log('change project removal status to %s', removal)
            h.log_action(log, 'change project removal status').info('')
            c.project.removal = removal
            c.project.removal_changed_date = datetime.utcnow()
        if 'delete_icon' in kw:
            M.ProjectFile.query.remove(dict(project_id=c.project._id, category='icon'))
            M.AuditLog.log('remove project icon')
            h.log_action(log, 'remove project icon').info('')
            g.post_event('project_updated')
            redirect('overview')
        elif 'delete' in kw:
            allow_project_delete = asbool(config.get('allow_project_delete', True))
            if allow_project_delete or not c.project.is_root:
                M.AuditLog.log('delete project')
                h.log_action(log, 'delete project').info('')
                plugin.ProjectRegistrationProvider.get().delete_project(c.project, c.user)
            redirect('overview')
        elif 'undelete' in kw:
            h.log_action(log, 'undelete project').info('')
            M.AuditLog.log('undelete project')
            plugin.ProjectRegistrationProvider.get().undelete_project(c.project, c.user)
            redirect('overview')
        if name != c.project.name:
            h.log_action(log, 'change project name').info('')
            M.AuditLog.log('change project name to %s', name)
            c.project.name = name
        if short_description != c.project.short_description:
            h.log_action(log, 'change project short description').info('')
            M.AuditLog.log('change short description to %s', short_description)
            c.project.short_description = short_description
        if summary != c.project.summary:
            h.log_action(log, 'change project summary').info('')
            M.AuditLog.log('change summary to %s', summary)
            c.project.summary = summary
        category = category and ObjectId(category) or None
        if category != c.project.category_id:
            h.log_action(log, 'change project category').info('')
            M.AuditLog.log('change category to %s', category)
            c.project.category_id = category
        if external_homepage != c.project.external_homepage:
            h.log_action(log, 'change external home page').info('')
            M.AuditLog.log('change external home page to %s', external_homepage)
            c.project.external_homepage = external_homepage
        if support_page != c.project.support_page:
            h.log_action(log, 'change project support page').info('')
            M.AuditLog.log('change project support page to %s', support_page)
            c.project.support_page = support_page
        if support_page_url != c.project.support_page_url:
            h.log_action(log, 'change project support page url').info('')
            M.AuditLog.log('change project support page url to %s', support_page_url)
            c.project.support_page_url = support_page_url
        if moved_to_url != c.project.moved_to_url:
            h.log_action(log, 'change project moved to url').info('')
            M.AuditLog.log('change project moved to url to %s', moved_to_url)
            c.project.moved_to_url = moved_to_url
        if export_controlled != c.project.export_controlled:
            h.log_action(log, 'change project export controlled status').info('')
            M.AuditLog.log('change project export controlled status to %s', export_controlled)
            c.project.export_controlled = not not export_controlled
            if not export_controlled:
                export_control_type = None
        if export_control_type != c.project.export_control_type:
            h.log_action(log, 'change project export control type').info('')
            M.AuditLog.log('change project export control type to %s', export_control_type)
            c.project.export_control_type = export_control_type
        if tracking_id != c.project.tracking_id:
            h.log_action(log, 'change project tracking ID').info('')
            M.AuditLog.log('change project tracking ID to %s', tracking_id)
            c.project.tracking_id = tracking_id

        if icon is not None and icon != '':
            if c.project.icon:
                M.ProjectFile.remove(dict(project_id=c.project._id, category='icon'))
            M.AuditLog.log('update project icon')
            M.ProjectFile.save_image(
                icon.filename, icon.file, content_type=icon.type,
                square=True, thumbnail_size=(48,48),
                thumbnail_meta=dict(project_id=c.project._id,category='icon'))
        g.post_event('project_updated')
        redirect('overview')

    def _add_trove(self, type, new_trove):
        current_troves = getattr(c.project,'trove_%s'%type)
        trove_obj = M.TroveCategory.query.get(trove_cat_id=int(new_trove))
        error_msg = None
        if type in ['license','audience','developmentstatus','language'] and len(current_troves) >= 6:
            error_msg = 'You may not have more than 6 of this category.'
        elif type in ['topic'] and len(current_troves) >= 3:
            error_msg = 'You may not have more than 3 of this category.'
        elif trove_obj is not None:
            if trove_obj._id not in current_troves:
                current_troves.append(trove_obj._id)
                g.post_event('project_updated')
            else:
                error_msg = 'This category has already been assigned to the project.'
        return (trove_obj, error_msg)

    @expose('json:')
    @require_post()
    def add_trove_js(self, type, new_trove, **kw):
        require_access(c.project, 'update')
        trove_obj, error_msg = self._add_trove(type, new_trove)
        return dict(trove_full_path = trove_obj.fullpath, trove_cat_id = trove_obj.trove_cat_id, error_msg=error_msg)
        redirect('trove')

    @expose()
    @require_post()
    def add_trove(self, type, new_trove, **kw):
        require_access(c.project, 'update')
        trove_obj, error_msg = self._add_trove(type, new_trove)
        M.AuditLog.log('add trove %s: %s', type, trove_obj.fullpath)
        if error_msg:
            flash(error_msg,'error')
        redirect('trove')

    @expose()
    @require_post()
    def delete_trove(self, type, trove, **kw):
        require_access(c.project, 'update')
        trove_obj = M.TroveCategory.query.get(trove_cat_id=int(trove))
        current_troves = getattr(c.project,'trove_%s'%type)
        if trove_obj is not None and trove_obj._id in current_troves:
            M.AuditLog.log('remove trove %s: %s', type, trove_obj.fullpath)
            current_troves.remove(trove_obj._id)
            g.post_event('project_updated')
        redirect('trove')

    @expose()
    @require_post()
    @validate(W.screenshot_admin)
    def add_screenshot(self, screenshot=None, caption=None, **kw):
        require_access(c.project, 'update')
        if len(c.project.get_screenshots()) >= 6:
            flash('You may not have more than 6 screenshots per project.','error')
        elif screenshot is not None and screenshot != '':
            M.AuditLog.log('add screenshot')
            M.ProjectFile.save_image(
                screenshot.filename, screenshot.file, content_type=screenshot.type,
                save_original=True,
                original_meta=dict(project_id=c.project._id,category='screenshot',caption=caption),
                square=True, thumbnail_size=(150,150),
                thumbnail_meta=dict(project_id=c.project._id,category='screenshot_thumb'))
            g.post_event('project_updated')
        redirect('screenshots')

    @expose()
    @require_post()
    def delete_screenshot(self, id=None, **kw):
        require_access(c.project, 'update')
        if id is not None and id != '':
            M.AuditLog.log('remove screenshot')
            M.ProjectFile.query.remove(dict(project_id=c.project._id, _id=ObjectId(id)))
            g.post_event('project_updated')
        redirect('screenshots')

    @expose()
    @require_post()
    def edit_screenshot(self, id=None, caption=None, **kw):
        require_access(c.project, 'update')
        if id is not None and id != '':
            M.ProjectFile.query.get(project_id=c.project._id, _id=ObjectId(id)).caption=caption
            g.post_event('project_updated')
        redirect('screenshots')

    @expose()
    @require_post()
    def join_neighborhood(self, nid):
        require_access(c.project, 'admin')
        if not nid:
            n = M.Neighborhood.query.get(name='Projects')
            c.project.neighborhood_id = n._id
            flash('Joined %s' % n.name)
            redirect(c.project.url() + 'admin/')
        nid = ObjectId(str(nid))
        if nid not in c.project.neighborhood_invitations:
            flash('No invitation to that neighborhood', 'error')
            redirect('.')
        c.project.neighborhood_id = nid
        n = M.Neighborhood.query.get(_id=nid)
        flash('Joined %s' % n.name)
        redirect('invitations')

    @h.vardec
    @expose()
    @require_post()
    def update_mount_order(self, subs=None, tools=None, **kw):
        if subs:
            for sp in subs:
                p = M.Project.query.get(shortname=sp['shortname'],
                                        neighborhood_id=c.project.neighborhood_id)
                p.ordinal = int(sp['ordinal'])
        if tools:
            for p in tools:
                c.project.app_config(p['mount_point']).options.ordinal = int(p['ordinal'])
        redirect('tools')

    @h.vardec
    @expose()
    @require_post()
    def update_mounts(self, subproject=None, tool=None, new=None, **kw):
        if subproject is None: subproject = []
        if tool is None: tool = []
        for sp in subproject:
            p = M.Project.query.get(shortname=sp['shortname'],
                                    neighborhood_id=c.project.neighborhood_id)
            if sp.get('delete'):
                require_access(c.project, 'admin')
                M.AuditLog.log('delete subproject %s', sp['shortname'])
                h.log_action(log, 'delete subproject').info(
                    'delete subproject %s', sp['shortname'],
                    meta=dict(name=sp['shortname']))
                p.removal = 'deleted'
                plugin.ProjectRegistrationProvider.get().delete_project(p, c.user)
            elif not new:
                M.AuditLog.log('update subproject %s', sp['shortname'])
                p.name = sp['name']
                p.ordinal = int(sp['ordinal'])
        for p in tool:
            if p.get('delete'):
                require_access(c.project, 'admin')
                M.AuditLog.log('uninstall tool %s', p['mount_point'])
                h.log_action(log, 'uninstall tool').info(
                    'uninstall tool %s', p['mount_point'],
                    meta=dict(mount_point=p['mount_point']))
                c.project.uninstall_app(p['mount_point'])
            elif not new:
                M.AuditLog.log('update tool %s', p['mount_point'])
                options = c.project.app_config(p['mount_point']).options
                options.mount_label = p['mount_label']
                options.ordinal = int(p['ordinal'])
        try:
            if new and new.get('install'):
                ep_name = new.get('ep_name', None)
                if not ep_name:
                    require_access(c.project, 'create')
                    mount_point = new['mount_point'].lower() or h.nonce()
                    M.AuditLog.log('create subproject %s', mount_point)
                    h.log_action(log, 'create subproject').info(
                        'create subproject %s', mount_point,
                        meta=dict(mount_point=mount_point,name=new['mount_label']))
                    sp = c.project.new_subproject(mount_point)
                    sp.name = new['mount_label']
                    sp.ordinal = int(new['ordinal'])
                else:
                    require_access(c.project, 'admin')
                    mount_point = new['mount_point'].lower() or ep_name.lower()
                    M.AuditLog.log('install tool %s', mount_point)
                    h.log_action(log, 'install tool').info(
                        'install tool %s', mount_point,
                        meta=dict(tool_type=ep_name, mount_point=mount_point, mount_label=new['mount_label']))
                    c.project.install_app(ep_name, mount_point, mount_label=new['mount_label'], ordinal=new['ordinal'])
        except forge_exc.ForgeError, exc:
            flash('%s: %s' % (exc.__class__.__name__, exc.args[0]),
                  'error')
        g.post_event('project_updated')
        redirect('tools')

class PermissionsController(BaseController):

    def _check_security(self):
        require_access(c.project, 'admin')

    @with_trailing_slash
    @expose('jinja:allura.ext.admin:templates/project_permissions.html')
    def index(self, **kw):
        c.card = W.permission_card
        return dict(permissions=self._index_permissions())

    @without_trailing_slash
    @expose()
    @h.vardec
    @require_post()
    def update(self, card=None, **kw):
        permissions = self._index_permissions()
        old_permissions = dict(permissions)
        for args in card:
            perm = args['id']
            new_group_ids = args.get('new', [])
            group_ids = args.get('value', [])
            if isinstance(new_group_ids, basestring):
                new_group_ids = [ new_group_ids ]
            if isinstance(group_ids, basestring):
                group_ids = [ group_ids ]
            # make sure the admin group has the admin permission
            if perm == 'admin':
                if c.project.is_root:
                    pid = c.project._id
                else:
                    pid = c.project.parent_id
                admin_group_id = str(M.ProjectRole.query.get(project_id=pid, name='Admin')._id)
                if admin_group_id not in group_ids + new_group_ids:
                    flash('You cannot remove the admin group from the admin permission.','warning')
                    group_ids.append(admin_group_id)
            permissions[perm] = []
            role_ids = map(ObjectId, group_ids + new_group_ids)
            permissions[perm] = role_ids
        c.project.acl = []
        for perm, role_ids in permissions.iteritems():
            role_names = lambda ids: ','.join(sorted(
                    pr.name for pr in M.ProjectRole.query.find(dict(_id={'$in':ids}))))
            old_role_ids = old_permissions.get(perm, [])
            if old_role_ids != role_ids:
                M.AuditLog.log('updated "%s" permissions: "%s" => "%s"',
                               perm,role_names(old_role_ids), role_names(role_ids))
            c.project.acl += [M.ACE.allow(rid, perm) for rid in role_ids]
        g.post_event('project_updated')
        redirect('.')

    def _index_permissions(self):
        permissions = dict(
            (p,[]) for p in c.project.permissions)
        for ace in c.project.acl:
            if ace.access == M.ACE.ALLOW:
                permissions[ace.permission].append(ace.role_id)
        return permissions

class GroupsController(BaseController):

    def _check_security(self):
        require_access(c.project, 'admin')

    def _index_permissions(self):
        permissions = dict(
            (p,[]) for p in c.project.permissions)
        for ace in c.project.acl:
            if ace.access == M.ACE.ALLOW:
                permissions[ace.permission].append(ace.role_id)
        return permissions

    def _map_group_permissions(self):
        roles = c.project.named_roles
        permissions=self._index_permissions()
        permissions_by_role = dict()
        auth_role = M.ProjectRole.authenticated()
        anon_role = M.ProjectRole.anonymous()
        for role in roles+[auth_role, anon_role]:
            permissions_by_role[str(role._id)] = []
            for perm in permissions:
                perm_info = dict(has="no", text="Does not have permission %s" % perm, name=perm)
                role_ids = permissions[perm]
                if role._id in role_ids:
                    perm_info['text'] = "Has permission %s" % perm
                    perm_info['has'] = "yes"
                else:
                    for r in role.child_roles():
                        if r._id in role_ids:
                            perm_info['text'] = "Inherited permission %s from %s" % (perm, r.name)
                            perm_info['has'] = "inherit"
                            break
                if perm_info['has'] == "no":
                    if anon_role._id in role_ids:
                        perm_info['text'] = "Inherited permission %s from Anonymous" % perm
                        perm_info['has'] = "inherit"
                    elif auth_role._id in role_ids and role != anon_role:
                        perm_info['text'] = "Inherited permission %s from Authenticated" % perm
                        perm_info['has'] = "inherit"
                permissions_by_role[str(role._id)].append(perm_info)
        return permissions_by_role

    @without_trailing_slash
    @expose()
    @h.vardec
    def delete_group(self, group_name, **kw):
        role = M.ProjectRole.by_name(group_name)
        if not role:
            flash('Group "%s" does not exist.' % group_name, 'error')
        else:
            role.delete()
            M.AuditLog.log('delete group %s', group_name)
            flash('Group "%s" deleted successfully.' % group_name)
            g.post_event('project_updated')
        redirect('.')

    @with_trailing_slash
    @expose('jinja:allura.ext.admin:templates/project_groups.html')
    def index(self, **kw):
        c.admin_modal = W.admin_modal
        c.card = W.group_card
        permissions_by_role = self._map_group_permissions()
        auth_role = M.ProjectRole.authenticated()
        anon_role = M.ProjectRole.anonymous()
        roles = c.project.named_roles
        roles.append(None)
        return dict(roles=roles, permissions_by_role=permissions_by_role,
                    auth_role=auth_role, anon_role=anon_role)

    @without_trailing_slash
    @expose('json:')
    @require_post()
    @h.vardec
    def change_perm(self, role_id, permission, allow="true", **kw):
        if allow=="true":
            M.AuditLog.log('granted permission %s to group with id %s', permission, role_id)
            c.project.acl.append(M.ACE.allow(ObjectId(role_id), permission))
        else:
            admin_group_id = str(M.ProjectRole.by_name('Admin')._id)
            if admin_group_id == role_id and permission == 'admin':
                return dict(error='You cannot remove the admin permission from the admin group.')
            M.AuditLog.log('revoked permission %s from group with id %s', permission, role_id)
            c.project.acl.remove(M.ACE.allow(ObjectId(role_id), permission))
        g.post_event('project_updated')
        return self._map_group_permissions()

    @without_trailing_slash
    @expose('json:')
    @require_post()
    @h.vardec
    def add_user(self, role_id, username, **kw):
        if not username or username=='*anonymous':
            return dict(error='You must choose a user to add.')
        group = M.ProjectRole.query.get(_id=ObjectId(role_id))
        user = M.User.by_username(username.strip())
        if not group:
            return dict(error='Could not find group with id %s' % role_id)
        if not user:
            return dict(error='User %s not found' % username)
        if group._id in user.project_role().roles:
            return dict(error='%s (%s) is already in the group %s.' % (user.display_name, username, group.name))
        M.AuditLog.log('add user %s to %s', username, group.name)
        user.project_role().roles.append(group._id)
        g.post_event('project_updated')
        return dict(username=username, displayname=user.display_name)

    @without_trailing_slash
    @expose('json:')
    @require_post()
    @h.vardec
    def remove_user(self, role_id, username, **kw):
        group = M.ProjectRole.query.get(_id=ObjectId(role_id))
        user = M.User.by_username(username.strip())
        if group.name == 'Admin' and len(group.users_with_role()) == 1:
            return dict(error='You must have at least one user with the Admin role.')
        if not group:
            return dict(error='Could not find group with id %s' % role_id)
        if not user:
            return dict(error='User %s not found' % username)
        if group._id not in user.project_role().roles:
            return dict(error='%s (%s) is not in the group %s.' % (user.display_name, username, group.name))
        M.AuditLog.log('remove user %s from %s', username, group.name)
        user.project_role().roles.remove(group._id)
        g.post_event('project_updated')
        return dict()

    @without_trailing_slash
    @expose()
    @require_post()
    @h.vardec
    def update(self, card=None, **kw):
        for pr in card:
            group = M.ProjectRole.query.get(_id=ObjectId(pr['id']))
            assert group.project == c.project, 'Security violation'
            user_ids = pr.get('value', [])
            new_users = pr.get('new', [])
            if isinstance(user_ids, basestring):
                user_ids = [ user_ids ]
            if isinstance(new_users, basestring):
                new_users = [ new_users ]
            # Handle new users in groups
            user_added = False
            for username in new_users:
                user = M.User.by_username(username.strip())
                if not user:
                    flash('User %s not found' % username, 'error')
                    redirect('.')
                if not user._id:
                    continue # never add anon users to groups
                M.AuditLog.log('add user %s to %s', username, group.name)
                user.project_role().roles.append(group._id)
                user_added = True
            # Make sure we aren't removing all users from the Admin group
            if group.name == u'Admin' and not (user_ids or user_added):
                flash('You must have at least one user with the Admin role.',
                      'warning')
                redirect('.')
            # Handle users removed from groups
            user_ids = set(
                uid and ObjectId(uid)
                for uid in user_ids)
            for role in M.ProjectRole.query.find(dict(user_id={'$ne':None}, roles=group._id)):
                if role.user_id and role.user_id not in user_ids:
                    role.roles = [ rid for rid in role.roles if rid != group._id ]
                    M.AuditLog.log('remove user %s from %s', role.user.username, group.name)
        g.post_event('project_updated')
        redirect('.')

    @without_trailing_slash
    @expose('jinja:allura.ext.admin:templates/project_group.html')
    def new(self):
        c.form = W.new_group_settings
        return dict(
            group=None,
            show_settings=True,
            action="create")

    @expose()
    @require_post()
    @validate(W.new_group_settings)
    @h.vardec
    def create(self, name=None, **kw):
        if M.ProjectRole.by_name(name):
            flash('%s already exists' % name, 'error')
        else:
            M.ProjectRole(project_id=c.project._id, name=name)
        M.AuditLog.log('create group %s', name)
        g.post_event('project_updated')
        redirect('.')

    @expose()
    def _lookup(self, name, *remainder):
        return GroupController(name), remainder

class GroupController(BaseController):

    def __init__(self, name):
        self._group = M.ProjectRole.query.get(_id=ObjectId(name))

    @with_trailing_slash
    @expose('jinja:allura.ext.admin:templates/project_group.html')
    def index(self):
        if self._group.name in ('Admin', 'Developer', 'Member'):
            show_settings = False
            action = None
        else:
            show_settings = True
            action = self._group.settings_href + 'update'
        c.form = W.group_settings
        return dict(
            group=self._group,
            show_settings=show_settings,
            action=action)

    @expose()
    @h.vardec
    @require_post()
    @validate(W.group_settings)
    def update(self, _id=None, delete=None, name=None, **kw):
        pr = M.ProjectRole.by_name(name)
        if pr and pr._id != _id._id:
            flash('%s already exists' % name, 'error')
            redirect('..')
        if delete:
            _id.delete()
            M.AuditLog.log('delete group %s', _id.name)
            flash('%s deleted' % name)
            redirect('..')
        M.AuditLog.log('update group name %s=>%s', _id.name, name)
        _id.name = name
        flash('%s updated' % name)
        redirect('..')

class AuditController(BaseController):

    @with_trailing_slash
    @expose('jinja:allura.ext.admin:templates/audit.html')
    def index(self, limit=10, page=0, **kwargs):
        limit = int(limit)
        page = int(page)
        count = M.AuditLog.query.find(dict(project_id=c.project._id)).count()
        q = M.AuditLog.query.find(dict(project_id=c.project._id))
        q = q.sort('timestamp', -1)
        q = q.skip(page * limit)
        if count > limit:
            q = q.limit(limit)
        else:
            limit=count
        c.widget = W.audit
        return dict(
            entries=q.all(),
            limit=limit,
            page=page,
            count=count)

class AdminAppAdminController(DefaultAdminController):
    '''Administer the admin app'''
    pass
