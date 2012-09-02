import os
import stat
import errno
import mimetypes
import logging
import string
import re
from difflib import SequenceMatcher
from hashlib import sha1
from datetime import datetime
from collections import defaultdict

import tg
from paste.deploy.converters import asbool
from pylons import c
import pymongo.errors

from ming import schema as S
from ming.utils import LazyProperty
from ming.orm import FieldProperty, session, Mapper
from ming.orm.declarative import MappedClass

from allura.lib import helpers as h
from allura.lib import utils

from .artifact import Artifact, VersionedArtifact, Feed
from .auth import User
from .session import repository_orm_session, project_orm_session
from .notification import Notification
from .repo_refresh import refresh_repo
from .timeline import ActivityObject

log = logging.getLogger(__name__)
config = utils.ConfigProxy(
    common_suffix='forgemail.domain',
    common_prefix='forgemail.url')

README_RE = re.compile('^README(\.[^.]*)?$', re.IGNORECASE)
VIEWABLE_EXTENSIONS = ['.php','.py','.js','.java','.html','.htm','.yaml','.sh',
    '.rb','.phtml','.txt','.bat','.ps1','.xhtml','.css','.cfm','.jsp','.jspx',
    '.pl','.php4','.php3','.rhtml','.svg','.markdown','.json','.ini','.tcl','.vbs','.xsl']

class RepositoryImplementation(object):

    # Repository-specific code
    def init(self): # pragma no cover
        raise NotImplementedError, 'init'

    def clone_from(self, source_path, source_url): # pragma no cover
        raise NotImplementedError, 'clone_from'

    def commit(self, revision): # pragma no cover
        raise NotImplementedError, 'commit'

    def all_commit_ids(self): # pragma no cover
        raise NotImplementedError, 'all_commit_ids'

    def new_commits(self, all_commits=False): # pragma no cover
        '''Return a list of native commits in topological order (heads first).

        "commit" is a repo-native object, NOT a Commit object.
        If all_commits is False, only return commits not already indexed.
        '''
        raise NotImplementedError, 'new_commits'

    def commit_parents(self, commit): # pragma no cover
        '''Return a list of native commits for the parents of the given (native)
        commit'''
        raise NotImplementedError, 'commit_parents'

    def refresh_heads(self): # pragma no cover
        '''Sets repository metadata such as heads, tags, and branches'''
        raise NotImplementedError, 'refresh_heads'

    def refresh_commit_info(self, oid, lazy=True): # pragma no cover
        '''Refresh the data in the commit with id oid'''
        raise NotImplementedError, 'refresh_commit_info'

    def _setup_hooks(self): # pragma no cover
        '''Install a hook in the repository that will ping the refresh url for
        the repo'''
        raise NotImplementedError, '_setup_hooks'

    def log(self, object_id, skip, count): # pragma no cover
        '''Return a list of (object_id, ci) beginning at the given commit ID and continuing
        to the parent nodes in a breadth-first traversal.  Also return a list of 'next commit' options
        (these are candidates for he next commit after 'count' commits have been
        exhausted).'''
        raise NotImplementedError, 'log'

    def compute_tree_new(self, commit, path='/'): # pragma no cover
        '''Used in hg and svn to compute a git-like-tree lazily with the new models'''
        raise NotImplementedError, 'compute_tree'

    def open_blob(self, blob): # pragma no cover
        '''Return a file-like object that contains the contents of the blob'''
        raise NotImplementedError, 'open_blob'

    def blob_size(self, blob):
        '''Return a blob size in bytes'''
        raise NotImplementedError, 'blob_size'

    @classmethod
    def shorthand_for_commit(cls, oid):
        return '[%s]' % oid[:6]

    def symbolics_for_commit(self, commit):
        '''Return symbolic branch and tag names for a commit.
        Default generic implementation is provided, subclasses
        may override if they have more efficient means.'''
        branches = [b.name for b in self._repo.branches if b.object_id == commit._id]
        tags = [t.name for t in self._repo.repo_tags if t.object_id == commit._id]
        return branches, tags

    def url_for_commit(self, commit):
        'return an URL, given either a commit or object id'
        if isinstance(commit, basestring):
            object_id = commit
        else:
            object_id = commit._id
        return '%sci/%s/' % (self._repo.url(), object_id)

    def _setup_paths(self, create_repo_dir=True):
        if not self._repo.fs_path.endswith('/'): self._repo.fs_path += '/'
        fullname = self._repo.fs_path + self._repo.name
        path = fullname if create_repo_dir else self._repo.fs_path
        try:
            os.makedirs(path)
        except OSError, e: # pragma no cover
            if e.errno != errno.EEXIST:
                raise
            else:
                log.warn('setup_paths error %s' % path, exc_info=True)
        return fullname

    def _setup_special_files(self):
        magic_file = os.path.join(self._repo.fs_path, self._repo.name, '.SOURCEFORGE-REPOSITORY')
        with open(magic_file, 'w') as f:
            f.write(self._repo.repo_id)
        os.chmod(magic_file, stat.S_IRUSR|stat.S_IRGRP|stat.S_IROTH)
        self._setup_hooks()

