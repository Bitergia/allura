import re
import os
import logging
import string
from collections import defaultdict
from urllib import quote
from urlparse import urljoin
from ConfigParser import RawConfigParser
from pprint import pformat

from tg import config
from pylons import c, g, request
from BeautifulSoup import BeautifulSoup

import markdown
import feedparser

from . import macro
from . import helpers as h

log = logging.getLogger(__name__)

PLAINTEXT_BLOCK_RE = re.compile( \
    r'(?P<bplain>\[plain\])(?P<code>.*?)(?P<eplain>\[\/plain\])',
    re.MULTILINE|re.DOTALL
    )

class ForgeExtension(markdown.Extension):

    def __init__(self, wiki=False, email=False, macro_context=None):
        markdown.Extension.__init__(self)
        self._use_wiki = wiki
        self._is_email = email
        self._macro_context = macro_context

    def extendMarkdown(self, md, md_globals):
        md.registerExtension(self)
        self.forge_processor = ForgeProcessor(self._use_wiki, md, macro_context=self._macro_context)
        self.forge_processor.install()
        md.preprocessors['fenced-code'] = FencedCodeProcessor()
        md.preprocessors.add('plain_text_block', PlainTextPreprocessor(md), "_begin")
        md.inlinePatterns['autolink_1'] = AutolinkPattern(r'(http(?:s?)://[a-zA-Z0-9./\-_0%?&=+#;~:]+)')
        md.treeprocessors['br'] = LineOrientedTreeProcessor(md)
        # Sanitize HTML
        md.postprocessors['sanitize_html'] = HTMLSanitizer()
        # Rewrite all relative links that don't start with . to have a '../' prefix
        md.postprocessors['rewrite_relative_links'] = RelativeLinkRewriter(
            make_absolute=self._is_email)
        # Put a class around markdown content for custom css
        md.postprocessors['add_custom_class'] = AddCustomClass()
        md.postprocessors['mark_safe'] = MarkAsSafe()

    def reset(self):
        self.forge_processor.reset()

class PlainTextPreprocessor(markdown.preprocessors.Preprocessor):

    def run(self, lines):
        text = "\n".join(lines)
        while 1:
            res = PLAINTEXT_BLOCK_RE.finditer(text)
            for m in res:
                code = self._escape(m.group('code'))
                placeholder = self.markdown.htmlStash.store(code, safe=True)
                text = '%s%s%s'% (text[:m.start()], placeholder, text[m.end():])
                break
            else:
                break
        return text.split("\n")

    def _escape(self, txt):
        """ basic html escaping """
        txt = txt.replace('&', '&amp;')
        txt = txt.replace('<', '&lt;')
        txt = txt.replace('>', '&gt;')
        txt = txt.replace('"', '&quot;')
        return txt

class FencedCodeProcessor(markdown.preprocessors.Preprocessor):
    pattern = '~~~~'

    def run(self, lines):
        in_block = False
        new_lines = []
        for line in lines:
            if line.lstrip().startswith(self.pattern):
                in_block = not in_block
                continue
            if in_block:
                new_lines.append('    ' + line)
            else:
                new_lines.append(line)
        return new_lines

class ForgeProcessor(object):
    alink_pattern = r'(?<!\[)\[([^\]\[]*)\]'
    macro_pattern = r'\[(\[([^\]\[]*)\])\]'
    placeholder_prefix = '#jgimwge'
    placeholder = '%s:%%s:%%.4d#khjhhj' % placeholder_prefix
    placeholder_re = re.compile('%s:(\\w+):(\\d+)#khjhhj' % placeholder_prefix)

    def __init__(self, use_wiki = False, markdown=None, macro_context=None):
        self.markdown = markdown
        self._use_wiki = use_wiki
        self._macro_context = macro_context
        self.inline_patterns = {
            'forge.alink' : ForgeInlinePattern(self, self.alink_pattern),
            'forge.macro' : ForgeInlinePattern(self, self.macro_pattern)}
        self.postprocessor = ForgePostprocessor(self)
        self.tree_processor = ForgeTreeProcessor(self)
        self.reset()
        self.artifact_re = re.compile(r'((.*?):)?((.*?):)?(.+)')
        self.macro_re = re.compile(self.alink_pattern)

    def install(self):
        for k,v in self.inline_patterns.iteritems():
            self.markdown.inlinePatterns[k] = v
        if self._use_wiki:
            self.markdown.treeprocessors['forge'] = self.tree_processor
        self.markdown.postprocessors['forge'] = self.postprocessor

    def store(self, raw):
        if self.macro_re.match(raw):
            stash = 'macro'
            raw = raw[1:-1] # strip off the enclosing []
        elif self.artifact_re.match(raw): stash = 'artifact'
        else: return raw
        return self._store(stash, raw)

    def _store(self, stash_name, value):
        placeholder = self.placeholder % (stash_name, len(self.stash[stash_name]))
        self.stash[stash_name].append(value)
        return placeholder

    def lookup(self, stash, id):
        stash = self.stash.get(stash, [])
        if id >= len(stash): return ''
        return stash[id]

    def compile(self):
        from allura import model as M
        if self.stash['artifact'] or self.stash['link']:
            try:
                self.alinks = M.Shortlink.from_links(*self.stash['artifact'])
                self.alinks.update(M.Shortlink.from_links(*self.stash['link']))
            except:
                self.alinks = {}
        self.stash['artifact'] = map(self._expand_alink, self.stash['artifact'])
        self.stash['link'] = map(self._expand_link, self.stash['link'])
        self.stash['macro'] = map(macro.parse(self._macro_context), self.stash['macro'])

    def reset(self):
        self.stash = dict(
            artifact=[],
            macro=[],
            link=[])
        self.alinks = {}
        self.compiled = False

    def _expand_alink(self, link):
        new_link = self.alinks.get(link, None)
        if new_link:
            return '<a href="%s">[%s]</a>' % (
                new_link.url, link)
        elif self._use_wiki and ':' not in link:
            return '<a href="%s" class="notfound">[%s]</a>' % (
                h.urlquote(link), link)
        else:
            return link

    def _expand_link(self, link):
        reference = self.alinks.get(link)
        mailto = u'\x02amp\x03#109;\x02amp\x03#97;\x02amp\x03#105;\x02amp\x03#108;\x02amp\x03#116;\x02amp\x03#111;\x02amp\x03#58;'
        if not reference and not link.startswith(mailto) and '#' not in link:
            return 'notfound'
        else:
            return ''

