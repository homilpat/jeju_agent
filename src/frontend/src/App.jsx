import { useEffect, useRef, useState } from "react";
import { api, crimeOverlayUrl } from "./api";
import { MSG_MAP_FAILED, MSG_SEARCH_FAILED, loadKakao, resolvePlace, searchPlaces } from "./kakao";

// 피그마 메이크 디자인(제주 안전 여행 플래너)에서 옮김. 화면 데이터는 아직 예시값이며 API 연결 시 바꾼다.

// 제주 본섬 실제 해안선: 통계청 2013 시도 경계(github.com/southkorea/southkorea-maps)를 24칸에 맞춰 줄임
const JEJU_PATH = "M18.5 5.7L19.0 6.0L19.8 5.9L19.9 6.4L20.7 7.1L21.1 6.9L21.9 7.1L22.3 7.9L22.0 8.4L22.2 8.9L22.9 8.9L23.0 9.2L22.6 9.6L22.1 11.1L22.1 11.6L21.6 11.8L21.1 12.3L21.0 12.9L20.3 13.6L20.4 13.9L20.1 14.4L19.3 14.7L18.4 14.5L17.5 15.5L17.1 15.4L16.0 15.8L15.1 15.7L14.6 15.9L14.0 16.7L13.4 16.9L13.0 16.6L12.0 16.8L11.2 16.7L10.9 17.1L10.3 17.1L9.8 17.3L9.2 16.7L8.7 17.0L8.1 16.6L7.3 17.0L5.5 16.8L4.9 17.3L4.8 17.9L4.2 18.3L3.5 17.7L3.1 16.9L2.1 16.5L1.6 16.1L1.0 15.0L1.2 14.8L1.1 13.9L1.2 13.2L1.9 12.5L2.5 12.1L2.8 11.6L3.2 11.5L3.8 10.8L3.8 10.2L5.1 9.6L5.2 9.1L6.0 9.1L7.4 8.3L7.6 8.5L10.0 7.6L10.4 7.2L11.0 7.4L11.7 6.9L12.0 7.3L12.9 7.1L13.3 6.7L14.3 6.7L14.6 6.1L15.1 6.2L16.7 6.1L17.0 5.9L17.8 6.0L18.5 5.7Z";