class Repository(Artifact, ActivityObject):
    BATCH_SIZE=100
    class __mongometa__:
        name='generic-repository'
        indexes = ['upstream_repo.name']
    _impl = None
    repo_id='repo'
    type_s='Repository'

    name=FieldProperty(str)
    tool=FieldProperty(str)
    fs_path=FieldProperty(str)
    url_path=FieldProperty(str)
    status=FieldProperty(str)
    email_address=''
    additional_viewable_extensions=FieldProperty(str)
    heads = FieldProperty([dict(name=str,object_id=str, count=int)])
    branches = FieldProperty([dict(name=str,object_id=str, count=int)])
    repo_tags = FieldProperty([dict(name=str,object_id=str, count=int)])
    upstream_repo = FieldProperty(dict(name=str,url=str))

    def __init__(self, **kw):
        if 'name' in kw and 'tool' in kw:
            if 'fs_path' not in kw:
                kw['fs_path'] = self.default_fs_path(c.project, kw['tool'])
            if 'url_path' not in kw:
                kw['url_path'] = self.default_url_path(c.project, kw['tool'])
        super(Repository, self).__init__(**kw)

    @property
    def activity_name(self):
        return 'repo %s' % self.name

    @classmethod
    def default_fs_path(cls, project, tool):
        repos_root = tg.config.get('scm.repos.root', '/')
        return os.path.join(repos_root, tool, project.url()[1:])

    @classmethod
    def default_url_path(cls, project, tool):
        return project.url()

    def __repr__(self): # pragma no cover
        return '<%s %s>' % (
            self.__class__.__name__,
            self.full_fs_path)

    # Proxy to _impl
    def init(self):
        return self._impl.init()
    def commit(self, rev):
        return self._impl.commit(rev)
    def all_commit_ids(self):
        return self._impl.all_commit_ids()
    def refresh_commit_info(self, oid, seen, lazy=True):
        return self._impl.refresh_commit_info(oid, seen, lazy)
    def open_blob(self, blob):
        return self._impl.open_blob(blob)
    def blob_size(self, blob):
        return self._impl.blob_size(blob)
    def shorthand_for_commit(self, oid):
        return self._impl.shorthand_for_commit(oid)
    def symbolics_for_commit(self, commit):
        return self._impl.symbolics_for_commit(commit)
    def url_for_commit(self, commit):
        return self._impl.url_for_commit(commit)
    def compute_tree_new(self, commit, path='/'):
        return self._impl.compute_tree_new(commit, path)

    def _log(self, rev, skip, max_count):
        ci = self.commit(rev)
        if ci is None: return []
        return ci.log(int(skip), int(max_count))

    def init_as_clone(self, source_path, source_name, source_url):
        self.upstream_repo.name = source_name
        self.upstream_repo.url = source_url
        session(self).flush(self)
        self._impl.clone_from(source_url)

    def log(self, branch='master', offset=0, limit=10):
        return list(self._log(rev=branch, skip=offset, max_count=limit))

    def count(self, branch='master'):
        try:
            ci = self.commit(branch)
            if ci is None: return 0
            return ci.count_revisions()
        except: # pragma no cover
            log.exception('Error getting repo count')
            return 0

    def latest(self, branch='master'):
        if self._impl is None: return None
        try:
            return self.commit(branch)
        except: # pragma no cover
            log.exception('Cannot get latest commit for a branch', branch)
            return None

    def url(self):
        return self.app_config.url()

    def shorthand_id(self):
        return self.name

    @property
    def email_address(self):
        domain = '.'.join(reversed(self.app.url[1:-1].split('/'))).replace('_', '-')
        return u'noreply@%s%s' % (domain, config.common_suffix)

    def index(self):
        result = Artifact.index(self)
        result.update(
            name_s=self.name,
            type_s=self.type_s,
            title_s='Repository %s %s' % (self.project.name, self.name))
        return result

    @property
    def full_fs_path(self):
        return os.path.join(self.fs_path, self.name)

    def suggested_clone_dest_path(self):
        return '%s-%s' % (c.project.shortname.replace('/', '-'), self.name)

    def clone_url(self, category, username=''):
        '''Return a URL string suitable for copy/paste that describes _this_ repo,
           e.g., for use in a clone/checkout command
        '''
        tpl = string.Template(tg.config.get('scm.host.%s.%s' % (category, self.tool)))
        return tpl.substitute(dict(username=username, path=self.url_path+self.name))

    def clone_command(self, category, username=''):
        '''Return a string suitable for copy/paste that would clone this repo locally
           category is one of 'ro' (read-only), 'rw' (read/write), or 'https' (read/write via https)
        '''
        if not username and c.user not in (None, User.anonymous()):
            username = c.user.username
        tpl = string.Template(tg.config.get('scm.clone.%s.%s' % (category, self.tool)) or
                              tg.config.get('scm.clone.%s' % self.tool))
        return tpl.substitute(dict(username=username,
                                   source_url=self.clone_url(category, username),
                                   dest_path=self.suggested_clone_dest_path()))

    def merge_requests_by_statuses(self, *statuses):
        return MergeRequest.query.find(dict(
                app_config_id=self.app.config._id,
                status={'$in':statuses})).sort(
            'request_number')

    @LazyProperty
    def _additional_viewable_extensions(self):
        ext_list = self.additional_viewable_extensions or ''
        ext_list = [ext.strip() for ext in ext_list.split(',') if ext]
        ext_list += [ '.ini', '.gitignore', '.svnignore', 'README' ]
        return ext_list

    def guess_type(self, name):
        '''Guess the mime type and encoding of a given filename'''
        content_type, encoding = mimetypes.guess_type(name)
        if content_type is None or not content_type.startswith('text/'):
            fn, ext = os.path.splitext(name)
            ext = ext or fn
            if ext in self._additional_viewable_extensions:
                content_type, encoding = 'text/plain', None
            if content_type is None:
                content_type, encoding = 'application/octet-stream', None
        return content_type, encoding

    def refresh(self, all_commits=False, notify=True):
        '''Find any new commits in the repository and update'''
        try:
            log.info('... %r analyzing', self)
            self.status = 'analyzing'
            session(self).flush(self)
            self._impl.refresh_heads()
            if asbool(tg.config.get('scm.new_refresh')):
                refresh_repo(self, all_commits, notify)
            for head in self.heads + self.branches + self.repo_tags:
                ci = self.commit(head.object_id)
                if ci is not None:
                    head.count = ci.count_revisions()
        finally:
            log.info('... %s ready', self)
            self.status = 'ready'
            session(self).flush(self)

    def push_upstream_context(self):
        project, rest=h.find_project(self.upstream_repo.name)
        with h.push_context(project._id):
            app = project.app_instance(rest[0])
        return h.push_context(project._id, app_config_id=app.config._id)

    def pending_upstream_merges(self):
        q = {
            'downstream.project_id':self.project_id,
            'downstream.mount_point':self.app.config.options.mount_point,
            'status':'open'}
        with self.push_upstream_context():
            return MergeRequest.query.find(q).count()

    def get_last_commit(self, obj):
        from .repo import LastCommitDoc
        lc = LastCommitDoc.m.get(
            repo_id=self._id, object_id=obj._id)
        if lc is None:
            return dict(
                author=None,
                author_email=None,
                author_url=None,
                date=None,
                id=None,
                shortlink=None,
                summary=None)
        return lc.commit_info

    @property
    def forks(self):
        return self.query.find({'upstream_repo.name': self.url()}).all()

