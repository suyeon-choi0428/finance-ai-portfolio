"""Portable handoff helpers. No automatic semantic relation extraction or API LLM.
Network fetch / DB write are separate explicit subcommands; examples run offline.
"""
import argparse,copy,datetime,getpass,hashlib,json,os,re,sys
from pathlib import Path

KINDS={'Company','Product','Technology','Facility','Organization','Event','ProductGroup'}
STATUSES={'reported','plan','forecast','in_progress','completed_reported','negative_with_exception'}
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def write(p,x):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x',encoding='utf-8') as f:json.dump(x,f,ensure_ascii=False,indent=2)
def digest(x):return hashlib.sha256(x.encode('utf-8')).hexdigest()
def normalize(x):return re.sub(r'\s+',' ',x or '').strip()

def pdf_pages(args):
 from pypdf import PdfReader
 p=Path(args.input);raw=p.read_bytes();reader=PdfReader(p)
 pages=[]
 for i,page in enumerate(reader.pages,1):
  text=page.extract_text() or '';m=re.search(r'dart\.fss\.or\.kr\s+Page\s+(\d+)',text)
  pages.append({'pdf_page':i,'printed_page':int(m[1]) if m else None,'text':text,'normalized_text':normalize(text)})
 write(args.output,{'schema_version':'handoff-source-v1','source_id':args.source_id,'origin':'disclosure','title':p.name,'filename':p.name,'sha256':hashlib.sha256(raw).hexdigest(),'company':None,'report_period_start':None,'report_period_end':None,'submission_date_on_cover':None,'official_disclosure_date':None,'receipt_number':None,'official_url':None,'pages':pages,'warning':'Company/report period/receipt must be checked from original pages. Empty/scanned pages need separate OCR; OCR not implemented.'})

def html_extract(html,article):
 from bs4 import BeautifulSoup
 result=copy.deepcopy(article)
 soup=BeautifulSoup(html,'html.parser')
 # Only tested selectors from the existing small CNBC / Motley Fool pilot.
 selectors=['.ArticleBody-articleBody'] if article.get('source_name')=='CNBC' else ['.article-body','.article-content','[itemprop="articleBody"]']
 container=None;selector=None
 for css in selectors:
  container=soup.select_one(css)
  if container is not None:selector=css;break
 if container is None:
  result.update(article_body=None,body_status='BODY_CONTAINER_NOT_FOUND',extraction_method='BeautifulSoup selectors',review_status='unreviewed',full_text_verified=False);return result
 for bad in container.select('script,style,aside,nav,form,button,figure,.ad,.advertisement'):bad.decompose()
 parts=[x.get_text(' ',strip=True) for x in container.select('p,h2,h3,li')]
 text='\n\n'.join(x for x in parts if x)
 blocked=bool(re.search(r'access denied|verify you are human|enable javascript.*cookies',text[:600],re.I))
 result.update(article_body=text if len(text)>=300 and not blocked else None,body_status='BLOCK_PAGE' if blocked else 'EXTRACTED_PENDING_REVIEW' if len(text)>=300 else 'BODY_TOO_SHORT',body_collected_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),extraction_method='BeautifulSoup '+selector,review_status='unreviewed',full_text_verified=False,review_flags=[p for p in parts if re.search(r'subscribe|sign up|premium|stock advisor',p,re.I)])
 return result
def body(args,fetch=False):
 article=read(args.article)
 if fetch:
  import requests
  try:
   r=requests.get(article['url'],timeout=(8,20),headers={'User-Agent':'Mozilla/5.0'})
   if r.status_code!=200:result={**article,'article_body':None,'body_status':'HTTP_ERROR','http_status':r.status_code,'review_status':'unreviewed'}
   elif 'html' not in r.headers.get('Content-Type','').lower():result={**article,'article_body':None,'body_status':'NOT_HTML','http_status':r.status_code,'review_status':'unreviewed'}
   else:result={**html_extract(r.text,article),'http_status':r.status_code,'final_url':r.url}
  except requests.RequestException as e:result={**article,'article_body':None,'body_status':'REQUEST_ERROR','error_type':type(e).__name__,'review_status':'unreviewed'}
 else:result=html_extract(Path(args.html).read_text(encoding='utf-8'),article)
 write(args.output,result)

