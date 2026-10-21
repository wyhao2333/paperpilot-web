"""Formula anchors belong to the reader, not to the translation provider."""
import re

MATH_TOKEN = re.compile(r'PPMATH\d{6}')
MATH_LIKE = re.compile(r'PP\s*MATH', re.I)
CONTEXT_FORMULA = re.compile(r'\[\s*(?:source\s+formula|来源公式|源公式|原文公式)\s*\]', re.I)


def normalize_formulas(text):
    return re.sub(r'PP\s*MATH\s*(\d{6})', lambda m: 'PPMATH' + m[1], text, flags=re.I)


def formula_sequence(text):
    return MATH_TOKEN.findall(text)


def formula_context(text):
    # Neighbouring paragraphs must not donate their formula IDs to the target.
    return MATH_TOKEN.sub('[source formula]', text)


def protected_parts(text, limit=1600):
    """Keep whitespace and anchors locally; give bounded text spans stable IDs."""
    parts, fields = [], []
    for piece in re.split(r'(PPMATH\d{6})', text):
        if not piece:
            continue
        if MATH_TOKEN.fullmatch(piece):
            parts.append(piece)
            continue
        while piece:
            end = min(len(piece), limit)
            if len(piece) > limit:
                boundary = max(piece.rfind(mark, limit // 2, limit) + len(mark)
                               for mark in ('. ', '; ', '\n', ' '))
                if boundary > limit // 2:
                    end = boundary
            span, piece = piece[:end], piece[end:]
            if not re.search(r'[^\W\d_]', span, re.UNICODE):
                parts.append(span)
                continue
            core = span.strip()
            leading = span[:len(span) - len(span.lstrip())]
            trailing = span[len(span.rstrip()):]
            key = str(len(fields))
            fields.append({'id': key, 'text': core})
            parts.extend([leading, {'id': key}, trailing])
    return parts, fields


def field_batches(fields, limit=1800):
    batch, size = [], 0
    for field in fields:
        if batch and (size + len(field['text']) > limit or len(batch) >= 16):
            yield batch
            batch, size = [], 0
        batch.append(field)
        size += len(field['text'])
    if batch:
        yield batch


def valid_field(value):
    return (isinstance(value, str) and bool(value.strip())
            and not MATH_LIKE.search(value) and not CONTEXT_FORMULA.search(value))
