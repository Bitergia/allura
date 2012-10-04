'''
Allura plugins for authentication and project registration
'''
import re
import os
import logging
import subprocess
import string
from urllib2 import urlopen
from cStringIO import StringIO
from random import randint
from hashlib import sha256
from base64 import b64encode
from datetime import datetime, timedelta
import json

try:
    import ldap
    from ldap import modlist
except ImportError:
    ldap = modlist = None
import pkg_resources
from tg import config, flash
from pylons import g, c
from webob import exc
from bson.tz_util import FixedOffset

from ming.utils import LazyProperty
from ming.orm import state
from ming.orm import ThreadLocalORMSession

from allura.lib import helpers as h
from allura.lib import security
from allura.lib import exceptions as forge_exc

log = logging.getLogger(__name__)

class AuthenticationProvider(object):
    '''
    An interface to provide authentication services for Allura.

    To use a new provider, expose an entry point in setup.py:

        [allura.auth]
        myprovider = foo.bar:MyAuthProvider

    Then in your .ini file, set auth.method=myprovider
    '''

    def __init__(self, request):
        self.request = request

    @classmethod
    def get(cls, request):
        '''returns the AuthenticationProvider instance for this request'''
        try:
            result = cls._loaded_ep
        except AttributeError:
            method = config.get('auth.method', 'local')
            result = cls._loaded_ep = g.entry_points['auth'][method]
        return result(request)

    @LazyProperty
    def session(self):
        return self.request.environ['beaker.session']

    def authenticate_request(self):
        from allura import model as M
        user = M.User.query.get(_id=self.session.get('userid', None))
        if user is None:
            return M.User.anonymous()
        return user

    def register_user(self, user_doc):
        '''
        Register a user.

        :param user_doc: a dict with 'username' and 'display_name'.  Optionally 'password' and others
        :rtype: :class:`User <allura.model.auth.User>`
        '''
        raise NotImplementedError, 'register_user'

    def _login(self):
        '''
        Authorize a user, usually using self.request.params['username'] and ['password']

        :rtype: :class:`User <allura.model.auth.User>`
        :raises: HTTPUnauthorized if user not found, or credentials are not valid
        '''
        raise NotImplementedError, '_login'

    def login(self, user=None):
        try:
            if user is None: user = self._login()
            self.session['userid'] = user._id
            self.session.save()
            g.zarkov_event('login', user=user)
            return user
        except exc.HTTPUnauthorized:
            self.logout()
            raise

    def logout(self):
        self.session['userid'] = None
        self.session.save()

    def by_username(self, username):
        '''
        Find a user by username.

        :rtype: :class:`User <allura.model.auth.User>` or None
        '''
        raise NotImplementedError, 'by_username'

    def set_password(self, user, old_password, new_password):
        '''
        Set a user's password.

        :param user: a :class:`User <allura.model.auth.User>`
        :rtype: None
        :raises: HTTPUnauthorized if old_password is not valid
        '''
        raise NotImplementedError, 'set_password'

    def upload_sshkey(self, username, pubkey):
        '''
        Upload an SSH Key.  Providers do not necessarily need to implement this.

        :rtype: None
        :raises: AssertionError with user message, upon any error
        '''
        raise NotImplemented, 'upload_sshkey'

    def account_navigation(self):
        return [
            {
                'tabid': 'account_sfnet_beta_index',
                'title': 'Subscriptions',
                'target': "/auth/prefs",
                'alt': 'Manage Subscription Preferences',
            },
        ]

    def project_url(self, user):
        '''
        :param user: a :class:`User <allura.model.auth.User>`
        :rtype: str
        '''
        raise NotImplementedError, 'project_url'

    def user_by_project_url(self, shortname):
        '''
        :param str: shortname
        :rtype: user: a :class:`User <allura.model.auth.User>`
        '''
        raise NotImplementedError, 'user_by_project_url'

    def update_notifications(self, user):
        raise NotImplemented, 'update_notifications'

