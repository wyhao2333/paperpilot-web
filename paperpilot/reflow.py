"""Academic reading order and source-exact mathematical/graphic fragments.

No network or generated scientific content. Called in an isolated subprocess.
"""
import math
import re
import statistics
import uuid
from collections import Counter
from pathlib import Path

import pdfplumber

PARSER_VERSION = 2
MATH_FONT = re.compile(r'MTSYN|RMTM|MTEX|Math|Symbol|CMSY|CMMI|CMEX|STIX|MSBM|MSAM', re.I)
MATH_SYMBOL = re.compile(r'[=+−×÷∑∫∏√∞≤≥≠∈∉⊂⊆∀∃∥⪰≻⎡⎢⎣⎤⎥⎦⎧⎨⎩†ραβγδλμξωΞΛΣΦΨΩ]')
CAPTION = re.compile(r'^(?:Fig(?:ure)?\.?|Table)\s*\d', re.I)


def median(values, default=10):
    return statistics.median(values) if values else default


def bbox(items):
    return [min(x['x0'] for x in items), min(x['top'] for x in items), max(x['x1'] for x in items), max(x['bottom'] for x in items)]


def inside(obj, box):
    return box[0] <= (obj['x0']+obj['x1'])/2 <= box[2] and box[1] <= (obj['top']+obj['bottom'])/2 <= box[3]


def is_math(c):
    return bool(MATH_FONT.search(c.get('fontname', '')) or MATH_SYMBOL.search(c['text']))


def baseline(c):
    matrix=c.get('matrix')
    return -matrix[5] if matrix and abs(matrix[1])<.01 else c['bottom']


def rows_for(chars, size):
    # Use ordinary glyph baselines, not their tops. Attach scripts to that row.
    anchors = [c for c in chars if not is_math(c) and c['size'] >= size*.74 and c['text'].strip()]
    rows, assigned = [], set()
    for c in sorted(anchors, key=lambda c:(baseline(c),c['x0'])):
        r = next((r for r in reversed(rows[-6:]) if abs(r['baseline']-baseline(c)) < .7), None)
        if r is None:
            r = {'baseline':baseline(c),'chars':[],'size':c['size']}; rows.append(r)
        r['chars'].append(c); assigned.add(id(c))
    for c in sorted(chars,key=lambda c:(c['top'],c['x0'])):
        if id(c) in assigned:
            continue
        cy = baseline(c)
        candidates = [r for r in rows if abs(cy-r['baseline']) < max(size*.66,c['size']*.55)]
        def distance(r):
            xs=[a['x0'] for a in r['chars']]+[c['x0']]
            gap=max(0,min(a['x0'] for a in r['chars'])-c['x1'],c['x0']-max(a['x1'] for a in r['chars']))
            return abs(cy-r['baseline'])+min(5,gap*.08)
        r = min(candidates,key=distance) if candidates else None
        if r is None:
            r = {'baseline':baseline(c),'chars':[],'size':c['size']}; rows.append(r)
        r['chars'].append(c)
    return sorted(rows,key=lambda r:r['baseline'])


def tokens_for(chars, size, baseline):
    groups, current, last = [], [], None
    for c in sorted(chars,key=lambda c:(c['x0'],c['top'])):
        gap = c['x0']-last['x1'] if last else 0
        if not c['text'].strip() or current and gap>max(.8,min(c['size'],last['size'] if last else size)*.15):
            if current:
                groups.append(current); current=[]
        if c['text'].strip():
            current.append(c)
        last=c
    if current:
        groups.append(current)
    result=[]
    for group in groups:
        text=''.join(c['text'] for c in group)
        mathematical=any(is_math(c) for c in group)
        if len(text)<15 and not re.search(r'[A-Za-z]{4,}',text):
            mathematical |= max(c['size'] for c in group)>min(c['size'] for c in group)*1.25
        result.append({'text':text,'math':mathematical,'chars':group,'bbox':bbox(group)})
    merged=[]
    for token in result:
        if token['math'] and merged and merged[-1]['math']:
            a=merged[-1];a['chars']+=token['chars'];a['bbox']=bbox(a['chars']);a['text']+=' '+token['text']
        else:
            merged.append(token)
    return merged


