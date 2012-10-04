import os
import re
import sys
import logging
from hashlib import sha1
from itertools import izip, chain
from datetime import datetime
from collections import defaultdict
from difflib import SequenceMatcher, unified_diff

from pylons import c
import pymongo.errors

from ming import Field, collection
from ming import schema as S
from ming.base import Object
from ming.utils import LazyProperty
from ming.orm import mapper, session

from allura.lib import utils
from allura.lib import helpers as h

from .auth import User
from .session import main_doc_session, project_doc_session
from .session import repository_orm_session

log = logging.getLogger(__name__)

# Some schema types
SUser = dict(name=str, email=str, date=datetime)
SObjType=S.OneOf('blob', 'tree', 'submodule')

# Used for when we're going to batch queries using $in
QSIZE = 100
README_RE = re.compile('^README(\.[^.]*)?$', re.IGNORECASE)
VIEWABLE_EXTENSIONS = ['.php','.py','.js','.java','.html','.htm','.yaml','.sh',
    '.rb','.phtml','.txt','.bat','.ps1','.xhtml','.css','.cfm','.jsp','.jspx',
    '.pl','.php4','.php3','.rhtml','.svg','.markdown','.json','.ini','.tcl','.vbs','.xsl']

DIFF_SIMILARITY_THRESHOLD = .5  # used for determining file renames

# Basic commit information
# One of these for each commit in the physical repo on disk. The _id is the
# hexsha of the commit (for Git and Hg).
CommitDoc = collection(
    'repo_ci', main_doc_session,
    Field('_id', str),
    Field('tree_id', str),
    Field('committed', SUser),
    Field('authored', SUser),
    Field('message', str),
    Field('parent_ids', [str], index=True),
    Field('child_ids', [str], index=True),
    Field('repo_ids', [ S.ObjectId() ], index=True))

# Basic tree information (also see TreesDoc)
TreeDoc = collection(
    'repo_tree', main_doc_session,
    Field('_id', str),
    Field('tree_ids', [dict(name=str, id=str)]),
    Field('blob_ids', [dict(name=str, id=str)]),
    Field('other_ids', [dict(name=str, id=str, type=SObjType)]))

# Information about the last commit to touch a tree/blob
# LastCommitDoc.object_id = TreeDoc._id
LastCommitDoc = collection(
    'repo_last_commit', project_doc_session,
    Field('_id', str),
    Field('object_id', str, index=True),
    Field('name', str),
    Field('commit_info', dict(
        id=str,
        date=datetime,
        author=str,
        author_email=str,
        author_url=str,
        shortlink=str,
        summary=str)))

# List of all trees contained within a commit
# TreesDoc._id = CommitDoc._id
# TreesDoc.tree_ids = [ TreeDoc._id, ... ]
TreesDoc = collection(
    'repo_trees', main_doc_session,
    Field('_id', str),
    Field('tree_ids', [str]))

# Information about which things were added/removed in  commit
# DiffInfoDoc._id = CommitDoc._id
DiffInfoDoc = collection(
    'repo_diffinfo', main_doc_session,
    Field('_id', str),
    Field(
        'differences',
        [ dict(name=str, lhs_id=str, rhs_id=str)]))

# List of commit runs (a run is a linear series of single-parent commits)
# CommitRunDoc.commit_ids = [ CommitDoc._id, ... ]
CommitRunDoc = collection(
    'repo_commitrun', main_doc_session,
    Field('_id', str),
    Field('parent_commit_ids', [str]),
    Field('commit_ids', [str], index=True),
    Field('commit_times', [datetime]))

class RepoObject(object):

    def __repr__(self): # pragma no cover
        return '<%s %s>' % (
            self.__class__.__name__, self._id)

    def primary(self):
        return self

    def index_id(self):
        '''Globally unique artifact identifier.  Used for
        SOLR ID, shortlinks, and maybe elsewhere
        '''
        id = '%s.%s#%s' % (
            self.__class__.__module__,
            self.__class__.__name__,
            self._id)
        return id.replace('.', '/')

    @classmethod
    def upsert(cls, id):
        isnew = False
        r = cls.query.get(_id=id)
        if r is not None: return r, isnew
        try:
            r = cls(_id=id)
            session(r).flush(r)
            isnew = True
        except pymongo.errors.DuplicateKeyError: # pragma no cover
            session(r).expunge(r)
            r = cls.query.get(_id=id)
        return r, isnew

