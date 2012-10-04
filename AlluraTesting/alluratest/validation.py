# -*- coding: utf-8 -*-
"""
Functions to syntax-validate output content
"""
from os import path, environ, getcwd
import os
import sys
import logging
import tempfile
import subprocess
import json
import urllib2
import re

import tg
import mock
import beaker.session
from paste.deploy import loadapp
from paste.script.appinstall import SetupCommand
from pylons import c, g, url, request, response, session
import webtest
from webtest import TestApp
from webob import Request, Response
from nose.tools import ok_, assert_true, assert_false
from poster.encode import multipart_encode
from poster.streaminghttp import register_openers
from ming.utils import LazyProperty

from allura.lib import utils

ENABLE_CONTENT_VALIDATION = False
# By default we want to run only validations which are fast,
# but on special test hosts - all.
COMPLETE_TESTS_HOST = 'sb-forge-4039'

log = logging.getLogger(__name__)

class Config(object):
    "Config to encapsulate flexible/complex test enabled/disabled rules."
    _instance = None

    def __init__(self):
        self.ini_config = None
        pass

    @classmethod
    def instance(cls):
        if not cls._instance:
            cls._instance = cls()
        return cls._instance

    @LazyProperty
    def test_ini(self):
        if not self.ini_config:
            from . import controller
            import ConfigParser
            conf = ConfigParser.ConfigParser({'validate_html5': 'false', 'validate_inlinejs': 'false'})
            conf.read(controller.get_config_file())
            self.ini_config = conf
        return self.ini_config

    @LazyProperty
    def hostname(self):
        if os.path.exists('/etc/soghost'):
            with open('/etc/soghost') as fp:
                return fp.read().strip()

    def validation_enabled(self, val_type):
        env_var = os.getenv('ALLURA_VALIDATION')
        if env_var == 'all':
            return True
        elif env_var == 'none':
            return False
        elif env_var is not None:
            return val_type in env_var.split(',')

        if self.hostname == COMPLETE_TESTS_HOST:
            return True

        enabled = self.test_ini.getboolean('validation', 'validate_' + val_type)
        return enabled

    def fail_on_validation(self, val_type):
        env_var = os.getenv('ALLURA_VALIDATION')
        if env_var == 'all':
            return True
        if self.hostname == COMPLETE_TESTS_HOST:
            return True
        return ENABLE_CONTENT_VALIDATION


def report_validation_error(val_name, filename, message):
    message = '%s Validation errors (%s):\n%s\n' % (val_name, filename, message)
    if Config.instance().fail_on_validation(val_name):
        ok_(False, message)
    else:
        sys.stderr.write('=' * 40 + '\n' + message)

def dump_to_file(prefix, html):
    f = tempfile.NamedTemporaryFile(prefix=prefix, delete=False)
    f.write(html)
    f.close()
    return f.name

def validate_html(html_or_response):
        if hasattr(html_or_response, 'body'):
            html = html_or_response.body
        else:
            html = html_or_response

        html = html.lstrip()

        if html.startswith('<!DOCTYPE html>'):
            return validate_html5(html)
        else:
            assert False, 'Non-valid HTML: ' + html[:100] + '...'

def validate_json(json_or_response):
        if hasattr(json_or_response, 'body'):
            j = json_or_response.body
        else:
            j = json_or_response

        try:
            obj = json.loads(j)
        except Exception, e:
            ok_(False, "Couldn't validate JSON: " + str(e) + ':' + j[:100] + '...')

        return obj

def validate_html5(html_or_response):
        if hasattr(html_or_response, 'body'):
            html = html_or_response.body
        else:
            html = html_or_response
        register_openers()
        params = [("out","text"),("content",html)]
        datagen, headers = multipart_encode(params)
        request = urllib2.Request("http://html5.validator.nu/", datagen, headers)
        count = 3
        while True:
            try:
                resp = urllib2.urlopen(request, timeout=3).read()
                break
            except:
                resp = "Couldn't connect to validation service to check the HTML"
                count -= 1
                if count == 0:
                    sys.stderr.write('WARNING: ' + resp + '\n')
                    break

        resp = resp.replace('“','"').replace('”','"').replace('–','-')

        ignored_errors = [
            'Required attributes missing on element "object"',
            'Stray end tag "embed".',
            'Stray end tag "param".',
            r'Bad value .+? for attribute "onclick" on element "input": invalid return',
        ]
        for ignore in ignored_errors:
            resp = re.sub('Error: ' + ignore, 'Ignoring: ' + ignore, resp)

        if 'Error:' in resp:
            fname = dump_to_file('html5-', html)
            message = resp.decode('ascii','ignore')
            report_validation_error('html5', fname, message)