function Icon({ name, size = 20, strokeWidth = 2 }) {
  const paths = {
    arrow: (
      <>
        <path d="M5 12h14" />
        <path d="m14 7 5 5-5 5" />
      </>
    ),
    back: (
      <>
        <path d="m15 18-6-6 6-6" />
      </>
    ),
    calendar: (
      <>
        <rect x="3" y="5" width="18" height="16" rx="2" />
        <path d="M16 3v4M8 3v4M3 10h18" />
      </>
    ),
    camera: (
      <>
        <path d="M14.5 4 16 7h3a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h3l1.5-3z" />
        <circle cx="12" cy="13" r="3" />
      </>
    ),
    check: <path d="m5 12 4 4L19 6" />,
    chevron: <path d="m9 18 6-6-6-6" />,
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    close: (
      <>
        <path d="m6 6 12 12M18 6 6 18" />
      </>
    ),
    drag: (
      <>
        <circle cx="9" cy="7" r=".8" fill="currentColor" stroke="none" />
        <circle cx="15" cy="7" r=".8" fill="currentColor" stroke="none" />
        <circle cx="9" cy="12" r=".8" fill="currentColor" stroke="none" />
        <circle cx="15" cy="12" r=".8" fill="currentColor" stroke="none" />
        <circle cx="9" cy="17" r=".8" fill="currentColor" stroke="none" />
        <circle cx="15" cy="17" r=".8" fill="currentColor" stroke="none" />
      </>
    ),
    food: (
      <>
        <path d="M7 3v8M4 3v5a3 3 0 0 0 6 0V3M7 11v10M16 3v18M16 3c3 2 4 5 4 8h-4" />
      </>
    ),
    // 점은 한라산
    island: (
      <>
        <path d={JEJU_PATH} />
        <circle cx="11.5" cy="12.5" r="1" fill="currentColor" stroke="none" />
      </>
    ),
    lamp: (
      <>
        <path d="M9 21h6M12 17v4M7 17h10l-2-8H9zM12 3v6" />
      </>
    ),
    location: (
      <>
        <path d="M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z" />
        <circle cx="12" cy="10" r="2.5" />
      </>
    ),
    map: (
      <>
        <path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3z" />
        <path d="M9 3v15M15 6v15" />
      </>
    ),
    menu: (
      <>
        <circle cx="12" cy="5" r="1" fill="currentColor" stroke="none" />
        <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
        <circle cx="12" cy="19" r="1" fill="currentColor" stroke="none" />
      </>
    ),
    plus: (
      <>
        <path d="M12 5v14M5 12h14" />
      </>
    ),
    refresh: (
      <>
        <path d="M20 7v5h-5" />
        <path d="M4 17v-5h5" />
        <path d="M6.1 8A7 7 0 0 1 18 6l2 6M17.9 16A7 7 0 0 1 6 18l-2-6" />
      </>
    ),
    search: (
      <>
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-4-4" />
      </>
    ),
    send: (
      <>
        <path d="m22 2-7 20-4-9-9-4z" />
        <path d="M22 2 11 13" />
      </>
    ),
    shield: (
      <>
        <path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z" />
        <path d="m9 12 2 2 4-5" />
      </>
    ),
    spark: (
      <>
        <path d="m12 3 1.3 4.2L17 9l-3.7 1.8L12 15l-1.3-4.2L7 9l3.7-1.8z" />
        <path d="m19 15 .7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7z" />
      </>
    ),
    trash: (
      <>
        <path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6" />
      </>
    ),
    user: (
      <>
        <circle cx="12" cy="8" r="4" />
        <path d="M4 21a8 8 0 0 1 16 0" />
      </>
    ),
    walk: (
      <>
        <circle cx="13" cy="4" r="2" />
        <path d="m10 21 2-7-3-3 2-4 4 3 3 1M12 14l4 3 1 4M9 11l-3 4" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}


// 카카오 지도에 장소 핀을 일정 번호와 함께 찍는다. 경로 선은 경로 계산을 붙인 뒤에 그린다.
// points: [{ key, number, name, lat, lon }], lines: [{ key, kind, path: [[lat, lon], ...] }]
// crime: 사용자가 "범죄주의구간 표시"에 네라고 했을 때만 생활안전지도 그림을 겹친다
// onView: 지도를 움직일 때마다 지금 보이는 범위 {center, south, west, north, east}를 알려 준다
function KakaoMap({ points, lines = [], crime = false, onView }) {
  const box = useRef(null);
  const viewCallback = useRef(onView);
  viewCallback.current = onView;
  const map = useRef(null);
  const overlays = useRef([]);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [crimeError, setCrimeError] = useState("");
  const pointsKey =
    points.map((p) => [p.key, p.number, p.lat, p.lon, p.name].join(":")).join("|") +
    "#" +
    lines.map((l) => `${l.key}:${l.kind}:${l.path.join(";")}`).join("|");

  useEffect(() => {
    let alive = true;
    loadKakao()
      .then((kakao) => {
        if (!alive || !box.current) return;
        setError("");
        // 지도는 처음 성공했을 때 만든다(앞서 불러오기에 실패했어도 다음 변경 때 다시 시도)
        map.current ??= new kakao.maps.Map(box.current, {
          center: new kakao.maps.LatLng(33.38, 126.55), // 제주도 가운데쯤
          level: 10,
        });
        setReady(true);
        overlays.current.forEach((o) => o.setMap(null));
        const bounds = new kakao.maps.LatLngBounds();
        const css = getComputedStyle(document.documentElement);
        const drawn = lines.map((l) => {
          const path = l.path.map(([lat, lon]) => new kakao.maps.LatLng(lat, lon));
          path.forEach((at) => bounds.extend(at));
          const style = ROUTE_LINE_STYLE[l.kind];
          return new kakao.maps.Polyline({
            map: map.current,
            path,
            strokeWeight: style.weight,
            strokeColor: css.getPropertyValue(style.color).trim(),
            strokeStyle: style.dash,
            strokeOpacity: 0.9,
          });
        });
        overlays.current = points.map((p) => {
          const at = new kakao.maps.LatLng(p.lat, p.lon);
          bounds.extend(at);
          const pin = document.createElement("span");
          pin.className = "map-pin kakao-pin";
          pin.appendChild(document.createElement("b")).textContent = String(p.number);
          pin.title = p.name;
          return new kakao.maps.CustomOverlay({ map: map.current, position: at, content: pin });
        });
        overlays.current.push(...drawn);
        if (points.length === 1 && lines.length === 0) map.current.setCenter(bounds.getSouthWest());
        else if (points.length + lines.length > 1) map.current.setBounds(bounds);
      })
      .catch(() => alive && setError(MSG_MAP_FAILED));
    return () => {
      alive = false;
    };
  }, [pointsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!ready) return undefined;
    const kakao = window.kakao;
    const m = map.current;
    const report = () => {
      const b = m.getBounds();
      const [sw, ne, c] = [b.getSouthWest(), b.getNorthEast(), m.getCenter()];
      viewCallback.current?.({
        center: { lat: c.getLat(), lon: c.getLng() },
        south: sw.getLat(),
        west: sw.getLng(),
        north: ne.getLat(),
        east: ne.getLng(),
      });
    };
    report();
    kakao.maps.event.addListener(m, "idle", report);
    return () => kakao.maps.event.removeListener(m, "idle", report);
  }, [ready]);

  // 지도를 멈출 때마다 지금 보이는 사각형만큼 그림 한 장을 받아 화면 가운데에 맞춰 붙인다.
  // 카카오 지도와 같은 좌표계로 받으므로 늘이거나 줄이지 않고 그대로 맞는다
  useEffect(() => {
    if (!crime || !ready) return undefined;
    const kakao = window.kakao;
    const m = map.current;
    // 가운데를 기준점으로 붙인다: 왼쪽 위 꼭짓점에 붙이면 크게 확대했을 때 카카오 지도가
    // 기준점이 화면 밖이라고 보고 그림을 숨기는 것으로 보임(확대하면 사라지는 문제)
    const overlay = new kakao.maps.CustomOverlay({ xAnchor: 0.5, yAnchor: 0.5, zIndex: 0 });
    let latest = 0; // 늦게 온 예전 그림이 새 그림을 덮지 않게
    const draw = () => {
      const bounds = m.getBounds();
      const [sw, ne] = [bounds.getSouthWest(), bounds.getNorthEast()];
      const [width, height] = [box.current.clientWidth, box.current.clientHeight];
      const metersPerPx =
        ((ne.getLng() - sw.getLng()) * 111320 * Math.cos((((sw.getLat() + ne.getLat()) / 2) * Math.PI) / 180)) / width;
      if (metersPerPx < CRIME_MIN_M_PER_PX) {
        latest += 1; // 받는 중인 그림이 뒤늦게 붙지 않게
        overlay.setMap(null);
        setCrimeError(MSG_CRIME_ZOOMED);
        return;
      }
      // 화면 가운데(지도 좌표계 기준). 그림도 같은 좌표계라 가운데끼리 맞추면 전체가 맞는다
      const middle = m.getProjection().coordsFromContainerPoint(new kakao.maps.Point(width / 2, height / 2));
      const img = new Image(width, height);
      img.className = "crime-overlay";
      // 지도와 똑같은 픽셀 크기로 고정한다. 기본 CSS(img max-width:100%, height:auto)가 너비 없는
      // 카카오 겹침 칸 안에서 그림을 줄여 길과 안 맞거나 안 보였음(2026-11-19 실제 앱에서 0×0으로 측정)
      img.style.width = `${width}px`;
      img.style.height = `${height}px`;
      img.alt = "";
      const mine = ++latest;
      img.onload = () => {
        if (mine !== latest) return;
        overlay.setContent(img);
        overlay.setPosition(middle);
        overlay.setMap(m);
        setCrimeError("");
      };
      img.onerror = () => {
        if (mine !== latest) return;
        overlay.setMap(null);
        setCrimeError(MSG_CRIME_FAILED);
      };
      img.src = crimeOverlayUrl({
        west: sw.getLng(),
        south: sw.getLat(),
        east: ne.getLng(),
        north: ne.getLat(),
        width,
        height,
      });
    };
    // 확대·축소가 시작되면 바로 숨긴다: 예전 그림은 크기가 그대로라 바뀌는 동안 길과 어긋나 보임.
    // 받는 중인 그림도 버리고, 확대가 끝나면(idle) 새 크기로 다시 그린다
    const hide = () => {
      latest += 1;
      overlay.setMap(null);
    };
    draw();
    kakao.maps.event.addListener(m, "idle", draw);
    kakao.maps.event.addListener(m, "zoom_start", hide);
    return () => {
      latest += 1;
      kakao.maps.event.removeListener(m, "idle", draw);
      kakao.maps.event.removeListener(m, "zoom_start", hide);
      overlay.setMap(null);
      setCrimeError("");
    };
  }, [crime, ready]);

  return (
    <div className="kakao-map-wrap">
      <div ref={box} className="kakao-map" aria-label="지도" />
      {error && <div className="map-placeholder map-message">{error}</div>}
      {crimeError && <p className="map-note">{crimeError}</p>}
    </div>
  );
}

const MSG_CRIME_FAILED = "범죄주의구간 지도를 불러오지 못했어요. 지도를 조금 움직이면 다시 시도해요.";
const MSG_CRIME_ZOOMED = "많이 확대하면 지도의 길과 몇 m 어긋나서 범죄주의구간 표시를 숨겼어요. 조금 축소하면 다시 보여요.";
// 이보다 자세히(1픽셀이 0.75m 미만) 보면 숨긴다 = 카카오 확대 1·2단계(0.25·0.5m)는 숨기고 3단계(약 1m)부터 보인다.
// 2026-11-19 제주시청·노형·서귀포 세 곳을 1~4단계로 찍어 비교: 1단계에서는 안전지도의 실제 도로 폭이
// 카카오 지도의 다듬은 길보다 넓어 건물을 덮었고 2단계부터는 맞았다. 표본이 세 곳이라 2단계도 숨겨 여유를 둠
// (사용자 결정, open-decisions)
const CRIME_MIN_M_PER_PX = 0.75;

// 범죄주의구간 표시 여부를 먼저 묻고, 네라고 하면 표시한다. 색 설명·출처를 함께 보여준다
function CrimeToggle({ value, onChange }) {
  if (value === null) {
    return (
      <div className="map-ask">
        <p>범죄주의구간을 지도에 표시할까요?</p>
        <div>
          <button onClick={() => onChange(true)}>네</button>
          <button onClick={() => onChange(false)}>아니요</button>
        </div>
      </div>
    );
  }
  if (!value) {
    return (
      <button className="map-ask map-ask-small" onClick={() => onChange(true)}>
        범죄주의구간 보기
      </button>
    );
  }
  return (
    <div className="map-ask">
      <p>
        <span className="crime-legend" /> 색이 진할수록 지난 1년 동안 범죄가 상대적으로 많이 난 길이에요. 지금 위험하다는
        뜻은 아니에요.
      </p>
      <small>출처: 생활안전지도</small>
      <div>
        <button onClick={() => onChange(false)}>숨기기</button>
      </div>
    </div>
  );
}

function AppHeader({ title, back, action }) {
  return (
    <header className="app-header">
      {back ? (
        <button className="icon-button" onClick={back} aria-label="뒤로 가기">
          <Icon name="back" size={24} />
        </button>
      ) : (
        <span className="header-spacer" />
      )}
      <h1>{title}</h1>
      <div className="header-action">{action || <span className="header-spacer" />}</div>
    </header>
  );
}

function Welcome({ onStart }) {
  return (
    <main className="welcome-screen">
      <div className="welcome-art">
        <div className="sun" />
        <div className="cloud cloud-a" />
        <div className="cloud cloud-b" />
        <div className="island-shape">
          <svg viewBox="0.5 5.2 23 13.6" aria-hidden="true">
            <path d={JEJU_PATH} fill="#bdd5c1" />
          </svg>
          {/* 섬을 빚은 설문대할망이 한라산 자리에 앉아 맞이한다 */}
          <img className="island-halmang" src="/halmang/welcome-cut.webp" alt="두 팔 벌려 반기는 설문대할망" />
        </div>
        <span className="path-dot dot-a" />
        <span className="path-dot dot-b" />
        <span className="path-dot dot-c" />
        <span className="welcome-pin">
          <Icon name="location" size={28} />
        </span>
      </div>
      <div className="welcome-copy">
        <span className="eyebrow">
          <Icon name="shield" size={17} /> 안심하고 걷는 제주
        </span>
        <h1>
          제주 하루,
          <br />
          <em>더 편안한 길</em>로
        </h1>
        <p>
          여행 계획부터 안전한 보행 길 안내까지
          <br />한 번에 도와드려요.
        </p>
      </div>
      <div className="welcome-bottom">
        <div className="notice">
          <Icon name="shield" size={20} />
          <span>
            안전 정보는 공공데이터 기준이며
            <br />
            실제와 다를 수 있어요. 공사·통제 정보는 반영하지 못했어요.
          </span>
        </div>
        <button className="primary-button" onClick={onStart}>
          시작하기 <Icon name="arrow" />
        </button>
      </div>
    </main>
  );
}

function FormError({ message }) {
  return message ? (
    <p className="form-error" role="alert">
      {message}
    </p>
  ) : null;
}

function Auth({ onLogin }) {
  const [signup, setSignup] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <main className="auth-screen">
      <div className="auth-island" aria-hidden="true">
        <svg viewBox="0.5 5.2 23 13.6">
          <path d={JEJU_PATH} />
        </svg>
        <img src="/halmang/welcome-cut.webp" alt="" />
      </div>
      <div className="brand-mark">
        <Icon name="island" size={34} />
      </div>
      <div className="auth-heading">
        <span className="eyebrow">제주여지도</span>
        <h1>{signup ? "반가워요!" : "다시 만나 반가워요"}</h1>
        <p>
          {signup ? "간단히 가입하고 여행을 만들어 보세요." : "내 여행을 이어서 준비해 볼까요?"}
        </p>
      </div>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setError("");
          try {
            const user = await (signup ? api.signup : api.login)(username, password);
            onLogin(user.username);
          } catch (e) {
            setError(e.message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          아이디
          <input
            type="text"
            placeholder="아이디를 입력해 주세요"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        <label>
          비밀번호
          <input
            type="password"
            placeholder="비밀번호를 입력해 주세요"
            autoComplete={signup ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <FormError message={error} />
        <button className="primary-button" type="submit" disabled={busy}>
          {signup ? "회원가입" : "로그인"}
        </button>
      </form>
      <button
        className="text-button"
        onClick={() => {
          setSignup(!signup);
          setError("");
        }}
      >
        {signup ? "이미 계정이 있나요? 로그인" : "처음 오셨나요? 회원가입"}
      </button>
    </main>
  );
}

const ROUTE_LABELS = { SHORTEST: "가장 빠른 길", BALANCED: "균형", SAFETY: "안전한 길" };
const ROUTE_ORDER = ["SHORTEST", "BALANCED", "SAFETY"];
const DEFAULT_ROUTE = "SAFETY";
// 경로 비교 창의 선 견본(.line-solid / .line-dotted / .line-thick)과 같은 모양
const ROUTE_LINE_STYLE = {
  SHORTEST: { color: "--orange", weight: 4, dash: "solid" },
  BALANCED: { color: "--sea-600", weight: 4, dash: "shortdot" },
  SAFETY: { color: "--sea-900", weight: 7, dash: "solid" },
  // 무장애 여행지에서 조사자가 실제로 걸은 코스(GPS, 제주데이터허브)
  ACCESS: { color: "--access-green", weight: 6, dash: "solid" },
};
const MAX_PLACES = 30; // 서버 상한과 같게(api/app.py)

function formatDates(start, end) {
  const fmt = (d) => {
    const [, m, day] = d.split("-").map(Number);
    return `${m}월 ${day}일`;
  };
  if (!start) return "날짜 미정";
  return end && end !== start ? `${fmt(start)} – ${fmt(end)}` : fmt(start);
}

function todayLocal() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function isPast(planner) {
  const last = planner.end_date || planner.start_date;
  return Boolean(last) && last < todayLocal();
}

// onChat(id, fresh): 그 여행을 AI 길잡이로 가져가 지도에 경로를 그리고 같이 이야기한다.
// fresh면 지금 대화는 지난 대화에 남겨 두고 새 대화에서 연다
function Planners({ onOpen, onChat, onLogout, onAccountDeleted }) {
  const [asking, setAsking] = useState(null); // 이어서/새 대화를 고르는 중인 여행
  const [menu, setMenu] = useState(false);
  const [planners, setPlanners] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .listPlanners()
      .then(setPlanners)
      .catch((e) => setError(e.message));
  }, []);

  const featuredId = planners?.find((p) => !isPast(p))?.id;
  return (
    <main className="standard-screen planners-screen">
      <AppHeader
        title="내 여행"
        action={
          <button className="icon-button" onClick={() => setMenu(!menu)} aria-label="계정 메뉴">
            <Icon name="menu" size={24} />
          </button>
        }
      />
      {menu && (
        <div className="account-menu">
          <button onClick={onLogout}>
            <Icon name="arrow" size={18} /> 로그아웃
          </button>
          <button
            className="danger"
            onClick={async () => {
              if (!window.confirm("계정을 지우면 모든 여행 계획도 함께 지워져요. 지울까요?")) return;
              try {
                await api.deleteAccount();
                onAccountDeleted();
              } catch (e) {
                setError(e.message);
              }
            }}
          >
            <Icon name="trash" size={18} /> 계정 삭제
          </button>
        </div>
      )}
      <section className="planner-content">
        <div className="section-heading">
          <div>
            <span className="eyebrow">내 여행 계획</span>
            <h2>어디로 떠날까요?</h2>
          </div>
        </div>
        <FormError message={error} />
        {planners?.length === 0 && (
          <p className="empty-note">아직 저장한 여행이 없어요. AI 길잡이에게 일정을 부탁하고 저장해 보세요.</p>
        )}
        {planners?.map((p) => (
          <div className="planner-card-wrap" key={p.id}>
          <button
            className={`planner-card ${p.id === featuredId ? "featured" : ""}`}
            onClick={() => onOpen(p.id)}
          >
            <div className="card-top">
              <span className={`trip-status ${isPast(p) ? "past" : ""}`}>
                <span /> {isPast(p) ? "지난 여행" : "준비 중"}
              </span>
              <Icon name="chevron" size={22} />
            </div>
            <h3>{p.title}</h3>
            <div className="trip-details">
              <span>
                <Icon name="calendar" size={18} /> {formatDates(p.start_date, p.end_date)}
              </span>
              <span>
                <Icon name="location" size={18} /> 장소 {p.place_count}곳
              </span>
            </div>
          </button>
          {asking === p.id ? (
            <div className="planner-ai planner-ai-choice" role="group" aria-label="어느 대화에서 볼까요?">
              <button onClick={() => onChat(p.id, false)}>지금 대화에 이어서</button>
              <button onClick={() => onChat(p.id, true)}>새 대화로</button>
              <button onClick={() => setAsking(null)} aria-label="닫기">
                <Icon name="close" size={14} />
              </button>
            </div>
          ) : (
            <button
              className="planner-ai"
              // 이어 갈 대화가 없으면 묻지 않고 바로 연다
              onClick={() => (readConversation(CONV_KEY) ? setAsking(p.id) : onChat(p.id, true))}
              aria-label={`${p.title} 여행을 AI 길잡이에서 보기`}
            >
              <Icon name="spark" size={14} /> AI 길잡이에서 보기
            </button>
          )}
          </div>
        ))}
      </section>
    </main>
  );
}

// 플래너에 장소를 더했을 때 할망 말풍선: 앞 장소에서 그 구간에 고른 경로로 걸어서 몇 분.
// 고른 경로가 없으면 다른 경로로 바꿔 말하지 않고, 계산 실패(error)와 길 없음을 나눠 말한다
function DistanceBubble({ plan, error, kind, onClose }) {
  const route = plan?.routes.find((r) => r.kind === kind);
  const text = error
    ? `앞 장소에서 여기까지 길을 계산하지 못했구나. ${error}`
    : route?.found
      ? `앞 장소에서 여기까지 ${route.title}로 걸어서 약 ${Math.round(route.minutes)}분이란다.`
      : `앞 장소에서 여기까지 ${route?.title || "고른 길"}은 찾지 못했구나. 아래 구간에서 다른 길과 이유를 볼 수 있단다.`;
  return (
    <div className="distance-bubble" role="status">
      <HalmangFace />
      <p>{text}</p>
      <button className="icon-button" onClick={onClose} aria-label="말풍선 닫기">
        <Icon name="close" size={18} />
      </button>
    </div>
  );
}

const ORDINALS = ["첫", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉", "열"];

function RouteCard({ index, active, onChoose, onOpen }) {
  return (
    <div className="route-card">
      <button className="route-card-top" onClick={onOpen}>
        <span className="route-title">
          <Icon name="walk" size={18} />{" "}
          {index < ORDINALS.length ? `${ORDINALS[index]} 번째 구간` : `${index + 1}번째 구간`}
        </span>
        <span className="route-summary">
          경로 비교 <Icon name="chevron" size={16} />
        </span>
      </button>
      <div className="route-options" role="radiogroup" aria-label="경로 종류">
        {ROUTE_ORDER.map((key) => (
          <button
            key={key}
            role="radio"
            aria-checked={key === active}
            className={key === active ? "active" : ""}
            onClick={() => onChoose(key)}
          >
            {key === "SAFETY" && <Icon name="shield" size={14} />}
            {ROUTE_LABELS[key]}
          </button>
        ))}
      </div>
    </div>
  );
}

// 저장된 여행 -> 지도에 그릴 장소와 고른 경로선. 장소는 열 때마다 다시 찾고 경로도 다시 계산한다
// (카카오 좌표·가공 자료는 저장하지 않음). 플래너와 AI 길잡이가 같이 쓴다
function useTripMap(planner, plannerId) {
  // 카카오 장소 ID -> 지금 검색한 결과(메모리에만). null은 검색은 됐는데 못 찾은 곳
  const [coords, setCoords] = useState({});
  const [lookupFailed, setLookupFailed] = useState(false);
  // 구간(앞 장소>뒤 장소) -> 서버가 계산한 세 경로. 저장하지 않고 열 때마다 다시 계산한다
  const [plans, setPlans] = useState({});
  const [planErrors, setPlanErrors] = useState({});
  const inflight = useRef(new Set());
  useEffect(() => {
    // 다른 여행으로 바뀌면 앞 여행의 위치·경로를 버린다
    setCoords({});
    setPlans({});
    setPlanErrors({});
    setLookupFailed(false);
  }, [plannerId]);

  // 저장된 장소는 ID·이름뿐이라 열 때마다 다시 검색해 좌표를 얻는다.
  // AI가 고른 장소는 서버가 만들 때와 같은 기준(기준점 둘레 거리순)으로, 직접 담은 장소는 담을 때처럼 이름으로.
  // 검색 자체가 실패한 곳은 기록하지 않아 다음 변경 때 다시 시도한다.
  const aiRefs = new Set((planner?.agent?.stops || []).map((s) => s.ref));
  useEffect(() => {
    if (!planner || !aiRefs.size) return;
    api
      .locatedPlaces(plannerId)
      .then(({ places }) =>
        setCoords((c) => {
          const next = { ...c };
          planner.places.forEach((p) => aiRefs.has(p.ref) && !(p.ref in next) && (next[p.ref] = places[p.ref] || null));
          return next;
        }),
      )
      .catch(() => setLookupFailed(true));
  }, [planner?.agent, plannerId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const todo = (planner?.places || []).filter(
      (p) => p.source === "kakao" && !aiRefs.has(p.ref) && !(p.ref in coords),
    );
    if (todo.length === 0) return;
    Promise.allSettled(todo.map(resolvePlace)).then((results) => {
      setCoords((c) => {
        const next = { ...c };
        results.forEach((r, i) => r.status === "fulfilled" && (next[todo[i].ref] = r.value));
        return next;
      });
      setLookupFailed(results.some((r) => r.status === "rejected"));
    });
  }, [planner?.places]); // eslint-disable-line react-hooks/exhaustive-deps

  const where = (p) => (p.source === "public" ? (p.lat != null ? p : null) : coords[p.ref]);
  const mapPoints = (planner?.places || []).flatMap((p, i) => {
    const at = where(p);
    return at ? [{ key: `${p.source}-${p.ref}`, number: i + 1, name: at.name || p.name, lat: at.lat, lon: at.lon }] : [];
  });
  const segKey = (i) => {
    const [a, b] = [planner.places[i], planner.places[i + 1]];
    // CCTV 선택이나 이동 조건(휠체어·유모차)이 바뀌면 다른 키가 되어 경로를 다시 계산한다
    return `${a.source}-${a.ref}>${b.source}-${b.ref}${planner.prefer_cctv ? "+cctv" : ""}+${planner.mobility || ""}`;
  };
  const routeLines = (planner?.places || []).slice(0, -1).flatMap((_, i) => {
    const kind = planner.route_choices[i] || DEFAULT_ROUTE;
    const route = plans[segKey(i)]?.routes.find((r) => r.kind === kind);
    return route?.found ? [{ key: segKey(i), kind, path: route.line, index: i }] : [];
  });
  // 위치를 아는 구간은 바로 계산해 둔다(다시 열었을 때도 고른 경로선이 보이게). 경로는 저장하지 않는다
  useEffect(() => {
    (planner?.places || []).slice(0, -1).forEach((_, i) => {
      const key = segKey(i);
      const [from, to] = [where(planner.places[i]), where(planner.places[i + 1])];
      if (!from || !to || plans[key] || planErrors[key] || inflight.current.has(key)) return;
      inflight.current.add(key);
      api
        .compareRoutes({ lat: from.lat, lon: from.lon }, { lat: to.lat, lon: to.lon }, Boolean(planner.prefer_cctv), planner.mobility)
        .then((plan) => setPlans((all) => ({ ...all, [key]: plan })))
        .catch((e) => setPlanErrors((all) => ({ ...all, [key]: e.message })))
        .finally(() => inflight.current.delete(key));
    });
  }); // 렌더마다 확인하지만 이미 계산했거나 계산 중인 구간은 건너뜀

  const notFound = (planner?.places || []).some((p) => p.source === "kakao" && coords[p.ref] === null);

  return { coords, setCoords, lookupFailed, plans, planErrors, setPlanErrors, where, mapPoints, routeLines, segKey, notFound };
}

// apiRef: AI 대화로 가기 전에 손으로 고친 것을 저장한다
function Editor({ plannerId, onBack, onTravel, onDeleted, apiRef }) {
  const [sheet, setSheet] = useState("none");
  const [planner, setPlanner] = useState(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const version = useRef(0); // 저장 중에 또 고쳤는지 알기 위한 수정 횟수
  const [routeIndex, setRouteIndex] = useState(0); // 경로 비교 창을 연 구간
  const [crime, setCrime] = useState(null); // 범죄주의구간 표시: null = 아직 안 물어봄
  const [bubble, setBubble] = useState(null); // 방금 더한 장소로 가는 구간 번호

  useEffect(() => {
    api
      .getPlanner(plannerId)
      .then(setPlanner)
      .catch((e) => setError(e.message));
  }, [plannerId]);

  const { coords, setCoords, lookupFailed, plans, planErrors, setPlanErrors, where, mapPoints, routeLines, segKey, notFound } =
    useTripMap(planner, plannerId);

  const addPlace = (found, searched) => {
    setCoords((c) => ({ ...c, [found.id]: found }));
    // 말풍선이 기다릴 구간 번호: 앞 장소 -> 새 장소(장소가 하나뿐이면 구간 없음)
    setBubble(planner.places.length > 0 ? planner.places.length - 1 : null);
    // 카카오 자료는 장소 ID만 저장한다. name 칸에는 사용자가 직접 쓴 검색어를 저장하고
    // (다시 열 때 이 검색어로 다시 찾음), 화면에 보이는 가게 이름·분류·좌표는 메모리에만 둔다.
    const place = { source: "kakao", ref: found.id, name: searched };
    const places = [...planner.places, place];
    const route_choices = places.length > 1 ? [...planner.route_choices, DEFAULT_ROUTE] : [];
    update({ places, route_choices });
  };

  const update = (patch) => {
    version.current += 1;
    setPlanner((p) => ({ ...p, ...patch }));
    setDirty(true);
    setSaved(false);
  };

  const save = async () => {
    const { title, start_date, end_date, places, route_choices } = planner;
    const sent = version.current;
    setSaving(true);
    setError("");
    try {
      // 서버가 읽을 때 붙여 준 좌표 등은 빼고 허용된 칸만 보낸다
      const sendable = places.map(({ source, ref, name, category }) =>
        source === "public" ? { source, ref, name, category } : { source, ref, name },
      );
      await api.updatePlanner(plannerId, {
        title,
        start_date,
        end_date,
        places: sendable,
        route_choices,
        prefer_cctv: planner.prefer_cctv ?? null,
        // Agent가 만든 일정의 조건과 이동 조건은 그대로 돌려보낸다(지우지 않게)
        mobility: planner.mobility ?? null,
        agent: planner.agent ?? null,
      });
      if (version.current === sent) {
        setDirty(false);
        setSaved(true);
        setTimeout(() => setSaved(false), 1600);
      }
      return true;
    } catch (e) {
      setError(e.message);
      return false;
    } finally {
      setSaving(false);
    }
  };

  if (apiRef) apiRef.current = { save: async () => !dirty || (await save()), isDirty: () => dirty };

  const leave = () => {
    if (!dirty || window.confirm("저장하지 않은 내용이 있어요. 저장하지 않고 나갈까요?")) onBack();
  };

  const remove = async () => {
    if (!window.confirm("이 여행을 지울까요? 지우면 되돌릴 수 없어요.")) return;
    setSaving(true); // 지우는 동안 저장·여행 시작·다시 지우기를 막는다
    try {
      await api.deletePlanner(plannerId);
      onDeleted(plannerId);
    } catch (e) {
      setError(e.message);
      setSaving(false);
    }
  };

  const startTravel = async () => {
    if (!dirty || (await save())) onTravel(planner, { points: mapPoints, lines: routeLines });
  };

  const removePlace = (index) => {
    setBubble(null); // 구간 번호가 바뀌므로 말풍선을 닫는다
    const places = planner.places.filter((_, i) => i !== index);
    // 구간 선택은 장소 사이마다 하나: 지운 장소에 붙은 구간 하나를 같이 뺀다
    const route_choices = planner.route_choices.filter((_, i) => i !== Math.max(0, index - 1));
    update({ places, route_choices: route_choices.slice(0, Math.max(0, places.length - 1)) });
  };

  if (!planner) {
    return (
      <main className="editor-screen">
        <AppHeader title="여행 계획" back={onBack} />
        <FormError message={error} />
      </main>
    );
  }
  return (
    <main className="editor-screen">
      <AppHeader
        title={planner.title}
        back={leave}
        action={
          <button className="save-link" onClick={save} disabled={saving}>
            {saved ? "저장됨" : "저장"}
          </button>
        }
      />
      <div className="editor-map">
        <KakaoMap points={mapPoints} lines={routeLines} crime={Boolean(crime)} />
        <CrimeToggle value={crime} onChange={setCrime} />
      </div>
      <section className="itinerary">
        <div className="sheet-handle" />
        {/* 일정 목록 안에 둔다: 넓은 화면 플래너는 칸 나누기라 따로 두면 한 칸을 통째로 차지한다 */}
        {bubble !== null && bubble < planner.places.length - 1 &&
          (plans[segKey(bubble)] || planErrors[segKey(bubble)]) && (
            <DistanceBubble
              plan={plans[segKey(bubble)]}
              error={planErrors[segKey(bubble)]}
              kind={planner.route_choices[bubble] || DEFAULT_ROUTE}
              onClose={() => setBubble(null)}
            />
          )}
        <label className="title-field">
          <span className="eyebrow">{formatDates(planner.start_date, planner.end_date)}</span>
          <input
            value={planner.title}
            maxLength={60}
            aria-label="여행 이름"
            onChange={(e) => update({ title: e.target.value })}
          />
        </label>
        <div className="date-fields">
          <label>
            시작일
            <input
              type="date"
              value={planner.start_date || ""}
              onChange={(e) => update({ start_date: e.target.value || null })}
            />
          </label>
          <label>
            종료일
            <input
              type="date"
              value={planner.end_date || ""}
              min={planner.start_date || undefined}
              onChange={(e) => update({ end_date: e.target.value || null })}
            />
          </label>
        </div>
        <div className="itinerary-heading">
          <div>
            <h2>여행 순서</h2>
          </div>
          <span>{planner.places.length}곳</span>
        </div>
        <FormError message={error} />
        {notFound && (
          <p className="empty-note">
            지도에서 위치를 다시 찾지 못한 장소가 있어요. 이름이 바뀌었거나 검색 결과에서 밀려났을 수 있어요.
          </p>
        )}
        {lookupFailed && <p className="empty-note">일부 장소의 위치를 불러오지 못했어요. 잠시 후 다시 열어 주세요.</p>}
        {planner.places.length === 0 && (
          <p className="empty-note">장소 추가를 눌러 가고 싶은 곳을 담아 보세요.</p>
        )}
        {planner.places.length > 1 && (
          <CctvQuestion value={planner.prefer_cctv ?? null} onChange={(v) => update({ prefer_cctv: v })} />
        )}
        <div className="places-list">
          {planner.places.map((place, index) => (
            <div key={`${place.source}-${place.ref}-${index}`}>
              <div className="place-row">
                <span className="place-number">{index + 1}</span>
                <span className="place-icon">
                  <Icon name="location" size={20} />
                </span>
                <span className="place-name">
                  <b>{(place.source === "kakao" && coords[place.ref]?.name) || place.name}</b>
                  {(place.category || coords[place.ref]?.category) && (
                    <small>{place.category || coords[place.ref]?.category}</small>
                  )}
                </span>
                <button
                  className="small-icon"
                  aria-label={`${place.name} 삭제`}
                  onClick={() => removePlace(index)}
                >
                  <Icon name="trash" size={18} />
                </button>
              </div>
              {index < planner.places.length - 1 && (
                <RouteCard
                  index={index}
                  active={planner.route_choices[index] || DEFAULT_ROUTE}
                  onChoose={(key) =>
                    update({
                      route_choices: planner.route_choices.map((c, i) => (i === index ? key : c)),
                    })
                  }
                  onOpen={() => {
                    setRouteIndex(index);
                    setSheet("routes");
                  }}
                />
              )}
            </div>
          ))}
        </div>
        <button className="add-place-button" onClick={() => setSheet("search")}>
          <Icon name="plus" /> 장소 추가
        </button>
        <div className="editor-actions">
          <button className="secondary-button" onClick={save} disabled={saving}>
            <Icon name="check" /> {saved ? "저장했어요" : "저장"}
          </button>
          <button
            className="primary-button"
            onClick={startTravel}
            disabled={planner.places.length === 0 || saving}
          >
            <Icon name="walk" /> 여행 시작
          </button>
        </div>
        <button className="text-button" onClick={remove} disabled={saving}>
          이 여행 지우기
        </button>
      </section>
      {sheet === "search" && (
        <SearchSheet
          added={new Set(planner.places.map((p) => p.ref))}
          full={planner.places.length >= MAX_PLACES}
          onAdd={addPlace}
          onClose={() => setSheet("none")}
        />
      )}
      {sheet === "routes" && routeIndex < planner.places.length - 1 && (
        <RoutesSheet
          index={routeIndex}
          from={where(planner.places[routeIndex])}
          to={where(planner.places[routeIndex + 1])}
          plan={plans[segKey(routeIndex)]}
          error={planErrors[segKey(routeIndex)]}
          onRetry={() =>
            setPlanErrors(({ [segKey(routeIndex)]: _, ...rest }) => rest)
          }
          active={planner.route_choices[routeIndex] || DEFAULT_ROUTE}
          onChoose={(key) =>
            update({ route_choices: planner.route_choices.map((c, i) => (i === routeIndex ? key : c)) })
          }
          onClose={() => setSheet("none")}
        />
      )}
    </main>
  );
}

// "공개된 방범 CCTV가 근처에 있는 길로 안내할까요?" 네라고 하면 안전환경 우선 경로가 CCTV 근처 길도 따진다
function CctvQuestion({ value, onChange }) {
  if (value === null) {
    return (
      <div className="ask-card">
        <p>공개된 방범 CCTV가 근처에 있는 길로 안내할까요?</p>
        <div>
          <button className="secondary-button" onClick={() => onChange(true)}>
            네
          </button>
          <button className="secondary-button" onClick={() => onChange(false)}>
            아니요
          </button>
        </div>
      </div>
    );
  }
  return (
    <p className="ask-done">
      {value ? "방범 CCTV가 근처에 있는 길도 따져서 안내해요." : "CCTV 위치는 따지지 않고 안내해요."}{" "}
      <button onClick={() => onChange(!value)}>{value ? "끄기" : "켜기"}</button>
    </p>
  );
}

function SheetFrame({ children, onClose, tall = false }) {
  return (
    <div
      className="overlay"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <section className={`bottom-sheet ${tall ? "tall" : ""}`}>
        <div className="sheet-handle" />
        <button className="sheet-close" onClick={onClose} aria-label="닫기">
          <Icon name="close" size={22} />
        </button>
        {children}
      </section>
    </div>
  );
}

function ComingSoon({ title, body }) {
  return (
    <article className="state-card coming-soon">
      <span className="state-icon">
        <Icon name="clock" size={30} />
      </span>
      <h3>{title}</h3>
      <p>{body}</p>
    </article>
  );
}

function SearchSheet({ added, full, onAdd, onClose }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const latest = useRef(0); // 늦게 도착한 이전 검색 결과가 최신 결과를 덮지 않게
  const [searched, setSearched] = useState(""); // 지금 보이는 결과를 낸 검색어(사용자 입력)

  const search = async (event) => {
    event.preventDefault();
    if (!query.trim()) return;
    const mine = ++latest.current;
    setBusy(true);
    setError("");
    try {
      const found = await searchPlaces(query.trim());
      if (mine === latest.current) {
        setResults(found);
        setSearched(query.trim());
      }
    } catch {
      if (mine === latest.current) {
        setResults(null);
        setError(MSG_SEARCH_FAILED);
      }
    } finally {
      if (mine === latest.current) setBusy(false);
    }
  };

  return (
    <SheetFrame onClose={onClose} tall>
      <div className="sheet-title">
        <h2>장소 추가</h2>
        <p>가고 싶은 곳을 찾아보세요.</p>
      </div>
      <form className="search-box" onSubmit={search}>
        <Icon name="search" size={20} />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="예: 성산일출봉, 빵집"
          aria-label="장소 검색"
          autoFocus
        />
      </form>
      <FormError message={error} />
      {busy && <p className="result-count">찾는 중이에요…</p>}
      {results && !busy && (
        <div className="search-results">
          <p className="result-count">
            {results.length ? `제주에서 ${results.length}곳을 찾았어요` : "제주에서 찾은 곳이 없어요. 다른 이름으로 찾아보세요."}
          </p>
          {full && <p className="result-count">한 계획에는 {MAX_PLACES}곳까지 담을 수 있어요.</p>}
          {results.map((r) => (
            <div className="search-result" key={r.id}>
              <span className="result-icon">
                <Icon name="location" size={20} />
              </span>
              <span>
                <b>{r.name}</b>
                <small>{[r.category, r.address].filter(Boolean).join(" · ")}</small>
              </span>
              <button
                className={added.has(r.id) ? "added" : ""}
                disabled={added.has(r.id) || full}
                onClick={() => onAdd(r, searched)}
              >
                {added.has(r.id) ? "담음" : "담기"}
              </button>
            </div>
          ))}
        </div>
      )}
    </SheetFrame>
  );
}

function RoutesSheet({ index, heading, from, to, plan, error, onRetry, active, onChoose, onClose }) {
  const ready = from && to;

  return (
    <SheetFrame onClose={onClose} tall>
      <div className="sheet-title route-sheet-title">
        <div>
          <h2>어떤 길로 걸을까요?</h2>
          <p>
            {heading ||
              `${index < ORDINALS.length ? `${ORDINALS[index]} 번째 구간` : `${index + 1}번째 구간`}의 세 가지 길을 비교해요.`}
          </p>
        </div>
      </div>
      {!ready && <p className="empty-note">장소 위치를 아직 불러오지 못했어요. 잠시 후 다시 열어 주세요.</p>}
      <FormError message={error} />
      {error && (
        <button className="secondary-button" onClick={onRetry}>
          다시 계산하기
        </button>
      )}
      {ready && !plan && !error && <p className="result-count">길을 계산하고 있어요. 처음에는 조금 걸려요…</p>}
      {plan && (
        <>
          {[...plan.comparison, ...plan.notes].map((t) => (
            <p className="result-count" key={t}>
              {t}
            </p>
          ))}
          <div className="route-list">
            {plan.routes.map((r) => (
              <button
                key={r.kind}
                className={`route-choice ${r.kind === active ? "selected" : ""}`}
                onClick={() => onChoose(r.kind)}
                disabled={!r.found}
                aria-pressed={r.kind === active}
              >
                <span className={`route-radio ${r.kind === active ? "checked" : ""}`}>
                  <span />
                </span>
                <span className="route-choice-body">
                  <span className="route-choice-head">
                    <b>
                      <span className={`line-${{ SHORTEST: "solid", BALANCED: "dotted", SAFETY: "thick" }[r.kind]}`} />
                      {r.title}
                    </b>
                    {r.found && <strong>약 {Math.round(r.minutes)}분</strong>}
                  </span>
                  {r.text.map((t) => (
                    <span className="reason" key={t}>
                      {t}
                    </span>
                  ))}
                </span>
              </button>
            ))}
          </div>
        </>
      )}
    </SheetFrame>
  );
}

function Travel({ planner, stop, setStop, onBack, onChat }) {
  const places = planner.places;
  const next = places[Math.min(stop + 1, places.length - 1)];
  const finished = stop >= places.length - 1;
  // 지금 구간(stop -> stop+1)의 핀과 고른 경로선만 보여준다
  const points = (planner.map?.points || []).filter((p) => p.number === stop + 1 || p.number === stop + 2);
  const given = (planner.map?.lines || []).find((l) => l.index === stop);
  // 플래너에서 경로 계산이 끝나기 전에 여행을 시작했으면 여기서 계산한다(저장 안 함)
  const [fetched, setFetched] = useState({});
  const [from, to] = [stop + 1, stop + 2].map((n) => points.find((p) => p.number === n));
  const kind = planner.route_choices[stop] || DEFAULT_ROUTE;
  useEffect(() => {
    if (given || !from || !to || stop in fetched) return;
    let alive = true;
    api
      .compareRoutes({ lat: from.lat, lon: from.lon }, { lat: to.lat, lon: to.lon }, Boolean(planner.prefer_cctv), planner.mobility)
      .then((plan) => {
        const route = plan.routes.find((r) => r.kind === kind);
        if (alive) setFetched((f) => ({ ...f, [stop]: route?.found ? { key: `t${stop}`, kind, path: route.line } : null }));
      })
      .catch(() => alive && setFetched((f) => ({ ...f, [stop]: null })));
    return () => {
      alive = false;
    };
  }, [stop, given, from, to]); // eslint-disable-line react-hooks/exhaustive-deps
  const line = given || fetched[stop];
  return (
    <main className="travel-screen">
      <div className="travel-map">
        <KakaoMap points={points} lines={line && !finished ? [line] : []} />
      </div>
      <div className="travel-topbar">
        <button className="map-round-button" onClick={onBack} aria-label="여행 나가기">
          <Icon name="close" />
        </button>
        <span className="live-pill">
          <span /> 여행 안내 중
        </span>
        <span className="header-spacer" />
      </div>
      <section className="next-card">
        <div className="next-title">
          <span className="walk-icon">
            <Icon name="walk" size={22} />
          </span>
          <div>
            <small>{finished ? "마지막 장소" : "다음 장소"}</small>
            <h2>{next.name}</h2>
          </div>
        </div>
        <p>
          {finished
            ? "오늘의 모든 장소를 둘러봤어요."
            : line
              ? "지도에 플래너에서 고른 길을 표시했어요."
              : stop in fetched
                ? "이 구간의 길을 계산하지 못했어요."
                : "길을 계산하고 있어요…"}
        </p>
        <div className="stop-controls">
          <button onClick={() => setStop(Math.max(0, stop - 1))} disabled={stop === 0}>
            <Icon name="back" size={18} /> 이전
          </button>
          <span>
            {Math.min(stop + 1, places.length)} / {places.length}
          </span>
          <button onClick={() => setStop(Math.min(places.length - 1, stop + 1))} disabled={finished}>
            다음 <Icon name="chevron" size={18} />
          </button>
        </div>
      </section>
      <div className="chat-dock">
        <button onClick={onChat}>
          <Icon name="spark" size={20} />
          <span>화장실 어디 있어?</span>
          <span className="send-circle">
            <Icon name="send" size={18} />
          </span>
        </button>
      </div>
    </main>
  );
}

const MSG_NO_LOCATION = "지금 위치를 알 수 없어요. 브라우저에서 위치 사용을 허용해 주세요.";
const SUGGESTIONS = ["근처 약국 찾아줘", "근처 화장실", "근처 공공 와이파이", "애월에서 빵집 두 곳 들르고 돌아오는 코스 짜줘"];
const CONV_KEY = "jeju-agent-conversation";

function myLocation() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) return reject(new Error(MSG_NO_LOCATION));
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
      () => reject(new Error(MSG_NO_LOCATION)),
      { enableHighAccuracy: true, timeout: 10000 },
    );
  });
}