class LocalAuthenticationProvider(AuthenticationProvider):
    '''
    Stores user passwords on the User model, in mongo.  Uses per-user salt and
    SHA-256 encryption.
    '''

    def register_user(self, user_doc):
        from allura import model as M
        u = M.User(**user_doc)
        if 'password' in user_doc:
            u.set_password(user_doc['password'])
        return u

    def _login(self):
        user = self.by_username(self.request.params['username'])
        if not self._validate_password(user, self.request.params['password']):
            raise exc.HTTPUnauthorized()
        return user

    def _validate_password(self, user, password):
        if user is None: return False
        if not user.password: return False
        salt = str(user.password[6:6+user.SALT_LEN])
        check = self._encode_password(password, salt)
        if check != user.password: return False
        return True

    def by_username(self, username):
        from allura import model as M
        un = re.escape(username)
        un = un.replace(r'\_', '[-_]')
        un = un.replace(r'\-', '[-_]')
        rex = re.compile('^' + un + '$')
        return M.User.query.get(username=rex)

    def set_password(self, user, old_password, new_password):
        user.password = self._encode_password(new_password)

    def _encode_password(self, password, salt=None):
        from allura import model as M
        if salt is None:
            salt = ''.join(chr(randint(1, 0x7f))
                           for i in xrange(M.User.SALT_LEN))
        hashpass = sha256(salt + password.encode('utf-8')).digest()
        return 'sha256' + salt + b64encode(hashpass)

    def project_url(self, user):
        return '/u/' + user.username.replace('_', '-') + '/'

    def user_by_project_url(self, shortname):
        from allura import model as M
        return M.User.query.get(username=shortname)

    def update_notifications(self, user):
        return ''

class LdapAuthenticationProvider(AuthenticationProvider):
    def register_user(self, user_doc):
        from allura import model as M
        password = user_doc['password'].encode('utf-8')
        result = M.User(**user_doc)
        dn_u = 'uid=%s,%s' % (user_doc['username'], config['auth.ldap.suffix'])
        uid = str(M.AuthGlobals.get_next_uid())
        try:
            con = ldap.initialize(config['auth.ldap.server'])
            con.bind_s(config['auth.ldap.admin_dn'],
                       config['auth.ldap.admin_password'])
            uname = user_doc['username'].encode('utf-8')
            display_name = user_doc['display_name'].encode('utf-8')
            ldif_u = modlist.addModlist(dict(
                uid=uname,
                userPassword=password,
                objectClass=['account', 'posixAccount' ],
                cn=display_name,
                uidNumber=uid,
                gidNumber='10001',
                homeDirectory='/home/' + uname,
                loginShell='/bin/bash',
                gecos=uname,
                description='SCM user account'))
            try:
                con.add_s(dn_u, ldif_u)
            except ldap.ALREADY_EXISTS:
                log.exception('Trying to create existing user %s', uname)
                raise
            con.unbind_s()
            argv = ('schroot -d / -c %s -u root /ldap-userconfig.py init %s' % (
                config['auth.ldap.schroot_name'], user_doc['username'])).split()
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            rc = p.wait()
            if rc != 0:
                log.error('Error creating home directory for %s',
                          user_doc['username'])
        except:
            raise
        return result

    def upload_sshkey(self, username, pubkey):
            argv = ('schroot -d / -c %s -u root /ldap-userconfig.py upload %s' % (
                config['auth.ldap.schroot_name'], username)).split() + [ pubkey ]
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            rc = p.wait()
            if rc != 0:
                errmsg = p.stdout.read()
                log.exception('Error uploading public SSH key for %s: %s',
                              username, errmsg)
                assert False, errmsg

    def by_username(self, username):
        from allura import model as M
        return M.User.query.get(username=username)

    def set_password(self, user, old_password, new_password):
        try:
            dn = 'uid=%s,%s' % (user.username, config['auth.ldap.suffix'])
            con = ldap.initialize(config['auth.ldap.server'])
            con.bind_s(dn, old_password.encode('utf-8'))
            con.modify_s(dn, [(ldap.MOD_REPLACE, 'userPassword', new_password.encode('utf-8'))])
            con.unbind_s()
        except ldap.INVALID_CREDENTIALS:
            raise exc.HTTPUnauthorized()

    def _login(self):
        from allura import model as M
        user = M.User.query.get(username=self.request.params['username'])
        if user is None: raise exc.HTTPUnauthorized()
        try:
            dn = 'uid=%s,%s' % (user.username, config['auth.ldap.suffix'])
            con = ldap.initialize(config['auth.ldap.server'])
            con.bind_s(dn, self.request.params['password'])
            con.unbind_s()
        except ldap.INVALID_CREDENTIALS:
            raise exc.HTTPUnauthorized()
        return user

