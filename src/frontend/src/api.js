// FastAPI 호출. 서버는 사용자에게 보여줄 한국어 문장을 message로 돌려준다.
const NETWORK_ERROR = "인터넷 연결이 원활하지 않아요. 잠시 후 다시 시도해 주세요.";
const UNKNOWN_ERROR = "잠시 문제가 생겼어요. 다시 시도해 주세요.";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body) {
  let res;
  try {
    res = await fetch(`/api${path}`, {
      method,
      credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(NETWORK_ERROR, 0);
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(data?.message || UNKNOWN_ERROR, res.status);
  return data;
}

export const api = {
  signup: (username, password) => request("POST", "/auth/signup", { username, password }),
  login: (username, password) => request("POST", "/auth/login", { username, password }),
  logout: () => request("POST", "/auth/logout"),
  me: () => request("GET", "/auth/me"),
  deleteAccount: () => request("DELETE", "/account"),
  listPlanners: () => request("GET", "/planners"),
  getPlanner: (id) => request("GET", `/planners/${id}`),
  createPlanner: (planner) => request("POST", "/planners", planner),
  updatePlanner: (id, planner) => request("PUT", `/planners/${id}`, planner),
  deletePlanner: (id) => request("DELETE", `/planners/${id}`),
  // 경로 계산용 임시 좌표(저장 안 함)
  compareRoutes: (origin, destination, preferCctv = false) =>
    request("POST", "/routes/compare", { origin, destination, prefer_cctv: preferCctv }),
  // 근처 시설. 화장실은 좌표 없는 후보(이름·주소)만 오고, 위치는 화면이 그때그때 찾는다
  nearbyToilets: (city, regions) =>
    request("GET", `/nearby/toilets?${new URLSearchParams([["city", city], ...regions.map((r) => ["region", r])])}`),
  nearby: (kind, lat, lon) => request("GET", `/nearby/${kind}?${new URLSearchParams({ lat, lon })}`),
};

// 범죄주의구간 지도 그림 주소(지금 보이는 지도 사각형·픽셀 크기). 서버가 키를 숨기고 대신 받아 준다
export const crimeOverlayUrl = ({ west, south, east, north, width, height }) =>
  `/api/overlays/crime?${new URLSearchParams({ west, south, east, north, width, height })}`;