class ForgeInlinePattern(markdown.inlinepatterns.Pattern):

    def __init__(self, parent, pattern):
        self.parent = parent
        markdown.inlinepatterns.Pattern.__init__(
            self, pattern, parent.markdown)

    def handleMatch(self, m):
        return self.parent.store(m.group(2))

class ForgePostprocessor(markdown.postprocessors.Postprocessor):

    def __init__(self, parent):
        self.parent = parent
        markdown.postprocessors.Postprocessor.__init__(
            self, parent.markdown)

    def run(self, text):
        self.parent.compile()
        def repl(mo):
            return self.parent.lookup(mo.group(1), int(mo.group(2)))
        return self.parent.placeholder_re.sub(repl, text)

class ForgeTreeProcessor(markdown.treeprocessors.Treeprocessor):
    '''This flags intra-wiki links that point to non-existent pages'''

    def __init__(self, parent):
        self.parent = parent

    def run(self, root):
        for node in root.getiterator('a'):
            href = node.get('href')
            if not href: continue
            if '/' in href: continue
            classes = node.get('class', '').split() + [ self.parent._store('link', href) ]
            node.attrib['class'] = ' '.join(classes)
        return root

class MarkAsSafe(markdown.postprocessors.Postprocessor):

    def run(self, text):
        return h.html.literal(text)

class AddCustomClass(markdown.postprocessors.Postprocessor):

    def run(self, text):
        return '<div class="markdown_content">%s</div>' % text

class RelativeLinkRewriter(markdown.postprocessors.Postprocessor):

    def __init__(self, make_absolute=False):
        self._make_absolute = make_absolute

    def run(self, text):
        try:
            if not request.path_info.endswith('/'): return text
        except:
            # Must be being called outside the request context
            pass
        soup = BeautifulSoup(text)
        if self._make_absolute:
            rewrite = self._rewrite_abs
        else:
            rewrite = self._rewrite
        for link in soup.findAll('a'):
            rewrite(link, 'href')
        for link in soup.findAll('img'):
            rewrite(link, 'src')
        return unicode(soup)

    def _rewrite(self, tag, attr):
        val = tag.get(attr)
        if val is None: return
        if ' ' in val:
            # Don't urllib.quote to avoid possible double-quoting
            # just make sure no spaces
            val = val.replace(' ', '%20')
            tag[attr] = val
        if '://' in val:
            if 'sf.net' in val or 'sourceforge.net' in val:
                return
            else:
                tag['rel']='nofollow'
                return
        if val.startswith('/'): return
        if val.startswith('.'): return
        if val.startswith('mailto:'): return
        if val.startswith('#'): return
        tag[attr] = '../' + val

    def _rewrite_abs(self, tag, attr):
        self._rewrite(tag, attr)
        val = tag.get(attr)
        val = urljoin(config.get('base_url', 'http://sourceforge.net/'),val)
        tag[attr] = val

class HTMLSanitizer(markdown.postprocessors.Postprocessor):

    def run(self, text):
        try:
            p = feedparser._HTMLSanitizer('utf-8')
        except TypeError: # $@%## pre-released versions from SOG
            p = feedparser._HTMLSanitizer('utf-8', '')
        p.feed(text.encode('utf-8'))
        return unicode(p.output(), 'utf-8')

class LineOrientedTreeProcessor(markdown.treeprocessors.Treeprocessor):
    '''Once MD is satisfied with the etree, this runs to replace \n with <br/>
    within <p>s.
    '''

    def __init__(self, md):
        self._markdown = md
    
    def run(self, root):
        for node in root.getiterator('p'):
            if not node.text: continue
            if '\n' not in node.text: continue
            text = self._markdown.serializer(node)
            text = self._markdown.postprocessors['raw_html'].run(text)
            text = text.strip().encode('utf-8')
            if '\n' not in text: continue
            new_text = (text
                        .replace('<br>', '<br/>')
                        .replace('\n', '<br/>'))
            new_node = None
            try:
                new_node = markdown.etree.fromstring(new_text)
            except SyntaxError:
                try:
                    new_node = markdown.etree.fromstring(unicode(BeautifulSoup(new_text)))
                except:
                    log.exception('Error adding <br> tags: new text is %s', new_text)
                    pass
            if new_node:
                node.clear()
                node.text = new_node.text
                node[:] = list(new_node)
        return root

class AutolinkPattern(markdown.inlinepatterns.LinkPattern):

    def handleMatch(self, mo):
        old_link = mo.group(2)
        result = markdown.etree.Element('a')
        result.text = old_link
        result.set('href', old_link)
        return result

