import os
import unittest
from itertools import count
from datetime import datetime

import mock
from nose.tools import assert_equal
from pylons import c
import tg
import ming
from ming.base import Object
from ming.orm import session, ThreadLocalORMSession

from alluratest.controller import setup_basic_test, setup_global_objects
from allura import model as M
from allura.lib import helpers as h


class _Test(unittest.TestCase):
    idgen = ( 'obj_%d' % i for i in count())

    def _make_tree(self, object_id, **kwargs):
        t, isnew = M.repo.Tree.upsert(object_id)
        repo = getattr(self, 'repo', None)
        t.repo = repo
        for k,v in kwargs.iteritems():
            if isinstance(v, basestring):
                obj = M.repo.Blob(
                    t, k, self.idgen.next())
                t.blob_ids.append(Object(
                        name=k, id=obj._id))
            else:
                obj = self._make_tree(self.idgen.next(), **v)
                t.tree_ids.append(Object(
                        name=k, id=obj._id))
        session(t).flush()
        return t

    def _make_commit(self, object_id, **tree_parts):
        ci, isnew = M.repo.Commit.upsert(object_id)
        if isnew:
            ci.committed.email=c.user.email_addresses[0]
            ci.authored.email=c.user.email_addresses[0]
            dt = datetime.utcnow()
            # BSON datetime resolution is to 1 millisecond, not 1 microsecond
            # like Python. Round this now so it'll match the value that's
            # pulled from MongoDB in the tests.
            ci.authored.date = dt.replace(microsecond=dt.microsecond/1000 * 1000)
            ci.message='summary\n\nddescription'
            ci.set_context(self.repo)
            ci.tree_id = 't_' + object_id
            ci.tree = self._make_tree(ci.tree_id, **tree_parts)
        return ci, isnew

    def _make_log(self, ci):
        session(ci).flush(ci)
        rb = M.repo_refresh.CommitRunBuilder([ci._id])
        rb.run()
        rb.cleanup()

    def setUp(self):
        setup_basic_test()
        setup_global_objects()
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()
        self.prefix = tg.config.get('scm.repos.root', '/')

class _TestWithRepo(_Test):
    def setUp(self):
        super(_TestWithRepo, self).setUp()
        h.set_context('test', neighborhood='Projects')
        c.project.install_app('svn', 'test1')
        h.set_context('test', 'test1', neighborhood='Projects')
        self.repo = M.Repository(name='test1', tool='svn')
        self.repo._impl = mock.Mock(spec=M.RepositoryImplementation())
        self.repo._impl.shorthand_for_commit = M.RepositoryImplementation.shorthand_for_commit
        self.repo._impl.url_for_commit = (
            lambda *a, **kw: M.RepositoryImplementation.url_for_commit(
                self.repo._impl, *a, **kw))
        self.repo._impl.log = lambda *a,**kw:(['foo'], [])
        self.repo._impl._repo = self.repo
        self.repo._impl.all_commit_ids = lambda *a,**kw: []
        ThreadLocalORMSession.flush_all()
        # ThreadLocalORMSession.close_all()

class _TestWithRepoAndCommit(_TestWithRepo):
    def setUp(self):
        super(_TestWithRepoAndCommit, self).setUp()
        self.ci, isnew = self._make_commit('foo')
        ThreadLocalORMSession.flush_all()
        # ThreadLocalORMSession.close_all()