class ProjectRegistrationProvider(object):
    '''
    Project registration services for Allura.  This is a full implementation
    and the default.  Extend this class with your own if you need to add more
    functionality.

    To use a new provider, expose an entry point in setup.py:

        [allura.project_registration]
        myprovider = foo.bar:MyAuthProvider

    Then in your .ini file, set registration.method=myprovider
    '''

    def __init__(self):
        from allura.lib.widgets import forms as forms
        self.add_project_widget = forms.NeighborhoodAddProjectForm

    @classmethod
    def get(cls):
        from allura.lib import app_globals
        method = config.get('registration.method', 'local')
        return app_globals.Globals().entry_points['registration'][method]()

    def name_taken(self, project_name, neighborhood):
        from allura import model as M
        p = M.Project.query.get(shortname=project_name, neighborhood_id=neighborhood._id)
        if p:
            return 'This project name is taken.'
        for check in self.extra_name_checks():
            if re.match(str(check[1]),project_name) is not None:
                return check[0]
        return False

    def extra_name_checks(self):
        '''This should be a list or iterator containing tuples.
        The first tiem in the tuple should be an error message and the
        second should be a regex. If the user attempts to register a
        project with a name that matches the regex, the field will
        be marked invalid with the message displayed to the user.
        '''
        return []

    def rate_limit(self, user, neighborhood):
        '''Check the various config-defined project registration rate
        limits, and if any are exceeded, raise ProjectRatelimitError.
        '''
        if security.has_access(neighborhood, 'admin', user=user)():
            return
        # have to have the replace because, despite being UTC,
        # the result from utcnow() is still offset-naive  :-(
        # maybe look into making the mongo connection offset-naive?
        now = datetime.utcnow().replace(tzinfo=FixedOffset(0, 'UTC'))
        project_count = len(list(user.my_projects()))
        rate_limits = json.loads(config.get('project.rate_limits', '{}'))
        for rate, count in rate_limits.items():
            user_age = now - user._id.generation_time
            user_age = (user_age.microseconds + (user_age.seconds + user_age.days * 24 * 3600) * 10**6) / 10**6
            if user_age < int(rate) and project_count >= count:
                raise forge_exc.ProjectRatelimitError()

    def register_neighborhood_project(self, neighborhood, users, allow_register=False):
        from allura import model as M
        shortname='--init--'
        p = neighborhood.neighborhood_project
        if p: raise forge_exc.ProjectConflict()
        name = 'Home Project for %s' % neighborhood.name
        database_uri = M.Project.default_database_uri(shortname)
        p = M.Project(neighborhood_id=neighborhood._id,
                    shortname=shortname,
                    name=name,
                    short_description='',
                    description=('You can edit this description in the admin page'),
                    homepage_title = '# ' + name,
                    database_uri=database_uri,
                    last_updated = datetime.utcnow(),
                    is_nbhd_project=True,
                    is_root=True)
        try:
            p.configure_project(
                users=users,
                is_user_project=False,
                apps=[
                    ('Wiki', 'wiki', 'Wiki'),
                    ('admin', 'admin', 'Admin')])
        except:
            ThreadLocalORMSession.close_all()
            log.exception('Error registering project %s' % p)
            raise
        if allow_register:
            role_auth = M.ProjectRole.authenticated(p)
            security.simple_grant(p.acl, role_auth._id, 'register')
            state(p).soil()
        return p

    def register_project(self, neighborhood, shortname, project_name, user, user_project, private_project, apps=None):
        '''Register a new project in the neighborhood.  The given user will
        become the project's superuser.
        '''
        from allura import model as M

        # Check for private project rights
        if neighborhood.features['private_projects'] == False and private_project:
            raise ValueError("You can't create private projects for %s neighborhood" % neighborhood.name)

        # Check for project limit creation
        pq = M.Project.query.find(dict(
                neighborhood_id=neighborhood._id,
                deleted=False,
                is_nbhd_project=False,
                ))
        count = pq.count()
        nb_max_projects = neighborhood.get_max_projects()

        if nb_max_projects is not None and count >= nb_max_projects:
            log.exception('Error registering project %s' % project_name)
            raise forge_exc.ProjectOverlimitError()

        self.rate_limit(user, neighborhood)

        if not h.re_path_portion.match(shortname.replace('/', '')):
            raise ValueError('Invalid project shortname: %s' % shortname)
        try:
            p = M.Project.query.get(shortname=shortname, neighborhood_id=neighborhood._id)
            if p:
                raise forge_exc.ProjectConflict()
            project_template = neighborhood.get_project_template()
            p = M.Project(neighborhood_id=neighborhood._id,
                        shortname=shortname,
                        name=project_name,
                        short_description='',
                        description=('You can edit this description in the admin page'),
                        homepage_title=shortname,
                        database_uri=M.Project.default_database_uri(shortname),
                        last_updated = datetime.utcnow(),
                        is_nbhd_project=False,
                        is_root=True)
            p.configure_project(
                users=[user],
                is_user_project=user_project,
                is_private_project=private_project or project_template.get('private', False),
                apps=apps or [] if 'tools' in project_template else None)

            # Setup defaults from neighborhood project template if applicable
            offset = p.next_mount_point(include_hidden=True)
            if 'groups' in project_template:
                for obj in project_template['groups']:
                    name = obj.get('name')
                    permissions = set(obj.get('permissions', [])) & \
                                  set(p.permissions)
                    usernames = obj.get('usernames', [])
                    # Must provide a group name
                    if not name: continue
                    # If the group already exists, we'll add users to it,
                    # but we won't change permissions on the group
                    group = M.ProjectRole.by_name(name, project=p)
                    if not group:
                        # If creating a new group, *must* specify permissions
                        if not permissions: continue
                        group = M.ProjectRole(project_id=p._id, name=name)
                        p.acl += [M.ACE.allow(group._id, perm)
                                for perm in permissions]
                    for username in usernames:
                        guser = M.User.by_username(username)
                        if not (guser and guser._id): continue
                        pr = guser.project_role(project=p)
                        if group._id not in pr.roles:
                            pr.roles.append(group._id)
            if 'tools' in project_template:
                for i, tool in enumerate(project_template['tools'].keys()):
                    tool_config = project_template['tools'][tool]
                    tool_options = tool_config.get('options', {})
                    for k, v in tool_options.iteritems():
                        if isinstance(v, basestring):
                            tool_options[k] = \
                                    string.Template(v).safe_substitute(
                                        p.__dict__.get('root_project', {}))
                    app = p.install_app(tool,
                        mount_label=tool_config['label'],
                        mount_point=tool_config['mount_point'],
                        ordinal=i + offset,
                        **tool_options)
                    if tool == 'wiki':
                        from forgewiki import model as WM
                        text = tool_config.get('home_text',
                            '[[project_admins]]\n[[download_button]]')
                        WM.Page.query.get(app_config_id=app.config._id).text = text

            if 'tool_order' in project_template:
                for i, tool in enumerate(project_template['tool_order']):
                    p.app_config(tool).options.ordinal = i
            if 'labels' in project_template:
                p.labels = project_template['labels']
            if 'trove_cats' in project_template:
                for trove_type in project_template['trove_cats'].keys():
                    troves = getattr(p, 'trove_%s' % trove_type)
                    for trove_id in project_template['trove_cats'][trove_type]:
                        troves.append(M.TroveCategory.query.get(trove_cat_id=trove_id)._id)
            if 'icon' in project_template:
                icon_file = StringIO(urlopen(project_template['icon']['url']).read())
                M.ProjectFile.save_image(
                    project_template['icon']['filename'], icon_file,
                    square=True, thumbnail_size=(48, 48),
                    thumbnail_meta=dict(project_id=p._id, category='icon'))
            # clear the RoleCache for the user so this project will
            # be picked up by user.my_projects()
            g.credentials.clear_user(user._id)
        except forge_exc.ProjectConflict:
            raise
        except:
            ThreadLocalORMSession.close_all()
            log.exception('Error registering project %s' % p)
            raise
        with h.push_config(c, project=p, user=user):
            ThreadLocalORMSession.flush_all()
            # have to add user to context, since this may occur inside auth code
            # for user-project reg, and c.user isn't set yet
            g.post_event('project_created')
        return p

    def register_subproject(self, project, name, user, install_apps):
        from allura import model as M
        assert h.re_path_portion.match(name), 'Invalid subproject shortname'
        shortname = project.shortname + '/' + name
        sp = M.Project(
            parent_id=project._id,
            neighborhood_id=project.neighborhood_id,
            shortname=shortname,
            name=name,
            database_uri=project.database_uri,
            last_updated = datetime.utcnow(),
            is_root=False)
        with h.push_config(c, project=sp):
            M.AppConfig.query.remove(dict(project_id=c.project._id))
            if install_apps:
                sp.install_app('admin', 'admin', ordinal=1)
                sp.install_app('search', 'search', ordinal=2)
            g.post_event('project_created')
        return sp

    def delete_project(self, project, user):
        for sp in project.subprojects:
            self.delete_project(sp, user)
        project.deleted = True

    def undelete_project(self, project, user):
        project.deleted = False
        for sp in project.subprojects:
            self.undelete_project(sp, user)

    def best_download_url(self, project):
        '''This is the url needed to render a download button.
           It should be overridden for your specific envirnoment'''
        return None