class MergeRequest(VersionedArtifact, ActivityObject):
    statuses=['open', 'merged', 'rejected']
    class __mongometa__:
        name='merge-request'
        indexes=['commit_id']
        unique_indexes=[('app_config_id', 'request_number')]
    type_s='MergeRequest'

    request_number=FieldProperty(int)
    status=FieldProperty(str, if_missing='open')
    downstream=FieldProperty(dict(
            project_id=S.ObjectId,
            mount_point=str,
            commit_id=str))
    target_branch=FieldProperty(str)
    creator_id=FieldProperty(S.ObjectId, if_missing=lambda:c.user._id)
    created=FieldProperty(datetime, if_missing=datetime.utcnow)
    summary=FieldProperty(str)
    description=FieldProperty(str)

    @property
    def activity_name(self):
        return 'merge request #%s' % self.request_number

    @LazyProperty
    def creator(self):
        from allura import model as M
        return M.User.query.get(_id=self.creator_id)

    @LazyProperty
    def creator_name(self):
        return self.creator.get_pref('display_name') or self.creator.username

    @LazyProperty
    def creator_url(self):
        return self.creator.url()

    @LazyProperty
    def downstream_url(self):
        with self.push_downstream_context():
            return c.app.url

    @LazyProperty
    def downstream_repo_url(self):
        with self.push_downstream_context():
            return c.app.repo.clone_url(
                category='ro',
                username=c.user.username)

    def push_downstream_context(self):
        return h.push_context(self.downstream.project_id, self.downstream.mount_point)

    @LazyProperty
    def commits(self):
        return self._commits()

    def _commits(self):
        from .repo import Commit
        result = []
        next = [ self.downstream.commit_id ]
        while next:
            oid = next.pop(0)
            ci = Commit.query.get(_id=oid)
            if self.app.repo._id in ci.repo_ids:
                continue
            result.append(ci)
            next += ci.parent_ids
        with self.push_downstream_context():
            for ci in result: ci.set_context(c.app.repo)
        return result

    @classmethod
    def upsert(cls, **kw):
        num = cls.query.find(dict(
                app_config_id=c.app.config._id)).count()+1
        while True:
            try:
                r = cls(request_number=num, **kw)
                session(r).flush(r)
                return r
            except pymongo.errors.DuplicateKeyError: # pragma no cover
                session(r).expunge(r)
                num += 1

    def url(self):
        return self.app.url + 'merge-requests/%s/' % self.request_number

    def index(self):
        result = Artifact.index(self)
        result.update(
            name_s='Merge Request #%d' % self.request_number,
            type_s=self.type_s,
            title_s='Merge Request #%d of %s:%s' % (
                self.request_number, self.project.name, self.app.repo.name))
        return result