class TestRepo(_TestWithRepo):

    def test_create(self):
        assert self.repo.fs_path == os.path.join(self.prefix, 'svn/p/test/')
        assert self.repo.url_path == '/p/test/'
        assert self.repo.full_fs_path == os.path.join(self.prefix, 'svn/p/test/test1')

    def test_passthrough(self):
        argless = ['init']
        for fn in argless:
            getattr(self.repo, fn)()
            getattr(self.repo._impl, fn).assert_called_with()
        unary = [ 'commit', 'open_blob' ]
        for fn in unary:
            getattr(self.repo, fn)('foo')
            getattr(self.repo._impl, fn).assert_called_with('foo')

    def test_shorthand_for_commit(self):
        self.assertEqual(
            self.repo.shorthand_for_commit('a'*40),
            '[aaaaaa]')

    def test_url_for_commit(self):
        self.assertEqual(
            self.repo.url_for_commit('a'*40),
            '/p/test/test1/ci/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/')

    def test_init_as_clone(self):
        self.repo.init_as_clone('srcpath', 'srcname', 'srcurl')
        assert self.repo.upstream_repo.name == 'srcname'
        assert self.repo.upstream_repo.url == 'srcurl'
        assert self.repo._impl.clone_from.called_with('srcpath')

    def test_log(self):
        ci = mock.Mock()
        ci.log = mock.Mock(return_value=[1,2,3])
        self.repo._impl.commit = mock.Mock(return_value=ci)
        assert self.repo.log() == [1,2,3], self.repo.log()

    def test_count_revisions(self):
        ci = mock.Mock()
        ci.count_revisions = mock.Mock(return_value=42)
        self.repo._impl.commit = mock.Mock(return_value=ci)
        assert self.repo.count() == 42

    def test_latest(self):
        ci = mock.Mock()
        self.repo._impl.commit = mock.Mock(return_value=ci)
        assert self.repo.latest() is ci

    def test_index(self):
        i = self.repo.index()
        assert i['type_s'] == 'Repository', i
        assert i['name_s'] == 'test1', i

    def test_scm_host_url(self):
        assert (
            self.repo.clone_url('rw', 'nobody')
            == 'svn+ssh://nobody@localhost:8022/scm-repo/p/test/test1/'),\
            self.repo.clone_url('rw', 'nobody')
        assert (
            self.repo.clone_url('https', 'nobody')
            == 'https://nobody@localhost:8022/scm-repo/p/test/test1/'),\
            self.repo.clone_url('https', 'nobody')

    def test_merge_request(self):
        M.MergeRequest.upsert(app_config_id=c.app.config._id, status='open')
        M.MergeRequest.upsert(app_config_id=c.app.config._id, status='closed')
        session(M.MergeRequest).flush()
        session(M.MergeRequest).clear()
        assert self.repo.merge_requests_by_statuses('open').count() == 1
        assert self.repo.merge_requests_by_statuses('closed').count() == 1
        assert self.repo.merge_requests_by_statuses('open', 'closed').count() == 2

    def test_guess_type(self):
        assert self.repo.guess_type('foo.txt') == ('text/plain', None)
        assert self.repo.guess_type('foo.gbaer') == ('application/octet-stream', None)
        assert self.repo.guess_type('foo.html') == ('text/html', None)
        assert self.repo.guess_type('.gitignore') == ('text/plain', None)

    def test_refresh(self):
        ci = mock.Mock()
        ci.count_revisions=mock.Mock(return_value=100)
        ci.authored.name = 'Test Committer'
        ci.author_url = '/u/test-committer/'
        self.repo._impl.commit = mock.Mock(return_value=ci)
        self.repo._impl.new_commits = mock.Mock(return_value=['foo%d' % i for i in range(100) ])
        self.repo._impl.all_commit_ids = mock.Mock(return_value=['foo%d' % i for i in range(100) ])
        self.repo.symbolics_for_commit = mock.Mock(return_value=[['master', 'branch'], []])
        def refresh_commit_info(oid, seen, lazy=False):
            M.repo.CommitDoc(dict(
                    authored=dict(
                        name='Test Committer',
                        email='test@test.com'),
                    _id=oid)).m.insert()
        def set_heads():
            self.repo.heads = [ ming.base.Object(name='head', object_id='foo0', count=100) ]
        self.repo._impl.refresh_commit_info = refresh_commit_info
        self.repo._impl.refresh_heads = mock.Mock(side_effect=set_heads)
        self.repo.shorthand_for_commit = lambda oid: '[' + str(oid) + ']'
        self.repo.url_for_commit = lambda oid: '/ci/' + str(oid) + '/'
        self.repo.refresh()
        ThreadLocalORMSession.flush_all()
        notifications = M.Notification.query.find().all()
        for n in notifications:
            if '100 new commits' in n.subject:
                assert "master,branch:  by Test Committer http://localhost/#" in n.text
                break
        else:
            assert False, 'Did not find notification'
        assert M.Feed.query.find(dict(
            title='New commit',
            author_name='Test Committer')).count()

    def test_refresh_private(self):
        ci = mock.Mock()
        ci.count_revisions=mock.Mock(return_value=100)
        self.repo._impl.commit = mock.Mock(return_value=ci)
        self.repo._impl.new_commits = mock.Mock(return_value=['foo%d' % i for i in range(100) ])
        def set_heads():
            self.repo.heads = [ ming.base.Object(name='head', object_id='foo0', count=100) ]
        self.repo._impl.refresh_heads = mock.Mock(side_effect=set_heads)

        # make unreadable by *anonymous, so additional notification logic executes
        self.repo.acl = []
        c.project.acl = []

        self.repo.refresh()

    def test_push_upstream_context(self):
        self.repo.init_as_clone('srcpath', '/p/test/svn/', '/p/test/svn/')
        old_app_instance = M.Project.app_instance
        try:
            M.Project.app_instance = mock.Mock(return_value=ming.base.Object(
                    config=ming.base.Object(_id=None)))
            with self.repo.push_upstream_context():
                assert c.project.shortname == 'test'
        finally:
            M.Project.app_instance = old_app_instance

    def test_pending_upstream_merges(self):
        self.repo.init_as_clone('srcpath', '/p/test/svn/', '/p/test/svn/')
        old_app_instance = M.Project.app_instance
        try:
            M.Project.app_instance = mock.Mock(return_value=ming.base.Object(
                    config=ming.base.Object(_id=None)))
            self.repo.pending_upstream_merges()
        finally:
            M.Project.app_instance = old_app_instance

