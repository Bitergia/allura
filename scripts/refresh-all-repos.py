import argparse
import logging
import re

import faulthandler
from pylons import c
from ming.orm import ThreadLocalORMSession

from allura import model as M
from allura.lib.utils import chunked_find, chunked_list

log = logging.getLogger(__name__)


def main(options):
    q_project = {}
    if options.nbhd:
        nbhd = M.Neighborhood.query.get(url_prefix=options.nbhd)
        if not nbhd:
            return "Invalid neighborhood url prefix."
        q_project['neighborhood_id'] = nbhd._id
    if options.project:
        q_project['shortname'] = options.project
    elif options.project_regex:
        q_project['shortname'] = {'$regex': options.project_regex}

    log.info('Refreshing repositories')
    if options.clean_all:
        log.info('Removing all repository objects')
        M.repo.CommitDoc.m.remove({})
        M.repo.TreeDoc.m.remove({})
        M.repo.TreesDoc.m.remove({})
        M.repo.DiffInfoDoc.m.remove({})
        M.repo.CommitRunDoc.m.remove({})

    for chunk in chunked_find(M.Project, q_project):
        for p in chunk:
            log.info("Refreshing repos for project '%s'." % p.shortname)
            if options.dry_run:
                continue
            c.project = p
            if options.mount_point:
                mount_points = [options.mount_point]
            else:
                mount_points = [ac.options.mount_point for ac in
                                M.AppConfig.query.find(dict(project_id=p._id))]
            for app in (p.app_instance(mp) for mp in mount_points):
                c.app = app
                if not hasattr(app, 'repo'):
                    continue
                if c.app.repo.tool.lower() not in options.repo_types:
                    log.info("Skipping %r: wrong type (%s)", c.app.repo,
                            c.app.repo.tool.lower())
                    continue
                try:
                    c.app.repo._impl._setup_hooks()
                except:
                    log.exception('Error setting up hooks for %r', c.app.repo)

                if options.clean:
                    ci_ids = list(c.app.repo.all_commit_ids())
                    log.info("Deleting mongo data for %i commits...", len(ci_ids))
                    tree_ids = [
                            tree_id for doc in
                            M.repo.TreesDoc.m.find({"_id": {"$in": ci_ids}},
                                                   {"tree_ids": 1})
                            for tree_id in doc.get("tree_ids", [])]

                    i = M.repo.CommitDoc.m.find({"_id": {"$in": ci_ids}}).count()
                    log.info("Deleting %i CommitDoc docs...", i)
                    M.repo.CommitDoc.m.remove({"_id": {"$in": ci_ids}})

                    # delete these in chunks, otherwise the query doc can
                    # exceed the max BSON size limit (16MB at the moment)
                    for tree_ids_chunk in chunked_list(tree_ids, 300000):
                        i = M.repo.TreeDoc.m.find({"_id": {"$in": tree_ids_chunk}}).count()
                        log.info("Deleting %i TreeDoc docs...", i)
                        M.repo.TreeDoc.m.remove({"_id": {"$in": tree_ids_chunk}})
                        i = M.repo.LastCommitDoc.m.find({"object_id": {"$in": tree_ids_chunk}}).count()
                        log.info("Deleting %i LastCommitDoc docs...", i)
                        M.repo.LastCommitDoc.m.remove({"object_id": {"$in": tree_ids_chunk}})
                    del tree_ids

                    # delete these after TreeDoc and LastCommitDoc so that if
                    # we crash, we don't lose the ability to delete those
                    i = M.repo.TreesDoc.m.find({"_id": {"$in": ci_ids}}).count()
                    log.info("Deleting %i TreesDoc docs...", i)
                    M.repo.TreesDoc.m.remove({"_id": {"$in": ci_ids}})

                    # delete LastCommitDocs for non-trees
                    repo_lastcommit_re = re.compile("^{}:".format(c.app.repo._id))
                    i = M.repo.LastCommitDoc.m.find(dict(_id=repo_lastcommit_re)).count()
                    log.info("Deleting %i remaining LastCommitDoc docs, by repo id...", i)
                    M.repo.LastCommitDoc.m.remove(dict(_id=repo_lastcommit_re))

                    i = M.repo.DiffInfoDoc.m.find({"_id": {"$in": ci_ids}}).count()
                    log.info("Deleting %i DiffInfoDoc docs...", i)
                    M.repo.DiffInfoDoc.m.remove({"_id": {"$in": ci_ids}})

                    i = M.repo.CommitRunDoc.m.find({"commit_ids": {"$in": ci_ids}}).count()
                    log.info("Deleting %i CommitRunDoc docs...", i)
                    M.repo.CommitRunDoc.m.remove({"commit_ids": {"$in": ci_ids}})
                    del ci_ids

                try:
                    if options.all:
                        log.info('Refreshing ALL commits in %r', c.app.repo)
                    else:
                        log.info('Refreshing NEW commits in %r', c.app.repo)
                    if options.profile:
                        import cProfile
                        cProfile.runctx('c.app.repo.refresh(options.all, notify=options.notify)',
                                globals(), locals(), 'refresh.profile')
                    else:
                        c.app.repo.refresh(options.all, notify=options.notify)
                except:
                    log.exception('Error refreshing %r', c.app.repo)
        ThreadLocalORMSession.flush_all()
        ThreadLocalORMSession.close_all()


