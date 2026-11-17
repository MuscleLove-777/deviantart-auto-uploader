"""Render description URLs explicitly and check the server-rendered result.

Only HTTP(S) links and line breaks are supported. Rich input is rejected rather
than silently flattening content. No network requests are made by this module.
"""

import html
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit


_URL = re.compile(r"(?<![\w@])https?://[^\s<>\"'`、。，；：！？「」『』【】（）〈〉《》]+", re.I)
_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_DOMAIN_URL = re.compile(r'(?<![\w@./-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}(?::\d+)?(?:/|\b)', re.I)
_TRAILING = ".,;:"
_CLOSERS = {')': '(', ']': '[', '}': '{'}
CANONICAL_PROMOTION_URL = 'https://linktr.ee/ILoveMyCats'


def _trim_url(candidate):
    # Query and fragment punctuation is data. In particular, trailing ? and !
    # must not silently change the target. Ambiguous prose suffixes remain data.
    if '?' in candidate or '#' in candidate:
        return candidate
    while candidate:
        last = candidate[-1]
        if last in _TRAILING:
            candidate = candidate[:-1]
        elif last in _CLOSERS and candidate.count(last) > candidate.count(_CLOSERS[last]):
            candidate = candidate[:-1]
        else:
            break
    return candidate


def _valid_url(value):
    if any(ord(char) < 32 for char in value) or any(char.isspace() for char in value):
        return False
    try:
        parts = urlsplit(value)
        _ = parts.port
        return (parts.scheme.lower() in ('http', 'https') and bool(parts.hostname)
                and parts.username is None and parts.password is None
                and '\\' not in parts.netloc)
    except ValueError:
        return False


def _render_text(text):
    pieces = []
    targets = []
    end = 0
    for match in _URL.finditer(text):
        value = _trim_url(match.group())
        if not _valid_url(value):
            continue
        pieces.append(html.escape(text[end:match.start()], quote=True))
        escaped = html.escape(value, quote=True)
        pieces.append(f'<a href="{escaped}">{escaped}</a>')
        targets.append(value)
        end = match.start() + len(value)
    pieces.append(html.escape(text[end:], quote=True))
    return ''.join(pieces).replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br />'), targets


