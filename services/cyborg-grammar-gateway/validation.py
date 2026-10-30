"""Reject AI edits which introduce LanguageTool errors; one local check per batch."""
import copy
import json
import re

MARKDOWN_MARKERS = frozenset('`$[]{}*_~#|\\<>\r\n')
LIST_OR_RULE = re.compile(r'^ {0,3}(?:[-+]\s|[0-9]{1,9}[.)]\s|---+\s*$)')


def units(text):
    return len(text.encode('utf-16-le')) // 2


def overlap(a, b, c, d):
    # Include the anchor of a deletion or a zero-length LanguageTool warning.
    return a < d and c < b if a != b and c != d else a <= d and c <= b


def protect_markup(params, matches):
    """Reuse Obsidian's annotated text boundaries; never parse Markdown again."""
    if not matches:
        return matches
    data = params.get('data')
    if data is not None:
        data = json.loads(data) if isinstance(data, str) else data
        parts = data.get('annotation', [])
    else:
        parts = [{'text': params.get('text', '')}]
    pieces, protected, position, wiki = [], [], 0, False
    for part in parts:
        value = part.get('text', part.get('markup', ''))
        end = position + units(value)
        markup = 'text' not in part
        if markup and value.startswith('[['):
            wiki = True
        if (markup or wiki) and end > position:
            protected.append((position, end))
        if markup and value.endswith(']]'):
            wiki = False
        pieces.append(value)
        position = end
    raw = ''.join(pieces).encode('utf-16-le')
    result = []
    for match in matches:
        start, length = match.get('offset'), match.get('length')
        if not isinstance(start, int) or not isinstance(length, int) or min(start, length) < 0:
            continue
        end = start + length
        if end > position or any((start < b and end > a) if length else a < start < b
                                 for a, b in protected):
            continue
        try:
            old = raw[start*2:end*2].decode('utf-16-le')
        except UnicodeDecodeError:
            continue
        signature = ''.join(c for c in old if c in MARKDOWN_MARKERS)
        replacements = []
        for replacement in match.get('replacements', []):
            value = replacement.get('value')
            if not isinstance(value, str) or ''.join(c for c in value if c in MARKDOWN_MARKERS) != signature:
                continue
            if LIST_OR_RULE.match(value) or LIST_OR_RULE.match(old):
                prefix = raw[:start*2].decode('utf-16-le').rsplit('\n', 1)[-1]
                if len(prefix) <= 3 and not prefix.strip():
                    before, after = LIST_OR_RULE.match(prefix+old), LIST_OR_RULE.match(prefix+value)
                    if (before.group(0) if before else None) != (after.group(0) if after else None):
                        continue
            replacements.append(replacement)
        if replacements or not match.get('replacements'):
            result.append({**match, 'replacements': replacements})
    return result


def validate(params, baseline, extra, verify):
    """Preserve original offsets/markup, validate only the best AI alternative.

    No second model call, history, persistent cache, or note modifications.
    An unavailable/incomplete verifier yields no AI additions.
    """
    data = params.get('data')
    if data is not None:
        data = json.loads(data) if isinstance(data, str) else data
        if not isinstance(data, dict) or not isinstance(data.get('annotation'), list):
            return {'matches': []}
        parts = copy.deepcopy(data['annotation'])
    else:
        parts = [{'text': params.get('text', '')}]
    segments, pos = [], 0
    for index, part in enumerate(parts):
        value = part.get('text', part.get('markup', ''))
        if not isinstance(value, str):
            return {'matches': []}
        end = pos + units(value)
        if 'text' in part:
            segments.append((pos, end, index, value))
        pos = end
    if pos > 96000:
        return {'matches': []}

    edits = []
    for match in sorted(extra.get('matches', []), key=lambda m: m['offset']):
        start, length = match['offset'], match['length']
        end = start + length
        if not length or any(overlap(start, end, e['start'], e['end']) for e in edits):
            continue
        replacement = match['replacements'][0].get('value')
        if not isinstance(replacement, str) or units(replacement) > 2000:
            continue
        for a, b, index, value in segments:
            if a <= start < end <= b:
                raw = value.encode('utf-16-le')
                # Strict decoding also rejects offsets splitting an emoji surrogate pair.
                before = raw[:(start-a)*2].decode('utf-16-le')
                old = raw[(start-a)*2:(end-a)*2].decode('utf-16-le')
                raw[(end-a)*2:].decode('utf-16-le')
                if old != replacement:
                    edits.append(dict(start=start, end=end, index=index, local=units(before),
                                      value=replacement, match=match))
                break
        if len(edits) >= 24:
            break
    if not edits:
        return {'matches': []}

    delta = 0
    for edit in edits:
        edit['new_start'] = edit['start'] + delta
        edit['new_end'] = edit['new_start'] + units(edit['value'])
        delta += units(edit['value']) - (edit['end'] - edit['start'])
    for edit in reversed(edits):
        part = parts[edit['index']]
        raw = part['text'].encode('utf-16-le')
        a, b = edit['local']*2, (edit['local']+edit['end']-edit['start'])*2
        part['text'] = (raw[:a] + edit['value'].encode('utf-16-le') + raw[b:]).decode('utf-16-le')
    proposed = dict(params)
    if data is not None:
        proposed['data'] = json.dumps({**data, 'annotation': parts}, ensure_ascii=False)
    else:
        proposed['text'] = parts[0]['text']
    checked = verify(proposed)
    if checked.get('warnings', {}).get('incompleteResults') or not isinstance(checked.get('matches'), list):
        return {'matches': []}

    def shifted(offset):
        return offset + sum(units(e['value']) - (e['end']-e['start']) for e in edits if e['end'] <= offset)

    existing = {(m['rule']['id'], shifted(m['offset']), shifted(m['offset']+m['length']))
                for m in baseline['matches']}
    for warning in checked['matches']:
        a, b = warning['offset'], warning['offset'] + warning['length']
        if (warning['rule']['id'], a, b) in existing:
            continue
        # Treat edits as a batch: retaining half could break interdependent corrections.
        return {'matches': []}
    return {'matches': [{**e['match'], 'replacements': [{'value': e['value']}]}
                        for e in edits]}