class TestMergeRequest(_TestWithRepoAndCommit):

    def setUp(self):
        super(TestMergeRequest, self).setUp()
        c.project.install_app('svn', 'test2')
        h.set_context('test', 'test2', neighborhood='Projects')
        self.repo2 = M.Repository(name='test2', tool='svn')
        self.repo2._impl = mock.Mock(spec=M.RepositoryImplementation())
        self.repo2._impl.log = lambda *a,**kw:(['foo'], [])
        self.repo2._impl._repo = self.repo2
        self.repo2.init_as_clone('/p/test/', 'test1', '/p/test/test1/')
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()

    def test_upsert(self):
        h.set_context('test', 'test1', neighborhood='Projects')
        mr = M.MergeRequest.upsert(
            downstream=ming.base.Object(
                project_id=c.project._id,
                mount_point='test2',
                commit_id='foo'),
            target_branch='foobranch',
            summary='summary',
            description='description')
        u = M.User.by_username('test-admin')
        assert mr.creator == u
        assert mr.creator_name == u.get_pref('display_name')
        assert mr.creator_url == u.url()
        assert mr.downstream_url == '/p/test/test2/'
        assert mr.downstream_repo_url == 'http://svn.localhost/p/test/test2/'
        assert mr.commits == [ self._make_commit('foo')[0] ]

class TestRepoObject(_TestWithRepoAndCommit):

    def test_upsert(self):
        obj0, isnew0 = M.repo.Tree.upsert('foo1')
        obj1, isnew1 = M.repo.Tree.upsert('foo1')
        assert obj0 is obj1
        assert isnew0 and not isnew1

    def test_set_last_commit(self):
        obj, isnew = M.repo.Tree.upsert('foo1')
        M.repo_refresh.set_last_commit(
            self.repo._id, '/', 'fakefile', obj._id,
            M.repo_refresh.get_commit_info(self.ci))

    def test_get_last_commit(self):
        obj, isnew = M.repo.Tree.upsert('foo1')
        lc0 = M.repo_refresh.set_last_commit(
            self.repo._id, '/', 'fakefile', obj._id,
            M.repo_refresh.get_commit_info(self.ci))

        lc1 = M.repo.LastCommitDoc.m.get(object_id=obj._id)
        assert lc0 == lc1

    def test_get_last_commit_missing(self):
        obj, isnew = M.repo.Tree.upsert('foo1')
        lc1 = M.repo.LastCommitDoc.m.get(object_id=obj._id)
        assert lc1 is None

    def test_artifact_methods(self):
        assert self.ci.index_id() == 'allura/model/repo/Commit#foo', self.ci.index_id()
        assert self.ci.primary() is self.ci, self.ci.primary()