def make_lines(chars,size):
    lines=[]
    for row in rows_for(chars,size):
        segments,segment,last=[],[],None
        for c in sorted(row['chars'],key=lambda c:c['x0']):
            if last and c['x0']-last['x1']>size*2.1:
                if segment:
                    segments.append(segment)
                segment=[]
            segment.append(c); last=c
        if segment:
            segments.append(segment)
        for segment in segments:
            x0,top,x1,bottom=bbox(segment)
            tokens=tokens_for(segment,size,row['baseline'])
            text=' '.join(t['text'] for t in tokens)
            if text:
                lines.append({'x0':x0,'top':top,'x1':x1,'bottom':bottom,'baseline':row['baseline'],'chars':segment,'tokens':tokens,'text':text,'size':median([c['size'] for c in segment if c['text'].strip()]),'bold':sum('bold' in c.get('fontname','').lower() for c in segment)/max(1,len(segment))})
    prose=[l for l in lines if len(re.findall(r'[A-Za-z]{3,}',l['text']))>=3]
    removed=set()
    for l in lines:
        if l in prose or re.search(r'\(\d+\)\s*$',l['text']):
            continue
        candidates=[a for a in prose if abs(a['baseline']-l['baseline'])<size*.66 and a['x0']-2<=l['x0'] and a['x1']+2>=l['x1'] and l['top']<a['bottom'] and l['bottom']>a['top']]
        if candidates:
            a=min(candidates,key=lambda a:abs(a['baseline']-l['baseline']))
            a['chars']+=l['chars'];a['tokens']=tokens_for(a['chars'],size,a['baseline']);a['text']=' '.join(t['text'] for t in a['tokens'])
            b=bbox(a['chars']);a.update(x0=b[0],top=b[1],x1=b[2],bottom=b[3]);removed.add(id(l))
    return sorted([l for l in lines if id(l) not in removed],key=lambda x:(x['top'],x['x0']))


def choose_gutter(lines,width,size):
    prose=[l for l in lines if len(re.findall(r'[A-Za-z]{3,}',l['text']))>=4 and l['size']<size*1.2]
    best=None
    for f in [.36+i*.01 for i in range(29)]:
        g=width*f; left=sum(l['x1']<g for l in prose); right=sum(l['x0']>g for l in prose)
        cross=len(prose)-left-right
        if min(left,right)<3:
            continue
        score=min(left,right)*3-cross*1.4-abs(f-.5)*8
        if best is None or score>best[0]:
            best=(score,g)
    return best[1] if best else None


