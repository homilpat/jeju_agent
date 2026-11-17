import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // 저장소 최상위 .env에서 카카오 JS 키만 화면에 넘긴다(JS 키는 등록한 주소에서만 동작하는 공개용 키).
  // 다른 키(REST·허브·LLM)는 접두어가 달라 화면 코드로 새지 않는다.
  envDir: "../..",
  envPrefix: ["VITE_", "KAKAO_JS_"],
  server: {
    port: 8000, // 카카오 JS 키에 등록한 주소(localhost:8000)와 맞춤
    // 개발 중에는 화면(8000)에서 부른 /api 요청을 FastAPI(8001)로 넘긴다
    strictPort: true,
    proxy: { "/api": "http://localhost:8001" },
  },
});
