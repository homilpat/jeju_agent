import { useEffect, useRef, useState } from "react";
import { api, crimeOverlayUrl } from "./api";
import { MSG_MAP_FAILED, MSG_SEARCH_FAILED, geocode, loadKakao, regionOf, resolvePlace, searchPlaces } from "./kakao";

// 피그마 메이크 디자인(제주 안전 여행 플래너)에서 옮김. 화면 데이터는 아직 예시값이며 API 연결 시 바꾼다.

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
    island: (
      <>
        <path d="M4 17c3-3 5-3 8-1s5 2 8-1" />
        <path d="M7 14c1-4 3-6 5-8 2 2 3 4 4 8" />
        <path d="M9 9c-2-1-3-1-5 0M13 7c1-2 2-3 4-4" />
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
function KakaoMap({ points, lines = [], crime = false }) {
  const box = useRef(null);
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
      // 화면 가운데(지도 좌표계 기준). 그림도 같은 좌표계라 가운데끼리 맞추면 전체가 맞는다
      const middle = m.getProjection().coordsFromContainerPoint(new kakao.maps.Point(width / 2, height / 2));
      const img = new Image(width, height);
      img.className = "crime-overlay";
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
    draw();
    kakao.maps.event.addListener(m, "idle", draw);
    return () => {
      latest += 1;
      kakao.maps.event.removeListener(m, "idle", draw);
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
          <Icon name="island" size={86} strokeWidth={1.4} />
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
            실제와 다를 수 있어요.
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

function Planners({ onOpen, onLogout, onAccountDeleted }) {
  const [menu, setMenu] = useState(false);
  const [planners, setPlanners] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .listPlanners()
      .then(setPlanners)
      .catch((e) => setError(e.message));
  }, []);

  const createPlanner = async () => {
    try {
      const { id } = await api.createPlanner({ title: "새 여행", places: [], route_choices: [] });
      onOpen(id);
    } catch (e) {
      setError(e.message);
    }
  };

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
          <p className="empty-note">아직 만든 여행이 없어요. 새 플래너를 만들어 보세요.</p>
        )}
        {planners?.map((p) => (
          <button
            key={p.id}
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
        ))}
      </section>
      <div className="bottom-action">
        <button className="primary-button" onClick={createPlanner}>
          <Icon name="plus" /> 새 플래너 만들기
        </button>
      </div>
    </main>
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

