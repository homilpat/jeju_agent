**판정: 현재 POC는 실서비스 배포 전 수정이 필요합니다.** 로컬 CI의 두 404는 코드와 파일 구성으로 원인을 확인했습니다.

- **[F11·F1] `config.js` 404:** [index.html](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/poc/index.html:118>)은 페이지 기준 `config.js`를 요청해 CI에서 `/poc/config.js`가 됩니다. [server.py](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/server.py:75>)는 POC를 사이트 루트로 제공하면서 **`/config.js`만** 동적으로 생성하고 포트도 8000으로 고정합니다. CI의 4000번 서버에서는 이 동적 파일이 없습니다. **수정 방향:** CI도 `server.py`로 실행하고 `/`를 검사하거나, 서버와 페이지의 배포 경로를 하나로 맞추세요. 정적 서버를 쓴다면 키 없는 설정 파일 생성 방식을 별도로 정해야 합니다.

- **[F5·F1] `graph.js` 404:** [index.html](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/poc/index.html:120>)이 요청하는 파일이 현재 `poc/data/`에 없습니다. [build_demo_graph.py](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/scripts/build_demo_graph.py:31>)의 생성물이며 입력 OSM 파일도 현재 작업 폴더에 없습니다. **수정 방향:** OSM 수집과 그래프 생성을 CI·배포의 필수 단계로 두고, 생성물 존재와 노드·간선 수를 검사한 뒤 브라우저 검사를 시작하세요. 지금 화면은 누락 안내를 띄우지만 경로 기능은 동작하지 않습니다.

- **[F11·F3] 공개 서비스 비용·장애 위험:** [server.py](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/server.py:96>)의 `/api/chat`은 인증·요청 제한 없이 매 요청을 외부 LLM에 보내며 최대 30초 동안 스레드를 점유합니다. 동시 접속·중복 제출·재시도 때 지연과 과금이 커집니다. 요청 제한, 동시 호출 제한, 타임아웃과 실패 기록을 마련하고 전송 중 버튼을 잠그세요.

- **[F3·F4] 느린 응답과 경쟁 상태:** [app.js](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/poc/app.js:345>)에는 브라우저 측 타임아웃·로딩 표시·응답 순서 검사가 없습니다. 질문을 연속 전송하면 먼저 보낸 질문의 늦은 답이 나중 목적지를 덮을 수 있습니다. 요청 취소 또는 순번 검사와 명시적 실패 안내가 필요합니다. LLM 장애 시 규칙 모드로 조용히 전환되어 영어 지명 질문도 매칭되지 않습니다.

- **[F5·F6·F2] 잘못된 길 안내 가능성:** [app.js](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/poc/app.js:53>)은 출발지·목적지를 거리 상한 없이 가장 가까운 그래프 노드에 붙입니다. 그래프 밖 장소나 끊어진 보행로 근처에서도 실제 진입 가능 여부를 확인하지 않습니다. 특히 휠체어 `PASS` 판정은 [build_demo_graph.py](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/scripts/build_demo_graph.py:55>)의 가상값입니다. 실서비스에서는 진입 거리와 연결성을 검증하고, 검증 전 데이터로 통행 가능 경로처럼 안내하지 않아야 합니다.

- **[F7·F9] 시설 결과의 신뢰성:** [app.js](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/poc/app.js:290>)은 시설까지의 길 거리를 단순히 두 배로 해 왕복 우회 거리로 표시합니다. 일방 통행 가능 간선이나 실제 시설 진입점이 다르면 값이 틀립니다. 시설 데이터가 비면 [공백 분석](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/poc/app.js:311>)은 전 구역을 공백으로 칠합니다. 빈 데이터는 “분석 불가”로 구분하세요.

- **[F1·F5] 외부 자원·기기 폴백:** 카카오 SDK는 로드 오류 때만 SVG로 전환합니다([app.js](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/poc/app.js:165>)). 응답이 멈추면 초기화와 입력 버튼 연결도 멈춥니다. SDK 시간 제한을 두고 SVG로 전환해야 합니다. Unsplash 이미지([index.html](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/poc/index.html:95>))에는 실패 대체 화면이 없습니다. 경로 카드는 `role="button"`이지만 키보드 Enter·Space 선택 처리가 없어 접근성도 미완성입니다([app.js](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/poc/app.js:239>)).

- **[F10·F11] 입력·운영:** 금지 문구 필터([server.py](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/server.py:43>))는 정해진 표현만 치환하므로 변형 문구를 통과시킬 수 있습니다. 현재 사용자 문장은 `textContent`로 표시되어 해당 경로의 XSS 위험은 낮지만, 카카오 마커 HTML 문자열([app.js](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/poc/app.js:87>))에 들어갈 이름은 데이터 교체 시 이스케이프해야 합니다. 서버는 API 오류를 502로 반환하면서 실패 원인·호출 시간 로그나 모니터링을 남기지 않아 운영자가 장애를 구분하기 어렵습니다.

`DECISIONS.md`는 작업 폴더에서 찾지 못해 [REQUIREMENTS_NOTES.md](<C:/Users/user/Desktop/통합 AIagent cli 관리/projects/default/REQUIREMENTS_NOTES.md>)의 합의 내용을 기준으로 비교했습니다. 파일은 수정하지 않았고, 브라우저 재실행은 하지 않았습니다.