class LastCommitFor(MappedClass):
    class __mongometa__:
        session = project_orm_session
        name='last_commit_for'
        unique_indexes = [ ('repo_id', 'object_id') ]

    _id = FieldProperty(S.ObjectId)
    repo_id = FieldProperty(S.ObjectId)
    object_id = FieldProperty(str)
    last_commit = FieldProperty(dict(
        date=datetime,
        author=str,
        author_email=str,
        author_url=str,
        id=str,
        href=str,
        shortlink=str,
        summary=str))

    @classmethod
    def upsert(cls, repo_id, object_id):
        isnew = False
        r = cls.query.get(repo_id=repo_id, object_id=object_id)
        if r is not None: return r, isnew
        try:
            r = cls(repo_id=repo_id, object_id=object_id)
            session(r).flush(r)
            isnew = True
        except pymongo.errors.DuplicateKeyError: # pragma no cover
            session(r).expunge(r)
            r = cls.query.get(repo_id=repo_id, object_id=object_id)
        return r, isnew

class RepoObject(MappedClass):
    class __mongometa__:
        session = repository_orm_session
        name='repo_object'
        polymorphic_on = 'type'
        polymorphic_identity=None
        indexes = [
            ('parent_ids',),
            ('repo_id','type'),
            ('type', 'object_ids.object_id'),
            ('type', 'tree_id'),
            ]
        unique_indexes = [ 'object_id' ]

    # ID Fields
    _id = FieldProperty(S.ObjectId)
    type = FieldProperty(str)
    repo_id = FieldProperty(S.Deprecated)
    object_id = FieldProperty(str)
    last_commit=FieldProperty(S.Deprecated)

    @classmethod
    def upsert(cls, object_id):
        isnew = False
        r = cls.query.get(object_id=object_id)
        if r is not None:
            return r, isnew
        try:
            r = cls(
                type=cls.__mongometa__.polymorphic_identity,
                object_id=object_id)
            session(r).flush(r)
            isnew = True
        except pymongo.errors.DuplicateKeyError: # pragma no cover
            session(r).expunge(r)
            r = cls.query.get(object_id=object_id)
        return r, isnew

    def set_last_commit(self, ci, repo=None):
        '''Update the last_commit_for object based on the passed in commit &
        repo'''
        if repo is None: repo = c.app.repo
        lc, isnew = LastCommitFor.upsert(repo_id=repo._id, object_id=self.object_id)
        if not ci.authored.date:
            repo._impl.refresh_commit(ci)
        if isnew:
            lc.last_commit.author = ci.authored.name
            lc.last_commit.author_email = ci.authored.email
            lc.last_commit.author_url = ci.author_url
            lc.last_commit.date = ci.authored.date
            lc.last_commit.id = ci.object_id
            lc.last_commit.href = ci.url()
            lc.last_commit.shortlink = ci.shorthand_id()
            lc.last_commit.summary = ci.summary
            assert lc.last_commit.date
        return lc, isnew

    def get_last_commit(self, repo=None):
        if repo is None: repo = c.app.repo
        return repo.get_last_commit(self)

    def __repr__(self):
        return '<%s %s>' % (
            self.__class__.__name__, self.object_id)

    def index_id(self):
        app_config = self.repo.app_config
        return '%s %s in %s %s' % (
            self.type, self.object_id,
            app_config.project.name,
            app_config.options.mount_label)

    def set_context(self, context): # pragma no cover
        '''Set ephemeral (unsaved) attributes based on a context object'''
        raise NotImplementedError, 'set_context'

    def primary(self): return self

