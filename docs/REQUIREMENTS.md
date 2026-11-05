# JEJU LifePath AI — 통합 요구사항 명세서
Version: 3.1 · Status: 설계 기준(미구현 사항은 완료로 간주하지 않음) · 2026-11-01

## 1. 목적 및 범위
제주빅데이터센터/제주데이터허브의 데이터 1종 이상을 실제 사용하여 비전문가가 자연어로 보행 경로, 생활편의 시설, 지역 인프라를 탐색·분석할 수 있는 AI Agent를 만든다. 핵심은 **하나의 보행도로 그래프 + 공통 도로구간 지표 + 하나의 통합 CA-MOR 경로 엔진**이다. 별도 안전/와이파이/이동약자 경로 엔진을 중복 개발하지 않는다.

대회 제출 시 기획서(15p 이내), 실행 소스·Prompt·Tool/API 정보, 데이터 출처·이용조건, 설치/사용 매뉴얼, 검증 결과와 한계가 필요하다. 센터 최소사양: Intel i7-8700, GTX 1060, RAM 32 GB. 제출 마감: 2026-11-20 10:00 KST.

### 기능 범위 및 우선순위
- P0: 자연어 질문과 조건 확인; 최단/안전환경우선/균형 경로; CCTV·조명·교통안전 공간지표(실제 확보된 데이터에 한함); 통행 제약; 공중화장실·약국 경로상 검색; 지도와 근거; 지역 시설 공백 분석; 데이터 출처·품질 표시.
- P1(데이터 확보 시): 범죄 지역 통계, 시간대별 유동인구, 기상·도로통제, 이동약자 상세 접근성, Wi-Fi AP 인접성 우선 경로.
- P2: 실측 Wi-Fi 연결 지속성 예측, 전역 검증 무장애 경로, 범죄 예측, 실시간 보행자 밀도.
- 금지: 실제 범죄 피해 확률·CCTV 실제 촬영 범위·Wi-Fi 무중단·미확인 휠체어 통행가능성을 보장하는 문구.

## 2. 15개 분석/최적화 책임 (하나의 공통 엔진)
| ID | 책임 | 결과 |
|---|---|---|
| A01 | 방범 CCTV 지리적 인접성 | cctv_index, provenance |
| A02 | 가로등·보안등 위치 인접성 | lighting_index |
| A03 | 범죄 통계의 지역 수준 맥락 | crime_context, spatial_resolution |
| A04 | 시간대·구역별 활동 수준 | activity_index, temporal_resolution |
| A05 | 보행자 사고 관련 노출 | traffic_exposure |
| A06 | 기상·도로 통제 | weather_exposure, closure_status |
| A07 | 계단·경사·단차 등 | accessibility(PASS/BLOCK/UNKNOWN) |
| B01 | Hard Constraint 필터 | valid edge set |
| B02 | CA-MOR 비용 기반 경로 최적화 | route |
| B03 | 경로 후보 비교 및 우회제한 | alternative routes |
| C01 | POI 검색 | 시설 목록 |
| C02 | 경로 주변 시설 접근성·우회거리 | route POIs |
| C03 | 필수 시설 경유 최적화 | waypoint route |
| D01 | Wi-Fi AP 인접성 기반 경로비교 | wifi_proximity_index |
| E01 | 구역별 시설 접근성 및 공백 분석 | area metrics |

C01~C03은 약국/화장실/Wi-Fi별 중복 시스템을 만들지 않고 category 필터로 공유한다. D01 역시 B02에 선택 지표를 추가하는 방식이다.

## 3. 공통 데이터 계약
모든 분석 결과는 동일한 보행도로 edge_id에 연결하고 좌표계, 단위 및 provenance를 고정한다.

```text
RoadEdge:
  edge_id: str
  source_node: str
  target_node: str
  geometry: LineString
  length_m: float > 0
  cctv_index: float[0,1] | null
  lighting_index: float[0,1] | null
  crime_context: float[0,1] | null
  activity_index: float[0,1] | null
  traffic_exposure: float[0,1] | null
  weather_exposure: float[0,1] | null
  accessibility: PASS | BLOCK | UNKNOWN
  closure_status: OPEN | CLOSED | UNKNOWN
  observed_at: datetime | null
  source_ids: list[str]
  resolution: {spatial: str|null, temporal: str|null}
  quality: {coverage: float|null, caveats: list[str]}
```

