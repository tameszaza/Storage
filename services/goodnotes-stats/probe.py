import pymupdf,json,collections
from pathlib import Path
print('PYMUPDF',pymupdf.__version__)
root=Path('/library')
for rel in ['NTU/Y1SEM1/AB1201/Note.pdf','NTU/Y1SEM1/SC1005/CX1005_Tut7_Sol.pdf','NTU/Y1SEM1/MH1812/Chapter6 - Homework(1).pdf']:
 with pymupdf.open(root/rel) as doc:
  page=doc[0];drawings=page.get_cdrawings();styles=collections.Counter()
  for d in drawings:
   styles[str((d.get('type'),d.get('width'),d.get('lineCap'),d.get('lineJoin'),d.get('color'),d.get('closePath'),tuple(sorted(set(i[0] for i in d.get('items',[]))))))]+=1
  print('SAMPLE',rel,'drawings',len(drawings),'styles',styles.most_common(8))