class Commit(RepoObject):
    type_s = 'Commit'
    # Ephemeral attrs
    repo=None

    def set_context(self, repo):
        self.repo = repo

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
            self.tree_id = self.repo.compute_tree_new(self)
        if self.tree_id is None:
            return None
        t = Tree.query.get(_id=self.tree_id)
        if t is None:
            self.tree_id = self.repo.compute_tree_new(self)
            t = Tree.query.get(_id=self.tree_id)
        if t is not None: t.set_context(self)
        return t

    @LazyProperty
    def summary(self):
        message = h.really_unicode(self.message)
        first_line = message.split('\n')[0]
        return h.text.truncate(first_line, 50)

    def shorthand_id(self):
        if self.repo is None: self.repo = self.guess_repo()
        if self.repo is None: return repr(self)
        return self.repo.shorthand_for_commit(self._id)

    @LazyProperty
    def symbolic_ids(self):
        return self.repo.symbolics_for_commit(self)

    def url(self):
        if self.repo is None: self.repo = self.guess_repo()
        if self.repo is None: return '#'
        return self.repo.url_for_commit(self)

    def guess_repo(self):
        for ac in c.project.app_configs:
            try:
                app = c.project.app_instance(ac)
                if app.repo._id in self.repo_ids:
                    return app.repo
            except AttributeError:
                pass
        return None

    def link_text(self):
        '''The link text that will be used when a shortlink to this artifact
        is expanded into an <a></a> tag.

        By default this method returns shorthand_id(). Subclasses should
        override this method to provide more descriptive link text.
        '''
        return self.shorthand_id()

    def log_iter(self, skip, count):
        for oids in utils.chunked_iter(commitlog([self._id]), QSIZE):
            oids = list(oids)
            commits = dict(
                (ci._id, ci) for ci in self.query.find(dict(
                        _id={'$in': oids})))
            for oid in oids:
                if skip:
                    skip -= 1
                    continue
                if count:
                    count -= 1
                    ci = commits[oid]
                    ci.set_context(self.repo)
                    yield ci
                else:
                    break

    def log(self, skip, count):
        return list(self.log_iter(skip, count))

    def count_revisions(self):
        from .repo_refresh import CommitRunBuilder
        result = 0
        # If there's no CommitRunDoc for this commit, the call to
        # commitlog() below will raise a KeyError. Repair the CommitRuns for
        # this repo by rebuilding them entirely.
        if self.repo and not CommitRunDoc.m.find(dict(commit_ids=self._id)).count():
            log.info('CommitRun incomplete, rebuilding with all commits')
            rb = CommitRunBuilder(list(self.repo.all_commit_ids()))
            rb.run()
            rb.cleanup()
        for oid in commitlog([self._id]): result += 1
        return result

    def context(self):
        result = dict(prev=None, next=None)
        if self.parent_ids:
            result['prev'] = self.query.find(dict(_id={'$in': self.parent_ids })).all()
            for ci in result['prev']:
                ci.set_context(self.repo)
        if self.child_ids:
            result['next'] = self.query.find(dict(_id={'$in': self.child_ids })).all()
            for ci in result['next']:
                ci.set_context(self.repo)
        return result

    @LazyProperty
    def diffs(self):
        di = DiffInfoDoc.m.get(_id=self._id)
        if di is None:
            return Object(added=[], removed=[], changed=[], copied=[])
        added = []
        removed = []
        changed = []
        copied = []
        for change in di.differences:
            if change.rhs_id is None:
                removed.append(change.name)
            elif change.lhs_id is None:
                added.append(change.name)
            else:
                changed.append(change.name)
        copied = self._diffs_copied(added, removed)
        return Object(
            added=added, removed=removed,
            changed=changed, copied=copied)

    def _diffs_copied(self, added, removed):
        '''Return list with file renames diffs.

        Will change `added` and `removed` lists also.
        '''
        def _blobs_similarity(removed_blob, added):
            best = dict(ratio=0, name='', blob=None)
            for added_name in added:
                added_blob = self.tree.get_obj_by_path(added_name)
                if not isinstance(added_blob, Blob):
                    continue
                diff = SequenceMatcher(None, removed_blob.text,
                                       added_blob.text)
                ratio = diff.ratio()
                if ratio > best['ratio']:
                    best['ratio'] = ratio
                    best['name'] = added_name
                    best['blob'] = added_blob

                if ratio == 1:
                    break  # we'll won't find better similarity than 100% :)

            if best['ratio'] > DIFF_SIMILARITY_THRESHOLD:
                diff = ''
                if best['ratio'] < 1:
                    added_blob = best['blob']
                    rpath = ('a' + removed_blob.path()).encode('utf-8')
                    apath = ('b' + added_blob.path()).encode('utf-8')
                    diff = ''.join(unified_diff(list(removed_blob),
                                                list(added_blob),
                                                rpath, apath))
                return dict(new=best['name'],
                            ratio=best['ratio'], diff=diff)

        def _trees_similarity(removed_tree, added):
            for added_name in added:
                added_tree = self.tree.get_obj_by_path(added_name)
                if not isinstance(added_tree, Tree):
                    continue
                if removed_tree._id == added_tree._id:
                    return dict(new=added_name,
                                ratio=1, diff='')

        if not removed:
            return []
        copied = []
        prev_commit = self.log(1, 1)[0]
        for removed_name in removed[:]:
            removed_blob = prev_commit.tree.get_obj_by_path(removed_name)
            rename_info = None
            if isinstance(removed_blob, Blob):
                rename_info = _blobs_similarity(removed_blob, added)
            elif isinstance(removed_blob, Tree):
                rename_info = _trees_similarity(removed_blob, added)
            if rename_info is not None:
                rename_info['old'] = removed_name
                copied.append(rename_info)
                removed.remove(rename_info['old'])
                added.remove(rename_info['new'])
        return copied

    def get_path(self, path):
        if path[0] == '/': path = path[1:]
        parts = path.split('/')
        cur = self.tree
        for part in parts:
            cur = cur[part]
        return cur