class ThemeProvider(object):
    '''
    Theme information for Allura.  This is a full implementation
    and the default.  Extend this class with your own if you need to add more
    functionality.

    To use a new provider, expose an entry point in setup.py:

        [allura.theme]
        myprovider = foo.bar:MyThemeProvider

    Then in your .ini file, set theme=mytheme

    The variables referencing jinja template files can be changed to point at your
    own jinja templates.  Use the standard templates as a reference, you should
    provide matching macros and block names.

    :var icons: a dictionary of sized icons for each tool
    '''

    master_template = 'allura:templates/jinja_master/master.html'
    jinja_macros = 'allura:templates/jinja_master/theme_macros.html'
    nav_menu = 'allura:templates/jinja_master/nav_menu.html'
    top_nav = 'allura:templates/jinja_master/top_nav.html'
    sidebar_menu = 'allura:templates/jinja_master/sidebar_menu.html'
    icons = {
        'subproject': {
            24: 'images/ext_24.png',
            32: 'images/ext_32.png',
            48: 'images/ext_48.png'
        }
    }

    def require(self):
        g.register_theme_css('css/site_style.css', compress=False)
        g.register_theme_css('css/allura.css', compress=False)

    @classmethod
    def register_ew_resources(cls, manager, name):
        manager.register_directory(
            'theme/%s' % name,
            pkg_resources.resource_filename(
                'allura',
                os.path.join('nf', name)))

    @LazyProperty
    def password_change_form(self):
        '''
        :return: None, or an easywidgets Form to render on the user preferences page
        '''
        from allura.lib.widgets.forms import PasswordChangeForm
        return PasswordChangeForm(action='/auth/prefs/change_password')

    @LazyProperty
    def upload_key_form(self):
        '''
        :return: None, or an easywidgets Form to render on the user preferences page
        '''
        from allura.lib.widgets.forms import UploadKeyForm
        return UploadKeyForm(action='/auth/prefs/upload_sshkey')

    @property
    def master(self):
        return self.master_template

    @classmethod
    def get(cls):
        name = config.get('theme', 'allura')
        return g.entry_points['theme'][name]()

    def app_icon_url(self, app, size):
        """returns the default icon for the given app (or non-app thing like 'subproject').
            Takes an instance of class Application, or else a string.
            Expected to be overriden by derived Themes.
        """
        if isinstance(app, str):
            if app in self.icons and size in self.icons[app]:
                return g.theme_href(self.icons[app][size])
            else:
                return g.entry_points['tool'][app].icon_url(size)
        else:
            return app.icon_url(size)