def graphics(page,lines,gutter,content_top):
    # Connected vector paths matter: academic plots are often not raster images.
    clusters=[]
    for obj in page.images+page.curves+page.rects+page.lines:
        b=[obj['x0'],obj['top'],obj['x1'],obj['bottom']]
        if b[3]<content_top or b[1]>page.height-30:
            continue
        touching=[i for i,a in enumerate(clusters) if b[0]<=a[2]+7 and b[2]>=a[0]-7 and b[1]<=a[3]+7 and b[3]>=a[1]-7]
        for i in reversed(touching):
            a=clusters.pop(i);b=[min(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),max(a[3],b[3])]
        clusters.append(b)
    result=[]
    for cap in sorted([l for l in lines if CAPTION.match(l['text'])],key=lambda l:l['top']):
        center=(cap['x0']+cap['x1'])/2
        split=gutter or page.width/2
        full=cap['x0']<split<cap['x1']
        lo,hi=(20,page.width-20) if full else ((20,split) if center<split else (split,page.width-20))
        if cap['text'].lower().startswith('table'):
            rules=[o for o in page.edges if cap['bottom']<o['top']<cap['bottom']+175 and o['x0']>=lo-5 and o['x1']<=hi+5 and o['x1']-o['x0']>(hi-lo)*.55 and abs(o['bottom']-o['top'])<1]
            if len(rules)>=2:
                result.append({'kind':'table','label':cap['text'],'bbox':[min(o['x0'] for o in rules)-2,cap['bottom']+1,max(o['x1'] for o in rules)+2,max(o['bottom'] for o in rules)+3]})
            continue
        candidates=[b for b in clusters if b[2]-b[0]>45 and b[3]-b[1]>38 and b[0]>=lo-8 and b[2]<=hi+8 and b[3]<=cap['top']+4 and 0<=cap['top']-b[3]<65]
        if not candidates:
            continue
        nearest=max(candidates,key=lambda b:b[3])
        selected=[b for b in clusters if b[0]>=lo-8 and b[2]<=hi+8 and b[1]>=nearest[1]-30 and b[3]<=cap['top']+4 and b[3]>=nearest[1]+15 and b[2]-b[0]>10]
        crop=[min(b[0] for b in selected)-5,min(b[1] for b in selected)-5,max(b[2] for b in selected)+5,cap['top']-3]
        labels=[c for c in page.chars if lo<=c['x0']<hi and crop[1]-12<c['top']<crop[3] and c['bottom']>crop[1]]
        if labels:
            a=bbox(labels);crop=[min(crop[0],a[0]-2),min(crop[1],a[1]-2),max(crop[2],a[2]+2),min(cap['top']-2,max(crop[3],a[3]+1))]
        if crop[3]-crop[1]>35:
            result.append({'kind':'figure','bbox':crop,'label':re.match(r'^(?:Fig(?:ure)?\.?)\s*\d+',cap['text'],re.I).group()})
    try:
        for table in page.find_tables():
            b=list(table.bbox)
            if len(table.cells)>=4 and b[2]-b[0]>65 and b[3]-b[1]>30 and not any(inside({'x0':b[0],'x1':b[2],'top':b[1],'bottom':b[3]},r['bbox']) for r in result):
                result.append({'kind':'table','bbox':[b[0]-2,b[1]-2,b[2]+2,b[3]+2],'label':'表格'})
    except (ValueError,TypeError):
        pass
    for obj in page.images:
        b=[obj['x0'],obj['top'],obj['x1'],obj['bottom']]
        if b[1]>=content_top and b[2]-b[0]>70 and b[3]-b[1]>45 and not any(inside(obj,r['bbox']) for r in result):
            result.append({'kind':'figure','bbox':b,'label':'插图'})
    return result


def kind_for(line,size):
    text=line['text'].strip();compact=re.sub(r'\s','',text).lower()
    if CAPTION.match(text):
        return 'caption'
    if line['size']>=size*1.4 and len(text)<250:
        return 'title'
    if compact in {'abstract','articleinfo','keywords:','references','bibliography','literaturecited','acknowledgments','acknowledgements'} or len(text)<140 and line.get('bold',0)>.65:
        return 'heading'
    natural=re.findall(r'[A-Za-z]{3,}',text)
    count=sum(len(t['text']) for t in line['tokens'] if t['math'])
    prose_words={'with','where','for','some','and','the','then','let','given','all','such','case','norm','system','assumption','are','known','matrix','note','definition','lemma'}
    ratio=count/max(1,len(text.replace(' ','')))
    math_line=not natural or all(w.lower() in {'rank','diag','sin','cos','max','min','det','sup','ref'} for w in natural) or len(natural)<=3 and ratio>.35 and not any(w.lower() in prose_words for w in natural)
    if count and math_line and ratio>.15 or re.search(r'\(\d+[a-z]?\)\s*$',text) and len(natural)<2 or re.fullmatch(r'[\d\s.,()\[\]{}|*+−-]+',text) and len(text)>3:
        return 'equation'
    return 'body'