class _DescriptionParser(HTMLParser):
    """Accept an existing simple link description without nesting anchors."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.targets = []
        self.in_anchor = False

    def handle_starttag(self, tag, attrs):
        if tag == 'br' and not attrs and not self.in_anchor:
            self.parts.append('<br />')
            return
        if tag != 'a' or self.in_anchor:
            raise ValueError('Unsupported rich description markup')
        names = [name for name, _ in attrs]
        if len(names) != len(set(names)) or any(name not in ('href', 'title', 'target', 'rel') for name in names):
            raise ValueError('Unsupported anchor attributes')
        href = dict(attrs).get('href')
        if not href or not _valid_url(href):
            raise ValueError('Only HTTP(S) anchors are supported')
        if any(value is None for _, value in attrs):
            raise ValueError('Incomplete anchor attributes')
        self.parts.append('<a' + ''.join(f' {name}="{html.escape(value, quote=True)}"' for name, value in attrs) + '>')
        self.targets.append(href)
        self.in_anchor = True

    def handle_startendtag(self, tag, attrs):
        if tag != 'br':
            raise ValueError('Unsupported self-closing markup')
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag != 'a' or not self.in_anchor:
            raise ValueError('Unbalanced description markup')
        self.parts.append('</a>')
        self.in_anchor = False

    def handle_data(self, data):
        if self.in_anchor:
            self.parts.append(html.escape(data, quote=True))
        else:
            rendered, targets = _render_text(data)
            self.parts.append(rendered)
            self.targets.extend(targets)

    def handle_comment(self, data):
        raise ValueError('Unsupported description comments')

    def handle_decl(self, decl):
        raise ValueError('Unsupported description declaration')

    def handle_pi(self, data):
        raise ValueError('Unsupported description processing instruction')

    def close(self):
        super().close()
        if self.in_anchor:
            raise ValueError('Unclosed description anchor')


def render_description(text):
    """Return safe link HTML; raise ValueError for unsupported HTML input.

    Existing simple HTTP(S) anchors are retained rather than nested or
    double-escaped. Plain input must be passed in its original, unescaped form.
    """
    if not isinstance(text, str):
        raise TypeError('Description must be a string')
    if not _TAG.search(text) and '<!--' not in text and '<!' not in text and '<?' not in text:
        return _render_text(text)[0]
    parser = _DescriptionParser()
    parser.feed(text)
    parser.close()
    return ''.join(parser.parts)


def build_canonical_description(disclosure=''):
    """Emit one neutral, untracked link; retain supplied non-link disclosure text.

    Disclosures are optional because the existing uploader does not generate
    any. Additional URL destinations must not be smuggled through this field.
    """
    if not isinstance(disclosure, str):
        raise TypeError('Disclosure must be a string')
    if _URL.search(disclosure) or _DOMAIN_URL.search(disclosure) or _TAG.search(disclosure):
        raise ValueError('Disclosure must be plain text without URL destinations')
    text = f'More content → {CANONICAL_PROMOTION_URL}'
    if disclosure:
        text += '\n\n' + disclosure
    return render_description(text)


class _AnchorReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.targets = []
        self.empty_anchors = []
        self.invalid_anchors = []
        self.outside = []
        self.anchor = None
        self.malformed = False
        self.suppressed = []

    def handle_starttag(self, tag, attrs):
        if self.suppressed:
            if tag in ('script', 'style', 'template'):
                self.suppressed.append(tag)
            return
        if tag in ('script', 'style', 'template'):
            self.suppressed.append(tag)
            return
        if tag == 'a':
            self.outside.append(' ')
            if self.anchor is not None:
                self.malformed = True
            href = dict(attrs).get('href')
            self.anchor = {'href': href, 'text': []}
        elif tag in ('br', 'p', 'div', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'hr'):
            self.outside.append('\n')

    def handle_data(self, data):
        if self.suppressed:
            return
        if self.anchor is None:
            self.outside.append(data)
        else:
            self.anchor['text'].append(data)

    def handle_endtag(self, tag):
        if self.suppressed:
            if tag == self.suppressed[-1]:
                self.suppressed.pop()
            return
        if tag == 'a':
            if self.anchor is None:
                self.malformed = True
                return
            href = self.anchor['href']
            if not href or not _valid_url(href):
                self.invalid_anchors.append(href)
            elif not any(not char.isspace() and unicodedata.category(char) not in ('Cf', 'Cc', 'Cs')
                         for char in ''.join(self.anchor['text'])):
                self.empty_anchors.append(href)
            else:
                self.targets.append(href)
            self.anchor = None
            self.outside.append(' ')
        elif tag in ('p', 'div', 'li', 'ul', 'ol', 'h1', 'h2', 'h3'):
            self.outside.append('\n')

    def close(self):
        super().close()
        if self.anchor is not None or self.suppressed:
            self.malformed = True

    @property
    def bare_targets(self):
        return tuple(_render_text(''.join(self.outside))[1])


def description_targets(description):
    """Return one expected destination per occurrence, including duplicates."""
    reader = _AnchorReader()
    reader.feed(render_description(description))
    reader.close()
    return tuple(reader.targets)


@dataclass(frozen=True)
class LinkValidation:
    expected: tuple
    matched: tuple
    missing: tuple
    bare: tuple
    empty_anchors: tuple
    invalid_anchors: tuple
    malformed: bool

    @property
    def passed(self):
        return not (self.missing or self.bare or self.empty_anchors
                    or self.invalid_anchors or self.malformed)


def validate_published_links(published_html, expected_targets):
    """Check text-anchor markup and every expected occurrence, including repeats.

    The caller must obtain fresh HTML from the saved public description. A
    successful upload response alone is not verification of clickable links.
    Empty anchors and bare HTTP(S) URL text fail. This parses source markup;
    CSS visibility, JavaScript behavior, redirects, and target reachability
    require a separate browser check. Image-only anchors are not accepted.
    """
    expected = tuple(expected_targets)
    if any(not _valid_url(value) for value in expected):
        raise ValueError('Invalid expected destination')
    reader = _AnchorReader()
    reader.feed(published_html)
    reader.close()
    remaining = Counter(reader.targets)
    matched = []
    missing = []
    for value in expected:
        if remaining[value]:
            matched.append(value)
            remaining[value] -= 1
        else:
            missing.append(value)
    return LinkValidation(expected, tuple(matched), tuple(missing), reader.bare_targets,
                          tuple(reader.empty_anchors), tuple(reader.invalid_anchors),
                          reader.malformed)