function readConversation(key) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function saveConversation(key, id) {
  try {
    sessionStorage.setItem(key, id);
  } catch {
    // 저장이 막혀 있으면 새로고침 때 대화가 이어지지 않을 뿐이다
  }
}

function clearConversation(key) {
  try {
    sessionStorage.removeItem(key);
  } catch {
    // 저장이 막혀 있으면 지울 것도 없다
  }
}

const newId = () =>
  globalThis.crypto?.randomUUID?.() || `${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}`;

// 2초마다 최대 30번(60초): 서버 실행 제한 41초(configs/agent_policy.json)보다 길게 기다린다.
// shortcut: 2초 간격 자체는 근거 없음(서버 부담과 기다림 사이 짐작)
const RUN_POLL_MS = 2000;
const RUN_POLL_TRIES = 30;

// 같은 메시지가 아직 처리 중이면 끝날 때까지 기다린다. 서버가 꺼졌다 켜져 멈춘 실행이면 이어서 한다
async function waitForRun(runId, location, view) {
  for (let i = 0; i < RUN_POLL_TRIES; i += 1) {
    await new Promise((r) => setTimeout(r, RUN_POLL_MS));
    const run = await api.agentRun(runId);
    if (run.response && !run.response.still_running) return run.response;
    if (run.stage === "interrupted") return api.agentResume(runId, location, view);
  }
  throw new Error("답이 너무 오래 걸려요. 잠시 후 다시 물어봐 주세요.");
}