def ordered_lines(lines,gutter,width):
    lines=sorted(lines,key=lambda l:(l['top'],l['x0']))
    if not gutter:
        return [(l,'single',min((x['x0'] for x in lines),default=30),max((x['x1'] for x in lines),default=width-30)) for l in lines]
    intro=next((l for l in lines if re.match(r'^1\.?\s+Introduction',l['text'],re.I)),None)
    abstract=next((l for l in lines if re.sub(r'\s','',l['text']).lower()=='abstract'),None)
    result=[]
    if intro and abstract and abstract['top']<intro['top']:
        upper=[l for l in lines if l['top']<abstract['top']-3]
        middle=[l for l in lines if abstract['top']-3<=l['top']<intro['top']-3]
        result.extend((l,'front-title',30,width-30) for l in sorted(upper,key=lambda l:(l['top'],l['x0'])))
        for side in ['left','right']:
            col=[l for l in middle if (l['x0']<abstract['x0']-10)==(side=='left')]
            result.extend((l,'front-'+side,min((l['x0'] for l in col),default=30),max((l['x1'] for l in col),default=width-30)) for l in sorted(col,key=lambda l:(l['top'],l['x0'])))
        lines=[l for l in lines if l not in upper and l not in middle]
    spanning=[l for l in lines if l['x0']<gutter-4 and l['x1']>gutter+4]
    remaining=[l for l in lines if l not in spanning]
    for band,anchor in enumerate(sorted(spanning,key=lambda l:l['top'])+[None]):
        boundary=anchor['top'] if anchor else math.inf
        group=[l for l in remaining if (l['top']+l['bottom'])/2<boundary]
        remaining=[l for l in remaining if l not in group]
        for side in ['left','right']:
            col=[l for l in group if ((l['x0']+l['x1'])/2<gutter)==(side=='left')]
            left=min((l['x0'] for l in col),default=30);right=max((l['x1'] for l in col),default=width-30)
            merged=[]
            for l in sorted(col,key=lambda l:(l['top'],l['x0'])):
                if merged and l.get('chars') and merged[-1].get('chars') and abs(l['baseline']-merged[-1]['baseline'])<1.8:
                    a=merged[-1];a.update(x0=min(a['x0'],l['x0']),x1=max(a['x1'],l['x1']),top=min(a['top'],l['top']),bottom=max(a['bottom'],l['bottom']),text=a['text']+' '+l['text'],tokens=a['tokens']+l['tokens'],chars=a['chars']+l['chars'])
                else:
                    merged.append(dict(l))
            result.extend((l,f'{band}-{side}',left,right) for l in merged)
        if anchor:
            result.append((anchor,f'{band}-span',30,width-30))
    return result


def join_text(parts):
    result=''
    for part in parts:
        if re.search(r'[A-Za-z]{2,}-$',result) and re.match(r'^[a-z]',part):
            result=result[:-1]+part
        else:
            result+=(' ' if result else '')+part
    return result.replace('\x00','').strip()