class TestCommit(_TestWithRepo):

    def setUp(self):
        super(TestCommit, self).setUp()
        self.ci, isnew = self._make_commit(
            'foo',
            a=dict(
                a=dict(
                    a='',
                    b='',),
                b=''))
        self.tree = self.ci.tree
        impl = M.RepositoryImplementation()
        impl._repo = self.repo
        self.repo._impl.shorthand_for_commit = impl.shorthand_for_commit
        self.repo._impl.url_for_commit = impl.url_for_commit

    def test_upsert(self):
        obj0, isnew0 = M.repo.Commit.upsert('foo')
        obj1, isnew1 = M.repo.Commit.upsert('foo')
        assert obj0 is obj1
        assert not isnew1
        u = M.User.by_username('test-admin')
        assert self.ci.author_url == u.url()
        assert self.ci.committer_url == u.url()
        assert self.ci.tree is self.tree
        assert self.ci.summary == 'summary'
        assert self.ci.shorthand_id() == '[foo]'
        assert self.ci.url() == '/p/test/test1/ci/foo/'

    def test_get_path(self):
        b = self.ci.get_path('a/a/a')
        assert isinstance(b, M.repo.Blob)
        x = self.ci.get_path('a/a')
        assert isinstance(x, M.repo.Tree)

    def test_log(self):
        rb = M.repo_refresh.CommitRunBuilder(['foo'])
        rb.run()
        rb.cleanup()
        commits = self.ci.log(0, 100)
        assert commits[0]._id == 'foo'

    def test_count_revisions(self):
        rb = M.repo_refresh.CommitRunBuilder(['foo'])
        rb.run()
        rb.cleanup()
        assert self.ci.count_revisions() == 1

    def test_compute_diffs(self):
        self.repo._impl.commit = mock.Mock(return_value=self.ci)
        M.repo_refresh.refresh_commit_trees(self.ci, {})
        M.repo_refresh.compute_diffs(self.repo._id, {}, self.ci)
        # self.ci.compute_diffs()
        assert self.ci.diffs.added == [ 'a' ]
        assert (self.ci.diffs.copied
                == self.ci.diffs.changed
                == self.ci.diffs.removed
                == [])
        ci, isnew = self._make_commit('bar')
        ci.parent_ids = [ 'foo' ]
        self._make_log(ci)
        M.repo_refresh.refresh_commit_trees(ci, {})
        M.repo_refresh.compute_diffs(self.repo._id, {}, ci)
        assert ci.diffs.removed == [ 'a' ]
        assert (ci.diffs.copied
                == ci.diffs.changed
                == ci.diffs.added
                == [])
        ci, isnew = self._make_commit(
            'baz',
            b=dict(
                a=dict(
                    a='',
                    b='',),
                b=''))
        ci.parent_ids = [ 'foo' ]
        self._make_log(ci)
        M.repo_refresh.refresh_commit_trees(ci, {})
        M.repo_refresh.compute_diffs(self.repo._id, {}, ci)
        assert ci.diffs.added == [ 'b' ]
        assert ci.diffs.removed == [ 'a' ]
        assert (ci.diffs.copied
                == ci.diffs.changed
                == [])

    def test_diffs_file_renames(self):
        def open_blob(blob):
            blobs = {
                u'a': u'Leia',
                u'/b/a/a': u'Darth Vader',
                u'/b/a/b': u'Luke Skywalker',
                u'/b/b': u'Death Star will destroy you',
                u'/b/c': u'Luke Skywalker',  # moved from /b/a/b
                u'/b/a/z': u'Death Star will destroy you\nALL',  # moved from /b/b and modified
            }
            from cStringIO import StringIO
            return StringIO(blobs[blob.path()])
        self.repo._impl.open_blob = open_blob

        self.repo._impl.commit = mock.Mock(return_value=self.ci)
        M.repo_refresh.refresh_commit_trees(self.ci, {})
        M.repo_refresh.compute_diffs(self.repo._id, {}, self.ci)
        assert self.ci.diffs.added == ['a']
        assert (self.ci.diffs.copied
                == self.ci.diffs.changed
                == self.ci.diffs.removed
                == [])

        ci, isnew = self._make_commit(
            'bar',
            b=dict(
                a=dict(
                    a='',
                    b='',),
                b=''))
        ci.parent_ids = ['foo']
        self._make_log(ci)
        M.repo_refresh.refresh_commit_trees(ci, {})
        M.repo_refresh.compute_diffs(self.repo._id, {}, ci)
        assert ci.diffs.added == ['b']
        assert ci.diffs.removed == ['a']
        assert (ci.diffs.copied
                == ci.diffs.changed
                == [])

        ci, isnew = self._make_commit(
            'baz',
            b=dict(
                a=dict(
                    z=''),
                c=''))
        ci.parent_ids = ['bar']
        self._make_log(ci)
        M.repo_refresh.refresh_commit_trees(ci, {})
        M.repo_refresh.compute_diffs(self.repo._id, {}, ci)
        assert ci.diffs.added == ci.diffs.changed == []
        assert ci.diffs.removed == ['b/a/a']
        # see mock for open_blob
        assert len(ci.diffs.copied) == 2
        assert ci.diffs.copied[0]['old'] == 'b/a/b'
        assert ci.diffs.copied[0]['new'] == 'b/c'
        assert ci.diffs.copied[0]['ratio'] == 1
        assert ci.diffs.copied[0]['diff'] == ''
        assert ci.diffs.copied[1]['old'] == 'b/b'
        assert ci.diffs.copied[1]['new'] == 'b/a/z'
        assert ci.diffs.copied[1]['ratio'] < 1
        assert '+++' in ci.diffs.copied[1]['diff']

    def test_context(self):
        self.ci.context()