function Editor({ plannerId, onBack, onTravel }) {
  const [sheet, setSheet] = useState("none");
  const [planner, setPlanner] = useState(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const version = useRef(0); // 저장 중에 또 고쳤는지 알기 위한 수정 횟수
  // 카카오 장소 ID -> 지금 검색한 결과(메모리에만). null은 검색은 됐는데 못 찾은 곳
  const [coords, setCoords] = useState({});
  const [lookupFailed, setLookupFailed] = useState(false);
  const [routeIndex, setRouteIndex] = useState(0); // 경로 비교 창을 연 구간
  // 구간(앞 장소>뒤 장소) -> 서버가 계산한 세 경로. 저장하지 않고 열 때마다 다시 계산한다
  const [plans, setPlans] = useState({});
  const [planErrors, setPlanErrors] = useState({});
  const inflight = useRef(new Set());
  const [crime, setCrime] = useState(null); // 범죄주의구간 표시: null = 아직 안 물어봄

  useEffect(() => {
    api
      .getPlanner(plannerId)
      .then(setPlanner)
      .catch((e) => setError(e.message));
  }, [plannerId]);

  // 저장된 장소는 ID·이름뿐이라 열 때마다 다시 검색해 좌표를 얻는다.
  // 검색 자체가 실패한 곳은 기록하지 않아 다음 변경 때 다시 시도한다.
  useEffect(() => {
    const todo = (planner?.places || []).filter((p) => p.source === "kakao" && !(p.ref in coords));
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

  const addPlace = (found, searched) => {
    setCoords((c) => ({ ...c, [found.id]: found }));
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

  const leave = () => {
    if (!dirty || window.confirm("저장하지 않은 내용이 있어요. 저장하지 않고 나갈까요?")) onBack();
  };

  const startTravel = async () => {
    if (!dirty || (await save())) onTravel(planner, { points: mapPoints, lines: routeLines });
  };

  const removePlace = (index) => {
    const places = planner.places.filter((_, i) => i !== index);
    // 구간 선택은 장소 사이마다 하나: 지운 장소에 붙은 구간 하나를 같이 뺀다
    const route_choices = planner.route_choices.filter((_, i) => i !== Math.max(0, index - 1));
    update({ places, route_choices: route_choices.slice(0, Math.max(0, places.length - 1)) });
  };

  const where = (p) => (p.source === "public" ? (p.lat != null ? p : null) : coords[p.ref]);
  const mapPoints = (planner?.places || []).flatMap((p, i) => {
    const at = where(p);
    return at ? [{ key: `${p.source}-${p.ref}`, number: i + 1, name: at.name || p.name, lat: at.lat, lon: at.lon }] : [];
  });
  const segKey = (i) => {
    const [a, b] = [planner.places[i], planner.places[i + 1]];
    // CCTV 선택이 바뀌면 다른 키가 되어 경로를 다시 계산한다
    return `${a.source}-${a.ref}>${b.source}-${b.ref}${planner.prefer_cctv ? "+cctv" : ""}`;
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
        .compareRoutes({ lat: from.lat, lon: from.lon }, { lat: to.lat, lon: to.lon }, Boolean(planner.prefer_cctv))
        .then((plan) => setPlans((all) => ({ ...all, [key]: plan })))
        .catch((e) => setPlanErrors((all) => ({ ...all, [key]: e.message })))
        .finally(() => inflight.current.delete(key));
    });
  }); // 렌더마다 확인하지만 이미 계산했거나 계산 중인 구간은 건너뜀

  const notFound = (planner?.places || []).some((p) => p.source === "kakao" && coords[p.ref] === null);

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

function RoutesSheet({ index, from, to, plan, error, onRetry, active, onChoose, onClose }) {
  const ready = from && to;

  return (
    <SheetFrame onClose={onClose} tall>
      <div className="sheet-title route-sheet-title">
        <div>
          <h2>어떤 길로 걸을까요?</h2>
          <p>{index < ORDINALS.length ? `${ORDINALS[index]} 번째 구간` : `${index + 1}번째 구간`}의 세 가지 길을 비교해요.</p>
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
      .compareRoutes({ lat: from.lat, lon: from.lon }, { lat: to.lat, lon: to.lon }, Boolean(planner.prefer_cctv))
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

const NEARBY_KINDS = [
  { key: "toilet", label: "화장실" },
  { key: "wifi", label: "무료 와이파이" },
  { key: "police", label: "경찰서·지구대" },
];
const NEARBY_SHOWN = 5; // 서버 상한과 같게(api/app.py NEARBY_LIMIT)
const GEOCODE_AT_ONCE = 8; // shortcut: 동시에 보내는 주소 검색 수(근거 없음), 느리면 조정
const MSG_NO_LOCATION = "지금 위치를 알 수 없어요. 브라우저에서 위치 사용을 허용해 주세요.";
const MSG_OUTSIDE_JEJU = "제주 안에서만 찾을 수 있어요.";

function distanceM(a, b) {
  const rad = (d) => (d * Math.PI) / 180;
  const h =
    Math.sin(rad(b.lat - a.lat) / 2) ** 2 +
    Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(rad(b.lon - a.lon) / 2) ** 2;
  return 2 * 6371008.8 * Math.asin(Math.sqrt(h));
}

const formatDistance = (m) => (m >= 1000 ? `${(m / 1000).toFixed(1)}km` : `${Math.round(m)}m`);

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

// 화장실 자료엔 위치가 없어 같은 읍면동 후보의 주소를 그때그때 카카오로 찾는다(결과는 저장하지 않음)
// stillWanted(): 화면을 떠났거나 다른 검색을 시작했으면 false — 남은 주소 검색을 보내지 않는다
async function nearbyToilets(me, stillWanted) {
  const region = await regionOf(me.lat, me.lon);
  if (!region) throw new Error(MSG_OUTSIDE_JEJU);
  const candidates = await api.nearbyToilets(region.city, region.regions);
  const found = [];
  for (let i = 0; i < candidates.length; i += GEOCODE_AT_ONCE) {
    if (!stillWanted()) return [];
    const batch = candidates.slice(i, i + GEOCODE_AT_ONCE);
    const spots = await Promise.all(
      batch.map(async (t) => {
        for (const address of t.addresses) {
          const at = await geocode(address);
          if (at) return at;
        }
        return null;
      }),
    );
    batch.forEach((t, j) => spots[j] && found.push({ ...t, ...spots[j], distance_m: distanceM(me, spots[j]) }));
  }
  return found.sort((a, b) => a.distance_m - b.distance_m).slice(0, NEARBY_SHOWN);
}

function toiletNotes(t) {
  return [
    t.hours && `여는 시간 ${t.hours}`,
    t.accessible && "장애인 화장실",
    t.diaper && "기저귀 교환대",
    t.bell && "비상벨",
  ].filter(Boolean);
}

function Chat({ onBack }) {
  const [kind, setKind] = useState(null);
  const [me, setMe] = useState(null);
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const latest = useRef(0);
  useEffect(() => () => void (latest.current += 1), []); // 화면을 떠나면 진행 중 검색을 멈춤

  const find = async (key) => {
    const mine = ++latest.current;
    setKind(key);
    setBusy(true);
    setError("");
    setResults(null);
    try {
      const here = await myLocation();
      if (mine !== latest.current) return;
      setMe(here);
      const found =
        key === "toilet"
          ? await nearbyToilets(here, () => mine === latest.current)
          : await api.nearby(key, here.lat, here.lon);
      if (mine === latest.current) setResults(found);
    } catch (e) {
      // 서버가 제주 밖 좌표를 거절하면 입력 확인 문구가 오므로 제주 안내로 바꾼다
      if (mine === latest.current) setError(e.status === 422 ? MSG_OUTSIDE_JEJU : e.message || MSG_SEARCH_FAILED);
    } finally {
      if (mine === latest.current) setBusy(false);
    }
  };

  const label = NEARBY_KINDS.find((k) => k.key === kind)?.label;
  const points = [
    ...(me ? [{ key: "me", number: "나", name: "지금 위치", lat: me.lat, lon: me.lon }] : []),
    ...(results || []).map((r, i) => ({ key: `r${i}`, number: i + 1, name: r.name, lat: r.lat, lon: r.lon })),
  ];

  return (
    <main className="chat-screen">
      <AppHeader title="근처 찾기" back={onBack} />
      {me && (
        <div className="nearby-map">
          <KakaoMap points={points} />
        </div>
      )}
      <section className="messages">
        <div className="agent-row">
          <span className="agent-avatar">
            <Icon name="spark" size={18} />
          </span>
          <div className="message agent-message">무엇을 찾을까요? 지금 위치에서 가까운 곳을 알려드릴게요.</div>
        </div>
        <div className="nearby-kinds">
          {NEARBY_KINDS.map((k) => (
            <button key={k.key} className={k.key === kind ? "active" : ""} onClick={() => find(k.key)} disabled={busy}>
              {k.label}
            </button>
          ))}
        </div>
        {busy && <p className="result-count">찾는 중이에요…</p>}
        <FormError message={error} />
        {results && !busy && (
          <div className="facility-list">
            {results.length === 0 && <p className="empty-note">근처에서 {label}을 찾지 못했어요.</p>}
            {results.map((r, i) => (
              <div className="facility-card" key={`${r.name}-${i}`}>
                <b>
                  {i + 1}. {r.name}
                </b>
                <small>
                  {[`약 ${formatDistance(r.distance_m)}`, r.detail, ...(kind === "toilet" ? toiletNotes(r) : [])]
                    .filter(Boolean)
                    .join(" · ")}
                </small>
              </div>
            ))}
            {results.length > 0 && <p className="result-count">거리는 직선거리예요.</p>}
          </div>
        )}
      </section>
    </main>
  );
}

function DesktopNav({ screen, username, hasTrip, onNavigate, onLogout }) {
  const items = [
    { label: "내 여행", icon: "calendar", screens: ["planners", "editor"], target: "planners" },
    { label: "여행 안내", icon: "walk", screens: ["travel"], target: "travel", needsTrip: true },
    { label: "근처 찾기", icon: "spark", screens: ["chat"], target: "chat", needsTrip: true },
  ];

  return (
    <aside className="desktop-nav" aria-label="주요 메뉴">
      <button className="desktop-brand" onClick={() => onNavigate("planners")}>
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
  // 여행 진행 상태는 화면 밖에 둬서 메뉴로 오가도 유지한다
  const [trip, setTrip] = useState(null);
  const [stop, setStop] = useState(0);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [screen]);

  useEffect(() => {
    // 이미 로그인돼 있으면 로그인 화면을 건너뛴다
    api
      .me()
      .then((me) => {
        setUsername(me.username);
        setScreen((s) => (s === "auth" ? "planners" : s));
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
    setScreen("auth");
  };

  return (
    <div className={`app-shell screen-${screen}`}>
      {screen !== "welcome" && screen !== "auth" && (
        <DesktopNav
          screen={screen}
          username={username}
          hasTrip={Boolean(trip)}
          onNavigate={setScreen}
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
              setScreen(username ? "planners" : "auth");
            }}
          />
        )}
        {screen === "auth" && (
          <Auth
            onLogin={(name) => {
              setUsername(name);
              setScreen("planners");
            }}
          />
        )}
        {screen === "planners" && (
          <Planners
            onOpen={(id) => {
              setPlannerId(id);
              setScreen("editor");
            }}
            onLogout={logout}
            onAccountDeleted={signedOut}
          />
        )}
        {screen === "editor" && plannerId && (
          <Editor
            plannerId={plannerId}
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
            onChat={() => setScreen("chat")}
          />
        )}
        {screen === "chat" && <Chat onBack={() => setScreen(trip ? "travel" : "planners")} />}
      </div>
    </div>
  );
}
