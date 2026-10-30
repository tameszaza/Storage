"""Conservative pasted-image OCR acceptance; stdlib, shared by local/remote workers."""
import csv,io,math
from collections import OrderedDict
from pathlib import Path

POLICY='tesseract5-eng-sparse11-3600-confidence-v2'
MAX_TEXT=100000

def assess(tsv,raw):
    """Keep reliable lines, without language dictionaries that would reject code/math.

    Confidence is an OCR estimate, not proof of correctness. A manual retry can
    retain the original Tesseract text when faint text/handwriting is rejected.
    """
    lines=OrderedDict();score=strong=weight=0
    for row in csv.DictReader(io.StringIO(tsv),delimiter='\t',quoting=csv.QUOTE_NONE):
        if row.get('level')!='5':continue
        word=row.get('text','').strip()
        if not word:continue
        try:conf=float(row['conf'])
        except (ValueError,KeyError,TypeError):continue
        if not math.isfinite(conf) or not 0<=conf<=100:continue
        key=tuple(row.get(k,'') for k in ('page_num','block_num','par_num','line_num'))
        tokens=lines.setdefault(key,[]);tokens.append((word,conf))
        chars=sum(c.isalnum() for c in word)
        weight+=chars;score+=chars*conf;strong+=chars*(conf>=70)
    mean=score/weight if weight else 0
    fraction=strong/weight if weight else 0
    accepted=[];previous=None
    # Reject a noisy image as a whole before keeping isolated accidental words.
    if weight and mean>=60 and fraction>=.55:
        for key,tokens in lines.items():
            count=sum(sum(c.isalnum() for c in word) for word,_ in tokens)
            if not count:continue
            line_score=sum(sum(c.isalnum() for c in word)*conf for word,conf in tokens)/count
            reliable=sum(sum(c.isalnum() for c in word) for word,conf in tokens if conf>=70)/count
            if line_score<55 or reliable<.4:continue
            if previous is not None:accepted.append('\n\n' if key[:3]!=previous[:3] else '\n')
            accepted.append(' '.join(word for word,_ in tokens));previous=key
    text=''.join(accepted).strip()[:MAX_TEXT]
    return {'text':text,'rawText':raw.strip()[:MAX_TEXT],'policy':POLICY,
            'confidence':round(mean,1),'reliableFraction':round(fraction,3)}

def result(base):
    with Path(str(base)+'.txt').open(encoding='utf8') as f:raw=f.read(MAX_TEXT)
    with Path(str(base)+'.tsv').open(encoding='utf8') as f:tsv=f.read(4*1024*1024+1)
    if len(tsv)>4*1024*1024:raise ValueError('OCR confidence output too large')
    return assess(tsv,raw)
