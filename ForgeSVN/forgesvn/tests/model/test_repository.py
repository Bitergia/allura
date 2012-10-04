import os
import shutil
import unittest
import pkg_resources

from ming.orm import ThreadLocalORMSession

from alluratest.controller import setup_basic_test, setup_global_objects
from allura.lib import helpers as h
from allura.lib.utils import svn_path_exists
from allura.tests import decorators as td
from allura.tests.model.test_repo import RepoImplTestBase
from allura import model as M
from forgesvn import model as SM

class TestNewRepo(unittest.TestCase):

    def setUp(self):
        setup_basic_test()
        self.setup_with_tools()

    @td.with_svn
    def setup_with_tools(self):
        setup_global_objects()
        h.set_context('test', 'src', neighborhood='Projects')
        repo_dir = pkg_resources.resource_filename(
            'forgesvn', 'tests/data/')
        self.repo = SM.Repository(
            name='testsvn',
            fs_path=repo_dir,
            url_path = '/test/',
            tool = 'svn',
            status = 'creating')
        self.repo.refresh()
        self.rev = M.repo.Commit.query.get(_id=self.repo.heads[0]['object_id'])
        self.rev.repo = self.repo
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()

    def test_last_commit_for(self):
        tree = self.rev.tree
        for row in tree.ls():
            assert row['last_commit']['author'] is not None

    def test_commit(self):
        assert self.rev.primary() is self.rev
        assert self.rev.index_id().startswith('allura/model/repo/Commit#')
        self.rev.author_url
        self.rev.committer_url
        assert self.rev.tree._id == self.rev.tree_id
        assert self.rev.summary == self.rev.message.splitlines()[0]
        assert self.rev.shorthand_id() == '[r5]'
        assert self.rev.symbolic_ids == ([], [])
        assert self.rev.url() == (
            '/p/test/src/5/')
        all_cis = self.rev.log(0, 1000)
        assert len(all_cis) == 5
        assert self.rev.log(1,1000) == all_cis[1:]
        assert self.rev.log(0,3) == all_cis[:3]
        assert self.rev.log(1,2) == all_cis[1:3]
        for ci in all_cis:
            ci.count_revisions()
            ci.context()
        self.rev.tree.ls()
        assert self.rev.tree.readme() == (
            'README', 'This is readme\nAnother Line\n')
        assert self.rev.tree.path() == '/'
        assert self.rev.tree.url() == (
            '/p/test/src/5/tree/')
        self.rev.tree.by_name['README']
        assert self.rev.tree.is_blob('README') == True
        assert self.rev.tree['a']['b']['c'].ls() == []
        self.assertRaises(KeyError, lambda:self.rev.tree['a']['b']['d'])

