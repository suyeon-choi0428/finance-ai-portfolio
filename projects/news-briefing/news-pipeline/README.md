# 뉴스 수집 및 Neo4j 관계 조회 파일럿

금융 뉴스 브리핑 팀 프로젝트에서 수행한 뉴스 수집·본문 추출 및 뉴스·공시 기반 관계 구조화 작업을 설명하는 대표 코드입니다. 기존 2026-10-28 팀 전달용 보조 도구를 선별했습니다. 원본 전체 서비스나 기존 DB를 그대로 배포한 것은 아닙니다. 코드 작성·수정·검토 과정에서 AI 도구를 활용했습니다.

## 코드 바로 보기

- [RSS 수집 코드](portable/rss_core.py): 피드 목록, 발행 시각 처리, URL 중복 제거, 누적 저장 및 수집 상태 기록
- [수집 실행 CLI](portable/rss_collect.py): 출력 경로·시각 선택과 오프라인/실제 수집 모드
- [본문 추출·관계 검증·그래프 변환·Neo4j 적재](portable/pipeline.py)
- [Neo4j 조회 Cypher](portable/queries.cypher): 기업 관계와 근거·원문 출처 및 미확인 후보 조회
- [오프라인 검증 코드](portable/test_offline.py)

## 구현 범위

### 뉴스 수집

CNBC 6개, Fox Business 4개, Fortune·The Motley Fool·Zacks·Business Insider 각 1개의 **14개 피드**를 사용하는 기존 설정입니다. Business Insider는 기존 Atom 피드 설정을 유지했습니다. Yahoo Finance·CNN Business는 피드 미확인 상태로 기록하며 다른 제공자로 대체하지 않습니다. 이 목록은 과거 설정이며 현재 모든 피드가 동작함을 보장하지 않습니다.

키워드 필터 없이 설정 매체의 피드를 읽고, 기사 `published` 기준 **종료 시각 직전 168시간 [시작, 종료)** 항목을 보존합니다. `updated`나 수집 시각을 발행 시각으로 대체하지 않습니다. 시간대가 없는 발행일은 임의로 UTC로 추정하지 않고 제외합니다. 매체 도메인 검사는 기사 주제 국가나 개별 기사의 출판 국가를 검증하는 기능은 아닙니다.

전체 피드를 받은 뒤 기간·도메인 조건으로 행을 거릅니다. 추적 파라미터를 제거한 URL로 중복을 관리하고, 최초·최근 관측 시각과 발행일 충돌을 기록합니다. RSS에 남아 있는 항목과 선택적으로 제공한 로컬 archive를 사용하므로 **7일 전체 기사 복원, 누락 없음, 기사 전문 확보를 보장하지 않습니다.**

### 본문과 공시

RSS 요약·피드 콘텐츠를 기사 전문과 구분합니다. 본문 추출은 기존 CNBC/Fool 소규모 파일럿의 selector 기반 보조 기능이며, 300자 이상이어도 `unreviewed` 상태입니다. 차단·로그인·광고·본문 누락을 사람이 검토해야 합니다. 이 보조 도구에는 원본의 trafilatura fallback을 포함하지 않았습니다. PDF 페이지 텍스트 추출은 지원하지만 OCR과 DART 자동 다운로드는 구현하지 않았습니다.

### Neo4j 관계 구조

이미 사람이거나 AI 도구의 도움으로 작성한 주석 JSON을 입력받아 근거 문장의 존재를 검사합니다. **새 기사에서 관계를 자동 추론하는 추출기가 아닙니다.** 문자열 포함 검사는 의미의 정확성·사실 여부·기업 동일성을 입증하지 않습니다.

기업·제품 등의 엔터티, Statement, Evidence, SourceDocument, Event와 미확인 Candidate를 구분합니다. 직접 연결의 관계 타입은 `CLAIM`이고 의미는 `predicate` 속성에 보존합니다. Statement → Evidence → SourceDocument로 관계의 근거와 출처를 조회합니다. 미확인 후보는 확정 관계와 분리하며 기간·계획/실적 등의 상태는 작성된 주석을 보존합니다.