class LogCache(RepoObject):
    '''Class to store nothing but lists of commit IDs in topo sort order'''
    class __mongometa__:
        polymorphic_identity='log_cache'
    type_s = 'LogCache'

    type = FieldProperty(str, if_missing='log_cache')
    object_ids = FieldProperty([str])
    candidates = FieldProperty([str])

    @classmethod
    def get(cls, repo, object_id):
        lc, new = cls.upsert('$' + object_id)
        if not lc.object_ids:
            lc.object_ids, lc.candidates = repo._impl.log(object_id, 0, 50)
        return lc

class Commit(RepoObject):
    class __mongometa__:
        polymorphic_identity='commit'
    type_s = 'Commit'

    # File data
    type = FieldProperty(str, if_missing='commit')
    tree_id = FieldProperty(str)
    diffs = FieldProperty(dict(
            added=[str],
            removed=[str],
            changed=[str],
            copied=[dict(old=str, new=str)]))
    # Commit metadata
    committed = FieldProperty(
        dict(name=str,
             email=str,
             date=datetime))
    authored = FieldProperty(
        dict(name=str,
             email=str,
             date=datetime))
    message = FieldProperty(str)
    parent_ids = FieldProperty([str])
    extra = FieldProperty([dict(name=str, value=str)])
     # All repos that potentially reference this commit
    repositories=FieldProperty([S.ObjectId])

    # Ephemeral attrs
    repo=None

    def set_context(self, repo):
        self.repo = repo

    @property
    def diffs_computed(self):
        if self.diffs.added: return True
        if self.diffs.removed: return True
        if self.diffs.changed: return True
        if self.diffs.copied: return True

    @LazyProperty
    def author_url(self):
        u = User.by_email_address(self.authored.email)
        if u: return u.url()

    @LazyProperty
    def committer_url(self):
        u = User.by_email_address(self.committed.email)
        if u: return u.url()

    @LazyProperty
    def tree(self):
        if self.tree_id is None:
            self.tree_id = self.repo.compute_tree(self)
        if self.tree_id is None:
            return None
        t = Tree.query.get(object_id=self.tree_id)
        if t is None:
            self.tree_id = self.repo.compute_tree(self)
            t = Tree.query.get(object_id=self.tree_id)
        if t is not None: t.set_context(self)
        return t

    @LazyProperty
    def summary(self):
        message = h.really_unicode(self.message)
        first_line = message.split('\n')[0]
        return h.text.truncate(first_line, 50)

    def get_path(self, path):
        '''Return the blob on the given path'''
        if path.startswith('/'): path = path[1:]
        path_parts = path.split('/')
        return self.tree.get_blob(path_parts[-1], path_parts[:-1])

    def shorthand_id(self):
        return self.repo.shorthand_for_commit(self.object_id)

    @LazyProperty
    def symbolic_ids(self):
        return self.repo.symbolics_for_commit(self)

    def url(self):
        return self.repo.url_for_commit(self)

    def log(self, skip, count):
        oids = list(self.log_iter(skip, count))
        commits = self.query.find(dict(
                type='commit',
                object_id={'$in':oids}))
        commits_by_oid = {}
        for ci in commits:
            ci.set_context(self.repo)
            commits_by_oid[ci.object_id] = ci
        return [ commits_by_oid[oid] for oid in oids ]

    def log_iter(self, skip, count):
        seen_oids = set()
        candidates = [ self.object_id ]
        while candidates and count:
            candidate = candidates.pop()
            if candidate in seen_oids: continue
            lc = LogCache.get(self.repo, candidate)
            oids = lc.object_ids
            candidates += lc.candidates
            for oid in oids:
                if oid in seen_oids: continue
                seen_oids.add(oid)
                if count == 0:
                    break
                elif skip == 0:
                    yield oid
                    count -= 1
                else:
                    skip -= 1

    def count_revisions(self):
        seen_oids = set()
        candidates = [ self.object_id ]
        while candidates:
            candidate = candidates.pop()
            if candidate in seen_oids: continue
            lc = LogCache.get(self.repo, candidate)
            seen_oids.update(lc.object_ids)
            candidates += lc.candidates
        return len(seen_oids)

    def compute_diffs(self):
        self.diffs.added = []
        self.diffs.removed = []
        self.diffs.changed = []
        self.diffs.copied = []
        if self.parent_ids:
            parent = self.repo.commit(self.parent_ids[0])
            for diff in Tree.diff(parent.tree, self.tree):
                if diff.is_new:
                    self.diffs.added.append(diff.b_path)
                    obj = RepoObject.query.get(object_id=diff.b_object_id)
                    obj.set_last_commit(self, self.repo)
                elif diff.is_delete:
                    self.diffs.removed.append(diff.a_path)
                else:
                    self.diffs.changed.append(diff.a_path)
                    obj = RepoObject.query.get(object_id=diff.b_object_id)
                    obj.set_last_commit(self, self.repo)
        else:
            # Parent-less, so the whole tree is additions
            tree = self.tree
            for x in tree.object_ids:
                self.diffs.added.append('/'+x.name)
                obj = RepoObject.query.get(object_id=x.object_id)
                obj.set_last_commit(self, self.repo)

    def context(self):
        return self.repo.commit_context(self)