// 서버가 준 사용자 문장만 보여준다(상태 코드·ID는 화면 분기에만 씀)
// save: 이 답의 일정 초안 저장 상태 {can, savedId}. routeKind/onChooseRoute: 지도에 보일 길 고르기
function AgentReply({ reply, onSend, onUseLocation, onOpenPlanner, save, onSave, routeKind, onChooseRoute, busy }) {
  const it = reply.itinerary;
  const notes = [...(it?.notes || []), ...(reply.warnings || []), ...(reply.missing_data || [])];
  const cards = (reply.places || []).filter((p) => !p.pin_only); // pin_only: 지도에만 찍는 점
  const [taxi, setTaxi] = useState(null);
  const taxiButton = (p) =>
    p.address ? (
      <button className="text-button taxi-button" onClick={() => setTaxi({ name: p.name, address: p.address })}>
        기사님께 보여주기
      </button>
    ) : null;
  return (
    <>
      {reply.questions?.map((q, i) => (
        <div className="nearby-kinds" key={i}>
          {reply.questions.length > 1 && <p className="result-count">{q.text}</p>}
          {q.options?.map((o) => (
            <button key={o.reply} onClick={() => onSend(o.reply)} disabled={busy}>
              {o.label}
            </button>
          ))}
          {q.needs_location && (
            <button onClick={onUseLocation} disabled={busy}>
              지금 위치 쓰기
            </button>
          )}
        </div>
      ))}
      {cards.length > 0 && !it && (
        <div className="facility-list">
          {cards.map((p, i) => (
            <div className="facility-card" key={`${p.name}-${i}`}>
              <b>
                {reply.routes ? p.detail : `${i + 1}.`} {p.name}
              </b>
              {!reply.routes && <small>{[p.detail, p.address].filter(Boolean).join(" · ")}</small>}
              {p.url && (
                <a href={p.url} target="_blank" rel="noreferrer">
                  카카오맵에서 보기
                </a>
              )}
              {taxiButton(p)}
            </div>
          ))}
        </div>
      )}
      {it && (
        <div className="facility-list">
          {it.stops.map((s) => (
            <div className={`facility-card ${s.done ? "muted" : ""}`} key={s.number}>
              <b>
                {s.number}. {s.name}
                {s.done ? " (다녀옴)" : s.fixed ? " (고정)" : ""}
              </b>
              <small>
                {[s.detail, s.walk_minutes != null && !s.done ? `걸어서 약 ${s.walk_minutes}분` : null]
                  .filter(Boolean)
                  .join(" · ")}
              </small>
              {s.url && (
                <a href={s.url} target="_blank" rel="noreferrer">
                  카카오맵에서 보기
                </a>
              )}
              {taxiButton(s)}
            </div>
          ))}
          {it.back_label && (
            <p className="result-count">
              {it.back_label}
              {it.back_minutes != null ? ` 약 ${it.back_minutes}분` : ""} · 모두 약 {it.total_minutes}분
            </p>
          )}
          {save?.savedId ? (
            <button className="secondary-button" onClick={() => onOpenPlanner(save.savedId)}>
              내 여행에서 열기
            </button>
          ) : save?.can && reply.unsaved?.replaces ? (
            <div className="save-actions">
              <button className="primary-button" onClick={() => onSave(reply.trip_id, true)} disabled={busy}>
                원래 여행에 덮어쓰기
              </button>
              <button className="secondary-button" onClick={() => onSave(reply.trip_id, false)} disabled={busy}>
                새 여행으로 저장
              </button>
            </div>
          ) : save?.can ? (
            <button className="primary-button" onClick={() => onSave(reply.trip_id, false)} disabled={busy}>
              내 여행에 저장
            </button>
          ) : null}
        </div>
      )}
      {reply.routes && (
        <div className="facility-list">
          {reply.routes.routes
            .filter((r) => r.found)
            .map((r) => (
              <button
                className={`facility-card ${r.kind === routeKind ? "chosen" : ""}`}
                key={r.kind}
                onClick={() => onChooseRoute?.(r.kind)}
                aria-pressed={r.kind === routeKind}
              >
                <b>{r.title}</b>
                <small>{r.text.join(" ")}</small>
              </button>
            ))}
          {reply.routes.comparison.map((line) => (
            <p className="result-count" key={line}>
              {line}
            </p>
          ))}
        </div>
      )}
      {taxi && <TaxiCard place={taxi} onClose={() => setTaxi(null)} />}
      {notes.length > 0 && (
        <div className="facility-list">
          {notes.map((n) => (
            <p className="result-count" key={n}>
              {n}
            </p>
          ))}
        </div>
      )}
    </>
  );
}