class Tree(RepoObject):
    # Ephemeral attrs
    repo=None
    commit=None
    parent=None
    name=None

    def compute_hash(self):
        '''Compute a hash based on the contents of the tree.  Note that this
        hash does not necessarily correspond to any actual DVCS hash.
        '''
        lines = (
            [ 'tree' + x.name + x.id for x in self.tree_ids ]
            + [ 'blob' + x.name + x.id for x in self.blob_ids ]
            + [ x.type + x.name + x.id for x in self.other_ids ])
        sha_obj = sha1()
        for line in sorted(lines):
            sha_obj.update(line)
        return sha_obj.hexdigest()

    def __getitem__(self, name):
        obj = self.by_name[name]
        if obj['type'] == 'blob':
            return Blob(self, name, obj['id'])
        obj = self.query.get(_id=obj['id'])
        if obj is None:
            oid = self.repo.compute_tree_new(self.commit, self.path() + name + '/')
            obj = self.query.get(_id=oid)
        if obj is None: raise KeyError, name
        obj.set_context(self, name)
        return obj

    def get_obj_by_path(self, path):
        if hasattr(path, 'get'):
            path = path['new']
        if path.startswith('/'):
            path = path[1:]
        path = path.split('/')
        obj = self
        for p in path:
            try:
                obj = obj[p]
            except KeyError:
                return None
        return obj

    def get_blob_by_path(self, path):
        obj = self.get_obj_by_path(path)
        return obj if isinstance(obj, Blob) else None

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
        for x in self.blob_ids:
            if README_RE.match(x.name):
                name = x.name
                blob = self[name]
                return (x.name, h.really_unicode(blob.text))
        return None, None

    def ls(self):
        # Load last commit info
        id_re = re.compile("^{0}:{1}:".format(
            self.repo._id,
            re.escape(h.really_unicode(self.path()).encode('utf-8'))))
        lc_index = dict(
            (lc.name, lc.commit_info)
            for lc in LastCommitDoc.m.find(dict(_id=id_re)))

        # FIXME: Temporarily fall back to old, semi-broken lookup behavior until refresh is done
        oids = [ x.id for x in chain(self.tree_ids, self.blob_ids, self.other_ids) ]
        id_re = re.compile("^{0}:".format(self.repo._id))
        lc_index.update(dict(
            (lc.object_id, lc.commit_info)
            for lc in LastCommitDoc.m.find(dict(_id=id_re, object_id={'$in': oids}))))
        # /FIXME

        results = []
        def _get_last_commit(name, oid):
            lc = lc_index.get(name, lc_index.get(oid, None))
            if lc is None:
                lc = dict(
                    author=None,
                    author_email=None,
                    author_url=None,
                    date=None,
                    id=None,
                    href=None,
                    shortlink=None,
                    summary=None)
            if 'href' not in lc:
                lc['href'] = self.repo.url_for_commit(lc['id'])
            return lc
        for x in sorted(self.tree_ids, key=lambda x:x.name):
            results.append(dict(
                    kind='DIR',
                    name=x.name,
                    href=x.name + '/',
                    last_commit=_get_last_commit(x.name, x.id)))
        for x in sorted(self.blob_ids, key=lambda x:x.name):
            results.append(dict(
                    kind='FILE',
                    name=x.name,
                    href=x.name,
                    last_commit=_get_last_commit(x.name, x.id)))
        for x in sorted(self.other_ids, key=lambda x:x.name):
            results.append(dict(
                    kind=x.type,
                    name=x.name,
                    href=None,
                    last_commit=_get_last_commit(x.name, x.id)))
        return results

    def path(self):
        if self.parent:
            assert self.parent is not self
            return self.parent.path() + self.name + '/'
        else:
            return '/'

    def url(self):
        return self.commit.url() + 'tree' + self.path()

    @LazyProperty
    def by_name(self):
        d = Object((x.name, x) for x in self.other_ids)
        d.update(
            (x.name, Object(x, type='tree'))
            for x in self.tree_ids)
        d.update(
            (x.name, Object(x, type='blob'))
            for x in self.blob_ids)
        return d

    def is_blob(self, name):
        return self.by_name[name]['type'] == 'blob'

    def get_blob(self, name):
        x = self.by_name[name]
        return Blob(self, name, x.id)

