# -*- coding: utf-8 -*-
import time
import unittest
from os import path

import pylons
from webob import Request

from pygments import highlight
from pygments.lexers import PythonLexer, get_lexer_for_filename
from pygments.formatters import HtmlFormatter

from ming.orm import state
from alluratest.controller import setup_unit_test

from allura.lib import utils

class TestChunkedIterator(unittest.TestCase):

    def setUp(self):
        from allura import model as M
        setup_unit_test()
        for i in range(10):
            p = M.User.upsert('sample-user-%d' % i)

    def test_can_iterate(self):
        from allura import model as M
        chunks = [
            chunk for chunk in utils.chunked_find(M.User, {}, 2) ]
        assert len(chunks) > 1, chunks
        assert len(chunks[0]) == 2, chunks[0]

class TestAntispam(unittest.TestCase):

    def setUp(self):
        setup_unit_test()
        pylons.request.remote_addr = '127.0.0.1'
        self.a = utils.AntiSpam()

    def test_generate_fields(self):
        fields = '\n'.join(self.a.extra_fields())
        assert 'name="timestamp"' in fields, fields
        assert 'name="spinner"' in fields, fields
        assert ('class="%s"' % self.a.honey_class) in fields, fields

    def test_valid_submit(self):
        form = dict(a='1', b='2')
        r = Request.blank('/', POST=self._encrypt_form(**form))
        validated = utils.AntiSpam.validate_request(r)
        assert dict(a='1', b='2') == validated, validated

    def test_invalid_old(self):
        form = dict(a='1', b='2')
        r = Request.blank('/', POST=self._encrypt_form(**form))
        self.assertRaises(
            ValueError,
            utils.AntiSpam.validate_request,
            r, now=time.time()+60*60+1)

    def test_invalid_future(self):
        form = dict(a='1', b='2')
        r = Request.blank('/', POST=self._encrypt_form(**form))
        self.assertRaises(
            ValueError,
            utils.AntiSpam.validate_request,
            r, now=time.time()-10)

    def test_invalid_spinner(self):
        form = dict(a='1', b='2')
        eform = self._encrypt_form(**form)
        eform['spinner'] += 'a'
        r = Request.blank('/', POST=eform)
        self.assertRaises(ValueError, utils.AntiSpam.validate_request, r)

    def test_invalid_honey(self):
        form = dict(a='1', b='2', honey0='a')
        eform = self._encrypt_form(**form)
        r = Request.blank('/', POST=eform)
        self.assertRaises(ValueError, utils.AntiSpam.validate_request, r)

    def _encrypt_form(self, **kwargs):
        encrypted_form = dict(
            (self.a.enc(k), v) for k,v in kwargs.items())
        encrypted_form.setdefault(self.a.enc('honey0'), '')
        encrypted_form.setdefault(self.a.enc('honey1'), '')
        encrypted_form['spinner'] = self.a.spinner_text
        encrypted_form['timestamp'] = self.a.timestamp_text
        return encrypted_form

class TestTruthyCallable(unittest.TestCase):
    def test_everything(self):
        def wrapper_func(bool_flag):
            def predicate(bool_flag=bool_flag):
                return bool_flag
            return utils.TruthyCallable(predicate)
        true_predicate = wrapper_func(True)
        false_predicate = wrapper_func(False)
        assert true_predicate(True) == True
        assert false_predicate(False) == False
        assert true_predicate() == True
        assert false_predicate() == False
        assert bool(true_predicate) == True
        assert bool(false_predicate) == False

class TestCaseInsensitiveDict(unittest.TestCase):

    def test_everything(self):
        d = utils.CaseInsensitiveDict(Foo=5)
        assert d['foo'] == d['Foo'] == d['FOO'] == 5
        d['bAr'] = 6
        assert d['bar'] == d['Bar'] == 6
        d['bar'] = 7
        assert d['bar'] == d['bAr'] == 7
        self.assertRaises(AssertionError, utils.CaseInsensitiveDict, foo=1, Foo=2)
        del d['bar']
        assert len(d) == 1, d
        assert d.popitem() == ('Foo', 5)
        self.assertRaises(AssertionError, d.update, foo=1, Foo=2)
        d.update(foo=1, Bar=2)
        assert d == dict(foo=1, bar=2)
        assert d != dict(Foo=1, bar=2)
        assert d == utils.CaseInsensitiveDict(Foo=1, bar=2)

class TestLineAnchorCodeHtmlFormatter(unittest.TestCase):
    def test_render(self):
        code = '#!/usr/bin/env python\n'\
               'print "Hello, world!"'

        formatter = utils.LineAnchorCodeHtmlFormatter(cssclass='codehilite',
                                                      linenos='inline')
        lexer = get_lexer_for_filename("some.py", encoding='chardet')
        hl_code = highlight(code, lexer, formatter)
        assert '<div class="codehilite">' in hl_code
        assert '<div id="l1" class="code_block">' in hl_code
        assert '<span class="lineno">1</span>' in hl_code


class TestIsTextFile(unittest.TestCase):
    def test_is_text_file(self):
        here_dir = path.dirname(__file__)
        assert utils.is_text_file(open(path.join(
            here_dir,
            'data/test_mime/text_file.txt')).read())
        assert not utils.is_text_file(open(path.join(
            here_dir,
            'data/test_mime/bin_file')).read())
