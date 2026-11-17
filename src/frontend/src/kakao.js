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

const inJejuBox = (lat, lon) => lat >= JEJU_SW[0] && lat <= JEJU_NE[0] && lon >= JEJU_SW[1] && lon <= JEJU_NE[1];

// 지금 위치의 시와 읍면동 이름(법정동·행정동 둘 다). 제주 밖이면 null
export async function regionOf(lat, lon) {
  if (!inJejuBox(lat, lon)) return null;
  const kakao = await loadKakao();
  const geocoder = new kakao.maps.services.Geocoder();
  return new Promise((resolve, reject) => {
    geocoder.coord2RegionCode(lon, lat, (data, status) => {
      if (status !== kakao.maps.services.Status.OK) return reject(new Error(MSG_SEARCH_FAILED));
      const jeju = data.filter((d) => d.region_1depth_name.startsWith("제주"));
      if (jeju.length === 0) return resolve(null);
      resolve({
        city: jeju[0].region_2depth_name,
        regions: [...new Set(jeju.map((d) => d.region_3depth_name).filter(Boolean))],
      });
    });
  });
}

// 주소 -> 좌표. 화면에 쓰는 동안만 메모리에 두고 저장하지 않는다(카카오 운영정책).
// 주소를 못 찾으면 null, 검색 자체가 실패하면 예외(빠진 후보로 엉뚱한 가까운 곳을 보여주지 않게)
export async function geocode(address) {
  const kakao = await loadKakao();
  const geocoder = new kakao.maps.services.Geocoder();
  const { OK, ZERO_RESULT } = kakao.maps.services.Status;
  return new Promise((resolve, reject) => {
    geocoder.addressSearch(address, (data, status) => {
      if (status === OK) resolve({ lat: Number(data[0].y), lon: Number(data[0].x) });
      else if (status === ZERO_RESULT) resolve(null);
      else reject(new Error(MSG_SEARCH_FAILED));
    });
  });
}