class Blob(object):
    '''Lightweight object representing a file in the repo'''

    def __init__(self, tree, name, _id):
        self._id = _id
        self.tree = tree
        self.name = name
        self.repo = tree.repo
        self.commit = tree.commit
        fn, ext = os.path.splitext(self.name)
        self.extension = ext or fn

    def path(self):
        return self.tree.path() + h.really_unicode(self.name)

    def url(self):
        return self.tree.url() + h.really_unicode(self.name)

    @LazyProperty
    def prev_commit(self):
        lc = self.repo.get_last_commit(self)
        if lc['id']:
            last_commit = self.repo.commit(lc.id)
            if last_commit.parent_ids:
                return self.repo.commit(last_commit.parent_ids[0])
        return None

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
                if other_blob is None or other_blob._id != self._id:
                    return cur
        except:
            log.exception('Lookup prev_commit')
            return None

    @LazyProperty
    def _content_type_encoding(self):
        return self.repo.guess_type(self.name)

    @LazyProperty
    def content_type(self):
        return self._content_type_encoding[0]

    @LazyProperty
    def content_encoding(self):
        return self._content_type_encoding[1]

    @property
    def has_pypeline_view(self):
        if README_RE.match(self.name) or self.extension in ['.md', '.rst']:
            return True
        return False

    @property
    def has_html_view(self):
        if (self.content_type.startswith('text/') or
            self.extension in VIEWABLE_EXTENSIONS or
            self.extension in self.repo._additional_viewable_extensions or
            utils.is_text_file(self.text)):
            return True
        return False

    @property
    def has_image_view(self):
        return self.content_type.startswith('image/')

    def context(self):
        path = self.path()
        prev = self.prev_commit
        next = self.next_commit
        if prev is not None: prev = prev.get_path(path)
        if next is not None: next = next.get_path(path)
        return dict(
            prev=prev,
            next=next)

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

mapper(Commit, CommitDoc, repository_orm_session)
mapper(Tree, TreeDoc, repository_orm_session)

def commitlog(commit_ids, skip=0, limit=sys.maxint):
    seen = set()
    def _visit(commit_id):
        if commit_id in seen: return
        run = CommitRunDoc.m.get(commit_ids=commit_id)
        if run is None: return
        index = False
        for pos, (oid, time) in enumerate(izip(run.commit_ids, run.commit_times)):
            if oid == commit_id: index = True
            elif not index: continue
            seen.add(oid)
            ci_times[oid] = time
            if pos+1 < len(run.commit_ids):
                ci_parents[oid] = [ run.commit_ids[pos+1] ]
            else:
                ci_parents[oid] = run.parent_commit_ids
        for oid in run.parent_commit_ids:
            _visit(oid)

    def _gen_ids(commit_ids, skip, limit):
        # Traverse the graph in topo order, yielding commit IDs
        commits = set(commit_ids)
        new_parent = None
        while commits and limit:
            # next commit is latest commit that's valid to log
            if new_parent in commits:
                ci = new_parent
            else:
                ci = max(commits, key=lambda ci:ci_times[ci])
            commits.remove(ci)
            if skip:
                skip -= 1
                continue
            else:
                limit -= 1
            yield ci
            # remove this commit from its parents children and add any childless
            # parents to the 'ready set'
            new_parent = None
            for oid in ci_parents.get(ci, []):
                children = ci_children[oid]
                children.discard(ci)
                if not children:
                    commits.add(oid)
                    new_parent = oid

    # Load all the runs to build a commit graph
    ci_times = {}
    ci_parents = {}
    ci_children = defaultdict(set)
    log.info('Build commit graph')
    for cid in commit_ids:
        _visit(cid)
    for oid, parents in ci_parents.iteritems():
        for ci_parent in parents:
            ci_children[ci_parent].add(oid)

    return _gen_ids(commit_ids, skip, limit)