def parse_pdf(path,max_pages=200,assets_dir=None):
    pages,total,counts=[],0,Counter()
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages)>max_pages:
            raise ValueError(f'PDF 共 {len(pdf.pages)} 页，超过当前 {max_pages} 页上限。')
        for n,page in enumerate(pdf.pages,1):
            chars=[c for c in page.dedupe_chars().chars if c['text']!='\x00'];total+=len(chars)
            if total>2_000_000:
                raise ValueError('PDF 文字量超过当前 200 万字符上限，请拆分后上传。')
            modes=Counter(round(c['size'],1) for c in chars if c['text'].isalpha()).most_common(1)
            size=modes[0][0] if modes else 10
            lines=make_lines(chars,size)
            for l in lines:
                if l['top']<50 or l['bottom']>page.height-28:
                    counts[re.sub(r'\d+','#',l['text'].lower())]+=1
            pages.append({'number':n,'width':float(page.width),'height':float(page.height),'size':size,'lines':lines})
        paragraphs,crops,two_columns,low_text=[],[],[],[];math_index=0;references=False
        for data,page in zip(pages,pdf.pages):
            n,size=data['number'],data['size'];lines=[]
            for l in data['lines']:
                margin=l['top']<50 or l['bottom']>page.height-28
                if (margin or l['top']>page.height*.93) and (re.fullmatch(r'[\d\s–—-]+',l['text']) or counts[re.sub(r'\d+','#',l['text'].lower())]>=2):
                    continue
                if re.match(r'^(?:https?://doi\.|\d{4}-\d{4}/|©\s*\d{4})',l['text']):
                    continue
                lines.append(l)
            if n==1:
                title=next((l for l in lines if l['size']>size*1.4 and l['x0']<page.width*.15 and len(l['text'])>30),None)
                if title:
                    lines=[l for l in lines if l['top']>=title['top']-2]
            gutter=choose_gutter(lines,page.width,size)
            if gutter:
                two_columns.append(n)
            content_top=min((l['top'] for l in lines),default=0) if n==1 else 48
            regions=graphics(page,lines,gutter,content_top)
            # Author superscripts and institutional footnotes are best kept as
            # one faithful, collapsible front-matter fragment.
            info=next((l for l in lines if re.sub(r'\s','',l['text']).lower()=='articleinfo'),None)
            title_lines=[l for l in lines if l['size']>size*1.4]
            if n==1 and info and title_lines:
                title_end=max(l['bottom'] for l in title_lines)
                authors=[l for l in lines if l['top']>title_end and l['bottom']<info['top']]
                if authors:
                    regions.append({'kind':'authors','label':'作者与机构','bbox':bbox(authors)})
            chars=[c for l in lines for c in l['chars'] if not any(inside(c,r['bbox']) for r in regions)]
            lines=make_lines(chars,size)
            for r in regions:
                b=r['bbox'];lines.append({'x0':b[0],'top':b[1],'x1':b[2],'bottom':b[3],'kind':r['kind'],'text':r['label'],'size':size})
            groups=[];current=previous=None
            for l,flow,left,right in ordered_lines(lines,gutter,page.width):
                kind=l.get('kind') or kind_for(l,size)
                compact=re.sub(r'\s','',l['text']).lower()
                if kind=='heading' and compact in {'references','bibliography','literaturecited'}:
                    references=True
                elif references and kind=='heading' and re.match(r'^(?:Appendix|Appendices|Supplementary)\b',l['text'],re.I):
                    references=False
                reference_line=references and kind not in {'heading','figure','table','authors','caption'}
                if reference_line:
                    # Hanging indents and volume/year/page-only lines belong to
                    # the same reference, never to an independent equation.
                    kind='body'
                if kind=='body' and not reference_line and not flow.startswith('front'):
                    words=re.findall(r'[A-Za-z]{2,}',l['text'])
                    math_words={'ref','cos','sin','tan','rank','diag','max','min','sup','det'}
                    only_variables=all(w.lower() in math_words or len(w)<=5 and not w.islower() for w in words)
                    if only_variables and (any(t['math'] for t in l.get('tokens',[])) or l['text'].strip() in math_words or not words and re.search(r'[()_]',l['text'])):
                        kind='equation'
                    atoms=re.findall(r'[A-Za-z]+',l['text'])
                    short_atoms=len(l['text'])<=30 and re.fullmatch(r'[A-Za-z0-9\s+−.,()_*-]+',l['text']) and all(len(w)<=2 and w.lower() not in {'of','in','to','by','as','at','if','on','is','or','we','it','be','an'} for w in atoms)
                    if current and current['kind']=='equation' and current['flow']==flow and short_atoms and previous and l['top']-previous['bottom']<=size*1.5:
                        # Matrix entries and detached transposes/subscripts may
                        # use ordinary text fonts. Keep them in the source crop.
                        kind='equation'
                if current and current['kind']=='caption' and kind=='body' and flow==current['flow'] and previous and l['top']-previous['bottom']<size*.85 and l['size']<size*.95:
                    kind='caption'
                if l['top']>page.height*.85 and l.get('size',size)<size*.9 and re.search(r'Corresponding author|E-mail address',l['text'],re.I):
                    kind='footnote'
                new=current is None or current['flow']!=flow or current['kind']!=kind or kind in {'figure','table','authors'}
                if previous and not new:
                    gap=l.get('baseline',l['top'])-previous.get('baseline',previous['top'])
                    ended=bool(re.search(r'[.!?:;)]$',previous['text']))
                    if kind=='equation':
                        new=l['top']-previous['bottom']>size*1.5
                    elif kind in {'title','heading'}:
                        new=gap>max(size*1.9,l['size']*1.5)
                    elif reference_line:
                        new=bool(re.match(r'^\s*(?:\[\d+\]|\d+\.)\s',l['text'])) or gap>size*2.5
                    else:
                        new=gap>size*1.95 or ended and (l['x0']-left>size*.8 or previous['x1']<right-size*2) or bool(re.match(r'^[•●▪]',l['text']))
                    if flow=='front-left' and re.match(r'^(?:Received|Accepted|Available)',l['text']):
                        new=True
                if new:
                    current={'kind':kind,'flow':flow,'lines':[],'reference':reference_line};groups.append(current)
                current['lines'].append(l);previous=l
            if sum(len(l.get('text','')) for l in lines)<60:
                low_text.append(n)
            def crop(box,kind):
                b=[max(0,box[0]-1),max(0,box[1]-1),min(page.width,box[2]+1),min(page.height,box[3]+1)]
                item={'asset':uuid.uuid4().hex+'.png','bbox':[round(x,2) for x in b],'width':round(b[2]-b[0],2),'height':round(b[3]-b[1],2),'kind':kind,'size':size}
                crops.append((n,item));return item
            groups=[g for g in groups if g['kind']!='footnote']+[g for g in groups if g['kind']=='footnote']
            for g in groups:
                gl=g['lines'];kind=g['kind'];box=bbox(gl);content={'size':size};units=[]
                if g.get('reference'):
                    content['reference']=True
                if kind in {'figure','table','equation','authors'}:
                    content.update(crop(box,kind))
                    text=' '.join(l['text'] for l in gl) if kind!='equation' else '公式 '+' '.join(m[0] for l in gl if (m:=re.search(r'\(\d+[a-z]?\)\s*$',l['text'])))
                else:
                    for l in gl:
                        parts=[]
                        for t in l['tokens']:
                            if t['math']:
                                math_index+=1;parts.append({'type':'math','token':f'PPMATH{math_index:06d}',**crop(t['bbox'],'inline')})
                            else:
                                parts.append({'type':'text','text':t['text']})
                        units.append({'text':' '.join(p['token'] if p['type']=='math' else p['text'] for p in parts),'parts':parts,'location':{'page':n,'bbox':[round(x,2) for x in bbox([l])],'width':data['width'],'height':data['height']}})
                    content['units']=units;content['parts']=[p for u in units for p in u['parts']]
                    text=join_text([u['text'] for u in units])
                    if kind=='heading':
                        standard={'abstract':'Abstract','articleinfo':'Article info','keywords:':'Keywords:'}
                        text=standard.get(re.sub(r'\s','',text).lower(),text)
                if text.strip():
                    paragraphs.append({'page':n,'kind':kind,'text':text,'content':content,'locations':[{'page':n,'bbox':[round(x,2) for x in box],'width':data['width'],'height':data['height']}]})
        if len(paragraphs)>12000 or len(crops)>20000:
            raise ValueError('解析内容超过当前上限，请拆分 PDF。')
        if assets_dir:
            directory=Path(assets_dir);directory.mkdir(parents=True,exist_ok=True)
            for n,page in enumerate(pdf.pages,1):
                items=[item for number,item in crops if number==n]
                if not items:
                    continue
                image=page.to_image(resolution=200).original;sx,sy=image.width/page.width,image.height/page.height
                for item in items:
                    b=item['bbox'];image.crop((int(b[0]*sx),int(b[1]*sy),math.ceil(b[2]*sx),math.ceil(b[3]*sy))).save(directory/item['asset'])
                image.close()
    # Continue a paragraph across a column/page boundary, retaining both sources.
    merged=[]
    footnotes=[p for p in paragraphs if p['kind']=='footnote']
    for p in [p for p in paragraphs if p['kind']!='footnote']:
        prev=merged[-1] if merged else None
        if prev and p['kind']==prev['kind']=='body' and not re.search(r'[.!?:;)]$',prev['text']) and re.match(r'^[a-z]',p['text']) and (prev['page']!=p['page'] or abs(prev['locations'][-1]['bbox'][0]-p['locations'][0]['bbox'][0])>70):
            prev['text']=join_text([prev['text'],p['text']]);prev['locations']+=p['locations'];prev['content']['units']+=p['content']['units'];prev['content']['parts']+=p['content']['parts']
        else:
            merged.append(p)
    paragraphs=merged+footnotes
    warnings=[]
    if two_columns:
        warnings.append(f'检测到 {len(two_columns)} 页双栏，已按正文栏顺序重排。')
    if low_text:
        warnings.append(f"第 {', '.join(map(str,low_text[:30]))} 页文字较少；扫描件仍需 OCR。")
    warnings.append('公式与图表取自原 PDF，保留原稿符号与外观；点击可回到来源页核对。')
    return {'page_count':len(pages),'paragraphs':paragraphs,'warnings':warnings,'needs_ocr':total<60,'parser_version':PARSER_VERSION}