- NULL은 위험도 0이 아니다. 누락 지표 재정규화와 coverage 별도 출력.
- CCTV/조명 설치 위치는 실제 작동·촬영 범위·밝기와 다르다.
- 읍면동 범죄 건수/유동인구를 개별 골목 범죄 확률·실측 보행량으로 표시하지 않는다.
- 데이터 레지스트리에 원본명, 발행기관, URL, 수집일, 기준일, 라이선스, 원본 해시, 컬럼 매핑, 좌표계, 결측률 기록.
- 제주 공식 데이터 1종 이상 실제 확보/파싱/분석 증빙이 필수 게이트. 미확보 시 가상 데이터 시연은 DEMO로만 표시.

## 4. 통합 경로 알고리즘 CA-MOR
1. 출발·목적지 위치를 보행 네트워크에 스냅; 스냅거리·비연결 검증.
2. 각 구간의 통행 제약을 PASS/BLOCK/UNKNOWN으로 판정. 확인된 폐쇄·불가능 구간 BLOCK은 탐색 대상에서 반드시 제외.
3. 휠체어 등 엄격한 접근성 요청은 PASS로 확인된 구간만 먼저 탐색; 가능 경로가 없으면 검증 경로 부재를 고지. UNKNOWN은 무장애 보증 금지.
4. 가용 데이터로 지표를 산출하고 정규화하되, 타당성 없는 환경 인과 주장은 금지.
5. 경로 유형별 비용을 계산하여 후보 탐색. 예시: `cost(e,u,t) = length_m(e) * (1 + lambda(u) * q(e,u,t))`; q는 관측된 불리한 지표의 가중 결합이며 실제 범죄확률이 아니다. 모든 비용은 비음수.
6. 길이 제한: `length(route) <= (1+detour_ratio) * shortest_feasible_length`. Dijkstra만으로 길이상한을 포함한 최적해 보장 불가: constrained path 탐색 혹은 후보집합+상한 검증, 최적성 주장 시 별도 증명/검증.
7. 최단·안전환경 우선·균형 후보의 연결성, 제약 위반, 길이상한, 출처와 누락률 재검증.
8. 공공 와이파이는 AP 설치 위치 인접성만 측정하며 신호 세기/연결 지속성을 보장하지 않는다.
9. 지오코딩 실패, 미연결 그래프, 폐쇄정보 갱신 실패에는 명시적 실패/불확실성 상태를 반환한다.

## 5. 근거 기반 가중치 규정
- 논문 CCTV/조명 개선의 범죄감소 효과크기(예: 13%, 14%)를 직접 `w_cctv`, `w_lighting`으로 대입 금지. 설치 대상 선정의 역인과·선택편향 고려.
- 우선순위: (a) 제주에서 충분한 공간·시간·범죄유형·노출량 확보 시 공간/시간 분리 검증된 통계효과, (b) 국내외 재현 가능한 연구의 방향·제약, (c) 설명 가능한 잠정 지표 가중치 + 민감도 실험.
- 범죄 건수는 노출량·시간창·공간단위를 고려한다. 가능하면 Poisson / Negative Binomial, 잔차·공간 자기상관·오차·누수 검사. 데이터가 부족하면 위험 추정 회귀를 수행하지 않는다.
- 유동인구는 '사람이 많을수록 무조건 안전'으로 처리하지 않는다. 야간 고립 회피/혼잡 회피 사용자 목적에 따라 방향을 다르게 정의한다.
- 가중치 버전, 출처/DOI, 적용 지역, 데이터 기간, 추정/수동 여부, confidence 및 sensitivity report를 남긴다. 사용자 선호와 실증 위험 관련 변수 분리.
- 추천이 바뀌는 가중치 임계값, 데이터 누락 시 순위 변동, 경로 길이 변화 측정.