def repo_type_list(s):
    repo_types = []
    for repo_type in s.split(','):
        repo_type = repo_type.strip()
        if repo_type not in ['svn', 'git', 'hg']:
            raise argparse.ArgumentTypeError(
                    '{} is not a valid repo type.'.format(repo_type))
        repo_types.append(repo_type)
    return repo_types


def parse_options():
    parser = argparse.ArgumentParser(description='Scan repos on filesytem and '
            'update repo metadata in MongoDB. Run for all repos (no args), '
            'or restrict by neighborhood, project, or code tool mount point.')
    parser.add_argument('--nbhd', action='store', default='', dest='nbhd',
            help='Restrict update to a particular neighborhood, e.g. /p/.')
    parser.add_argument('--project', action='store', default='', dest='project',
            help='Restrict update to a particular project. To specify a '
            'subproject, use a slash: project/subproject.')
    parser.add_argument('--project-regex', action='store', default='',
            dest='project_regex',
            help='Restrict update to projects for which the shortname matches '
            'the provided regex.')
    parser.add_argument('--repo-types', action='store', type=repo_type_list,
            default=['svn', 'git', 'hg'], dest='repo_types',
            help='Only refresh repos of the given type(s). Defaults to: '
            'svn,git,hg. Example: --repo-types=git,hg')
    parser.add_argument('--mount_point', default='', dest='mount_point',
            help='Restrict update to repos at the given tool mount point. ')
    parser.add_argument('--clean', action='store_true', dest='clean',
            default=False, help='Remove repo-related mongo docs (for '
            'project(s) being refreshed only) before doing the refresh.')
    parser.add_argument('--clean-all', action='store_true', dest='clean_all',
            default=False, help='Remove ALL repo-related mongo docs before '
            'refresh.')
    parser.add_argument('--all', action='store_true', dest='all', default=False,
            help='Refresh all commits (not just the ones that are new).')
    parser.add_argument('--notify', action='store_true', dest='notify',
            default=False, help='Send email notifications of new commits.')
    parser.add_argument('--dry-run', action='store_true', dest='dry_run',
            default=False, help='Log names of projects that would have their '
            'repos refreshed, but do not perform the actual refresh.')
    parser.add_argument('--profile', action='store_true', dest='profile',
            default=False, help='Enable the profiler (slow). Will log '
            'profiling output to ./refresh.profile')
    return parser.parse_args()

if __name__ == '__main__':
    import sys
    faulthandler.enable()
    sys.exit(main(parse_options()))
