"""PDF geometry from MuPDF; no OCR, pixel decoding or notebook mutation."""
import collections,json,math,os,sys
from pathlib import Path
import pymupdf
from fontTools.misc.bezierTools import calcCubicArcLength
POLICY='pdf-ink-and-rounded-vector-paths-v1'
MM=25.4/72

def number(name,default):return float(os.environ.get(name,str(default)))
def color(value):
 if value is None:return 'unknown'
 if len(value)==1:value=value*3
 if len(value)!=3:return 'unknown'
 return '#'+''.join(f'{max(0,min(255,round(float(v)*255))):02x}' for v in value)
def blank():return {'recorded_strokes':0,'estimated_strokes':0,'length_pt':0.,'colors':{},'widths':{}}
def add(target,count,length,shade,width,recorded=False):
 target['recorded_strokes' if recorded else 'estimated_strokes']+=count;target['length_pt']+=length
 target['colors'][shade]=target['colors'].get(shade,0)+count
 bucket=f'{round(width*MM,2):.2f}'
 target['widths'][bucket]=target['widths'].get(bucket,0)+count

def page_stats(page,quartz):
 result=blank();excluded=[];annotations=page.annots()
 if annotations:
  for annotation in annotations:
   if annotation.type[0]!=pymupdf.PDF_ANNOT_INK:continue
   count=0;length=0.
   for stroke in annotation.vertices or []:
    if not stroke:continue
    count+=1;length+=sum(math.dist(a,b) for a,b in zip(stroke,stroke[1:]))
   add(result,count,length,color(annotation.colors.get('stroke')),float(annotation.border.get('width',1) or 1),True)
   excluded.append(tuple(annotation.rect))
 # Rounded path style is evidence of ink in Quartz exports, never proof of a
 # physical pen-down. Printed graphics can share it; scans are not recognized.
 candidates=0;other_paths=0
 if quartz:
  drawings=page.get_cdrawings()
  maximum=int(number('GOODNOTES_STATS_MAX_PATHS',50000))
  if len(drawings)>maximum:raise ValueError('Page has too many vector paths')
  for drawing in drawings:
   items=drawing.get('items',[]);width=float(drawing.get('width') or 0)
   cap=drawing.get('lineCap',0);cap=max(cap) if isinstance(cap,(list,tuple)) else cap
   if (drawing.get('type')!='s' or cap!=1 or drawing.get('lineJoin')!=1
       or not number('GOODNOTES_STATS_MIN_WIDTH_PT',.1)<=width<=number('GOODNOTES_STATS_MAX_WIDTH_PT',8)
       or drawing.get('closePath') or float(drawing.get('stroke_opacity',1))<=0
       or str(drawing.get('dashes','[] 0')).replace(' ','')!='[]0'
       or not items or any(item[0] not in ('l','c') for item in items)):
    other_paths+=1;continue
   rect=tuple(drawing['rect'])
   if any(r[0]-.5<=rect[0] and r[1]-.5<=rect[1] and r[2]+.5>=rect[2] and r[3]+.5>=rect[3] for r in excluded):continue
   if len(items)>int(number('GOODNOTES_STATS_MAX_SEGMENTS',20000)):raise ValueError('Path has too many segments')
   count=0;length=0.;previous=None
   for item in items:
    start,end=item[1],item[-1]
    if previous is None or math.dist(previous,start)>.01:count+=1
    if item[0]=='l':length+=math.dist(start,end)
    else:length+=calcCubicArcLength(*item[1:],tolerance=number('GOODNOTES_STATS_CURVE_TOLERANCE',.005))
    previous=end
   if not math.isfinite(length) or length<0:raise ValueError('Invalid geometry')
   add(result,count,length,color(drawing.get('color')),width);candidates+=1
 result.update(page_width_pt=page.rect.width,page_height_pt=page.rect.height,other_vector_paths=other_paths,
               candidate_paths=candidates,method='mixed' if result['recorded_strokes'] and result['estimated_strokes'] else
               'pdf-ink-records' if result['recorded_strokes'] else 'vector-estimate' if result['estimated_strokes'] else 'no-detected-ink')
 return result

def analyze(file,number):
 with pymupdf.open(file) as document:
  if document.needs_pass:raise ValueError('PDF is encrypted')
  if not 1<=document.page_count<=int(number_env('GOODNOTES_STATS_MAX_PAGES',20000)):raise ValueError('PDF page limit')
  producer=str(document.metadata.get('producer',''))
  if number==0:return {'pages':document.page_count,'quartz':'Quartz PDFContext' in producer,'policy':POLICY}
  if not 1<=number<=document.page_count:raise ValueError('Page outside PDF')
  return page_stats(document[number-1],'Quartz PDFContext' in producer)
def number_env(name,default):return os.environ.get(name,str(default))
if __name__=='__main__':
 try:print(json.dumps(analyze(Path(sys.argv[1]),int(sys.argv[2]))))
 except Exception as error:
  print(json.dumps({'error':type(error).__name__,'message':str(error)[:160]}));sys.exit(1)