## 6. 대화형 Agent
- 의도: ROUTE, POI_NEARBY, POI_ON_ROUTE, AREA_GAP, REPLAN, EXPLAIN.
- 슬롯: origin, destination, departure_time, route_preference(SHORTEST/SAFETY/BALANCED), mobility_profile, constraints, detour_ratio, requested_poi.
- '동문시장 가고 싶어'처럼 경로 유형이 없으면 '안전환경 우선/최단/균형 중 무엇을 원하시나요?'를 질문. 출발지가 미확인일 때 위치 권한 또는 출발지 입력 요청.
- 사용자가 경로 유형을 명시하면 재질문하지 않는다. 후속 '10%만 돌아가'는 해당 슬롯만 수정, 기존 출발·목적지·접근성 조건 보존.
- LLM은 도구 선택·요청 해석·근거 설명. 도로 탐색/안전지표 수치는 결정론적 Python 모듈이 산출.
- 단일 Orchestrator + tool registry; 무작정 다중 LLM Agent 분할 금지.
- Tool 예시: resolve_place, retrieve_data, get_weather_closures, build_edge_features, find_routes, find_pois, route_via_poi, analyze_gap, validate_result.
- 도구 호출기록, 모델/API 명세, 실패 fallback, 입력 검증, 환각 방지 및 원본값 교차 확인.

## 7. 비기능 요구사항
- CPU 기반 핵심 경로 계산 작동; GTX 1060/RAM 32GB 센터 PC 기준 설치·실행 문서.
- 외부 API가 없으면 온라인 정보는 unavailable 처리; 사전에 검증된 로컬 스냅샷 모드 명시, 실시간 정보로 표시하지 않음.
- 개인정보·GPS 보관 최소화, 위치·API Key·인증정보를 로그 및 리포지토리에 포함 금지. env.example만 공개.
- 모든 결과에 데이터 기준일, 출처, 관측 가능 범위, 추정/미확인 표시.
- 프론트엔드(모든 Agent 응답 포함)에는 JSON, dict, 구간·노드 ID, enum 코드(SHORTEST/PASS 등), 내부 필드명 같은 내부값을 노출하지 않는다. 사용자에게는 자연어 문구와 지도·표 등 시각 요소만 보여준다. 내부 결과는 표시 계층에서 사용자 문구로 변환한다.
- "안전 보장 아님" 같은 공통 안내는 웹 시작 시 한 번만 쉬운 말로 보여주고, 경로 결과마다 반복하지 않는다. "대리변수" 같은 전문 용어는 화면에 쓰지 않는다.
- 예외처리, reproducible fixtures, 테스트 명령, dependency pinning, 라이선스/오픈소스 목록.

## 8. 인수 테스트
- 제주 공식 데이터 원본 ≥1종 사용 및 출처/라이선스/변환 흐름 입증.
- BLOCK/공식 폐쇄 구간을 포함하는 결과 0건.
- 휠체어 경로 PASS-only 동작; UNKNOWN 혼입 시 '검증 완료' 표시 0건.
- 가중치 변화/결측/유동인구 방향전환 테스트와 결과 재현.
- 우회율 상한 위반 0건, 경로 연결성 검증.
- 동일 질의·고정 데이터·동일 설정은 동일 결과.
- 미입력 경로 선호 질문/이미 입력된 조건 재질문 방지/대화 중 수정값만 변경.
- POI 직선거리가 아닌 네트워크 우회거리 계산.
- Wi-Fi 무중단 보장 문구 없음.
- 데이터·API 실패 시 오류와 마지막 검증 시점 표시.
- pytest, 정적 검사, 설치 재현, README, 시연 로그, 오픈소스/외부 데이터 명세 완료.

## 9. 단계별 개발 게이트
G0 저장소·스키마 → G1 공식 데이터 확보 → G2 보행 그래프 → G3 7종 특징 계산기 → G4 가중치 근거/검증 → G5 통행 제약 → G6 CA-MOR → G7 POI·Wi-Fi → G8 지역 공백 → G9 대화 Agent → G10 UI → G11 통합 검증 → G12 제출 패키지.

G1 미통과 시 공식 데이터 사용 완료를 주장하지 않는다. G0~G12 각 단계마다 변경 파일·테스트 결과·미해결 블로커를 기록한다.

## 10. 참고 출처(효과값은 가중치가 아님)
- CCTV: Piza et al. (2019), 40 years of CCTV crime prevention research, https://doi.org/10.1111/1745-9133.12419
- Street lighting: Welsh et al. (2022), street lighting systematic review, https://doi.org/10.1111/1745-9133.12585
- 제주 공식 데이터: https://www.jejudatahub.net/ ; https://www.data.go.kr/
- 보행 네트워크: https://osmnx.readthedocs.io/
- 대회 최종 제출 전 각 자료의 라이선스·기준일·원문 및 데이터 사용조건을 다시 검증.
