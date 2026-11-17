// 카카오 지도 JS SDK 불러오기와 장소 검색.
// 카카오 운영정책: 검색 결과(좌표·주소)는 화면에 쓰는 동안만 메모리에 두고 저장하지 않는다.
// 플래너에는 장소 ID와 사용자가 쓴 검색어만 저장하고, 다시 열 때 그 검색어로 다시 검색해 같은 ID를 찾는다.

const KEY = import.meta.env.KAKAO_JS_KEY;
// shortcut: 제주도 전체(추자도·마라도 포함)를 덮는 사각형(서남·동북 꼭짓점의 위도·경도), 검색을 제주로 좁히는 용도.
// 행정경계와 같지 않아서 결과 주소가 "제주"로 시작하는지도 한 번 더 본다.
const JEJU_SW = [33.0, 125.9];
const JEJU_NE = [34.05, 127.0];

export const MSG_MAP_FAILED = "지도를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.";
export const MSG_SEARCH_FAILED = "장소 검색에 실패했어요. 잠시 후 다시 시도해 주세요.";

let loading = null;

export function loadKakao() {
  if (loading) return loading;
  loading = new Promise((resolve, reject) => {
    if (!KEY) return reject(new Error(MSG_MAP_FAILED));
    const script = document.createElement("script");
    script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${KEY}&libraries=services&autoload=false`;
    script.onload = () => window.kakao.maps.load(() => resolve(window.kakao));
    script.onerror = () => reject(new Error(MSG_MAP_FAILED));
    document.head.appendChild(script);
  }).catch((e) => {
    loading = null; // 다음에 다시 시도할 수 있게
    throw e;
  });
  return loading;
}

// 검색 결과 한 건을 화면용 값으로 바꾼다. category는 "음식점 > 카페 > 커피전문점"의 마지막 칸만.
export function toPlace(doc) {
  return {
    id: doc.id,
    name: doc.place_name,
    category: doc.category_name ? doc.category_name.split(" > ").pop() : null,
    address: doc.road_address_name || doc.address_name,
    lon: Number(doc.x),
    lat: Number(doc.y),
  };
}

export const inJeju = (p) => (p.address || "").startsWith("제주");

export async function searchPlaces(query) {
  const kakao = await loadKakao().catch(() => {
    throw new Error(MSG_SEARCH_FAILED);
  });
  const places = new kakao.maps.services.Places();
  return new Promise((resolve, reject) => {
    places.keywordSearch(
      query,
      (data, status) => {
        if (status === kakao.maps.services.Status.OK) resolve(data.map(toPlace).filter(inJeju));
        else if (status === kakao.maps.services.Status.ZERO_RESULT) resolve([]);
        else reject(new Error(MSG_SEARCH_FAILED));
      },
      {
        bounds: new kakao.maps.LatLngBounds(
          new kakao.maps.LatLng(...JEJU_SW),
          new kakao.maps.LatLng(...JEJU_NE),
        ),
      },
    );
  });
}

// 저장된 장소(ID·검색어)를 다시 검색해 지금 좌표를 얻는다. 검색은 됐는데 같은 ID가 없으면 null,
// 검색 자체가 실패하면 예외(호출하는 쪽이 다시 시도할 수 있게 구분).
export async function resolvePlace(saved) {
  const found = await searchPlaces(saved.name);
  return found.find((p) => p.id === saved.ref) || null;
}
