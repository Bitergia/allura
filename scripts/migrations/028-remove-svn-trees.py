import logging

from ming.orm import ThreadLocalORMSession

from allura.lib import utils
from allura import model as M
from forgesvn import model as SM

log = logging.getLogger(__name__)

def kill_tree(repo, commit_id, path, tree):
    '''They were arboring terrorists, I swear.'''
    M.repo.Tree.query.remove(dict(_id=tree._id))
    for tree_rec in tree.tree_ids:
        tid = repo._tree_oid(commit_id, path + '/' + tree_rec.name)
        child_tree = M.repo.Tree.query.get(_id=tid)
        if child_tree:
            print '  Found {0}'.format((path + '/' + tree_rec.name).encode('utf8'))
            kill_tree(repo, commit_id, path + '/' + tree_rec.name, child_tree)
        else:
            print '  Missing {0}'.format((path + '/' + tree_rec.name).encode('utf8'))

def main():
    for chunk in utils.chunked_find(SM.Repository):
        for r in chunk:
            print 'Processing {0}'.format(r)
            all_commit_ids = r._impl.all_commit_ids()
            if all_commit_ids:
                for commit in M.repo.Commit.query.find({'_id':{'$in':all_commit_ids}}):
                    if commit.tree_id and M.repo.Tree.query.get(_id=commit.tree_id):
                        kill_tree(r._impl, commit._id, '', commit.tree)
                ThreadLocalORMSession.flush_all()
                ThreadLocalORMSession.close_all()

if __name__ == '__main__':
    main()