class LocalProjectRegistrationProvider(ProjectRegistrationProvider):
    pass

class UserPreferencesProvider(object):
    '''
    An interface for user preferences, like display_name and email_address

    To use a new provider, expose an entry point in setup.py:

        [allura.user_prefs]
        myprefs = foo.bar:MyUserPrefProvider

    Then in your .ini file, set user_prefs_storage.method=myprefs
    '''

    @classmethod
    def get(cls):
        method = config.get('user_prefs_storage.method', 'local')
        return g.entry_points['user_prefs'][method]()

    def get_pref(self, user, pref_name):
        '''
        :param user: a :class:`User <allura.model.auth.User>`
        :param str pref_name:
        :return: pref_value
        :raises: AttributeError if pref_name not found
        '''
        raise NotImplementedError, 'get_pref'

    def save_pref(self, user, pref_name, pref_value):
        '''
        :param user: a :class:`User <allura.model.auth.User>`
        :param str pref_name:
        :param pref_value:
        '''
        raise NotImplementedError, 'set_pref'

    def find_by_display_name(self, name):
        '''
        :rtype: list of :class:`Users <allura.model.auth.User>`
        '''
        raise NotImplementedError, 'find_by_display_name'

class LocalUserPreferencesProvider(UserPreferencesProvider):
    '''
    The default UserPreferencesProvider, storing preferences on the User object
    in mongo.
    '''

    def get_pref(self, user, pref_name):
        if pref_name in user.preferences:
            return user.preferences[pref_name]
        else:
            return getattr(user, pref_name)

    def set_pref(self, user, pref_name, pref_value):
        if pref_name in user.preferences:
            user.preferences[pref_name] = pref_value
        else:
            setattr(user, pref_name, pref_value)

    def find_by_display_name(self, name):
        from allura import model as M
        name_regex = re.compile('(?i)%s' % re.escape(name))
        users = M.User.query.find(dict(
                display_name=name_regex)).sort('username').all()
        return users