def compile_annotations(data):
 """Validate annotations ALREADY authored by a person/Codex; never infer edges."""
 sources={s['source_id']:s for s in data['sources']}
 if len(sources)!=len(data['sources']):raise ValueError('Duplicate source_id')
 if any(s['origin'] not in {'news','disclosure'} for s in sources.values()):raise ValueError('Unsupported source origin')
 seen=set();confirmed=[];candidates=[]
 for ann in data['annotations']:
  rid=ann['annotation_id']
  if rid in seen:raise ValueError('Duplicate annotation_id')
  seen.add(rid)
  if ann['status']=='unconfirmed':
   if not ann.get('missing_evidence'):raise ValueError('Candidate needs missing_evidence')
   candidates.append(ann);continue
  if ann['status'] not in STATUSES:raise ValueError('Unknown status')
  source=sources[ann['source_id']]
  quote=ann['evidence_text']
  if not quote:raise ValueError('Empty evidence')
  if source['origin']=='disclosure' and 'pages' in source:
   page=next(p for p in source['pages'] if p['pdf_page']==ann['pdf_page']);text=page.get('normalized_text',normalize(page['text']))
  else:text=source['text']
  if quote not in text:raise ValueError('Evidence not found: '+rid)
  for entity in [ann['subject'],ann['object']]:
   if entity['kind'] not in KINDS or not entity['id'] or not entity['name']:raise ValueError('Bad typed entity')
  if ann.get('event'):
   if not ann['event'].get('status'):raise ValueError('Event status required')
   if any(not isinstance(v,(str,int,float,bool)) for v in ann['event'].values()):raise ValueError('Event properties must be flat scalar values; omit unknowns')
  confirmed.append({**ann,'assertion_origin':source['origin'],'evidence_sha256':digest(quote),'evidence_start':text.index(quote),'evidence_end':text.index(quote)+len(quote),'independently_verified':False})
 return {'schema_version':'handoff-relations-v1','sources':data['sources'],'relations':confirmed,'candidates':candidates,'warning':'Source declarations and semantic interpretation are author responsibility. Substring match does not prove truth or product identity.'}

def graph(data,namespace):
 if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',namespace):raise ValueError('Use a distinct ASCII namespace')
 nodes={};edges=[]
 def node(kind,ident,props):
  k=f'{namespace}:{kind}:{ident}';v={'key':k,'namespace':namespace,'kind':kind,**props}
  if k in nodes and nodes[k]!=v:raise ValueError('Entity alias collision; resolve canonical IDs explicitly')
  nodes[k]=v;return k
 def edge(a,typ,b,ident,props=None):edges.append({'start':a,'end':b,'type':typ,'key':namespace+':edge:'+ident,'props':props or {}})
 docs={}
 for s in data['sources']:
  docs[s['source_id']]=node('SourceDocument',s['source_id'],{'origin':s['origin'],'name':s['title'],'source_record_json':json.dumps(s,ensure_ascii=False,sort_keys=True)})
 for r in data['relations']:
  rid=r['annotation_id'];sub=r['subject'];obj=r['object']
  a=node(sub['kind'],sub['id'],{'name':sub['name']});b=node(obj['kind'],obj['id'],{'name':obj['name']})
  st=node('Statement',rid,{'predicate':r['predicate'],'status':r['status'],'origin':r['assertion_origin'],'source_relation_id':rid,'record_json':json.dumps(r,ensure_ascii=False,sort_keys=True)})
  ev=node('Evidence',rid,{'text':r['evidence_text'],'sha256':r['evidence_sha256'],'pdf_page':r.get('pdf_page',0),'source_relation_id':rid})
  edge(a,'CLAIM',b,rid+':CLAIM',{'predicate':r['predicate'],'statement_key':st,'status':r['status'],'origin':r['assertion_origin']})
  for typ,x,y in [('SUBJECT',st,a),('OBJECT',st,b),('HAS_EVIDENCE',st,ev),('FROM_DOCUMENT',ev,docs[r['source_id']])]:edge(x,typ,y,rid+':'+typ)
  if r.get('event'):
   ep=r['event'];event=node('Event',rid,ep)
   edge(st,'ABOUT_EVENT',event,rid+':ABOUT_EVENT')
   if b!=event:edge(event,'INVOLVES',b,rid+':INVOLVES')
 for c in data['candidates']:node('Candidate',c['annotation_id'],{'record_json':json.dumps(c,ensure_ascii=False),'missing_evidence':c['missing_evidence']})
 return {'schema_version':'handoff-graph-v1','namespace':namespace,'nodes':list(nodes.values()),'edges':edges,'counts':{'relations':len(data['relations']),'evidence':len(data['relations']),'candidates':len(data['candidates']),'nodes':len(nodes),'edges':len(edges)},'warning':'New handoff schema, not directly interchangeable with the old 49-row pilot.'}

