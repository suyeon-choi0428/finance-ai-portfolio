// New handoff schema, not the old NewsPilot graph. Browser parameters:
:param ns => 'team_semiconductor_demo_v1';
:param company => 'SYNTHETIC Alpha Devices';

MATCH (a:HandoffCompany {namespace:$ns,name:$company})-[r:CLAIM]-(b:HandoffPilot)
MATCH (s:HandoffStatement {key:r.statement_key})-[:HAS_EVIDENCE]->(e:HandoffEvidence)-[:FROM_DOCUMENT]->(d:HandoffSourceDocument)
RETURN startNode(r).name,r.predicate,endNode(r).name,r.status,r.origin,
 s.record_json,e.text,e.pdf_page,d.source_record_json;

MATCH p=(a:HandoffCompany {namespace:$ns,name:$company})-[:CLAIM*1..3]-(b:HandoffPilot)
WHERE all(n IN nodes(p) WHERE n.namespace=$ns)
 AND any(r IN relationships(p) WHERE r.origin='news')
RETURN p;

MATCH (c:HandoffCandidate {namespace:$ns}) RETURN c.record_json,c.missing_evidence;
