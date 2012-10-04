import re
import logging
from itertools import groupby
from cPickle import dumps, loads
from collections import defaultdict
from urllib import unquote

import bson
import pymongo
from pylons import c

from ming import collection, Field, Index
from ming import schema as S
from ming.utils import LazyProperty
from ming.orm import session, mapper
from ming.orm import ForeignIdProperty, RelationProperty

from allura.lib import helpers as h

from .session import main_doc_session, main_orm_session
from .project import Project

log = logging.getLogger(__name__)

# Collection definitions
ArtifactReferenceDoc = collection(
    'artifact_reference', main_doc_session,
    Field('_id', str),
    Field('artifact_reference', dict(
            cls=S.Binary(),
            project_id=S.ObjectId(),
            app_config_id=S.ObjectId(),
            artifact_id=S.Anything(if_missing=None))),
    Field('references', [str], index=True),
    Index('artifact_reference.project_id'),  # used in ReindexCommand
)

ShortlinkDoc = collection(
    'shortlink', main_doc_session,
    Field('_id', S.ObjectId()),
    Field('ref_id', str, index=True),  # index needed for from_artifact() and index_tasks.py:del_artifacts
    Field('project_id', S.ObjectId()),
    Field('app_config_id', S.ObjectId()),
    Field('link', str),
    Field('url', str),
    Index('project_id', 'link'), # used by from_links()  More helpful to have project_id first, for other queries
)

# Class definitions
class ArtifactReference(object):

    @classmethod
    def from_artifact(cls, artifact):
        '''Upsert logic to generate an ArtifactReference object from an artifact'''
        obj = cls.query.get(_id=artifact.index_id())
        if obj is not None: return obj
        try:
            obj = cls(
                _id=artifact.index_id(),
                artifact_reference=dict(
                    cls=bson.Binary(dumps(artifact.__class__)),
                    project_id=artifact.app_config.project_id,
                    app_config_id=artifact.app_config._id,
                    artifact_id=artifact._id))
            session(obj).flush(obj)
            return obj
        except pymongo.errors.DuplicateKeyError: # pragma no cover
            session(obj).expunge(obj)
            return cls.query.get(_id=artifact.index_id())

    @LazyProperty
    def artifact(self):
        '''Look up the artifact referenced'''
        aref = self.artifact_reference
        try:
            cls = loads(str(aref.cls))
            with h.push_context(aref.project_id):
                return cls.query.get(_id=aref.artifact_id)
        except:
            log.exception('Error loading artifact for %s: %r',
                          self._id, aref)

class Shortlink(object):
    '''Collection mapping shorthand_ids for artifacts to ArtifactReferences'''

    # Regexes used to find shortlinks
    _core_re = r'''(\[
            (?:(?P<project_id>.*?):)?      # optional project ID
            (?:(?P<app_id>.*?):)?      # optional tool ID
            (?P<artifact_id>.*)             # artifact ID
    \])'''
    re_link_1 = re.compile(r'\s' + _core_re, re.VERBOSE)
    re_link_2 = re.compile(r'^' +  _core_re, re.VERBOSE)

    def __repr__(self):
        with h.push_context(self.project_id):
            if self.app_config:
                return '[%s:%s:%s] -> %s' % (
                    self.project.shortname,
                    self.app_config.options.mount_point,
                    self.link,
                    self.ref_id)
            else:
                return '[%s:*:%s] -> %s' % (
                    self.project.shortname,
                    self.link,
                    self.ref_id)

    @classmethod
    def lookup(cls, link):
        return cls.from_links(link)[link]

    @classmethod
    def from_artifact(cls, a):
        result = cls.query.get(ref_id=a.index_id())
        if result is None:
            try:
                result = cls(
                    ref_id = a.index_id(),
                    project_id = a.app_config.project_id,
                    app_config_id = a.app_config._id)
                session(result).flush(result)
            except pymongo.errors.DuplicateKeyError: # pragma no cover
                session(result).expunge(result)
                result = cls.query.get(ref_id=a.index_id())
        result.link = a.shorthand_id()
        result.url = a.url()
        if result.link is None:
            result.delete()
            return None
        return result

    @classmethod
    def from_links(cls, *links):
        '''Convert a sequence of shortlinks to the matching Shortlink objects'''
        if len(links):
            # Parse all the links
            parsed_links = dict((link, cls._parse_link(link)) for link in links)
            links_by_artifact = defaultdict(list)
            project_ids = set()
            for link, d in parsed_links.iteritems():
                project_ids.add(d['project_id'])
                links_by_artifact[unquote(d['artifact'])].append(d)
            q = cls.query.find(dict(
                    link={'$in': links_by_artifact.keys()},
                    project_id={'$in': list(project_ids)}
                ), validate=False)
            result = {}
            matches_by_artifact = dict(
                (link, list(matches))
                for link, matches in groupby(q, key=lambda s:unquote(s.link)))
            result = {}
            for link, d in parsed_links.iteritems():
                matches = matches_by_artifact.get(unquote(d['artifact']), [])
                matches = (
                    m for m in matches
                    if m.project.shortname == d['project'] and
                       m.project.neighborhood_id == d['nbhd'] and
                       m.app_config is not None and
                       m.project.app_instance(m.app_config.options.mount_point))
                if d['app']:
                    matches = (
                        m for m in matches
                        if m.app_config.options.mount_point == d['app'])
                matches = list(matches)
                if matches:
                    result[link] = matches[0]
                else:
                    result[link] = None
                if len(matches) > 1:
                    log.warn('Ambiguous link to %s', link)
                    for m in matches:
                        log.warn('... %r', m)
            return result
        else:
            return {}

    @classmethod
    def _parse_link(cls, s):
        '''Parse a shortlink into its nbhd/project/app/artifact parts'''
        s = s.strip()
        if s.startswith('['):
            s = s[1:]
        if s.endswith(']'):
            s = s[:-1]
        parts = s.split(':')
        p_shortname = None
        p_id = None
        p_nbhd = None
        if hasattr(c, 'project'):
            p_shortname = getattr(c.project, 'shortname', None)
            p_id = getattr(c.project, '_id', None)
            p_nbhd = c.project.neighborhood_id
        if len(parts) == 3:
            p = Project.query.get(shortname=parts[0], neighborhood_id=p_nbhd)
            if p:
                p_id = p._id
            return dict(
                nbhd=p_nbhd,
                project=parts[0],
                project_id=p_id,
                app=parts[1],
                artifact=parts[2])
        elif len(parts) == 2:
            return dict(
                nbhd=p_nbhd,
                project=p_shortname,
                project_id=p_id,
                app=parts[0],
                artifact=parts[1])
        elif len(parts) == 1:
            return dict(
                nbhd=p_nbhd,
                project=p_shortname,
                project_id=p_id,
                app=None,
                artifact=parts[0])
        else:
            return None

# Mapper definitions
mapper(ArtifactReference, ArtifactReferenceDoc, main_orm_session)
mapper(Shortlink, ShortlinkDoc, main_orm_session, properties=dict(
    ref_id = ForeignIdProperty(ArtifactReference),
    project_id = ForeignIdProperty('Project'),
    app_config_id = ForeignIdProperty('AppConfig'),
    project = RelationProperty('Project'),
    app_config = RelationProperty('AppConfig'),
    ref = RelationProperty(ArtifactReference)))