class TestGitLikeTree(object):

    def test_set_blob(self):
        tree = M.GitLikeTree()
        tree.set_blob('/dir/dir2/file', 'file-oid')

        assert_equal(tree.blobs, {})
        assert_equal(tree.get_tree('dir').blobs, {})
        assert_equal(tree.get_tree('dir').get_tree('dir2').blobs, {'file': 'file-oid'})

    def test_hex(self):
        tree = M.GitLikeTree()
        tree.set_blob('/dir/dir2/file', 'file-oid')
        hex = tree.hex()

        # check the reprs. In case hex (below) fails, this'll be useful
        assert_equal(repr(tree.get_tree('dir').get_tree('dir2')), 'b file-oid file')
        assert_equal(repr(tree), 't 96af1772ecce1e6044e6925e595d9373ffcd2615 dir')
        # the hex() value shouldn't change, it's an important key
        assert_equal(hex, '4abba29a43411b9b7cecc1a74f0b27920554350d')

        # another one should be the same
        tree2 = M.GitLikeTree()
        tree2.set_blob('/dir/dir2/file', 'file-oid')
        hex2 = tree2.hex()
        assert_equal(hex, hex2)


class RepoImplTestBase(object):

    def test_commit_run(self):
        M.repo.CommitRunDoc.m.remove()
        commit_ids = list(self.repo.all_commit_ids())
        # simulate building up a commit run from multiple pushes
        for c_id in commit_ids:
            crb = M.repo_refresh.CommitRunBuilder([c_id])
            crb.run()
            crb.cleanup()
        runs = M.repo.CommitRunDoc.m.find().all()
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run.commit_ids, commit_ids)
        self.assertEqual(len(run.commit_ids), len(run.commit_times))
        self.assertEqual(run.parent_commit_ids, [])

    def test_repair_commit_run(self):
        commit_ids = list(self.repo.all_commit_ids())
        # simulate building up a commit run from multiple pushes, but skip the
        # last commit to simulate a broken commit run
        for c_id in commit_ids[:-1]:
            crb = M.repo_refresh.CommitRunBuilder([c_id])
            crb.run()
            crb.cleanup()
        # now repair the commitrun by rebuilding with all commit ids
        crb = M.repo_refresh.CommitRunBuilder(commit_ids)
        crb.run()
        crb.cleanup()
        runs = M.repo.CommitRunDoc.m.find().all()
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run.commit_ids, commit_ids)
        self.assertEqual(len(run.commit_ids), len(run.commit_times))
        self.assertEqual(run.parent_commit_ids, [])