class Tree(RepoObject):
    '''
    A representation of files & directories.  E.g. what is present at a single commit

    :var object_ids: dict(object_id: name)  Set by refresh_tree in the scm implementation
    '''
    class __mongometa__:
        polymorphic_identity='tree'
    type_s = 'Tree'

    type = FieldProperty(str, if_missing='tree')
    object_ids = FieldProperty([dict(object_id=str,name=str)])

    # Ephemeral attrs
    repo=None
    commit=None
    parent=None
    name=None

    def compute_hash(self):
        '''Compute a hash based on the contents of the tree.  Note that this
        hash does not necessarily correspond to any actual DVCS hash.
        '''
        lines = []
        for x in self.object_ids:
            obj = RepoObject.query.get(x.object_id)
            lines.append(obj.type[0] + x.object_id + x.name)
        sha_obj = sha1()
        for line in sorted(lines):
            sha_obj.update(line)
        return sha_obj.hexdigest()

    def set_last_commit(self, ci, repo=None):
        lc, isnew = super(Tree, self).set_last_commit(ci, repo)
        if isnew:
            for x in self.object_ids:
                obj = RepoObject.query.get(object_id=x.object_id)
                obj.set_last_commit(ci, repo)
        return lc, isnew

    @LazyProperty
    def object_id_index(self):
        return dict((x.name, x.object_id) for x in self.object_ids)

    @LazyProperty
    def object_name_index(self):
        result = defaultdict(list)
        for x in self.object_ids:
            result[x.object_id].append(x.name)
        return result

    def get(self, name, default=None):
        try:
            return self[name]
        except KeyError:
            return default

    def __getitem__(self, name):
        oid = self.object_id_index[name]
        obj = RepoObject.query.get(object_id=oid)
        if obj is None:
            oid = self.repo.compute_tree(self.commit, self.path() + name + '/')
            obj = RepoObject.query.get(object_id=oid)
        if obj is None: raise KeyError, name
        obj.set_context(self, name)
        return obj

    @classmethod
    def diff(cls, a, b):
        '''Recursive diff of two tree objects, yielding DiffObjects'''
        if not isinstance(a, Tree) or not isinstance(b, Tree):
            yield DiffObject(a, b)
        else:
            for a_x in a.object_ids:
                b_oid = b.object_id_index.get(a_x.name)
                if a_x.object_id == b_oid: continue
                a_obj = a.get(a_x.name)
                b_obj = b.get(a_x.name)
                if b_obj is None:
                    yield DiffObject(a_obj, None)
                else:
                    for x in cls.diff(a_obj, b_obj): yield x
            for b_x in b.object_ids:
                if b_x.name in a.object_id_index: continue
                b_obj = b.get(b_x.name)
                yield DiffObject(None, b_obj)

    def set_context(self, commit_or_tree, name=None):
        assert commit_or_tree is not self
        self.repo = commit_or_tree.repo
        if name:
            self.commit = commit_or_tree.commit
            self.parent = commit_or_tree
            self.name = name
        else:
            self.commit = commit_or_tree

    def readme(self):
        'returns (filename, unicode text) if a readme file is found'
        for x in self.object_ids:
            if README_RE.match(x.name):
                obj = self[x.name]
                if isinstance(obj, Blob):
                    return (x.name, h.really_unicode(obj.text))
        return (None, '')

    def ls(self):
        results = []
        for x in self.object_ids:
            obj = self[x.name]
            ci = obj.get_last_commit()
            d = dict(last_commit=ci, name=x.name)
            if isinstance(obj, Tree):
                results.append(dict(d, kind='DIR', href=x.name + '/'))
            else:
                results.append(dict(d, kind='FILE', href=x.name))
        results.sort(key=lambda d:(d['kind'], d['name']))
        return results

    def index_id(self):
        return repr(self)

    def path(self):
        if self.parent:
            assert self.parent is not self
            return self.parent.path() + self.name + '/'
        else:
            return '/'

    def url(self):
        return self.commit.url() + 'tree' + self.path()

    def is_blob(self, name):
        obj = RepoObject.query.get(
            object_id=self.object_id_index[name])
        return isinstance(obj, Blob)

    def get_tree(self, name):
        t = self.get(name)
        if isinstance(t, Tree): return t
        return None

    def get_blob(self, name, path_parts=None):
        if not path_parts:
            t = self
        else:
            t = self.get_object(*path_parts)
        if t is None: return None
        b = t.get(name)
        if isinstance(b, Blob): return b
        return None

    def get_object(self, *path_parts):
        cur = self
        for part in path_parts:
            if not isinstance(cur, Tree): return None
            cur = cur.get(part)
        return cur

