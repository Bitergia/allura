import re
import os, allura
import pkg_resources
import Image, StringIO
from contextlib import contextmanager

from nose.tools import assert_equals
from ming.orm.ormsession import ThreadLocalORMSession

try:
    import sfx
except ImportError:
    sfx = None

from allura.tests import TestController
from allura.tests import decorators as td
from allura import model as M

@contextmanager
def audits(*messages):
    M.AuditLog.query.remove()
    yield
    entries = M.AuditLog.query.find().sort('_id').all()
    if not messages:
        for e in entries:
            print e.message
        import pdb; pdb.set_trace()
    for message in messages:
        assert M.AuditLog.query.find(dict(
            message=re.compile(message))).count()

class TestProjectAdmin(TestController):

    def test_admin_controller(self):
        self.app.get('/admin/')
        with audits(
            'change summary to Milkshakes are for crazy monkeys',
            'change project name to Test Project',
            u'change short description to (\u00bf A Test Project \?){45}'):
            self.app.post('/admin/update', params=dict(
                    name='Test Project',
                    shortname='test',
                    summary='Milkshakes are for crazy monkeys',
                    short_description=u'\u00bf A Test Project ?'.encode('utf-8') * 45,
                    labels='aaa,bbb'))
        r = self.app.get('/admin/overview')
        assert 'A Test Project ?\xc2\xbf A' in r
        assert 'Test Subproject' not in r
        assert 'Milkshakes are for crazy monkeys' in r
        sidebar = r.html.find(id='sidebar')
        assert sidebar.find('a', href='/p/test/admin/tools'), sidebar

        # Add a subproject
        with audits('create subproject test-subproject'):
            self.app.post('/admin/update_mounts', params={
                    'new.install':'install',
                    'new.ep_name':'',
                    'new.ordinal':'1',
                    'new.mount_point':'test-subproject',
                    'new.mount_label':'Test Subproject'})
        r = self.app.get('/admin/overview')
        assert 'Test Subproject' in r
        # Rename a subproject
        with audits('update subproject test/test-subproject'):
            self.app.post('/admin/update_mounts', params={
                    'subproject-0.shortname':'test/test-subproject',
                    'subproject-0.name':'Tst Sbprj',
                    'subproject-0.ordinal':'100',
                    })
        r = self.app.get('/admin/overview')
        assert 'Tst Sbprj' in r
        # Remove a subproject
        with audits('delete subproject test/test-subproject'):
            self.app.post('/admin/update_mounts', params={
                    'subproject-0.delete':'on',
                    'subproject-0.shortname':'test/test-subproject',
                    'new.ep_name':'',
                    })

        # Add a tool
        with audits('install tool test-tool'):
            r = self.app.post('/admin/update_mounts', params={
                    'new.install':'install',
                    'new.ep_name':'Wiki',
                    'new.ordinal':'1',
                    'new.mount_point':'test-tool',
                    'new.mount_label':'Test Tool'})
        assert 'error' not in self.webflash(r)
        # check tool in the nav
        r = self.app.get('/p/test/test-tool/').follow()
        active_link = r.html.findAll('span',{'class':'diamond'})
        assert len(active_link) == 1
        assert active_link[0].parent['href'] == '/p/test/test-tool/'
        with audits('install tool test-tool2'):
            r = self.app.post('/admin/update_mounts', params={
                    'new.install':'install',
                    'new.ep_name':'Wiki',
                    'new.ordinal':'1',
                    'new.mount_point':'test-tool2',
                    'new.mount_label':'Test Tool2'})
        assert 'error' not in self.webflash(r)
        # check the nav - the similarly named tool should NOT be active
        r = self.app.get('/p/test/test-tool/Home/')
        active_link = r.html.findAll('span',{'class':'diamond'})
        assert len(active_link) == 1
        assert active_link[0].parent['href'] == '/p/test/test-tool/'
        r = self.app.get('/p/test/test-tool2/Home/')
        active_link = r.html.findAll('span',{'class':'diamond'})
        assert len(active_link) == 1
        assert active_link[0].parent['href'] == '/p/test/test-tool2/'
        # check can't create dup tool
        r = self.app.post('/admin/update_mounts', params={
                'new.install':'install',
                'new.ep_name':'Wiki',
                'new.ordinal':'1',
                'new.mount_point':'test-tool',
                'new.mount_label':'Test Tool'})
        assert 'error' in self.webflash(r)
        # Rename a tool
        with audits('update tool test-tool'):
            self.app.post('/admin/update_mounts', params={
                    'tool-0.mount_point':'test-tool',
                    'tool-0.mount_label':'Tst Tuul',
                    'tool-0.ordinal':'200',
                    })
        r = self.app.get('/admin/overview')
        assert 'Tst Tuul' in r
        # Remove a tool
        with audits('uninstall tool test-tool'):
            self.app.post('/admin/update_mounts', params={
                    'tool-0.delete':'on',
                    'tool-0.mount_point':'test-tool',
                    'new.ep_name':'',
                    })

        # Check the audit log
        r = self.app.get('/admin/audit/')
        assert "uninstall tool test-tool" in r.body, r.body

    def test_tool_permissions(self):
        self.app.get('/admin/')
        for i, ep in enumerate(pkg_resources.iter_entry_points('allura')):
            app = ep.load()
            if not app.installable: continue
            tool = ep.name
            with audits('install tool test-%d' % i):
                self.app.post('/admin/update_mounts', params={
                        'new.install':'install',
                        'new.ep_name':tool,
                        'new.ordinal':str(i),
                        'new.mount_point':'test-%d' % i,
                        'new.mount_label':tool })
            r = self.app.get('/admin/test-%d/permissions' % i)
            cards = [
                tag for tag in r.html.findAll('input')
                if (
                    tag.get('type') == 'hidden' and
                    tag['name'].startswith('card-') and
                    tag['name'].endswith('.id')) ]
            assert len(cards) == len(app.permissions), cards

    def test_tool_list(self):
        r = self.app.get('/admin/tools')
        new_ep_opts = r.html.findAll('a',{'class':"install_trig"})
        tool_strings = [ ' '.join(opt.find('span').string.strip().split()) for opt in new_ep_opts ]
        expected_tools = [
            'External Link',
            'Git',
            'Mercurial',
            'SVN',
            'Wiki',
            'Tickets',
            'Discussion',
            'Chat (alpha)',
            'Blog',
            'Subproject']
        # check using sets, because their may be more tools installed by default
        # that we don't know about
        assert len(set(expected_tools) - set(tool_strings)) == 0, tool_strings

    def test_project_icon(self):
        file_name = 'neo-icon-set-454545-256x350.png'
        file_path = os.path.join(allura.__path__[0],'nf','allura','images',file_name)
        file_data = file(file_path).read()
        upload = ('icon', file_name, file_data)

        self.app.get('/admin/')
        with audits('update project icon'):
            self.app.post('/admin/update', params=dict(
                    name='Test Project',
                    shortname='test',
                    short_description='A Test Project'),
                    upload_files=[upload])
        r = self.app.get('/p/test/icon')
        image = Image.open(StringIO.StringIO(r.body))
        assert image.size == (48,48)
        
        r = self.app.get('/p/test/icon?foo=bar')

    def test_project_screenshot(self):
        file_name = 'neo-icon-set-454545-256x350.png'
        file_path = os.path.join(allura.__path__[0],'nf','allura','images',file_name)
        file_data = file(file_path).read()
        upload = ('screenshot', file_name, file_data)

        self.app.get('/admin/')
        with audits('add screenshot'):
            self.app.post('/admin/add_screenshot', params=dict(
                    caption='test me'),
                    upload_files=[upload])
        p_nbhd = M.Neighborhood.query.get(name='Projects')
        project = M.Project.query.get(shortname='test', neighborhood_id=p_nbhd._id)
        filename = project.get_screenshots()[0].filename
        r = self.app.get('/p/test/screenshot/'+filename)
        uploaded = Image.open(file_path)
        screenshot = Image.open(StringIO.StringIO(r.body))
        assert uploaded.size == screenshot.size
        r = self.app.get('/p/test/screenshot/'+filename+'/thumb')
        thumb = Image.open(StringIO.StringIO(r.body))
        assert thumb.size == (150,150)
        #FIX: home pages don't currently support screenshots (now that they're a wiki);
        # reinstate this code (or appropriate) when we have a macro for that
        #r = self.app.get('/p/test/home/')
        #assert '/p/test/screenshot/'+filename in r
        #assert 'test me' in r
        # test edit
        req = self.app.get('/admin/screenshots')
        req.forms[0]['caption'].value = 'aaa'
        req.forms[0].submit()
        #r = self.app.get('/p/test/home/')
        #assert 'aaa' in r
        # test delete
        req = self.app.get('/admin/screenshots')
        req.forms[1].submit()
        #r = self.app.get('/p/test/home/')
        #assert 'aaa' not in r

    def test_project_delete_undelete(self):
        # create a subproject
        with audits('create subproject sub-del-undel'):
            self.app.post('/admin/update_mounts', params={
                    'new.install':'install',
                    'new.ep_name':'',
                    'new.ordinal':'1',
                    'new.mount_point':'sub-del-undel',
                    'new.mount_label':'sub-del-undel'})
        r = self.app.get('/p/test/admin/overview')
        assert 'This project has been deleted and is not visible to non-admin users' not in r
        assert r.html.find('input',{'name':'removal','value':''}).has_key('checked')
        assert not r.html.find('input',{'name':'removal','value':'deleted'}).has_key('checked')
        with audits('delete project'):
            self.app.post('/admin/update', params=dict(
                    name='Test Project',
                    shortname='test',
                    removal='deleted',
                    short_description='A Test Project',
                    delete='on'))
        r = self.app.get('/p/test/admin/overview')
        assert 'This project has been deleted and is not visible to non-admin users' in r
        assert not r.html.find('input',{'name':'removal','value':''}).has_key('checked')
        assert r.html.find('input',{'name':'removal','value':'deleted'}).has_key('checked')
        # make sure subprojects get deleted too
        r = self.app.get('/p/test/sub-del-undel/admin/overview')
        assert 'This project has been deleted and is not visible to non-admin users' in r
        with audits('undelete project'):
            self.app.post('/admin/update', params=dict(
                    name='Test Project',
                    shortname='test',
                    removal='',
                    short_description='A Test Project',
                    undelete='on'))
        r = self.app.get('/p/test/admin/overview')
        assert 'This project has been deleted and is not visible to non-admin users' not in r
        assert r.html.find('input',{'name':'removal','value':''}).has_key('checked')
        assert not r.html.find('input',{'name':'removal','value':'deleted'}).has_key('checked')

    def test_project_delete_not_allowed(self):
        # turn off project delete option
        from allura.ext.admin.admin_main import config
        old_allow_project_delete = config.get('allow_project_delete', ())
        config['allow_project_delete'] = False
        try:
            # create a subproject
            with audits('create subproject sub-no-del'):
                self.app.post('/admin/update_mounts', params={
                        'new.install':'install',
                        'new.ep_name':'',
                        'new.ordinal':'1',
                        'new.mount_point':'sub-no-del',
                        'new.mount_label':'sub-no-del'})
            # root project doesn't have delete option
            r = self.app.get('/p/test/admin/overview')
            assert not r.html.find('input',{'name':'removal','value':'deleted'})
            # subprojects can still be deleted
            r = self.app.get('/p/test/sub-no-del/admin/overview')
            assert r.html.find('input',{'name':'removal','value':'deleted'})
            # attempt to delete root project won't do anything
            self.app.post('/admin/update', params=dict(
                    name='Test Project',
                    shortname='test',
                    removal='deleted',
                    short_description='A Test Project',
                    delete='on'))
            r = self.app.get('/p/test/admin/overview')
            assert 'This project has been deleted and is not visible to non-admin users' not in r
            # make sure subproject delete works
            with audits(
                'change project removal status to deleted',
                'delete project'):
                self.app.post('/p/test/sub-no-del/admin/update', params=dict(
                        name='sub1',
                        shortname='sub1',
                        removal='deleted',
                        short_description='A Test Project',
                        delete='on'))
            r = self.app.get('/p/test/sub-no-del/admin/overview')
            assert 'This project has been deleted and is not visible to non-admin users' in r
            assert r.html.find('input',{'name':'removal','value':'deleted'}).has_key('checked')
        finally:
            if old_allow_project_delete == ():
                del config['allow_project_delete']
            else:
                config['allow_project_delete'] = old_allow_project_delete

    def test_add_remove_trove_cat(self):
        r = self.app.get('/admin/trove')
        assert 'No Database Environment categories have been selected.' in r
        assert '<span class="trove_fullpath">Database Environment :: Database API</span>' not in r
        # add a cat
        with audits('add trove root_database: Database Environment :: Database API'):
            form = r.forms['add_trove_root_database']
            form['new_trove'].value = '499'
            r = form.submit().follow()
        # make sure it worked
        assert 'No Database Environment categories have been selected.' not in r
        assert '<span class="trove_fullpath">Database Environment :: Database API</span>' in r
        # delete the cat
        with audits('remove trove root_database: Database Environment :: Database API'):
            r = r.forms['delete_trove_root_database_499'].submit().follow()
        # make sure it worked
        assert 'No Database Environment categories have been selected.' in r
        assert '<span class="trove_fullpath">Database Environment :: Database API</span>' not in r

    def test_add_remove_label(self):
        r = self.app.get('/admin/trove')
        form = r.forms['label_edit_form']
        form['labels'].value = 'foo,bar,baz'
        with audits('updated labels'):
            r = form.submit()
        r = r.follow()
        p_nbhd = M.Neighborhood.query.get(name='Projects')
        p = M.Project.query.get(shortname='test', neighborhood_id=p_nbhd._id)
        assert p.labels == ['foo', 'bar', 'baz']
        assert form['labels'].value == 'foo,bar,baz'
        ThreadLocalORMSession.close_all()
        form['labels'].value = 'asdf'
        with audits('updated labels'):
            r = form.submit()
        r = r.follow()
        p = M.Project.query.get(shortname='test', neighborhood_id=p_nbhd._id)
        assert_equals(p.labels, ['asdf'])
        assert form['labels'].value == 'asdf'

    def test_project_permissions(self):
        r = self.app.get('/admin/permissions/')
        assert len(r.html.findAll('input', {'name': 'card-0.value'})) == 1
        select = r.html.find('select', {'name': 'card-0.new'})
        opt_admin = select.find(text='Admin').parent
        opt_developer = select.find(text='Developer').parent
        assert opt_admin.name == 'option'
        assert opt_developer.name == 'option'
        with audits('updated "admin" permissions: "Admin" => "Admin,Developer"'):
            r = self.app.post('/admin/permissions/update', params={
                    'card-0.new': opt_developer['value'],
                    'card-0.value': opt_admin['value'],
                    'card-0.id': 'admin'})
        r = self.app.get('/admin/permissions/')
        assigned_ids = [t['value'] for t in r.html.findAll('input', {'name': 'card-0.value'})]
        assert len(assigned_ids) == 2
        assert opt_developer['value'] in assigned_ids
        assert opt_admin['value'] in assigned_ids

    def test_subproject_permissions(self):
        with audits('create subproject test-subproject'):
            self.app.post('/admin/update_mounts', params={
                    'new.install':'install',
                    'new.ep_name':'',
                    'new.ordinal':'1',
                    'new.mount_point':'test-subproject',
                    'new.mount_label':'Test Subproject'})
        r = self.app.get('/test-subproject/admin/permissions/')
        assert len(r.html.findAll('input', {'name': 'card-0.value'})) == 0
        select = r.html.find('select', {'name': 'card-0.new'})
        opt_admin = select.find(text='Admin').parent
        opt_developer = select.find(text='Developer').parent
        assert opt_admin.name == 'option'
        assert opt_developer.name == 'option'
        with audits('updated "admin" permissions: "" => "Admin,Developer"'):
            r = self.app.post('/test-subproject/admin/permissions/update', params={
                    'card-0.new': opt_developer['value'],
                    'card-0.value': opt_admin['value'],
                    'card-0.id': 'admin'})
        r = self.app.get('/test-subproject/admin/permissions/')
        assigned_ids = [t['value'] for t in r.html.findAll('input', {'name': 'card-0.value'})]
        assert len(assigned_ids) == 2
        assert opt_developer['value'] in assigned_ids
        assert opt_admin['value'] in assigned_ids

    def test_project_groups(self):
        r = self.app.get('/admin/groups/')
        dev_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[2]
        developer_id = dev_holder['data-group']
        with audits('add user test-user to Developer'):
            r = self.app.post('/admin/groups/add_user', params={
                    'role_id': developer_id,
                    'username': 'test-user'})
        r = self.app.get('/admin/groups/')
        dev_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[2]
        users = dev_holder.find('ul',{'class':'users'}).findAll('li',{'class':'deleter'})
        assert 'test-user' in users[0]['data-user']
        # Make sure we can open role page for builtin role
        r = self.app.get('/admin/groups/' + developer_id + '/', validate_chunk=True)

    def test_subroles(self):
        """Make sure subroles are preserved during group updates."""
        def check_roles(r):
            dev_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[2]
            mem_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[3]
            assert 'All users in Admin group' in str(dev_holder)
            assert 'All users in Developer group' in str(mem_holder)

        r = self.app.get('/admin/groups/')

        admin_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[1]
        admin_id = admin_holder['data-group']
        # test that subroles are intact after user added
        with audits('add user test-user to Admin'):
            r = self.app.post('/admin/groups/add_user', params={
                    'role_id': admin_id,
                    'username': 'test-user'})
        r = self.app.get('/admin/groups/')
        check_roles(r)
        # test that subroles are intact after user deleted
        with audits('remove user test-user from Admin'):
            r = self.app.post('/admin/groups/remove_user', params={
                    'role_id': admin_id,
                    'username': 'test-user'})
        r = self.app.get('/admin/groups/')
        check_roles(r)

    def test_cannot_remove_all_admins(self):
        """Must always have at least one user with the Admin role (and anon
        doesn't count)."""
        r = self.app.get('/admin/groups/')
        admin_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[1]
        admin_id = admin_holder['data-group']
        users = admin_holder.find('ul',{'class':'users'}).findAll('li',{'class':'deleter'})
        assert len(users) == 1
        r = self.app.post('/admin/groups/remove_user', params={
                'role_id': admin_id,
                'username': 'admin1'})
        assert r.json['error'] == 'You must have at least one user with the Admin role.'
        r = self.app.get('/admin/groups/')
        admin_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[1]
        users = admin_holder.find('ul',{'class':'users'}).findAll('li',{'class':'deleter'})
        assert len(users) == 1

    def test_cannot_add_anon_to_group(self):
        r = self.app.get('/admin/groups/')
        dev_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[2]
        developer_id = dev_holder['data-group']
        r = self.app.post('/admin/groups/add_user', params={
                'role_id': developer_id,
                'username': ''})
        assert r.json['error'] == 'You must choose a user to add.'
        r = self.app.get('/admin/groups/')
        dev_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[2]
        users = dev_holder.find('ul',{'class':'users'}).findAll('li',{'class':'deleter'})
        # no user was added
        assert len(users) == 0
        assert M.ProjectRole.query.find(dict(
                name='*anonymous', user_id=None,
                roles={'$ne': []})).count() == 0

    def test_project_multi_groups(self):
        r = self.app.get('/admin/groups/')
        user_id = M.User.by_username('test-admin')._id
        admin_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[1]
        admin_id = admin_holder['data-group']
        with audits('add user test-user to Admin'):
            r = self.app.post('/admin/groups/add_user', params={
                    'role_id': admin_id,
                    'username': 'test-user'})
            assert 'error' not in r.json
        r = self.app.post('/admin/groups/add_user', params={
                'role_id': admin_id,
                'username': 'test-user'})
        assert r.json['error'] == 'Test User (test-user) is already in the group Admin.'
        r = self.app.get('/admin/groups/')
        assert 'test-user' in str(r), r.showbrowser()
        with audits('remove user test-user from Admin'):
            r = self.app.post('/admin/groups/remove_user', params={
                    'role_id': admin_id,
                    'username': 'test-user'})
        r = self.app.get('/admin/groups/')
        assert 'test-user' not in str(r), r.showbrowser()

    @td.with_wiki
    def test_new_group(self):
        r = self.app.get('/admin/groups/new', validate_chunk=True)
        with audits('create group Developer'):
            r = self.app.post('/admin/groups/create', params={'name': 'Developer'})
        assert 'error' in self.webflash(r)
        with audits('create group RoleNew1'):
            r = self.app.post('/admin/groups/create', params={'name': 'RoleNew1'})
        r = self.app.get('/admin/groups/')
        role_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[4]
        assert 'RoleNew1' in str(role_holder)
        role_id = role_holder['data-group']
        r = self.app.get('/admin/groups/' + role_id + '/', validate_chunk=True)
        r = self.app.post('/admin/groups/'
                          + str(role_id) + '/update', params={'_id': role_id, 'name': 'Developer'})
        assert 'error' in self.webflash(r)
        assert 'already exists' in self.webflash(r)

        with audits('update group name RoleNew1=>rleNew2'):
            r = self.app.post('/admin/groups/' + str(role_id) + '/update', params={'_id': role_id, 'name': 'rleNew2'}).follow()
        assert 'RoleNew1' not in r
        assert 'rleNew2' in r

        # add test-user to role
        role_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[4]
        rleNew2_id = role_holder['data-group']
        with audits('add user test-user to rleNew2'):
            r = self.app.post('/admin/groups/add_user', params={
                    'role_id': rleNew2_id,
                    'username': 'test-user'})

        with audits('delete group rleNew2'):
            r = self.app.post('/admin/groups/delete_group', params={
                    'group_name': 'rleNew2'})
        assert 'deleted' in self.webflash(r)
        r = self.app.get('/admin/groups/', status=200)
        roles = [str(t) for t in r.html.findAll('td',{'class':'group'})]
        assert 'RoleNew1' not in roles
        assert 'rleNew2' not in roles

        # make sure can still access homepage after one of user's roles were deleted
        r = self.app.get('/p/test/wiki/', extra_environ=dict(username='test-user')).follow()
        assert r.status == '200 OK'

    def test_change_perms(self):
        r = self.app.get('/admin/groups/')
        dev_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[2]
        mem_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[3]
        mem_id = mem_holder['data-group']
        # neither group has update permission
        assert dev_holder.findAll('ul')[1].findAll('li')[2]['class'] == "no"
        assert mem_holder.findAll('ul')[1].findAll('li')[2]['class'] == "no"
        # add update permission to Member
        r = self.app.post('/admin/groups/change_perm', params={
                'role_id': mem_id,
                'permission': 'create',
                'allow': 'true'})
        r = self.app.get('/admin/groups/')
        dev_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[2]
        mem_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[3]
        # Member now has update permission
        assert mem_holder.findAll('ul')[1].findAll('li')[2]['class'] == "yes"
        # Developer has inherited update permission from Member
        assert dev_holder.findAll('ul')[1].findAll('li')[2]['class'] == "inherit"
        # remove update permission from Member
        r = self.app.post('/admin/groups/change_perm', params={
                'role_id': mem_id,
                'permission': 'create',
                'allow': 'false'})
        r = self.app.get('/admin/groups/')
        dev_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[2]
        mem_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[3]
        # neither group has update permission
        assert dev_holder.findAll('ul')[1].findAll('li')[2]['class'] == "no"
        assert mem_holder.findAll('ul')[1].findAll('li')[2]['class'] == "no"

    def test_permission_inherit(self):
        r = self.app.get('/admin/groups/')
        admin_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[1]
        admin_id = admin_holder['data-group']
        mem_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[3]
        mem_id = mem_holder['data-group']
        anon_holder = r.html.find('table',{'id':'usergroup_admin'}).findAll('tr')[5]
        anon_id = anon_holder['data-group']
        #first remove create from Admin so we can see it inherit
        r = self.app.post('/admin/groups/change_perm', params={
                'role_id': admin_id,
                'permission': 'create',
                'allow': 'false'})
        # updates to anon inherit up
        r = self.app.post('/admin/groups/change_perm', params={
                'role_id': anon_id,
                'permission': 'create',
                'allow': 'true'})
        assert {u'text': u'Inherited permission create from Anonymous', u'has': u'inherit', u'name': u'create'} in r.json[admin_id]
        assert {u'text': u'Inherited permission create from Anonymous', u'has': u'inherit', u'name': u'create'} in r.json[mem_id]
        assert {u'text': u'Has permission create', u'has': u'yes', u'name': u'create'} in r.json[anon_id]
        r = self.app.post('/admin/groups/change_perm', params={
                'role_id': anon_id,
                'permission': 'create',
                'allow': 'false'})
        assert {u'text': u'Does not have permission create', u'has': u'no', u'name': u'create'} in r.json[admin_id]
        assert {u'text': u'Does not have permission create', u'has': u'no', u'name': u'create'} in r.json[mem_id]
        assert {u'text': u'Does not have permission create', u'has': u'no', u'name': u'create'} in r.json[anon_id]
        # updates to Member inherit up
        r = self.app.post('/admin/groups/change_perm', params={
                'role_id': mem_id,
                'permission': 'create',
                'allow': 'true'})
        assert {u'text': u'Inherited permission create from Member', u'has': u'inherit', u'name': u'create'} in r.json[admin_id]
        assert {u'text': u'Has permission create', u'has': u'yes', u'name': u'create'} in r.json[mem_id]
        assert {u'text': u'Does not have permission create', u'has': u'no', u'name': u'create'} in r.json[anon_id]
        r = self.app.post('/admin/groups/change_perm', params={
                'role_id': mem_id,
                'permission': 'create',
                'allow': 'false'})
        assert {u'text': u'Does not have permission create', u'has': u'no', u'name': u'create'} in r.json[admin_id]
        assert {u'text': u'Does not have permission create', u'has': u'no', u'name': u'create'} in r.json[mem_id]
        assert {u'text': u'Does not have permission create', u'has': u'no', u'name': u'create'} in r.json[anon_id]
