# -*- coding: utf-8 -*-
"""Main Controller"""
import logging, string, os
from datetime import datetime
from collections import defaultdict

import pkg_resources
from tg import expose, flash, redirect, session, config, response, request, config
from tg.decorators import with_trailing_slash, without_trailing_slash
from tg.flash import TGFlash
from pylons import c, g, cache

import ew
import ming

import allura
from allura.app import SitemapEntry
from allura.lib.base import WsgiDispatchController
from allura.lib import helpers as h
from allura.lib import plugin
from allura.controllers.error import ErrorController
from allura import model as M
from allura.lib.widgets import project_list as plw
from .auth import AuthController
from .search import SearchController, ProjectBrowseController
from .static import NewForgeController
from .site_admin import SiteAdminController
from .project import NeighborhoodController, HostNeighborhoodController
from .rest import RestController

__all__ = ['RootController']

log = logging.getLogger(__name__)

TGFlash.static_template = '''$('#messages').notify('%(message)s', {status: '%(status)s'});'''

class W:
    project_summary = plw.ProjectSummary()

class RootController(WsgiDispatchController):
    """
    The root controller for the allura application.

    All the other controllers and WSGI applications should be mounted on this
    controller. For example::

        panel = ControlPanelController()
        another_app = AnotherWSGIApplication()

    Keep in mind that WSGI applications shouldn't be mounted directly: They
    must be wrapped around with :class:`tg.controllers.WSGIAppController`.

    """

    auth = AuthController()
    error = ErrorController()
    nf = NewForgeController()
    nf.admin = SiteAdminController()
    search = SearchController()
    rest = RestController()

    def __init__(self):
        n_url_prefix = '/%s/' % request.path.split('/')[1]
        n = M.Neighborhood.query.get(url_prefix=n_url_prefix)
        if n and not n.url_prefix.startswith('//'):
            n.bind_controller(self)
        self.browse = ProjectBrowseController()
        super(RootController, self).__init__()

    def _setup_request(self):
        c.project = c.app = None
        c.memoize_cache = {}
        c.user = plugin.AuthenticationProvider.get(request).authenticate_request()
        assert c.user is not None, 'c.user should always be at least User.anonymous()'

    def _cleanup_request(self):
        pass

    @expose('jinja:allura:templates/project_list.html')
    @with_trailing_slash
    def index(self, **kw):
        """Handle the front-page."""
        c.project_summary = W.project_summary
        projects = M.Project.query.find(
            dict(is_root=True,
                 is_nbhd_project=False,
                 deleted=False)).sort('shortname').all()
        neighborhoods = M.Neighborhood.query.find().sort('name')
        psort = [ (n, [ p for p in projects if p.neighborhood_id==n._id ])
                  for n in neighborhoods ]
        categories = M.ProjectCategory.query.find({'parent_id':None}).sort('name').all()
        c.custom_sidebar_menu = [
            SitemapEntry(cat.label, '/browse/'+cat.name) for cat in categories
        ]
        return dict(projects=psort,title="All Projects",text=None)