class Blob(RepoObject):
    class __mongometa__:
        polymorphic_identity='blob'
    type_s = 'Blob'

    type = FieldProperty(str, if_missing='blob')

    # Ephemeral attrs
    repo=None
    commit=None
    tree=None
    name=None

    def set_context(self, tree, name):
        self.repo = tree.repo
        self.commit = tree.commit
        self.tree = tree
        self.name = name
        fn, ext = os.path.splitext(self.name)
        self.extension = ext or fn

    @LazyProperty
    def _content_type_encoding(self):
        return self.repo.guess_type(self.name)

    @LazyProperty
    def content_type(self):
        return self._content_type_encoding[0]

    @LazyProperty
    def content_encoding(self):
        return self._content_type_encoding[1]

    @LazyProperty
    def next_commit(self):
        try:
            path = self.path()
            cur = self.commit
            next = cur.context()['next']
            while next:
                cur = next[0]
                next = cur.context()['next']
                other_blob = cur.get_path(path)
                if other_blob is None or other_blob.object_id != self.object_id:
                    return cur
        except:
            log.exception('Lookup prev_commit')
            return None

    @LazyProperty
    def prev_commit(self):
        lc = self.get_last_commit()
        if lc['id']:
            last_commit = self.repo.commit(lc.id)
            if last_commit.parent_ids:
                return self.repo.commit(last_commit.parent_ids[0])
        return None

    def url(self):
        return self.tree.url() + h.really_unicode(self.name)

    def path(self):
        return self.tree.path() + h.really_unicode(self.name)

    @property
    def has_pypeline_view(self):
        if README_RE.match(self.name) or self.extension in ['.md', '.rst']:
            return True
        return False

    @property
    def has_html_view(self):
        if self.content_type.startswith('text/') or self.extension in VIEWABLE_EXTENSIONS or \
            self.extension in self.repo._additional_viewable_extensions:
            return True
        return False

    @property
    def has_image_view(self):
        return self.content_type.startswith('image/')

    def context(self):
        path = self.path()[1:]
        prev = self.prev_commit
        next = self.next_commit
        if prev is not None: prev = prev.get_path(path)
        if next is not None: next = next.get_path(path)
        return dict(
            prev=prev,
            next=next)

    def compute_hash(self):
        '''Compute a hash based on the contents of the blob.  Note that this
        hash does not necessarily correspond to any actual DVCS hash.
        '''
        fp = self.open()
        sha_obj = sha1()
        while True:
            buffer = fp.read(4096)
            if not buffer: break
            sha_obj.update(buffer)
        return sha_obj.hexdigest()

    def open(self):
        return self.repo.open_blob(self)

    def __iter__(self):
        return iter(self.open())

    @LazyProperty
    def size(self):
        return self.repo.blob_size(self)

    @LazyProperty
    def text(self):
        return self.open().read()

    @classmethod
    def diff(cls, v0, v1):
        differ = SequenceMatcher(v0, v1)
        return differ.get_opcodes()