def validate_html5_chunk(html):
        """ When you don't have a html & body tags - this adds it"""
        # WebTest doesn't like HTML fragments without doctype,
        # so we output them sometimes for fragments, which is hack.
        # Unhack it here.
        doctype = '<!DOCTYPE html>'
        if html.startswith(doctype):
            html = html[len(doctype):]

        html = '''<!DOCTYPE html>
        <html>
        <head><title></title></head>
        <body>
        %s
        </body></html>''' % html
        return validate_html5(html)

def validate_js(html_or_response):
        if hasattr(html_or_response, 'body'):
            if html_or_response.status_int != 200:
                return
            html = html_or_response.body
        else:
            html = html_or_response
        basedir = path.dirname(path.abspath(__file__))
        jslint_dir = basedir + '/../jslint'
        fname = dump_to_file('jslint-', html)
        cmd = 'java -jar ' + jslint_dir + '/js.jar '+ jslint_dir +'/jslint.js ' + fname
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        stdout, stderr = p.communicate(html)
        if stdout.startswith('jslint: No problems found'):
            os.unlink(fname)
            return
        stdout = stdout.decode('UTF-8', 'replace')
        msg = '\n'.join(repr(s) for s in stdout.split('\n') if s)
        report_validation_error('js', fname, msg)

def validate_page(html_or_response):
    if Config.instance().validation_enabled('html5'):
        validate_html(html_or_response)
    if Config.instance().validation_enabled('inlinejs'):
        validate_js(html_or_response)

class AntiSpamTestApp(TestApp):

    def post(self, *args, **kwargs):
        if kwargs.pop('antispam', False):
            antispam = utils.AntiSpam()
            params = {
                'timestamp': antispam.timestamp_text,
                'spinner': antispam.spinner_text,
                antispam.enc('honey0'): '',
                antispam.enc('honey1'): '',
            }
            for k, v in kwargs['params'].iteritems():
                params[antispam.enc(k)] = v
            kwargs['params'] = params
        return super(AntiSpamTestApp, self).post(*args, **kwargs)

class PostParamCheckingTestApp(AntiSpamTestApp):

    def _validate_params(self, params, method):
        if not params:
            return
        # params can be a list or a dict
        if hasattr(params, 'items'):
            params = params.items()
        for k, v in params:
            if not isinstance(k, basestring):
                raise TypeError('%s key %s is %s, not str' % (method, k, type(k)))
            if not isinstance(v, (basestring, webtest.app.File)):
                raise TypeError('%s key %s has value %s of type %s, not str. ' % (method, k, v, type(v)))

    def get(self, *args, **kwargs):
        self._validate_params(kwargs.get('params'), 'get')
        return super(PostParamCheckingTestApp, self).get(*args, **kwargs)

    def post(self, *args, **kwargs):
        self._validate_params(kwargs.get('params'), 'post')
        return super(PostParamCheckingTestApp, self).post(*args, **kwargs)

class ValidatingTestApp(PostParamCheckingTestApp):

    # Subclasses may set this to True to skip validation altogether
    validate_skip = False

    def _validate(self, resp, method, val_params):
        """Perform validation on webapp response. This handles responses of
        various types and forms."""
        if resp.status_int != 200:
            return

        content = resp.body
        content_type = resp.headers['Content-Type']
        if content_type.startswith('text/html'):
            if val_params['validate_chunk']:
                validate_html5_chunk(content)
            else:
                validate_page(resp)
        elif content_type.split(';', 1)[0] in ('text/plain', 'text/x-python', 'application/octet-stream'):
            pass
        elif content_type.startswith('application/json'):
            validate_json(content)
        elif content_type.startswith('application/x-javascript'):
            validate_js(content)
        elif content_type.startswith('application/javascript'):
            validate_js(content)
        elif content_type.startswith('application/xml'):
            import feedparser
            d = feedparser.parse(content)
            assert d.bozo == 0, 'Non-wellformed feed'
        elif content_type.startswith('image/'):
            pass
        else:
            assert False, 'Unexpected output content type: ' + content_type

    def _get_validation_params(self, kw):
        "Separate validation params from normal TestApp methods params."
        params = {}
        for k in ('validate_skip', 'validate_chunk'):
            params[k] = kw.pop(k, False)
        return params, kw

    def get(self, *args, **kw):
        val_params, kw = self._get_validation_params(kw)
        resp = super(ValidatingTestApp, self).get(*args, **kw)
        if not self.validate_skip and not val_params['validate_skip']:
            self._validate(resp, 'get', val_params)
        return resp

    def post(self, *args, **kw):
        val_params, kw = self._get_validation_params(kw)
        resp = super(ValidatingTestApp, self).post(*args, **kw)
        if not self.validate_skip and not val_params['validate_skip']:
            self._validate(resp, 'post', val_params)
        return resp