def load(args):
 from neo4j import GraphDatabase
 g=read(args.input)
 if g['schema_version']!='handoff-graph-v1':raise ValueError('Wrong graph schema')
 password=getpass.getpass('Neo4j password (not saved): ')
 labels=KINDS|{'SourceDocument','Statement','Evidence','Candidate'}
 types={'CLAIM','SUBJECT','OBJECT','HAS_EVIDENCE','FROM_DOCUMENT','ABOUT_EVENT','INVOLVES'}
 with GraphDatabase.driver(args.uri,auth=(args.user,password)) as driver:
  password=None;driver.verify_connectivity()
  with driver.session(database=args.database) as session:
   session.run('CREATE CONSTRAINT handoff_key IF NOT EXISTS FOR (n:HandoffPilot) REQUIRE n.key IS UNIQUE').consume()
   def txload(tx):
    before=tx.run('MATCH (n:HandoffPilot {namespace:$ns}) RETURN count(n) AS n',ns=g['namespace']).single()['n']
    for n in g['nodes']:
     if n['kind'] not in labels:raise ValueError('Unexpected label')
     old=tx.run('MATCH (n:HandoffPilot {key:$key}) RETURN properties(n) AS p',key=n['key']).single()
     if old and dict(old['p'])!=n:raise ValueError('Existing data differs; use a new namespace')
     tx.run('MERGE (n:HandoffPilot:Handoff'+n['kind']+' {key:$key}) ON CREATE SET n += $props',key=n['key'],props=n).consume()
    for e in g['edges']:
     if e['type'] not in types:raise ValueError('Unexpected type')
     tx.run('MATCH (a:HandoffPilot {key:$a}),(b:HandoffPilot {key:$b}) MERGE (a)-[r:'+e['type']+' {key:$key}]->(b) ON CREATE SET r += $props',a=e['start'],b=e['end'],key=e['key'],props=e['props']).consume()
    actual={r['key']:r['props'] for r in tx.run('MATCH (n:HandoffPilot {namespace:$ns}) RETURN n.key AS key,properties(n) AS props',ns=g['namespace'])}
    if actual!={n['key']:n for n in g['nodes']}:raise ValueError('Node property roundtrip mismatch')
    rows=tx.run('MATCH (a:HandoffPilot {namespace:$ns})-[r]->(b:HandoffPilot {namespace:$ns}) RETURN a.key AS start,b.key AS end,type(r) AS type,properties(r) AS props',ns=g['namespace']).data()
    expected={e['key']:{'start':e['start'],'end':e['end'],'type':e['type'],'props':{'key':e['key'],**e['props']}} for e in g['edges']}
    if len(rows)!=len(expected) or {r['props']['key']:r for r in rows}!=expected:raise ValueError('Edge property roundtrip mismatch')
    return {'before_nodes':before,'after_nodes':len(actual),'after_edges':len(rows),'roundtrip':'passed'}
   result=session.execute_write(txload)
 write(args.output,result)

def main():
 parser=argparse.ArgumentParser(description=__doc__);s=parser.add_subparsers(dest='command',required=True)
 q=s.add_parser('pdf-pages');q.add_argument('--input',required=True);q.add_argument('--source-id',required=True);q.add_argument('--output',required=True)
 for name in ['extract-html','fetch-body']:
  q=s.add_parser(name);q.add_argument('--article',required=True);q.add_argument('--output',required=True)
  if name=='extract-html':q.add_argument('--html',required=True)
 for name in ['validate-relations','graph']:
  q=s.add_parser(name);q.add_argument('--input',required=True);q.add_argument('--output',required=True)
  if name=='graph':q.add_argument('--namespace',required=True)
 q=s.add_parser('load');q.add_argument('--input',required=True);q.add_argument('--output',required=True);q.add_argument('--uri',default='bolt://localhost:7687');q.add_argument('--user',default='neo4j');q.add_argument('--database',default='neo4j')
 args=parser.parse_args()
 # Fail before network / DB work if a target output already exists.
 if Path(args.output).exists():parser.error('Output exists; choose a new path')
 if args.command=='pdf-pages':pdf_pages(args)
 elif args.command in ['extract-html','fetch-body']:body(args,args.command=='fetch-body')
 elif args.command=='validate-relations':write(args.output,compile_annotations(read(args.input)))
 elif args.command=='graph':write(args.output,graph(read(args.input),args.namespace))
 elif args.command=='load':load(args)
if __name__=='__main__':main()
