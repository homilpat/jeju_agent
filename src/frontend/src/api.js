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
  // mobility: 플래너에 저장된 이동 조건(유모차·휠체어). 없으면 서버 기본(일반 보행)
  compareRoutes: (origin, destination, preferCctv = false, mobility = null) =>
    request("POST", "/routes/compare", {
      origin,
      destination,
      prefer_cctv: preferCctv,
      ...(mobility ? { mobility } : {}),
    }),
  // AI 길잡이. message_id는 화면이 만든 번호: 같은 번호로 다시 보내도 한 번만 실행된다
  agentMessage: (body) => request("POST", "/agent/messages", body),
  agentCancel: (runId) => request("POST", `/agent/runs/${encodeURIComponent(runId)}/cancel`),
  agentRun: (runId) => request("GET", `/agent/runs/${encodeURIComponent(runId)}`),
  agentResume: (runId, location, mapView) =>
    request("POST", `/agent/runs/${encodeURIComponent(runId)}/resume`, {
      location: location || null,
      map_view: mapView || null,
    }),
  agentConversation: (id) => request("GET", `/agent/conversations/${encodeURIComponent(id)}`),
};

// 범죄주의구간 지도 그림 주소(지금 보이는 지도 사각형·픽셀 크기). 서버가 키를 숨기고 대신 받아 준다
export const crimeOverlayUrl = ({ west, south, east, north, width, height }) =>
  `/api/overlays/crime?${new URLSearchParams({ west, south, east, north, width, height })}`;