class DiffObject(object):
    a_path = b_path = None
    a_object_id = b_object_id = None
    is_new = False
    is_delete = False

    def __init__(self, a, b):
        if a:
            self.a_path = a.path()
            self.a_object_id = a.object_id
        else:
            self.is_new = True
        if b:
            self.b_path = b.path()
            self.b_object_id = b.object_id
        else:
            self.is_delete = True

    def __repr__(self):
        if self.is_new:
            return '<new %s>' % self.b_path
        elif self.is_delete:
            return '<remove %s>' % self.a_path
        else:
            return '<change %s>' % (self.a_path)


class GitLikeTree(object):
    '''
    A tree node similar to that which is used in git

    :var dict blobs: files at this level of the tree.  name => oid
    :var dict trees: subtrees (child dirs).  name => GitLikeTree
    '''

    def __init__(self):
        self.blobs = {}  # blobs[name] = oid
        self.trees = defaultdict(GitLikeTree) #trees[name] = GitLikeTree()
        self._hex = None

    def get_tree(self, path):
        if path.startswith('/'): path = path[1:]
        if not path: return self
        cur = self
        for part in path.split('/'):
            cur = cur.trees[part]
        return cur

    def get_blob(self, path):
        if path.startswith('/'): path = path[1:]
        path_parts = path.split('/')
        dirpath, last = path_parts[:-1], path_parts[-1]
        cur = self
        for part in dirpath:
            cur = cur.trees[part]
        return cur.blobs[last]

    def set_blob(self, path, oid):
        if path.startswith('/'): path = path[1:]
        path_parts = path.split('/')
        dirpath, filename = path_parts[:-1], path_parts[-1]
        cur = self
        for part in dirpath:
            cur = cur.trees[part]
        cur.blobs[filename] = oid

    def hex(self):
        '''Compute a recursive sha1 hash on the tree'''
        # dependent on __repr__ below
        if self._hex is None:
            sha_obj = sha1('tree\n' + repr(self))
            self._hex = sha_obj.hexdigest()
        return self._hex

    def __repr__(self):
        # this can't change, is used in hex() above
        lines = ['t %s %s' % (t.hex(), name)
                  for name, t in self.trees.iteritems() ]
        lines += ['b %s %s' % (oid, name)
                  for name, oid in self.blobs.iteritems() ]
        return h.really_unicode('\n'.join(sorted(lines))).encode('utf-8')

    def __unicode__(self):
        return self.pretty_tree(recurse=False)

    def pretty_tree(self, indent=0, recurse=True, show_id=True):
        '''For debugging, show a nice tree representation'''
        lines = [' '*indent + 't %s %s' %
                 (name, '\n'+t.unicode_full_tree(indent+2, show_id=show_id) if recurse else t.hex())
                  for name, t in sorted(self.trees.iteritems()) ]
        lines += [' '*indent + 'b %s %s' % (name, oid if show_id else '')
                  for name, oid in sorted(self.blobs.iteritems()) ]
        output = h.really_unicode('\n'.join(lines)).encode('utf-8')
        return output

def topological_sort(graph):
    '''Return the topological sort of a graph.

    The graph is a dict with each entry representing
    a node (the key is the node ID) and its parent(s) (a
    set of node IDs). Result is an iterator over the topo-sorted
    node IDs.

    The algorithm is based on one seen in
    http://en.wikipedia.org/wiki/Topological_sorting#CITEREFKahn1962
    '''
    # Index children, identify roots
    children = defaultdict(list)
    roots = []
    for nid, parents in graph.items():
        if not parents:
            graph.pop(nid)
            roots.append(nid)
        for p_nid in parents: children[p_nid].append(nid)
    # Topo sort
    while roots:
        n = roots.pop()
        yield n
        for child in children[n]:
            graph[child].remove(n)
            if not graph[child]:
                graph.pop(child)
                roots.append(child)
    assert not graph, 'Cycle detected'

Mapper.compile_all()