// AI 길잡이 얼굴: 설문대할망(기획서 캐릭터, Google Gemini로 만든 그림)
function HalmangFace() {
  return <img className="agent-avatar halmang-face" src="/halmang/face.webp" alt="설문대할망" />;
}

// 기사님께 보여주기: 장소 이름과 주소를 화면 가득 큰 글씨로(외국인·말로 설명하기 어려운 사람용)
function TaxiCard({ place, onClose }) {
  const close = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose; // 부모가 렌더마다 새 함수를 줘도 열릴 때 한 번만 설정
  // 열면 닫기 버튼에 초점, Esc로 닫기, 닫으면 연 버튼으로 초점을 돌려준다. 버튼이 하나라 Tab은 그 자리에 둔다
  useEffect(() => {
    const opener = document.activeElement;
    close.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") onCloseRef.current();
      if (e.key === "Tab") {
        e.preventDefault();
        close.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener?.focus?.();
    };
  }, []);
  return (
    <div className="taxi-card" role="dialog" aria-modal="true" aria-label="기사님께 보여주기">
      <p className="taxi-lead">이곳으로 가 주세요</p>
      <strong>{place.name}</strong>
      <span>{place.address}</span>
      <button ref={close} className="primary-button" onClick={onClose}>
        닫기
      </button>
    </div>
  );
}

