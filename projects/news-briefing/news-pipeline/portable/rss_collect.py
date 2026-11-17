"""Export of original RSS cells; same feeds and rolling 168h publication window.
New output directory required. No network unless --collect. --fixture is offline.
"""
import argparse,json,shutil
from pathlib import Path
from datetime import datetime,timezone
SOURCE_CODE = Path(__file__).with_name("rss_core.py").read_text(encoding="utf-8")
def main():
 p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--archive');p.add_argument('--end');g=p.add_mutually_exclusive_group(required=True);g.add_argument('--collect',action='store_true');g.add_argument('--fixture');args=p.parse_args()
 dest=Path(args.output)
 if dest.exists():p.error('Output exists; use a new folder')
 end=datetime.fromisoformat(args.end.replace('Z','+00:00')) if args.end else datetime.now(timezone.utc)
 if end.tzinfo is None:p.error('--end must include timezone')
 dest.mkdir(parents=True)
 if args.archive:shutil.copyfile(args.archive,dest/'news_archive.json')
 source=SOURCE_CODE.replace('OUT = Path("data/news_rss")','OUT = Path('+repr(str(dest))+')').replace('END = datetime.now(UTC)','END = datetime.fromisoformat('+repr(end.isoformat())+')')
 if args.fixture:
  import requests
  original_get=requests.get
  class Response:
   status_code=200
   def __init__(self,url):self.url=url;self.content=Path(args.fixture).read_bytes()
   def raise_for_status(self):pass
  requests.get=lambda url,**kwargs:Response(url)
  try:exec(compile(source,'rss_notebook_export','exec'),{})
  finally:requests.get=original_get
 else:exec(compile(source,'rss_notebook_export','exec'),{})
if __name__=='__main__':main()