새 보조 도구의 `handoff-*-v1` 형식은 과거 49행 파일럿과 다른 형식입니다. 기존 파일럿의 실제 DB 적재 이력을 이 보조 로더의 실제 적재 검증으로 해석하지 않습니다.

## 실행 환경

Python 3.11 이상. 이 폴더에서 실행합니다.

```bash
python -m pip install -r requirements-core.txt
python -m unittest discover -s portable -p test_offline.py -v
```

### 네트워크·DB 없이 실행

아래 입력은 **가상 뉴스·기업·공시**이며 투자 근거가 아닙니다. 매번 새 출력 경로를 사용하세요.

```bash
python portable/rss_collect.py --fixture examples/synthetic/feed.xml --end 2026-10-28T00:00:00+00:00 --output work/rss_fixture_01
python portable/pipeline.py extract-html --article examples/synthetic/article.json --html examples/synthetic/article.html --output work/body_fixture_01.json
python portable/pipeline.py pdf-pages --input examples/synthetic/disclosure.pdf --source-id SYN-PDF --output work/pdf_fixture_01.json
python portable/pipeline.py validate-relations --input examples/synthetic/annotations.json --output work/relations_fixture_01.json
python portable/pipeline.py graph --input work/relations_fixture_01.json --namespace team_semiconductor_demo_v1 --output work/graph_fixture_01.json
```

이 명령들은 자동으로 연결된 전체 파이프라인이 아닙니다. 본문·PDF를 검토해 관계 주석을 작성하는 단계는 별도로 필요합니다.

### 실제 RSS 수집 (선택)

```bash
python portable/rss_collect.py --collect --output work/rss_live_01
```

`--end`로 시간대가 있는 고정 종료 시각, `--archive`로 기존 누적 JSON을 지정할 수 있습니다. archive 입력은 읽기만 하고 새 출력에 병합합니다. 피드별 상태와 원본 XML, 최근 7일 JSON·CSV 및 실행 메타데이터를 새 폴더에 저장합니다. 이번 포트폴리오 정리에서는 실제 사이트에 수집 요청을 보내지 않았습니다.

### Neo4j 적재 (선택, DB에 쓰는 작업)

```bash
python -m pip install -r requirements-neo4j.txt
python portable/pipeline.py load --input work/graph_fixture_01.json --uri bolt://localhost:7687 --user neo4j --database neo4j --output work/load_check_01.json
```

개인 테스트 DB에서 실행하세요. 비밀번호는 실행 시 직접 입력하며 코드에 포함하지 않습니다. `HandoffPilot` 라벨과 namespace를 사용합니다. 같은 키의 기존 속성이 다르면 중단하고, 노드·관계 속성 확인 후 트랜잭션을 확정합니다. 고유 제약 생성은 별도 단계이므로 이후 작업이 실패해도 제약은 남을 수 있습니다. 이번 정리에서 DB에 접속하거나 이 보조 로더의 실제 적재를 검증하지 않았습니다.

Neo4j Browser에서 `portable/queries.cypher`의 namespace와 기업명을 적재한 예제에 맞춰 조회할 수 있습니다.

## 공개 자료 범위

실제 기사 전문·수집 archive·공시 원문·DB 덤프·접속 정보는 포함하지 않았습니다. 가상 예제와 코드만 제공합니다. 전체 종목 수집, 자동 관련성 판정, 자동 의미 관계 추출, 연속 운영 및 서비스 성능을 입증하는 결과는 아닙니다.

## 이번 정리에서 확인한 내용

기존 오프라인 테스트 5개가 모두 통과했습니다. 가상 입력으로 RSS, HTML 본문, PDF 페이지 추출, 주석 검증, 그래프 변환의 CLI 5단계를 별도 임시 폴더에서 실행했습니다. 실제 수집·DB 접속은 수행하지 않았습니다. `verification.json`에 검증 범위를 기록했습니다. RSS 본체는 기존 export의 문자열을 읽기 쉬운 별도 파일로 분리했으며 내용은 동일합니다.