// 제주 안에서만 보낸다(서버도 확인). 지도가 아직 안 떴으면 null
const inJejuView = (v) =>
  v && v.center.lat >= 33 && v.center.lat <= 34.05 && v.center.lon >= 125.9 && v.center.lon <= 127 ? v : null;

// AI와 대화하는 칸. 지금 보이는 지도(view)를 함께 보내 AI가 같은 지도를 보고 답한다.
// tripId: 지금 열어 둔 여행(플래너에서 같이 짤 때). onReply: 답마다 부름(지도 갱신용)
// context: 지금 다루는 여행 {name, tripId?}. onSaved: 초안을 내 여행에 넣었을 때(저장된 번호)
// memory: 화면을 오가도 남는 대화(답에 딸린 일정·저장 상태까지). shown/onShow: 지도에 보일 답과 고른 길
function AgentPanel({ context, view, storageKey, onReply, onOpenPlanner, onSaved, memory, shown, onShow }) {
  const [conversation, setConversation] = useState(() => readConversation(storageKey));
  const [log, setLog] = useState(() => memory.current.log);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [here, setHere] = useState(null); // 사용자가 "지금 위치 쓰기"를 눌렀을 때만
  const [rulesOnly, setRulesOnly] = useState(false);
  // 답 순번 -> 저장된 여행 번호(초안 번호는 쓰지 않음: 서버가 같은 번호를 다시 줄 수 있음)
  const [saved, setSaved] = useState(() => memory.current.saved);
  useEffect(() => {
    memory.current = { ...memory.current, log, saved };
  }, [log, saved, memory]);
  const running = useRef(null);
  const bottom = useRef(null);
  const box = useRef(null);
  // 칸을 넘으면 줄바꿈되고 입력창이 늘어남(최대 높이는 CSS), 보내서 비면 한 줄로 돌아옴
  useEffect(() => {
    box.current.style.height = "auto";
    box.current.style.height = `${box.current.scrollHeight}px`;
  }, [text]);

  useEffect(() => {
    if (!conversation) return;
    api
      .agentConversation(conversation)
      .then((c) => setLog((l) => (l.length ? l : c.history.map((h) => ({ role: h.role, text: h.text })))))
      .catch(() => setConversation(null)); // 지난 대화가 없거나 만료됨: 새로 시작
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // 중괄호 필수: 최신 크롬은 scrollIntoView가 값을 돌려줘 React가 정리 함수로 착각하고 화면이 통째로 꺼진다
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [log, busy]);

  const send = async (said, location = here) => {
    const message = said.trim();
    if (!message || busy) return;
    setBusy(true);
    const id = newId();
    running.current = id;
    setError("");
    setText("");
    setLog((l) => [...l, { role: "user", text: message }]);
    // 대화 번호는 화면이 먼저 만들어 저장한다: 답을 받기 전에 새로고침해도 같은 대화로 이어진다
    const conv = conversation || newId();
    setConversation(conv);
    saveConversation(storageKey, conv);
    const body = {
      message_id: id,
      conversation_id: conv,
      text: message,
      location,
      // 지금 다루는 여행(내 여행에서 가져왔거나 방금 만든 초안). 없으면 서버가 이 대화에 연결해 둔 여행
      trip_id: context?.tripId ?? null,
      detach_trip: !context?.tripId, // "그만 보기"를 눌렀으면 서버도 앞서 다룬 여행을 쓰지 않는다
      map_view: inJejuView(view),
      screen: context ? { name: context.name, travel_stop: context.travelStop ?? null } : null,
    };
    try {
      let reply;
      try {
        reply = await api.agentMessage(body);
      } catch (e) {
        if (e.status !== 0) throw e;
        reply = await api.agentMessage(body); // 연결이 끊겼으면 같은 메시지 번호로 한 번 더(중복 실행 안 됨)
      }
      if (reply.still_running) reply = await waitForRun(id, location, inJejuView(view));
      setRulesOnly(reply.interpreter === "rules");
      // "저장해줘"로 저장했으면 저장한 그 초안의 가장 최근 일정 답의 저장 버튼을 '저장됨'으로
      const planIndex = log.findLastIndex((m) => m.reply?.itinerary && m.reply.trip_id === reply.saved_draft);
      if (reply.saved_trip && planIndex >= 0) setSaved((m) => ({ ...m, [planIndex]: reply.saved_trip }));
      setLog((l) => [...l, { role: "agent", text: reply.message, reply }]);
      onReply?.(reply);
    } catch (e) {
      setError(e.message);
    } finally {
      running.current = null;
      setBusy(false);
    }
  };

  const useLocation = async () => {
    try {
      const at = await myLocation();
      setHere(at);
      send("지금 위치", at);
    } catch (e) {
      setError(e.message);
    }
  };

  const saveDraft = async (index, draftId, overwrite) => {
    setBusy(true);
    setError("");
    try {
      const { id } = await api.saveDraft(draftId, overwrite);
      setSaved((m) => ({ ...m, [index]: id }));
      setLog((l) => [...l, { role: "agent", text: overwrite ? "원래 여행에 덮어써서 저장해 뒀단다." : "내 여행에 잘 저장해 뒀단다." }]);
      onSaved?.(id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  // 저장 버튼은 여행(초안)마다 가장 최근 일정 답에만(같은 초안의 앞선 답은 이미 고쳐졌을 수 있음).
  // 여러 날 일정은 날마다 초안이 달라 날마다 저장할 수 있다
  const lastPlanOf = {};
  log.forEach((m, i) => {
    if (m.reply?.itinerary && m.reply.status === "ok" && m.reply.trip_id) lastPlanOf[m.reply.trip_id] = i;
  });

  const cancel = () => running.current && api.agentCancel(running.current).catch(() => {});

  return (
    <div className="agent-panel">
      <section className="messages">
        <div className="agent-row">
          <HalmangFace />
          <div className="message agent-message">
            <img className="halmang-pose" src="/halmang/welcome.webp" alt="" />
            어서 오너라! 내가 치마폭에 흙을 날라 빚어낸 보물섬, 제주에 온 걸 환영해. 오늘은 어디부터 걸어볼까?
            일정을 짜 주면 마음에 들 때 내 여행에 저장할 수 있고, 길을 물으면 지도에 그려 주마. 지도에 보이는
            곳이나 말한 곳을 기준으로 찾아 준단다.
          </div>
        </div>
        {rulesOnly && <p className="result-count">지금은 정해진 말 위주로 알아듣는단다. 짧고 분명하게 말해 주렴.</p>}
        {log.map((m, i) =>
          m.role === "user" ? (
            <div className="agent-row" key={i}>
              <div className="message user-message">{m.text}</div>
            </div>
          ) : (
            <div key={i}>
              <div className="agent-row">
                <HalmangFace />
                <div className="message agent-message">{m.text}</div>
              </div>
              {m.reply && (
                <AgentReply
                  reply={m.reply}
                  onSend={(t) => send(t)}
                  onUseLocation={useLocation}
                  onOpenPlanner={onOpenPlanner}
                  save={
                    m.reply.trip_id
                      ? {
                          can: i === lastPlanOf[m.reply.trip_id] && m.reply.unsaved != null && !saved[i],
                          savedId: saved[i] || (m.reply.unsaved == null ? m.reply.trip_id : null),
                        }
                      : null
                  }
                  onSave={(draftId, overwrite) => saveDraft(i, draftId, overwrite)}
                  routeKind={shown?.reply === m.reply ? shown.kind : null}
                  onChooseRoute={(kind) => onShow({ reply: m.reply, kind })}
                  busy={busy}
                />
              )}
            </div>
          ),
        )}
        {busy && (
          <div className="agent-row halmang-busy">
            <img className="halmang-pose" src="/halmang/search.webp" alt="" />
            <p className="result-count">
              돋보기로 살펴보는 중이란다…{" "}
              <button className="text-button" onClick={cancel}>
                그만하기
              </button>
            </p>
          </div>
        )}
        <FormError message={error} />
        <div ref={bottom} />
      </section>
      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        {log.length === 0 && (
          <div className="suggestions">
            {SUGGESTIONS.map((x) => (
              <button type="button" key={x} onClick={() => send(x)} disabled={busy}>
                {x}
              </button>
            ))}
          </div>
        )}
        <div className="input-row">
          <textarea
            ref={box}
            rows={1}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              // Enter는 보내기, Shift+Enter는 줄바꿈. 한글 조합 중 Enter는 글자 확정이라 보내지 않음
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                e.currentTarget.form.requestSubmit();
              }
            }}
            placeholder="예: 애월에서 빵집 두 곳 들르는 코스 짜줘"
            aria-label="AI 길잡이에게 할 말"
            maxLength={500}
          />
          <button type="submit" disabled={busy || !text.trim()} aria-label="보내기">
            <Icon name="send" size={18} />
          </button>
        </div>
      </form>
    </div>
  );
}

// 로그인 뒤 첫 화면: AI와 대화하며 일정을 짜고 길을 묻는다. 일정은 초안이고 저장해야 내 여행에 들어간다.
// tripId: 지금 다루는 여행(내 여행에서 "AI 길잡이에서 보기"로 왔거나 방금 만든 초안)
function AgentScreen({ tripId, setTripId, onOpenPlanner, onTrips, memory }) {
  const [view, setView] = useState(null);
  const [crime, setCrime] = useState(null); // 범죄주의구간 표시: null = 아직 안 물어봄
  // 지도에 보일 답과 그 답에서 고른 길. 화면을 오가도 남게 memory에도 둔다
  const [shown, setShownState] = useState(() => memory.current.shown || null);
  const setShown = (next) => {
    memory.current = { ...memory.current, shown: next };
    setShownState(next);
  };
  const latest = shown?.reply;
  const routeKind = shown?.kind;
  const [session, setSession] = useState(0); // 새 대화·지난 대화를 열 때 대화 칸을 새로 그린다
  const sessionNow = useRef(0); // 이전 대화 칸에서 늦게 온 답을 버리려고 지금 대화 순번을 둔다
  // 이 화면을 떠나면(내 여행으로 가서 다른 여행을 새 대화로 여는 등) 늦게 온 답은 모두 버린다
  useEffect(
    () => () => {
      sessionNow.current = -1;
    },
    [],
  );
  const [past, setPast] = useState(null); // 지난 대화 목록(열었을 때만)
  const [error, setError] = useState("");

  const onReply = (reply) => {
    if (reply.trip_id) setTripId(reply.trip_id);
    // 사용자가 고른 경로 유형(chosen_route)을 먼저 그린다
    setShown({ reply, kind: reply.chosen_route || reply.routes?.routes.find((r) => r.found)?.kind || null });
  };

  // 함께 보는 여행: 저장된 장소를 다시 찾아 고른 경로까지 그린다(답이 올 때마다 바뀐 일정을 다시 읽음)
  const [trip, setTrip] = useState(null);
  useEffect(() => {
    if (!tripId) return setTrip(null);
    let alive = true; // 그사이 여행을 끊거나 바꾸면 늦게 온 응답은 버린다
    api
      .getPlanner(tripId)
      .then((t) => alive && setTrip(t))
      .catch(() => alive && setTrip(null)); // 지워진 여행: 답의 지도만 보인다
    return () => {
      alive = false;
    };
  }, [tripId, latest]);
  const tripMap = useTripMap(trip, tripId);

  // 길찾기·장소 찾기 답은 그 답을, 일정 답이거나 아직 답이 없으면 함께 보는 여행을 그린다
  // (여행을 함께 보는 중에도 답에 여행 번호가 붙으므로 일정이 있는 답인지로 가른다)
  const replyOwnMap = latest && !latest.itinerary && (latest.routes || latest.places?.length);
  const showTrip = tripId && trip && !replyOwnMap;
  const points = showTrip
    ? tripMap.mapPoints
    : (latest?.places || [])
        .filter((p) => p.lat != null)
        .map((p, i) => ({ key: `ai${i}`, number: i + 1, name: p.name, lat: p.lat, lon: p.lon }));
  const chosen = latest?.routes?.routes.find((r) => r.kind === routeKind && r.found);
  const lines = showTrip
    ? tripMap.routeLines
    : chosen
      ? [{ key: "ai-route", kind: chosen.kind, path: chosen.line }]
      : latest?.track?.length
        ? [{ key: "access-track", kind: "ACCESS", path: latest.track }]
        : [];

  // 대화를 바꾼다: id가 없으면 새 대화, 있으면 그 지난 대화를 불러온다
  const switchTo = (id) => {
    if (id) saveConversation(CONV_KEY, id);
    else clearConversation(CONV_KEY);
    memory.current = { log: [], saved: {}, shown: null };
    setShownState(null);
    setTripId(null);
    setPast(null);
    setError("");
    sessionNow.current += 1;
    setSession(sessionNow.current);
    if (id)
      api
        .agentConversation(id)
        .then((c) => c.trip_id && setTripId(c.trip_id))
        .catch(() => {}); // 대화 칸이 따로 알려 준다
  };
  const openPast = () =>
    api
      .agentConversations()
      .then((r) => setPast(r.conversations))
      .catch((e) => setError(e.message));
  const remove = async (id) => {
    if (!window.confirm("이 대화 기록을 지울까요? 지우면 되돌릴 수 없어요.")) return;
    try {
      await api.deleteConversation(id);
    } catch (e) {
      if (e.status !== 404) return setError(e.message); // 이미 없는 대화면 지운 것과 같다
    }
    if (id === readConversation(CONV_KEY)) switchTo(null);
    setPast((list) => list && list.filter((c) => c.id !== id));
  };
  const rename = async (c, title) => {
    try {
      const r = await api.renameConversation(c.id, title);
      // 지우면 기본 제목은 서버 목록에서 다시 받아 온다
      if (r.custom) setPast((list) => list.map((x) => (x.id === c.id ? { ...x, ...r } : x)));
      else openPast();
    } catch (e) {
      setError(e.message);
    }
  };
  const askTitle = (c) => {
    const title = window.prompt("대화 제목을 적어 주세요. (40자까지)", c.title);
    if (title !== null && title.trim()) rename(c, title.trim());
  };
  const current = readConversation(CONV_KEY);

  return (
    <main className="agent-screen" aria-label="AI 길잡이">
      <div className="agent-head">
        <b>
          <Icon name="spark" size={18} /> AI 길잡이
        </b>
        <span className="agent-actions">
          <button className="text-button" onClick={() => switchTo(null)}>
            새 대화
          </button>
          <button className="text-button" onClick={() => (past ? setPast(null) : openPast())} aria-expanded={Boolean(past)}>
            지난 대화
          </button>
          {current && (
            <button className="text-button" onClick={() => remove(current)}>
              기록 삭제
            </button>
          )}
          <button className="text-button agent-trips-link" onClick={onTrips}>
            내 여행
          </button>
        </span>
      </div>
      <div className="agent-extras">
      <FormError message={error} />
      {past && (
        <div className="agent-past" role="list" aria-label="지난 대화">
          {past.length === 0 && <p className="empty-note">지난 대화가 없어요. 대화 기록은 7일 동안 남아요.</p>}
          {past.map((c) => (
            <div className={`agent-past-item ${c.id === current ? "current" : ""}`} role="listitem" key={c.id}>
              <button className="agent-past-open" onClick={() => switchTo(c.id)}>
                <b>{c.title}</b>
                <small>{new Date(c.updated_at).toLocaleString("ko-KR", { month: "long", day: "numeric", hour: "numeric", minute: "2-digit" })}</small>
              </button>
              <button className="text-button" onClick={() => askTitle(c)} aria-label={`${c.title} 제목 바꾸기`}>
                제목 바꾸기
              </button>
              {c.custom && (
                <button className="text-button" onClick={() => rename(c, null)} aria-label={`${c.title} 제목 지우기`}>
                  제목 지우기
                </button>
              )}
              <button className="text-button" onClick={() => remove(c.id)} aria-label={`${c.title} 대화 지우기`}>
                지우기
              </button>
            </div>
          ))}
        </div>
      )}
      {tripId && (
        <div className="agent-trip">
          <Icon name="calendar" size={15} /> {trip ? `'${trip.title}' 여행을 함께 보고 있어요` : "고르신 여행을 함께 보고 있어요"}
          <button className="text-button" onClick={() => setTripId(null)}>
            그만 보기
          </button>
        </div>
      )}
      {showTrip && (tripMap.notFound || tripMap.lookupFailed) && (
        <p className="map-note">일부 장소의 위치를 지금 찾지 못해 지도에 빠졌어요.</p>
      )}
      </div>
      <div className="nearby-map">
        <KakaoMap points={points} lines={lines} crime={Boolean(crime)} onView={setView} />
        <CrimeToggle value={crime} onChange={setCrime} />
      </div>
      <AgentPanel
        key={session}
        context={{ name: "chat", tripId }}
        view={view}
        storageKey={CONV_KEY}
        onReply={(reply) => sessionNow.current === session && onReply(reply)}
        onOpenPlanner={onOpenPlanner}
        onSaved={(id) => sessionNow.current === session && setTripId(id)}
        memory={memory}
        shown={shown}
        onShow={setShown}
      />
    </main>
  );
}

function DesktopNav({ screen, username, hasTrip, onNavigate, onLogout }) {
  const items = [
    { label: "AI 길잡이", icon: "spark", screens: ["chat"], target: "chat" },
    { label: "내 여행", icon: "calendar", screens: ["planners", "editor"], target: "planners" },
    { label: "여행 안내", icon: "walk", screens: ["travel"], target: "travel", needsTrip: true },
  ];

  return (
    <aside className="desktop-nav" aria-label="주요 메뉴">
      <button className="desktop-brand" onClick={() => onNavigate("chat")}>
        <span>
          <Icon name="island" size={27} />
        </span>
        <strong>제주여지도</strong>
      </button>
      <nav>
        {items.map((item) => (
          <button
            key={item.label}
            className={item.screens.includes(screen) ? "active" : ""}
            onClick={() => onNavigate(item.target)}
            disabled={item.needsTrip && !hasTrip}
            title={item.needsTrip && !hasTrip ? "여행을 시작하면 쓸 수 있어요" : undefined}
          >
            <Icon name={item.icon} size={21} />
            {item.label}
          </button>
        ))}
      </nav>
      <div className="desktop-profile">
        <span className="profile-avatar">
          <Icon name="user" size={20} />
        </span>
        <span>
          <b>{username}</b>
          <small>오늘도 안전한 여행</small>
        </span>
        <button onClick={onLogout}>로그아웃</button>
      </div>
    </aside>
  );
}

function readWelcomeSeen() {
  try {
    return Boolean(localStorage.getItem("jeju-welcome-seen"));
  } catch {
    return false;
  }
}

export default function App() {
  const [screen, setScreen] = useState(() => (readWelcomeSeen() ? "auth" : "welcome"));
  const [username, setUsername] = useState("");
  const [plannerId, setPlannerId] = useState(null);
  const [chatTrip, setChatTrip] = useState(null); // AI 대화에서 지금 다루는 여행
  const chatMemory = useRef({ log: [], saved: {}, shown: null }); // 다른 화면에 갔다 와도 대화 유지
  const [editorTick, setEditorTick] = useState(0); // AI 초안을 저장한 뒤 플래너를 새로 불러온다
  const editorApi = useRef(null);
  // 여행 진행 상태는 화면 밖에 둬서 메뉴로 오가도 유지한다
  const [trip, setTrip] = useState(null);
  const [stop, setStop] = useState(0);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [screen]);

  const loggedIn = screen !== "welcome" && screen !== "auth";
  const openPlanner = (id) => {
    setPlannerId(id);
    setEditorTick((t) => t + 1);
    setScreen("editor");
  };
  // AI 대화로 간다. tripId를 주면 그 여행을 AI와 함께 고친다(플래너에서 손으로 고친 건 먼저 저장)
  const goChat = async (tripId = null) => {
    if (screen === "editor" && editorApi.current && !(await editorApi.current.save())) return;
    if (tripId) setChatTrip(tripId);
    setScreen("chat");
  };

  useEffect(() => {
    // 이미 로그인돼 있으면 로그인 화면을 건너뛴다
    api
      .me()
      .then((me) => {
        setUsername(me.username);
        setScreen((s) => (s === "auth" ? "chat" : s));
      })
      .catch(() => {});
  }, []);

  const logout = () =>
    api
      .logout()
      .then(signedOut)
      .catch((e) => window.alert(e.message)); // 서버에 닿지 못하면 로그인 상태가 남아 있다

  const signedOut = () => {
    setUsername("");
    setPlannerId(null);
    setTrip(null);
    setChatTrip(null);
    chatMemory.current = { log: [], saved: {}, shown: null }; // 다음 사람에게 앞 대화가 보이지 않게
    setScreen("auth");
  };

  return (
    <div className={`app-shell screen-${screen}`}>
      {screen !== "welcome" && screen !== "auth" && (
        <DesktopNav
          screen={screen}
          username={username}
          hasTrip={Boolean(trip)}
          onNavigate={(target) =>
            target === "chat" ? goChat(screen === "editor" ? plannerId : null) : setScreen(target)
          }
          onLogout={logout}
        />
      )}
      <div className="phone-frame">
        {screen === "welcome" && (
          <Welcome
            onStart={() => {
              try {
                localStorage.setItem("jeju-welcome-seen", "yes");
              } catch {
                // 저장이 막혀 있으면 다음에 시작 안내가 다시 보일 뿐이다
              }
              setScreen(username ? "chat" : "auth");
            }}
          />
        )}
        {screen === "auth" && (
          <Auth
            onLogin={(name) => {
              setUsername(name);
              setScreen("chat");
            }}
          />
        )}
        {screen === "planners" && (
          <Planners
            onOpen={(id) => {
              setPlannerId(id);
              setScreen("editor");
            }}
            onChat={(id, fresh) => {
              if (fresh) {
                // 지금 대화는 서버에 남아 "지난 대화"에서 다시 열 수 있다
                clearConversation(CONV_KEY);
                chatMemory.current = { log: [], saved: {}, shown: null };
              } else {
                // 앞 답의 지도 대신 고른 여행의 경로가 보이게 한다
                chatMemory.current = { ...chatMemory.current, shown: null };
              }
              goChat(id);
            }}
            onLogout={logout}
            onAccountDeleted={signedOut}
          />
        )}
        {screen === "chat" && (
          <AgentScreen
            tripId={chatTrip}
            setTripId={setChatTrip}
            onOpenPlanner={openPlanner}
            onTrips={() => setScreen("planners")}
            memory={chatMemory}
          />
        )}
        {screen === "editor" && plannerId && (
          <Editor
            key={`${plannerId}-${editorTick}`}
            plannerId={plannerId}
            apiRef={editorApi}
            onDeleted={(id) => {
              if (chatTrip === id) setChatTrip(null); // AI 대화가 지운 여행을 붙잡지 않게
              setScreen("planners");
            }}
            onBack={() => setScreen("planners")}
            onTravel={(planner, map) => {
              setTrip({ ...planner, map });
              setStop(0);
              setScreen("travel");
            }}
          />
        )}
        {screen === "travel" && trip && (
          <Travel
            planner={trip}
            stop={stop}
            setStop={setStop}
            onBack={() => setScreen("editor")}
            onChat={() => goChat(trip.id)}
          />
        )}

      </div>
      {loggedIn && screen !== "chat" && (
        <button
          className="ai-fab"
          onClick={() => goChat(screen === "editor" ? plannerId : null)}
          aria-label={screen === "editor" ? "이 여행을 AI와 고치기" : "AI 길잡이로 가기"}
        >
          <Icon name="spark" size={26} />
        </button>
      )}
    </div>
  );
}