class TestSVNRepo(unittest.TestCase, RepoImplTestBase):

    def setUp(self):
        setup_basic_test()
        self.setup_with_tools()

    @td.with_svn
    def setup_with_tools(self):
        setup_global_objects()
        h.set_context('test', 'src', neighborhood='Projects')
        repo_dir = pkg_resources.resource_filename(
            'forgesvn', 'tests/data/')
        self.repo = SM.Repository(
            name='testsvn',
            fs_path=repo_dir,
            url_path = '/test/',
            tool = 'svn',
            status = 'creating')
        self.repo.refresh()
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()

    def test_init(self):
        repo = SM.Repository(
            name='testsvn',
            fs_path='/tmp/',
            url_path = '/test/',
            tool = 'svn',
            status = 'creating')
        dirname = os.path.join(repo.fs_path, repo.name)
        if os.path.exists(dirname):
            shutil.rmtree(dirname)
        repo.init()
        shutil.rmtree(dirname)

    def test_fork(self):
        repo = SM.Repository(
            name='testsvn',
            fs_path='/tmp/',
            url_path = '/test/',
            tool = 'svn',
            status = 'creating')
        repo_path = pkg_resources.resource_filename(
            'forgesvn', 'tests/data/testsvn')
        dirname = os.path.join(repo.fs_path, repo.name)
        if os.path.exists(dirname):
            shutil.rmtree(dirname)
        repo.init()
        repo._impl.clone_from('file://' + repo_path, copy_hooks=False)
        assert len(repo.log())
        assert os.path.exists('/tmp/testsvn/hooks/pre-revprop-change')
        assert os.access('/tmp/testsvn/hooks/pre-revprop-change', os.X_OK)
        with open('/tmp/testsvn/hooks/pre-revprop-change') as f: c = f.read()
        self.assertEqual(c, '#!/bin/sh\n')
        assert not os.path.exists('/tmp/testsvn/hooks/post-revprop-change')
        assert not os.path.exists('/tmp/testsvn/hooks/post-commit-user')
        assert os.path.exists('/tmp/testsvn/hooks/post-commit')
        assert os.access('/tmp/testsvn/hooks/post-commit', os.X_OK)
        with open('/tmp/testsvn/hooks/post-commit') as f: c = f.read()
        self.assertIn('curl -s http://localhost//auth/refresh_repo/p/test/src/\n', c)
        self.assertIn('exec $DIR/post-commit-user "$@"\n', c)
        shutil.rmtree(dirname)

    def test_clone(self):
        repo = SM.Repository(
            name='testsvn',
            fs_path='/tmp/',
            url_path = '/test/',
            tool = 'svn',
            status = 'creating')
        repo_path = pkg_resources.resource_filename(
            'forgesvn', 'tests/data/testsvn')
        dirname = os.path.join(repo.fs_path, repo.name)
        if os.path.exists(dirname):
            shutil.rmtree(dirname)
        repo.init()
        repo._impl.clone_from('file://' + repo_path, copy_hooks=True)
        assert len(repo.log())
        assert os.path.exists('/tmp/testsvn/hooks/pre-revprop-change')
        assert os.access('/tmp/testsvn/hooks/pre-revprop-change', os.X_OK)
        with open('/tmp/testsvn/hooks/pre-revprop-change') as f: c = f.read()
        self.assertEqual(c, 'pre-revprop-change\n')
        assert os.path.exists('/tmp/testsvn/hooks/post-revprop-change')
        assert os.access('/tmp/testsvn/hooks/post-revprop-change', os.X_OK)
        with open('/tmp/testsvn/hooks/post-revprop-change') as f: c = f.read()
        self.assertEqual(c, 'post-revprop-change\n')
        assert os.path.exists('/tmp/testsvn/hooks/post-commit-user')
        assert os.access('/tmp/testsvn/hooks/post-commit-user', os.X_OK)
        with open('/tmp/testsvn/hooks/post-commit-user') as f: c = f.read()
        self.assertEqual(c, 'post-commit\n')
        assert os.path.exists('/tmp/testsvn/hooks/post-commit')
        assert os.access('/tmp/testsvn/hooks/post-commit', os.X_OK)
        with open('/tmp/testsvn/hooks/post-commit') as f: c = f.read()
        self.assertIn('curl -s http://localhost//auth/refresh_repo/p/test/src/\n', c)
        self.assertIn('exec $DIR/post-commit-user "$@"\n', c)
        shutil.rmtree(dirname)

    def test_index(self):
        i = self.repo.index()
        assert i['type_s'] == 'SVN Repository', i

    def test_log(self):
        for entry in self.repo.log():
            assert entry.committed.name == 'rick446'
            assert entry.message
            print '=='
            print entry._id
            print entry.message
            print entry.diffs

    def test_diff_create_file(self):
        entry = self.repo.log(1, limit=1)[0]
        self.assertEqual(
            entry.diffs, dict(
                copied=[], changed=[],
                removed=[], added=['/README']))

    def test_diff_create_path(self):
        entry = self.repo.log(2, limit=1)[0]
        self.assertEqual(
            entry.diffs, dict(
                copied=[], changed=[], removed=[],
                added=[
                    '/a', '/a/b', '/a/b/c',
                    '/a/b/c/hello.txt']))

    def test_diff_modify_file(self):
        entry = self.repo.log(3, limit=1)[0]
        self.assertEqual(
            entry.diffs, dict(
                copied=[], changed=['/README'],
                removed=[], added=[]))

    def test_diff_delete(self):
        entry = self.repo.log(4, limit=1)[0]
        self.assertEqual(
            entry.diffs, dict(
                copied=[], changed=[],
                removed=['/a/b/c/hello.txt'], added=[]))

    def test_diff_copy(self):
        # Copies are currently only detected as 'add'
        entry = self.repo.log(5, limit=1)[0]
        self.assertEqual(
            entry.diffs, dict(
                copied=[], changed=[],
                removed=[], added=['/b']))

    def test_commit(self):
        entry = self.repo.commit(1)
        assert entry.committed.name == 'rick446'
        assert entry.message

    def test_svn_path_exists(self):
        repo_path = pkg_resources.resource_filename(
            'forgesvn', 'tests/data/testsvn')
        assert svn_path_exists("file://%s/a" % repo_path)
        assert svn_path_exists("file://%s" % repo_path)
        assert not svn_path_exists("file://%s/badpath" % repo_path)


class TestSVNRev(unittest.TestCase):

    def setUp(self):
        setup_basic_test()
        self.setup_with_tools()

    @td.with_svn
    def setup_with_tools(self):
        setup_global_objects()
        h.set_context('test', 'src', neighborhood='Projects')
        repo_dir = pkg_resources.resource_filename(
            'forgesvn', 'tests/data/')
        self.repo = SM.Repository(
            name='testsvn',
            fs_path=repo_dir,
            url_path = '/test/',
            tool = 'svn',
            status = 'creating')
        self.repo.refresh()
        self.rev = self.repo.commit(1)
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()

    def test_url(self):
        assert self.rev.url().endswith('/1/')

    def test_primary(self):
        assert self.rev.primary() == self.rev

    def test_shorthand(self):
        assert self.rev.shorthand_id() == '[r1]'

    def test_diff(self):
        diffs = (self.rev.diffs.added
                 +self.rev.diffs.removed
                 +self.rev.diffs.changed
                 +self.rev.diffs.copied)
        for d in diffs:
            print d
