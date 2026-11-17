"""Independent synthetic fixtures only; no original outputs, network or DB."""
import copy,json,tempfile,unittest,importlib.util
from pathlib import Path
import pipeline
ROOT=Path(__file__).resolve().parents[1]
class OfflineTests(unittest.TestCase):
 def setUp(self):self.data=json.loads((ROOT/'examples/synthetic/annotations.json').read_text(encoding='utf-8'))
 def test_quote_and_period_preservation(self):
  result=pipeline.compile_annotations(self.data)
  self.assertEqual(len(result['relations']),3)
  self.assertEqual(result['relations'][0]['evidence_text'],self.data['annotations'][0]['evidence_text'])
  self.assertNotEqual(result['relations'][0]['report_period_end'],result['relations'][1]['report_period_end'])
 def test_reject_fabricated_evidence(self):
  self.data['annotations'][0]['evidence_text']='This sentence is absent.'
  with self.assertRaises(ValueError):pipeline.compile_annotations(self.data)
 def test_no_fixed_49_or_company_dependency(self):
  g=pipeline.graph(pipeline.compile_annotations(self.data),'synthetic_test')
  self.assertEqual(g['counts']['relations'],3);self.assertEqual(g['counts']['evidence'],3)
  self.assertEqual(g,pipeline.graph(pipeline.compile_annotations(self.data),'synthetic_test'))
  candidates={n['key'] for n in g['nodes'] if n['kind']=='Candidate'}
  self.assertTrue(candidates)
  self.assertTrue(all(e['start'] not in candidates and e['end'] not in candidates for e in g['edges']))
 def test_no_overwrite(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'x.json';pipeline.write(p,{'a':1})
   with self.assertRaises(FileExistsError):pipeline.write(p,{'a':2})
 @unittest.skipUnless(importlib.util.find_spec('bs4'),'beautifulsoup4 not in this runtime')
 def test_html_pending_review_and_block_rejection(self):
  article=json.loads((ROOT/'examples/synthetic/article.json').read_text(encoding='utf-8'))
  html=(ROOT/'examples/synthetic/article.html').read_text(encoding='utf-8')
  result=pipeline.html_extract(html,article)
  self.assertEqual(result['body_status'],'EXTRACTED_PENDING_REVIEW')
  self.assertEqual(result['summary'],article['summary'])
  self.assertEqual(result['feed_content'],article['feed_content'])
  self.assertFalse(result['full_text_verified'])
  block=(ROOT/'examples/synthetic/block.html').read_text(encoding='utf-8')
  self.assertEqual(pipeline.html_extract(block,article)['body_status'],'BLOCK_PAGE')
if __name__=='__main__':unittest.main()
