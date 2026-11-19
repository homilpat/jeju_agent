"""단일 오케스트레이터: 해석 -> 정보 보완 또는 도구 실행 -> 결과 관찰 -> 추가 조회/재계획/검증 -> 게시 -> 응답.

거리·시간·통행 제약·일정 유효성·권한은 코드가 판정한다. LLM(있으면)은 요청 해석만 한다.
도구 순서는 의도별로 코드가 고르고, 관찰 결과에 따라 검색 반경을 넓히거나 후보를 바꾼다
(필수 조건·복귀 시각은 바꾸지 않는다, AG-302/303).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

import shapely

from jeju_lifepath.agent import present
from jeju_lifepath.agent.itinerary import legs, plan_greedy, validate
from jeju_lifepath.agent import loop
from jeju_lifepath.agent.llm import _read, _say, _talk, llm_parse
from jeju_lifepath.agent.models import ParseResult, Slots, ToolError
from jeju_lifepath.agent.parse import LODGING, SEOUL, THEME_CATEGORY, is_kind, parse_rules
from jeju_lifepath.agent.services import AgentContext, build_tools
from jeju_lifepath.data.barrier_free import name_key
from jeju_lifepath.agent.tools import Budget, BudgetExceeded, ToolRunner
from jeju_lifepath.api.store import DraftOutdated, RunCancelled, Store, VersionConflict
from jeju_lifepath.geo.geodesy import haversine_m
from jeju_lifepath.routing.optimizer import SPEED_M_PER_MIN
from jeju_lifepath.schemas import MobilityProfile, RoutePreference

log = logging.getLogger(__name__)


# 지도를 아주 크게 확대했을 때도 이만큼은 찾는다(카카오 검색 최소 단위와 무관한 화면 기준값).
# shortcut: 근거 없는 값, 확대 화면에서 결과가 너무 적으면 조정
MIN_VIEW_RADIUS_M = 300
KAKAO_MAX_RADIUS_M = 20000  # 카카오 로컬 검색 반경 상한(API 규격)


LLM_STUCK = "잠깐 생각이 막혔구나, 다시 말해 주렴."
# 일정 종류 "무장애 여행지": 후보를 공공 무장애 조사 자료에서 고른다(카카오 분류가 아님)
BARRIER_FREE_THEME = "무장애 여행지"
# 잠정값: 후보마다 카카오 이름 검색을 한 번씩 해서 도구 호출 예산(22)을 아낌
BARRIER_FREE_CANDIDATES = 6
# 잠정값: 조사 자료 위치와 카카오 장소가 이만큼 안이면 같은 곳으로 봄
BARRIER_FREE_MATCH_M = 1000
# 이동 조건에 따라 결과가 달라지는 요청(길·일정·지역 공백)
MOBILITY_INTENTS = ("route", "route_poi", "plan_create", "plan_modify", "area_gap")


class NeedInput(Exception):  # noqa: N818 - 흐름 제어용(오류 아님)
    def __init__(self, questions: list[dict], pending: dict | None = None, replies: list | None = None,
                 missing: bool = False):
        super().__init__("needs_input")
        self.questions = questions
        self.pending = pending
        self.replies = replies  # None이면 질문의 버튼 답으로 정함
        # 빠진 정보(출발지·개수·경로 유형). 루프에서는 먼저 LLM에 돌려줘 사용자가 이미 말했는지 보게 한다.
        # 확인·승인 질문(고친 이름, 휠체어 짐작, 저장)과 후보 고르기는 False: 곧바로 사용자에게
        self.missing = missing


class Conflict(Exception):
    """같은 메시지 ID에 다른 내용(AG-405)."""


class _StaleTrip(Exception):  # noqa: N818 - 흐름 제어용
    """버전 충돌 뒤 다시 읽은 여행의 장소 목록이 달라 번호 지시를 다시 적용할 수 없음."""


class NotFound(Exception):
    """없는 실행·대화이거나 다른 사용자의 것(AT-15)."""


def _hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _option_pick(text: str, n: int) -> int | None:
    m = re.fullmatch(r"\s*(\d+)\s*(번|번째)?\s*(이요|요|으로|로)?\s*", text)
    if m and 1 <= int(m.group(1)) <= n:
        return int(m.group(1)) - 1
    return None


class Agent:
    def __init__(
        self,
        store: Store,
        ctx: AgentContext,
        llm=None,
        clock: Callable[[], datetime] = lambda: datetime.now(SEOUL),
        sleep: Callable[[float], None] = time.sleep,
        stage_hook: Callable[[str], None] | None = None,
    ):
        self.store = store
        self.ctx = ctx
        self.policy = ctx.policy
        self.llm = llm
        self.clock = clock
        self.sleep = sleep
        self.tools = build_tools(ctx)
        self._hook = stage_hook or (lambda _stage: None)
        self.last_trajectory: list[dict] = []  # 평가 어댑터가 읽는다
        self.keep_tool_results = False  # 평가(합성 세계)에서만 True: 도구 결과를 last_trajectory에

    # public -------------------------------------------------------------
    def handle_message(
        self,
        user_id: int,
        message_id: str,
        text: str,
        *,
        conversation_id: str | None = None,
        location: dict | None = None,
        trip_id: int | None = None,
        map_view: dict | None = None,
        screen: dict | None = None,
        detach_trip: bool = False,
    ) -> dict:
        """detach_trip: trip_id가 없으면 이 대화에 연결해 둔 여행도 쓰지 않는다(화면에서 연결을 끊음).
        screen: 사용자가 지금 보고 있는 화면({name, travel_stop?}). AI 해석에 함께 넘긴다.
        map_view: 사용자가 지금 보고 있는 지도({center: {lat, lon}, south, west, north, east}).
        AI가 같은 지도를 보고 "여기", "이 근처"를 이해하게 한다. 저장하지 않는다."""
        conv_id = conversation_id or uuid.uuid4().hex
        owner = self.store.conversation_owner(conv_id)
        if owner is not None and owner != user_id:
            raise NotFound(conv_id)
        loc_key = {k: round(v, 4) for k, v in location.items()} if location else None
        payload = {
            "conversation_id": conversation_id,
            "text": text,
            "trip_id": trip_id,
            "location": loc_key,
        }
        status, stored = self.store.start_run(
            user_id,
            message_id,
            conv_id,
            _hash(payload),
            {"text": text, "trip_id": trip_id, "conversation_id": conv_id, "screen": screen},
        )
        if status == "conflict":
            raise Conflict(message_id)
        if status == "duplicate":
            return stored or present.still_running(message_id, conv_id)
        return self._run(user_id, message_id, conv_id, text, location, trip_id, saved_parse=None,
                         map_view=map_view, screen=screen, detach_trip=detach_trip)

    def resume(
        self, user_id: int, run_id: str, location: dict | None = None,
        map_view: dict | None = None,
    ) -> dict:
        run = self.store.get_run(user_id, run_id)
        if run is None:
            raise NotFound(run_id)
        if run["response"] is not None:
            return run["response"]
        if run["stage"] != "interrupted":
            return present.still_running(run_id, run["conversation_id"])
        cp = run["checkpoint"]
        if run["committed"] and cp.get("draft"):
            trip_id, version = cp["committed_trip"]
            return self._finish(
                user_id,
                run_id,
                cp["conversation_id"],
                cp["draft"],
                trip_id,
                version,
                cp.get("state") or {},
                cp["text"],
            )
        if run["cancel_requested"]:
            resp = present.cancelled(run_id, run["conversation_id"], self.policy.version)
            self.store.update_run(
                user_id, run_id, stage="cancelled", status="cancelled", response=resp
            )
            return resp
        # 저장된 해석은 쓰지 않고 다시 해석한다: "6시까지" 같은 상대 시각과 운영·통제 정보는
        # 재개 시점 기준으로 다시 확인해야 한다(AG-504). 읽기 도구만 다시 부르므로 중복 변경은 없다.
        return self._run(
            user_id,
            run_id,
            run["conversation_id"],
            cp["text"],
            location,
            cp.get("trip_id"),
            saved_parse=None,
            pinned_version=cp.get("trip_version", 0) if "parse" in cp else None,
            map_view=map_view,
            screen=cp.get("screen"),
            history=cp.get("history"),
        )

    def cancel(self, user_id: int, run_id: str) -> bool:
        return self.store.request_cancel(user_id, run_id)

    def run_status(self, user_id: int, run_id: str, detail: bool = False) -> dict:
        run = self.store.get_run(user_id, run_id)
        if run is None:
            raise NotFound(run_id)
        out = {
            "run_id": run_id,
            "conversation_id": run["conversation_id"],
            "status": run["status"],
            "stage": run["stage"],
            "response": run["response"],
            "message": present.stage_text(run["stage"]),
        }
        if detail:  # 소유자만 부른다(엔드포인트가 세션 사용자로 조회). 화면 기본에는 쓰지 않는다
            out["trace"] = self.store.invocations(user_id, run_id)
        return out

    # core ---------------------------------------------------------------
    def _stage(self, user_id: int, run_id: str, stage: str, **kw) -> None:
        self.store.update_run(user_id, run_id, stage=stage, **kw)
        self._hook(stage)

    def _run(  # noqa: PLR0917
        self, user_id, run_id, conv_id, text, location, trip_id, saved_parse,
        pinned_version: int | None = None,
        map_view: dict | None = None,
        screen: dict | None = None,
        detach_trip: bool = False,
        history: list | None = None,
    ) -> dict:
        """history: 재개일 때 처음 실행 때의 앞 대화("거기"가 그사이 바뀐 대화를 가리키지 않게).
        pinned_version: 재개일 때 실행 당시 대상 여행 버전(None 여행이면 0). 대화의 현재 여행으로
        바꾸지 않고, 그사이 여행이 바뀌었으면 게시하지 않는다."""
        budget = Budget(self.policy)
        runner = ToolRunner(self.tools, self.store, user_id, run_id, budget, self.sleep)
        runner.keep_results = self.keep_tool_results
        self.last_trajectory = runner.trajectory
        found = self.store.get_conversation(user_id, conv_id)
        state = (
            found[0]
            if found
            else {"history": [], "pending": None, "trip_id": None, "options": None, "prefs": {}}
        )
        conv_rev = found[1] if found else 0
        state.setdefault("prefs", {})
        explicit_trip = trip_id is not None  # 화면이 지금 열어 둔 여행(플래너 화면에서 AI와 같이 짤 때)
        if detach_trip and trip_id is None:
            state["trip_id"] = None
        elif pinned_version is None:
            trip_id = trip_id or state.get("trip_id")
        trip = self.store.get_trip(user_id, trip_id) if trip_id else None
        if trip is None:
            trip_id = None
        ctx = {
            "user_id": user_id,
            "run_id": run_id,
            "conv_id": conv_id,
            "text": text,
            "location": location,
            "map_view": map_view,
            "explicit_trip": explicit_trip,
            "trip_id": trip_id,
            "trip": trip,
            "state": state,
            "runner": runner,
            "budget": budget,
            "replans": 0,
            "observations": [],
            "conv_rev": conv_rev,
        }
        if pinned_version and (trip is None or trip[1] != pinned_version):
            resp = present.version_conflict(
                {"run_id": run_id, "conv_id": conv_id}, self.policy.version
            )
            return self._close(ctx, resp, "failed", state)
        try:
            self._stage(user_id, run_id, "interpreting")
            talk = state.get("history", []) if history is None else history
            # LLM에는 카카오 응답(가게 이름·주소·좌표)을 보내지 않는다(카카오 운영정책, 데브톡 152791).
            # 대화 기록과 일정에는 카카오 자료가 없다(test_kakao_results_not_stored)
            if saved_parse is None and self._use_loop(text, state):
                draft = self._loop(text, state, talk, ctx, budget, present.screen_note(screen, trip))
                return self._publish(ctx, draft)
            parsed = saved_parse or self._parse(
                text, {**state, "history": talk}, bool(trip), budget,
                present.screen_note(screen, trip),
            )
            parsed = self._merge_pending(parsed, text, state)
            ctx["parse"] = parsed
            self._stage(
                user_id,
                run_id,
                "running_tool",
                checkpoint={
                    "text": text,
                    "trip_id": trip_id,
                    "trip_version": trip[1] if trip else 0,
                    "conversation_id": conv_id,
                    "screen": screen,  # 재개 때 같은 화면 맥락으로 다시 해석
                    "history": talk,  # 같은 앞 대화로
                    "parse": parsed.model_dump(mode="json"),
                },
            )
            draft = self._dispatch(parsed, ctx)
            return self._publish(ctx, draft)
        except NeedInput as q:
            state["pending"] = q.pending
            # 버튼으로 줄 수 있는 답: 이 말이 그대로 오면 해석과 상관없이 앞 질문의 답으로 본다
            state["pending_replies"] = q.replies if q.replies is not None else [
                o["reply"] for qq in q.questions for o in qq.get("options", [])
            ]
            resp = present.needs_input(
                run_id,
                conv_id,
                q.questions,
                self.policy.version,
                ctx.get("parse"),
                trip_id,
                trip[1] if trip else None,
            )
            return self._close(ctx, resp, "completed", state)
        except BudgetExceeded as b:
            resp = (
                present.cancelled(run_id, conv_id, self.policy.version)
                if b.kind == "cancelled"
                else present.budget_stop(run_id, conv_id, b.kind, ctx, self.policy.version)
            )
            return self._close(ctx, resp, "cancelled" if b.kind == "cancelled" else "failed", state)
        except RunCancelled:
            resp = present.cancelled(run_id, conv_id, self.policy.version)
            return self._close(ctx, resp, "cancelled", state)
        except ToolError as e:
            log.warning("agent tool error %s", e.kind)
            resp = present.tool_failure(run_id, conv_id, e.kind, ctx, self.policy.version)
            return self._close(ctx, resp, "failed", state)
        except Exception:
            log.exception("agent run failed")  # 개발자 기록용. 사용자에게는 일반 문구
            resp = present.tool_failure(run_id, conv_id, "internal", ctx, self.policy.version)
            return self._close(ctx, resp, "failed", state)

    def _close(self, ctx, resp, stage, state) -> dict:
        # 대화 기록: 새로고침 후 화면 복원과 LLM의 앞 대화(카카오 자료는 넣지 않음). 횟수 제한 없이
        # 보유기간(trace_retention_days)이 지나면 대화째 지운다
        state["history"] = [
            *state.get("history", []),
            {"role": "user", "text": ctx["text"]},
            {"role": "agent", "text": resp["message"]},
        ]
        if ctx.get("trip_id"):
            state["trip_id"] = ctx["trip_id"]
        resp["summary"] = present.run_summary(ctx["runner"].trajectory)
        # 화면이 갈 곳: 해석한 요청 종류 그대로(일정 -> 플래너, 길찾기 -> 경로 안내, 그 밖 -> 대화)
        intent = ctx["parse"].intent if ctx.get("parse") else None
        resp["screen"] = resp.get("screen") or (
            "planner" if intent in ("plan_create", "plan_modify") and resp.get("itinerary")
            and resp["status"] == "ok"
            else "route" if intent == "route" and resp.get("routes") and resp["status"] == "ok"
            else "chat"
        )
        # 카카오 검색 결과(이름·주소·좌표)는 저장하지 않는다: 저장본·대화 기록에서는 빼고 화면에만 보낸다
        stored = present.for_storage(resp, present.used_kakao(ctx["runner"].trajectory))
        state["history"][-1]["text"] = stored["message"]
        self._save_state(ctx, state)
        self.store.update_run(
            ctx["user_id"], ctx["run_id"], stage=stage, status=resp["status"], response=stored
        )
        return resp

    def _save_state(self, ctx, state: dict) -> None:
        """같은 대화의 다른 요청이 먼저 저장했으면(revision 다름) 최신 상태를 다시 읽어
        이번 요청의 대화 기록 두 줄과 바꾼 칸만 얹는다(마지막 저장이 앞 변경을 지우지 않게)."""
        mine = {
            k: state.get(k)
            for k in ("pending", "pending_replies", "options", "prefs", "picked", "days")
        }
        added = state["history"][-2:]
        if ctx.get("trip_id"):
            mine["trip_id"] = ctx["trip_id"]
        for _ in range(3):
            found = self.store.get_conversation(ctx["user_id"], ctx["conv_id"])
            # 실행 중에 사용자가 이 대화 기록을 지웠다: 다시 만들지 않는다. 첫 메시지라 대화 행이 아직
            # 없었으면, 지울 때 함께 지워지는 이 실행의 기록이 없는 것으로 안다
            if found is None and (
                ctx.get("conv_rev", 0) or self.store.get_run(ctx["user_id"], ctx["run_id"]) is None
            ):
                return
            base, rev = found if found else ({"history": []}, 0)
            if rev == ctx.get("conv_rev", 0):
                merged = state
            else:
                merged = {**base, **{k: v for k, v in mine.items() if v is not None}}
                merged["history"] = [*base.get("history", []), *added]
            if self.store.save_conversation(
                ctx["user_id"], ctx["conv_id"], merged, rev, run_id=ctx["run_id"]
            ):
                return
        log.warning("conversation state save kept losing the race")

    def _publish(self, ctx, draft: dict) -> dict:
        body = draft.pop("commit", None)
        state = ctx["state"]
        state["pending"] = None
        state["options"] = draft.pop("options_state", None)
        for k in ("mobility", "prefer_cctv", "route_preference"):
            v = getattr(ctx["parse"].slots, k)
            if v is not None:
                state["prefs"][k] = v.value if hasattr(v, "value") else v
        if body is None:
            draft.setdefault("trip_id", ctx["trip_id"])
            if draft.get("saved_trip"):
                draft["unsaved"] = None
            draft.setdefault("trip_version", ctx["trip"][1] if ctx["trip"] else None)
            return self._close(ctx, draft, "completed", state)
        self._stage(ctx["user_id"], ctx["run_id"], "validating")
        if self.ctx.trip_validator:
            try:
                body = self.ctx.trip_validator(body)
            except Exception:
                log.warning("agent trip body failed planner validation")
                resp = present.validation_failed(ctx, ctx["parse"], {}, self.policy.version)
                return self._close(ctx, resp, "failed", state)
        self._stage(ctx["user_id"], ctx["run_id"], "committing")
        if ctx["parse"].intent == "plan_create":
            empty = ctx["trip"] and not ctx["trip"][0].get("places")
            if ctx.get("explicit_trip") and empty:
                # 열어 둔 빈 플래너를 채운다(제목·날짜는 사용자가 정한 값 유지)
                old = ctx["trip"][0]
                keep = {k: old[k] for k in ("title", "start_date", "end_date") if old.get(k)}
                if keep.get("title") == "새 여행":
                    keep.pop("title")
                body = {**body, **keep}
            else:  # 그 밖의 새 일정은 언제나 새 여행으로(기존 여행을 덮지 않음)
                ctx["trip_id"], ctx["trip"] = None, None
        expected = ctx["trip"][1] if ctx["trip"] else None
        draft["screen"] = "planner"  # 게시하는 건 늘 일정: 재개해도 플래너로 가게 저장본에 남긴다
        cp = {
            "text": ctx["text"],
            "trip_id": ctx["trip_id"],
            "conversation_id": ctx["conv_id"],
            "draft": present.for_storage(draft, True),
            "state": state,
        }
        while True:
            ctx["runner"].check()  # deadline·취소 뒤에는 게시하지 않는다(AG-306)
            try:
                trip_id, version = self.store.commit_trip(
                    ctx["user_id"], ctx["run_id"], ctx["trip_id"], expected, body, cp
                )
                break
            except VersionConflict:
                # 다른 곳에서 먼저 고쳤다: 최신 여행을 다시 읽고 이번 요청의 변경만 다시 적용(1회)
                ctx["replans"] += 1
                if ctx["replans"] > self.policy.max_replans or ctx["parse"].intent != "plan_modify":
                    resp = present.version_conflict(ctx, self.policy.version)
                    return self._close(ctx, resp, "failed", state)
                ctx["trip"] = self.store.get_trip(ctx["user_id"], ctx["trip_id"])
                if ctx["trip"] is None:
                    raise NotFound(ctx["trip_id"]) from None
                try:
                    draft = self._dispatch(ctx["parse"], ctx)
                except _StaleTrip:
                    resp = present.version_conflict(ctx, self.policy.version)
                    return self._close(ctx, resp, "failed", state)
                body = draft.pop("commit", None)
                draft.pop("options_state", None)
                if body is None:
                    return self._close(ctx, draft, "completed", state)
                if self.ctx.trip_validator:
                    body = self.ctx.trip_validator(body)
                expected = ctx["trip"][1]
                cp["draft"] = present.for_storage(draft, True)
        if "days_after" in ctx:  # 여러 날: 이 날을 게시했으니 남은 날만 기억
            state["days"] = ctx["days_after"]
        self._hook("committed")
        return self._finish(
            ctx["user_id"],
            ctx["run_id"],
            ctx["conv_id"],
            draft,
            trip_id,
            version,
            state,
            ctx["text"],
            ctx,
        )

    def _finish(
        self, user_id, run_id, conv_id, draft, trip_id, version, state, text, ctx=None
    ) -> dict:
        # unsaved: 아직 내 여행에 넣지 않은 AI 초안(화면이 저장 버튼을 보일지 정하는 데만 씀)
        info = self.store.draft_info(user_id, trip_id)
        draft = {**draft, "trip_id": trip_id, "trip_version": version, "unsaved": info}
        state["trip_id"] = trip_id
        ctx = ctx or {
            "user_id": user_id,
            "run_id": run_id,
            "conv_id": conv_id,
            "text": text,
            "trip_id": trip_id,
            "runner": ToolRunner({}, self.store, user_id, run_id, Budget(self.policy)),
        }
        ctx["trip_id"] = trip_id
        return self._close(ctx, draft, "completed", state)

    # interpretation -------------------------------------------------------
    # LLM 중심 루프 ----------------------------------------------------------
    def _use_loop(self, text: str, state: dict) -> bool:
        """도구 호출을 지원하는 LLM이면 루프. 버튼 답·후보 번호 고르기는 우리가 만든 질문의 답이라
        앞 요청을 그대로 이어 가는 기존 길로(빠르고 틀리지 않음)."""
        if not (self.policy.agent_loop and hasattr(self.llm, "chat")):
            return False
        if text.strip() in state.get("pending_replies", []):
            return False
        options = state.get("options")
        return not (options and _option_pick(text, len(options["items"])) is not None)

    def _loop(self, text: str, state: dict, talk: list, ctx, budget: Budget, note: str) -> dict:
        """LLM이 도구를 고르고 Harness가 검증·실행해 관찰을 돌려준다. LLM이 답을 끝내거나 예산이 다할 때까지."""
        now = self.clock()
        msgs = [{"role": "system", "content": loop.LOOP_PROMPT}]
        # 대화 기록에는 카카오 자료가 없다(저장할 때 뺌)
        msgs += [{"role": "user" if h.get("role") == "user" else "assistant", "content": str(h.get("text", ""))}
                 for h in talk[-40:]]
        msgs.append({"role": "user", "content": f"[지금 시각: {now.strftime('%Y-%m-%d %H:%M')} (서울)]\n"
                                                f"[사용자가 보고 있는 화면: {note}]\n{text}"})
        context = _talk(talk) + note  # 장소 이름 확인(대화·화면에 나온 이름인지)
        draft = None
        shown_parse, nudged = None, False
        for _ in range(self.policy.max_loop_steps):
            ctx["runner"].check()  # 취소·시간 예산(도구와 같은 기준)
            budget.llm_calls += 1
            try:
                msg = self.llm.chat(msgs, loop.SCHEMAS)
            except ToolError as e:
                log.info("llm loop failed: %s", e.kind)
                raise NeedInput([{"text": LLM_STUCK}], state.get("pending"),
                                state.get("pending_replies", [])) from None
            calls = msg.get("tool_calls") or []
            reply = next((c for c in calls if (c.get("function") or {}).get("name") == "reply"), None)
            if reply is not None:  # 그냥 답하기도 도구로: 이번 말의 끝 행동이 늘 명시된다
                try:
                    said = json.loads(reply["function"].get("arguments") or "{}").get("text")
                except (ValueError, AttributeError):
                    said = None
                for c in calls:  # 같은 걸음에 부른 다른 도구는 실행하고 이 답으로 끝낸다
                    if c is not reply:
                        _obs, shown = self._loop_tool(c.get("function") or {}, text, context, now, ctx)
                        if shown is not None:
                            draft, shown_parse = shown, ctx["parse"]
                msg, calls = {"content": said}, []
            elif not calls and not nudged and draft is None and not ctx["runner"].trajectory:
                # 행동을 고르지 않고 글로만 끝내면 한 번 되묻는다(못 하는 요청이 자유 문장 거절로 흩어지지 않게).
                # API가 도구 호출 강제(tool_choice=required)를 지원하지 않아서 Harness가 대신 한다
                nudged = True
                msgs.append({"role": "assistant", "content": msg.get("content") or ""})
                msgs.append({"role": "user", "content": "[도우미 규칙] 답을 끝내려면 reply, 못 하는 요청이면 cannot_do, "
                                                        "물어야 하면 ask_user 중 하나를 불러라."})
                continue
            if not calls and ctx.get("pending_missing"):  # 앞 화면보다 아직 못 푼 질문이 먼저
                raise ctx["pending_missing"]  # 빠진 정보를 채우지 못함: 원래 질문으로 사용자에게
            if not calls:
                final = _say(msg.get("content"))
                if draft is None:
                    ctx["parse"] = ParseResult(intent="chat", reply=final or LLM_STUCK, parser=self.llm.mode,
                                               reference_time=now.isoformat())
                    return present.chat(ctx, ctx["parse"], self.policy.version)
                if final and final not in draft["message"]:
                    draft["message"] = f"{draft['message']} {final}"
                ctx["parse"] = shown_parse  # 게시(_publish)는 이 화면을 만든 해석 기준으로
                return draft
            msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for c in calls:
                # 도구가 돌려준 공공자료 이름(지식그래프)도 대화에 나온 이름으로 본다(지어낸 이름 아님)
                context += " " + " ".join(ctx.get("seen_names", []))
                obs, shown = self._loop_tool(c.get("function") or {}, text, context, now, ctx)
                if shown is not None:
                    draft, shown_parse = shown, ctx["parse"]
                msgs.append({"role": "tool", "tool_call_id": c.get("id", ""),
                             "content": json.dumps(obs, ensure_ascii=False)})
        if draft is None:  # 예산 안에 답을 끝내지 못함
            raise ctx.get("pending_missing") or NeedInput([{"text": LLM_STUCK}], None)
        ctx["parse"] = shown_parse
        return draft

    def _loop_tool(self, fn: dict, text: str, context: str, now, ctx) -> tuple[dict, dict | None]:
        """도구 하나 실행 -> (LLM에 줄 관찰, 화면 응답 또는 None). 잘못된 인자·실패는 구조화된 실패로 돌려준다."""
        name = fn.get("name")
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = None
        if name not in loop.INTENT or not isinstance(args, dict):
            return {"error": "invalid_call", "detail": "모르는 도구이거나 인자가 JSON 객체가 아님"}, None
        if name == "ask_user":
            if ctx.get("pending_missing"):  # 도구가 만든 질문이 있으면 그것으로(버튼·앞 요청 이어가기)
                raise ctx["pending_missing"]
            q = _say(args.get("question"))
            if not q:
                return {"error": "invalid_args", "detail": "질문 문장이 비었거나 영어 코드가 섞임"}, None
            opts = [o for o in (_say(x) for x in (args.get("options") or [])[:4]) if o]
            raise NeedInput([{"text": q, **({"options": [{"label": o, "reply": o} for o in opts]} if opts else {})}],
                            None)
        try:
            return self._loop_run(name, args, text, context, now, ctx)
        except NeedInput as q:
            if not q.missing or name in ctx.setdefault("fed_back", set()):
                raise
            ctx["fed_back"].add(name)
            ctx["pending_missing"] = q
            return {"status": "needs_more_info", "missing": [x["text"] for x in q.questions],
                    "hint": "사용자가 이미 말한 정보면 인자를 채워 다시 부르고, 아니면 ask_user"}, None

    def _loop_run(self, name: str, args: dict, text: str, context: str, now, ctx) -> tuple[dict, dict | None]:
        if name == "knowledge_graph":
            return self._kg_tool(args, text, context, now, ctx)
        if name == "save_trip" and "저장" not in text:
            # LLM 판단만으로 여행을 저장·덮어쓰지 않는다(위험한 상태 변경은 사용자 승인)
            raise NeedInput([{"text": "이 일정을 내 여행에 저장할까?",
                              "options": [{"label": "응, 저장해줘", "reply": "내 여행에 저장해줘"}]}], None)
        names = args.pop("barrier_free_names", None)
        try:
            p = _read({**args, "intent": loop.INTENT[name]}, text, context, now, self.llm.mode)
        except (ValueError, TypeError, AttributeError) as e:
            return {"error": "invalid_args", "detail": type(e).__name__}, None
        ctx["parse"] = p
        ctx["bf_names"] = names if isinstance(names, list) else None
        try:
            shown = self._dispatch(p, ctx)
        except ToolError as e:  # 다른 도구·조건으로 다시 해 볼 수 있게 이유를 돌려준다
            return {"error": e.kind, "tool": name}, None
        ctx.pop("pending_missing", None)
        return loop.observe(shown, present.used_kakao(ctx["runner"].trajectory)), shown

    def _kg_tool(self, args: dict, text: str, context: str, now, ctx) -> tuple[dict, dict | None]:
        if args.get("within_km") and not args.get("location_text"):
            args["use_current_location"] = True  # 거리 조건은 기준 위치가 있어야 함
        filters = {k: v for k, v in args.items() if k not in ("location_text", "use_current_location")}
        if args.get("location_text") or args.get("use_current_location"):
            where = {k: args[k] for k in ("location_text", "use_current_location") if k in args}
            ctx["parse"] = _read({**where, "intent": "barrier_free"}, text, context, now, self.llm.mode)
            try:
                self._ask_first(ctx["parse"])  # 대화에 없는 장소 이름은 확인부터
                at = self._point(ctx["parse"], ctx)
            except NeedInput as q:
                # 답은 버튼 이어가기(검색 조건 없는 기존 길)가 아니라 루프로: LLM이 같은 조건으로 다시 찾는다
                q.pending, q.replies = None, []
                raise
            filters["near"] = {"lon": at["lon"], "lat": at["lat"]}  # 좌표는 도구에만, LLM에는 안 보냄
        try:
            found = ctx["runner"].call("knowledge_graph", filters)["places"]
        except ToolError as e:
            return {"error": e.kind, "tool": "knowledge_graph"}, None
        ctx.setdefault("seen_names", []).extend(x["name"] for x in found)
        # 공공 조사 자료라 이름까지 LLM에 보낸다(카카오 자료 아님). 기준 위치 좌표는 보내지 않는다
        cols = ("name", "distance_m", "disabled_toilet", "disabled_parking", "barrier_points", "has_course",
                "recommended_for", "nearby")
        obs = {"results": [{k: x[k] for k in cols} for x in found]}
        if not found:
            return {**obs, "note": "조건에 맞는 곳이 없음. 조건을 줄여 다시 해 볼 수 있음"}, None
        ctx.setdefault("parse", ParseResult(intent="barrier_free", parser=self.llm.mode))
        return obs, present.kg_places(ctx, found, self.policy.version)

    def _parse(
        self, text: str, state: dict, has_trip: bool, budget: Budget, screen_note: str = ""
    ) -> ParseResult:
        now = self.clock()
        # 버튼 답은 우리가 만든 정해진 문장이라 LLM 없이 푼다(빠르고 틀리지 않음)
        button = text.strip() in state.get("pending_replies", [])
        if self.llm is not None and not button and budget.llm_calls < self.policy.max_llm_calls:
            budget.llm_calls += 1
            try:
                return llm_parse(self.llm, text, now, state.get("history", []), screen_note)
            except (ToolError, ValueError) as e:
                # 규칙 해석으로 넘기면 엉뚱하게 알아듣는다. 모른다고 솔직히 말하고 다시 듣는다
                log.info("llm parse failed: %s", type(e).__name__)
                # 앞 질문에 답하던 중이면 그 질문과 버튼 답을 그대로 남겨 다시 답할 수 있게 한다
                raise NeedInput(
                    [{"text": LLM_STUCK}], state.get("pending"), state.get("pending_replies", [])
                ) from None
        return parse_rules(text, now, has_trip)  # LLM이 없을 때(개발·시험)와 버튼 답만

    def _merge_pending(self, parsed: ParseResult, text: str, state: dict) -> ParseResult:
        """앞 질문의 답이면 원래 요청을 이어간다(AT-01). 새 요청이면 앞 질문은 버린다."""
        pending = state.get("pending")
        options = state.get("options")
        if options and (pick := _option_pick(text, len(options["items"]))) is not None:
            chosen = options["items"][pick]
            base = ParseResult.model_validate(options["parse"])
            slots = base.slots.model_copy(update={options["slot"]: chosen["query"]})
            picked = dict(base.slot_sources, **{options["slot"]: "explicit"})
            # 출발지·도착지를 차례로 고를 수 있게 앞에서 고른 것도 남긴다
            state.setdefault("picked", {})[options["slot"]] = chosen["ref"]
            return base.model_copy(
                update={"slots": slots, "slot_sources": picked, "ambiguities": []}
            )
        if not pending:
            state.pop("picked", None)  # 새 요청: 앞 요청에서 고른 후보는 쓰지 않음
            return parsed
        base = ParseResult.model_validate(pending)
        # LLM은 앞 질문을 보고 답이면 같은 요청으로 해석한다. 그래서 LLM이 잡담으로 본 말은 답이 아님
        # ("처음에 뭐라 했지?"가 앞 질문의 답으로 실행되지 않게). 규칙 해석은 잡담도 답으로 본다
        talk = ("chat",) if parsed.parser == "rules" else ()
        answer_only = text.strip() in state.get("pending_replies", []) or parsed.intent in (
            *talk, base.intent
        ) or (
            base.intent == "plan_modify" and parsed.intent in (*talk, "plan_modify")
        )
        if not answer_only:
            return parsed
        merged = base.slots.model_dump()
        sources = {k: "previous" for k in base.slot_sources}
        for k, v in parsed.slots.model_dump().items():
            if k in parsed.slot_sources:
                merged[k] = v
                sources[k] = "explicit"
        rules = parsed.parser == "rules"  # 글자 맞추기는 규칙 해석일 때만. LLM 값은 덮지 않음
        if rules and re.search(r"돌아|원점|제자리", text):
            merged["round_trip"] = True
        if parsed.slots.use_current_location:
            merged["location_text"] = None
        if rules and re.fullmatch(r"\s*(걸어서|걸어|도보)\s*(로|요|갈게요|갈래요)?\s*", text):
            merged["transport"] = None
        changes = parsed.requested_changes or base.requested_changes
        return base.model_copy(
            update={
                "slots": Slots(**merged),
                "slot_sources": sources,
                # 규칙 해석: 고칠 대상을 답했으면 앞의 "몇 번째?" 되묻기는 끝남. LLM 질문(지난 시각 등)은 남김
                "ambiguities": parsed.ambiguities
                if base.intent != "plan_modify" or not changes or parsed.parser != "rules"
                else [],
                "requested_changes": changes,
                "reference_time": parsed.reference_time,
                # 앞에서 남은 확인 + 이번 답에서 새로 고친 이름(답에서 고친 이름도 묻는다)
                "confirm": list(dict.fromkeys([*base.confirm, *parsed.confirm])),
                "ask_mobility": parsed.ask_mobility or base.ask_mobility,  # 다른 질문에 먼저 답해도 남김
                "mobility_unclear": base.mobility_unclear or parsed.mobility_unclear,  # 이동 조건이 채워지면 묻지 않음
            }
        )

    # dispatch -----------------------------------------------------------
    def _dispatch(self, p: ParseResult, ctx: dict) -> dict:
        self._ask_first(p)
        if p.intent == "unsupported" or (
            p.slots.transport in ("car", "bus", "taxi") and p.intent in ("route", "plan_create")
        ):
            return present.unsupported(ctx, p, self.policy.version)
        return self._dispatch_handler(p, ctx)

    def _ask_first(self, p: ParseResult) -> None:
        """실행 전에 사용자에게 물을 것(빠진 조건, 몸이 불편할 때, 짐작한 이동 조건, 대화에 없는 이름)."""
        if p.ambiguities:
            raise NeedInput([{"text": a} for a in p.ambiguities], p.model_dump(mode="json"))
        # 이동이 힘들다고만 했으면 길·일정을 짜기 전에 어떤 도움이 필요한지 묻는다(짐작해서 정하지 않음)
        if p.mobility_unclear and not p.slots.mobility and p.intent in MOBILITY_INTENTS:
            opts = [("휠체어", "휠체어로 갈게"), ("유모차", "유모차로 갈게"), ("아니, 걸어서", "그냥 걸어서 갈게")]
            ask = [{"text": "휠체어나 유모차를 쓰니? 맞는 길로 찾아 줄게.",
                    "options": [{"label": a, "reply": b} for a, b in opts]}]
            # 이동 조건이 채워질 때까지 표시를 남긴다(다른 것만 답하면 다시 묻는다)
            raise NeedInput(ask, p.model_dump(mode="json"))
        if p.ask_mobility:  # 짐작한 휠체어·유모차는 묻고 나서 쓴다. 답 버튼의 낱말로 규칙 해석이 칸을 채움
            word = present.MOBILITY_WORD[p.ask_mobility]
            yes, no = f"응, {word} 길로", "아니, 그냥 걸어서"
            ask = [{"text": f"{word} 쓰는 길로 찾아줄까?",
                    "options": [{"label": yes, "reply": yes}, {"label": no, "reply": no}]}]
            raise NeedInput(ask, p.model_copy(update={"ask_mobility": None}).model_dump(mode="json"))
        if p.confirm:  # 고친 이름은 하나씩 묻는다(버튼 하나로 여러 곳을 승인하지 않게)
            name, rest = p.confirm[0], p.confirm[1:]
            ask = [{"text": f"{name} 말하는 거니?", "options": [{"label": "응, 맞아", "reply": "응, 맞아"}]}]
            # 고친 이름은 이미 칸에 들어 있어서 "응, 맞아"면 남은 확인을 거쳐 그대로 이어간다
            raise NeedInput(ask, p.model_copy(update={"confirm": rest}).model_dump(mode="json"))

    def _dispatch_handler(self, p: ParseResult, ctx: dict) -> dict:
        handler = {
            "guide": self._guide,
            "route": self._route,
            "route_poi": self._route_poi,
            "area_gap": self._area_gap,
            "plan_create": self._plan_create,
            "plan_modify": self._plan_modify,
            "save_trip": self._save_trip,
            "olle": self._olle,
            "barrier_free": self._barrier_free,
        }.get(p.intent)
        if handler is None:
            return present.chat(ctx, p, self.policy.version)
        draft = handler(p, ctx)
        if p.unsupported_features:  # 지원 부분은 처리하고, 못 하는 부분은 따로 알린다(AT-09)
            draft["status"] = "partial" if draft["status"] == "ok" else draft["status"]
            draft["warnings"] = draft.get("warnings", []) + [
                present.unsupported_line(p.unsupported_features)
            ]
        return draft

    def _mobility(self, p: ParseResult, ctx) -> MobilityProfile:
        if p.slots.mobility:
            return p.slots.mobility
        saved = ctx["state"]["prefs"].get("mobility")
        if saved:
            return MobilityProfile(saved)
        body = ctx["trip"][0] if ctx.get("trip") else {}
        return MobilityProfile(body.get("mobility") or "STANDARD")

    def _point(
        self,
        p: ParseResult,
        ctx,
        *,
        text_slot: str = "location_text",
        ask: str = "어디를 기준으로 찾아 줄까?",
    ) -> dict:
        """기준 위치. 사용자가 말한 지명을 먼저, 없으면 화면이 보낸 현재 위치. 둘 다 없으면 묻는다."""
        text = getattr(p.slots, text_slot)
        if text:
            if text in LODGING:
                raise NeedInput(
                    [{"text": f"{text} 이름이나 주소를 알려 주렴."}], self._without(p, text_slot)
                )
            return self._resolve(text, p, ctx, text_slot)
        if ctx["location"]:
            return {
                "lon": ctx["location"]["lon"],
                "lat": ctx["location"]["lat"],
                "label": "지금 위치",
            }
        if ctx.get("map_view"):  # 사용자가 보고 있는 지도 가운데(답에 "지도 가운데"라고 밝힌다)
            c = ctx["map_view"]["center"]
            return {"lon": c["lon"], "lat": c["lat"], "label": "지도 가운데"}
        raise NeedInput([{"text": ask, "needs_location": True}], p.model_dump(mode="json"), missing=True)

    @staticmethod
    def _without(p: ParseResult, slot: str) -> dict:
        d = p.model_dump(mode="json")
        d["slots"][slot] = None
        return d

    def _resolve(self, text: str, p: ParseResult, ctx, slot: str) -> dict:
        picked = ctx["state"].get("picked", {}).get(slot)
        r = ctx["runner"].call("resolve_location", {"text": text})
        if picked and r["status"] == "ambiguous":
            match = [o for o in r["options"] if o["ref"] == picked]
            if match:
                return {"lon": match[0]["lon"], "lat": match[0]["lat"], "label": match[0]["name"],
                        "ref": picked}
        if picked:  # 고른 후보가 다시 찾은 목록에 없으면 이름으로 다시 찾는다
            r2 = ctx["runner"].call("search_places", {"query": text})
            match = [o for o in r2["places"] if o["ref"] == picked]
            if match:
                return {"lon": match[0]["lon"], "lat": match[0]["lat"], "label": match[0]["name"],
                        "ref": picked}
        if r["status"] == "found":
            return {"lon": r["lon"], "lat": r["lat"], "label": r["label"], "ref": r.get("ref")}
        if r["status"] == "not_found":
            raise NeedInput(
                [{"text": f"'{text}'을(를) 찾지 못했구나. 다른 이름이나 주소로 알려 주렴."}],
                self._without(p, slot),
            )
        items = [
            {"ref": o["ref"], "query": text, "label": f"{o['name']} ({o['address']})"}
            for o in r["options"]
        ]
        ctx["state"]["options"] = {
            "slot": slot,
            "items": [{"ref": it["ref"], "query": it["query"]} for it in items],
            "parse": p.model_dump(mode="json"),
        }
        raise NeedInput(
            [
                {
                    "text": f"'{text}'(으)로 찾은 곳이 여러 곳이구나(입구·주차장 등). 어디로 할까?",
                    "options": [
                        {"label": it["label"], "reply": f"{i + 1}번"} for i, it in enumerate(items)
                    ],
                }
            ],
            None,
        )

    def _search(self, ctx, query: str, near: dict) -> tuple[list[dict], bool]:
        """주변 검색. 0곳이면 반경을 한 번 넓힌다(조건은 그대로, 검색 전략만 변경)."""
        if query == BARRIER_FREE_THEME:
            return self._barrier_free_candidates(ctx, near), False
        radius = self.policy.search_radius_m
        view = ctx.get("map_view")
        if near.get("label") == "지도 가운데" and view:
            # 지도에 보이는 범위(가운데에서 모서리까지)만큼 찾는다. 카카오 반경 상한 20km
            corner = haversine_m(view["center"]["lon"], view["center"]["lat"], view["east"],
                                 view["north"])
            radius = int(min(max(corner, MIN_VIEW_RADIUS_M), KAKAO_MAX_RADIUS_M))
        args = {
            "query": query,
            "near": {"lon": near["lon"], "lat": near["lat"]},
            "radius_m": radius,
            "category": THEME_CATEGORY.get(query),
            "only_kind": True,
        }
        found = ctx["runner"].call("search_places", args)["places"]
        if found or radius >= self.policy.widened_radius_m:
            return found, False
        ctx["observations"].append("widened")
        args["radius_m"] = self.policy.widened_radius_m
        return ctx["runner"].call("search_places", args)["places"], True

    def _barrier_free_candidates(self, ctx, near: dict) -> list[dict]:
        """일정 후보: 가까운 무장애 여행지(공공 조사 자료)를 이름으로 카카오에서 찾아 보통 장소처럼 쓴다.
        저장·수정·다시 열기는 카카오 장소와 같은 길(검색어 = 여행지 이름). 같은 이름이 없으면 뺀다."""
        names = ctx.get("bf_names")
        if names:  # 지식그래프로 고른 여행지(LLM이 넘김): 그 이름들만, 공공 자료에서 바로
            want = {name_key(n) for n in names}
            picked = [x for x in self.ctx.barrier_free if name_key(x["name"]) in want][:BARRIER_FREE_CANDIDATES]
        else:
            picked = ctx["runner"].call(
                "barrier_free_places",
                {"near": {"lon": near["lon"], "lat": near["lat"]}, "limit": BARRIER_FREE_CANDIDATES},
            )["places"]
        out = []
        for bf in picked:
            name = bf["name"][:40]
            # 다시 열 때와 같은 검색(이름만, 카카오 정확도 순)으로 찾는다. 조사 위치 근처만.
            # "산지천갤러리와산짓물공원"처럼 두 곳을 묶은 이름은 못 찾으면 앞 이름으로 한 번 더
            hit = None
            first = re.split(r"(?<=[가-힣])(?:와|과)(?=[가-힣])", name)[0]
            # 앞 이름이 너무 짧으면("제주과학관" -> "제주") 다른 곳이 잡히므로 쓰지 않는다
            for word in dict.fromkeys([name, first] if len(first) >= 4 else [name]):  # noqa: PLR2004
                hits = ctx["runner"].call("search_places", {"query": word})["places"]
                want = name_key(word)
                # 같은 곳 = 이름이 같거나 앞에 지역 이름만 붙은 곳("제주동문시장", "제주특별자치도 민속자연사박물관").
                # 뒤에 말이 붙은 곳("격자공원 주차장", "○○박물관점")은 딸린 시설이라 뺀다
                same = [
                    h for h in hits
                    if name_key(h["name"]).endswith(want)
                    and haversine_m(bf["lon"], bf["lat"], h["lon"], h["lat"]) <= BARRIER_FREE_MATCH_M
                ]
                same.sort(key=lambda h: name_key(h["name"]) != want)  # 같은 이름 먼저, 그다음 카카오 정확도 순
                if same:
                    hit, name = same[0], word
                    break
            if hit is None:
                ctx["observations"].append("barrier_free_unmatched")
                continue
            out.append({**hit, "query": name, "access": " · ".join(present.access_bits(bf))})
        return out

    # intents ------------------------------------------------------------
    def _guide(self, p: ParseResult, ctx) -> dict:
        if p.slots.place_query and not p.slots.facility:  # 근처 시설 요청이 먼저
            r = ctx["runner"].call("search_places", {"query": p.slots.place_query})
            places = r["places"]
            picked = ctx["state"].pop("picked", {}).get("place_query")
            if picked:  # 앞에서 번호로 고른 곳
                places = [x for x in places if x["ref"] == picked] or places
            exact = [x for x in places if x["name"] == p.slots.place_query]
            if picked and len(places) == 1:
                exact = places
            if len(places) > 1 and len(exact) != 1:
                items = [
                    {
                        "ref": x["ref"],
                        "query": p.slots.place_query,
                        "label": f"{x['name']} ({x['address']})",
                    }
                    for x in places[:3]
                ]
                return present.place_choices(ctx, p, items, self.policy.version)
            return present.place_info(ctx, p, (exact or places)[:1], r, self.policy.version)
        at = self._point(
            p, ctx, ask="어디 근처에서 찾아 줄까? 지금 위치를 쓰거나 동네 이름을 알려 주렴."
        )
        if p.slots.facility:
            r = ctx["runner"].call(
                "find_nearby_facilities",
                {"kind": p.slots.facility, "at": {"lon": at["lon"], "lat": at["lat"]}},
            )
            return present.facilities(ctx, p, at, r, self.policy.version)
        if p.slots.themes:
            found, widened = self._search(ctx, p.slots.themes[0], at)
            return present.theme_places(ctx, p, at, found[:5], widened, self.policy.version)
        return present.chat(ctx, p, self.policy.version)

    def _barrier_free(self, p: ParseResult, ctx) -> dict:
        """무장애 여행지. 장소 이름을 말했으면 그곳 상세, 아니면 기준 위치에서 가까운 곳."""
        if p.slots.place_query:
            r = ctx["runner"].call("barrier_free_places", {"name": p.slots.place_query})
            return present.barrier_free(ctx, p, None, r, self.policy.version)
        at = self._point(p, ctx, ask="어디 근처를 찾아 줄까? 지금 위치를 쓰거나 동네 이름을 알려 주렴.")
        r = ctx["runner"].call("barrier_free_places", {"near": {"lon": at["lon"], "lat": at["lat"]}})
        return present.barrier_free(ctx, p, at, r, self.policy.version)

    def _olle(self, p: ParseResult, ctx) -> dict:
        """올레 코스 안내. 코스 번호를 말했으면 그 코스, 아니면 기준 위치에서 가까운 코스."""
        said = " ".join(filter(None, [p.slots.place_query, ctx["text"]]))
        m = re.search(r"(\d+(?:-\d+)?)\s*코스", said)
        if m:
            r = ctx["runner"].call("olle_courses", {"course": m.group(1)})
        else:
            at = self._point(p, ctx, ask="어디 근처 올레 코스를 찾아 줄까? 지금 위치를 쓰거나 동네 이름을 알려 주렴.")
            r = ctx["runner"].call("olle_courses", {"near": {"lon": at["lon"], "lat": at["lat"]}})
        return present.olle(ctx, p, r, self.policy.version)

    def _route(self, p: ParseResult, ctx) -> dict:
        origin = self._point(
            p, ctx, ask="어디에서 출발하니? 지금 위치를 쓰거나 출발지 이름을 알려 주렴."
        )
        if not p.slots.destination_text:
            raise NeedInput([{"text": "어디로 가니?"}], p.model_dump(mode="json"))
        # 경로 유형을 말하지 않았으면 묻는다(§6). 이 대화에서 이미 정했으면 다시 묻지 않는다
        saved_pref = ctx["state"]["prefs"].get("route_preference")
        pref = p.slots.route_preference or (RoutePreference(saved_pref) if saved_pref else None)
        if pref is None:
            raise NeedInput([present.route_preference_question()], p.model_dump(mode="json"), missing=True)
        dest = self._point(p, ctx, text_slot="destination_text")
        mobility = self._mobility(p, ctx)
        prefer_cctv = bool(
            p.slots.prefer_cctv
            if p.slots.prefer_cctv is not None
            else ctx["state"]["prefs"].get("prefer_cctv")
        )
        r = ctx["runner"].call(
            "compare_routes",
            {
                "origin": {"lon": origin["lon"], "lat": origin["lat"]},
                "destination": {"lon": dest["lon"], "lat": dest["lat"]},
                "mobility": mobility.value,
                "prefer_cctv": prefer_cctv,
            },
        )
        return present.routes(ctx, p, origin, dest, mobility, r, self.policy.version, pref)

    def _route_poi(self, p: ParseResult, ctx) -> dict:
        """두 곳 사이 걷는 길에 들를 시설(§2 C02, §11.1 후보 규칙). 고르는 건 사용자(한 곳 단정 추천 안 함)."""
        origin = self._point(
            p, ctx, ask="어디에서 출발하니? 지금 위치를 쓰거나 출발지 이름을 알려 주렴."
        )
        if not p.slots.destination_text:
            raise NeedInput([{"text": "어디로 가는 길이니?"}], p.model_dump(mode="json"))
        dest = self._point(p, ctx, text_slot="destination_text")
        mobility = self._mobility(p, ctx)
        r = ctx["runner"].call(
            "find_route_stops",
            {
                "origin": {"lon": origin["lon"], "lat": origin["lat"]},
                "destination": {"lon": dest["lon"], "lat": dest["lat"]},
                "mobility": mobility.value,
                "facility": p.slots.facility,
                "theme": None if p.slots.facility else (p.slots.themes or [None])[0],
            },
        )
        return present.route_stops(
            ctx, p, origin, dest, mobility, r, self._speed(mobility), self.policy.version
        )

    def _area_gap(self, p: ParseResult, ctx) -> dict:
        """한 지역의 시설 접근 공백(§2 E01, FR-115). 지역 = 말한 읍면동(행정동 경계), 아니면 말한 곳이 속한 동."""
        if not p.slots.facility:
            raise NeedInput(
                [
                    {
                        "text": "어떤 시설을 살펴볼까?",
                        "options": [
                            {"label": word, "reply": word}
                            for word in ("약국", "화장실", "와이파이", "파출소")
                        ],
                    }
                ],
                p.model_dump(mode="json"),
            )
        areas = self.ctx.areas
        if not areas:
            return present.areas_unavailable(ctx, self.policy.version)
        name, said = None, p.slots.location_text
        if said:
            # 말한 이름이 읍면동 이름이면 그 동("이도2동"), 앞부분만 맞으면 후보("이도" -> 이도1동·이도2동)
            hits = [said] if said in areas else sorted(n for n in areas if n.startswith(said))
            if len(hits) == 1:
                name = hits[0]
            elif len(hits) > 1:  # 고른 번호로 이 요청을 이어간다(_merge_pending의 후보 선택)
                ctx["state"]["options"] = {
                    "slot": "location_text",
                    "items": [{"ref": n, "query": n} for n in hits],
                    "parse": p.model_dump(mode="json"),
                }
                raise NeedInput(
                    [{"text": "어느 동네를 살펴볼까?",
                      "options": [{"label": n, "reply": f"{i + 1}번"} for i, n in enumerate(hits)]}],
                    None,
                )
        place = None
        if name is None:  # 동네 이름이 아니면 그 장소(또는 지금 위치)가 속한 읍면동
            place = self._point(p, ctx, ask="어느 동네를 살펴볼까? 동네 이름을 알려 주렴.")
            name = next(
                (n for n, g in areas.items() if shapely.contains_xy(g, place["lon"], place["lat"])),
                None,
            )
            if name is None:
                return present.outside_areas(ctx, place, self.policy.version)
        mobility = self._mobility(p, ctx)
        r = ctx["runner"].call(
            "analyze_area_access",
            {"area": name, "facility": p.slots.facility, "mobility": mobility.value},
        )
        return present.area_access(
            ctx, p, name, place, r, mobility, self._speed(mobility), self.policy.version
        )

    def _save_trip(self, p: ParseResult, ctx) -> dict:
        """대화에서 만든 일정 초안을 내 여행에 넣는다(화면의 저장 버튼과 같은 동작)."""
        trip_id = ctx.get("trip_id")
        info = self.store.draft_info(ctx["user_id"], trip_id) if trip_id else None
        if info is None:
            return present.nothing_to_save(ctx, bool(trip_id), self.policy.version)
        mode = p.slots.save_mode
        if info["replaces"] and mode is None:
            raise NeedInput([present.save_mode_question()], p.model_dump(mode="json"))
        ctx["runner"].check()  # 취소·시간 초과 뒤에는 저장하지 않는다
        try:
            saved = self.store.save_draft(ctx["user_id"], trip_id, overwrite=mode == "overwrite")
        except DraftOutdated:
            return present.save_outdated(ctx, self.policy.version)
        if saved is None:
            return present.nothing_to_save(ctx, True, self.policy.version)
        ctx["trip_id"] = saved
        return present.saved(ctx, saved, trip_id, mode == "overwrite", self.policy.version)

    def _speed(self, mobility: MobilityProfile) -> float:
        return SPEED_M_PER_MIN[mobility]

    def _dist_fn(self, ctx, mobility: MobilityProfile):
        def dist(src, targets):
            if not targets:
                return []
            r = ctx["runner"].call(
                "get_travel_matrix",
                {
                    "source": {"lon": src[0], "lat": src[1]},
                    "targets": [{"lon": t[0], "lat": t[1]} for t in targets],
                    "mobility": mobility.value,
                },
            )
            return r["metres"]

        return dist

    def _budget_minutes(self, s: Slots, direct_min: float) -> float | None:
        if s.return_by:
            start = datetime.fromisoformat(s.depart_at) if s.depart_at else self.clock()
            return (datetime.fromisoformat(s.return_by) - start).total_seconds() / 60
        if s.free_minutes:
            return direct_min + s.free_minutes  # §11.4: 직행 대비 늘어나는 시간
        return None

    def _plan_create(self, p: ParseResult, ctx) -> dict:
        s = p.slots
        days = ctx["state"].get("days")
        if s.next_day and not s.themes:  # "2일차 일정도 짜줘": 앞서 말한 여러 날 중 다음 날
            if not days or not days.get("left"):
                raise NeedInput(
                    [{"text": "이어서 짤 날이 없구나. 어떤 곳을 들르고 싶은지 말해 주렴."}], None
                )
            nxt = days["left"][0]
            # 앞 날과 같은 조건(출발지·돌아오기·들를 곳 수)으로, 그날의 종류만 바꾼다
            first = datetime.fromisoformat(days["first_date"]).date()
            s = Slots(**{**days["base"], "themes": nxt["themes"], "day_no": nxt["day"],
                         "date": (first + timedelta(days=nxt["day"] - 1)).isoformat()})
            p = p.model_copy(update={"slots": s})
            ctx["days_after"] = {**days, "left": days["left"][1:]}
        elif len(s.day_themes) >= 2:  # noqa: PLR2004 - 여러 날 요청: 하루씩 짠다(첫날부터)
            s = s.model_copy(update={"themes": s.day_themes[0], "theme_counts": {}, "day_no": 1})
            p = p.model_copy(update={"slots": s})
            base = s.model_dump(mode="json", exclude={"themes", "day_themes", "theme_counts",
                                                     "next_day", "day_no", "date"})
            ctx["days_after"] = {
                "base": base,
                # 1일차 날짜(말하지 않았으면 오늘). 2일차부터 하루씩 더한다
                "first_date": s.date or self.clock().date().isoformat(),
                "left": [{"day": i + 2, "themes": t} for i, t in enumerate(s.day_themes[1:])],
            }
        questions = []
        if not s.themes:
            questions.append({"text": "어떤 곳을 들르고 싶니? 예: 빵집, 카페, 바다"})
        if not (s.stop_count or s.theme_counts or s.free_minutes or s.return_by):
            questions.append({"text": "몇 곳을 들를까? 아니면 몇 시까지 돌아와야 하니?"})
        if not (s.destination_text or s.round_trip):
            questions.append(
                {
                    "text": "다 둘러본 뒤엔 어디로 가니?",
                    "options": [{"label": "출발한 곳으로 돌아오기", "reply": "돌아올게요"}],
                }
            )
        if not (s.location_text or ctx["location"] or ctx.get("map_view")):
            questions.append(
                {
                    "text": "어디에서 출발하니? 지금 위치를 쓰거나 출발지 이름을 알려 주렴.",
                    "needs_location": True,
                }
            )
        if questions:  # 관련 질문을 한 번에 묶어 묻는다(AG-107)
            raise NeedInput(questions, p.model_dump(mode="json"), missing=True)
        start = self._point(p, ctx)
        ctx["start_label"] = start["label"]
        end = start if s.round_trip else self._point(p, ctx, text_slot="destination_text")
        mobility = self._mobility(p, ctx)
        m_per_min = self._speed(mobility)
        dist = self._dist_fn(ctx, mobility)
        direct = (
            0.0
            if s.round_trip
            else (dist((start["lon"], start["lat"]), [(end["lon"], end["lat"])])[0] or 0)
            / m_per_min
        )
        budget_min = self._budget_minutes(s, direct)
        if budget_min is not None and budget_min <= 0:
            raise NeedInput(
                [{"text": "돌아올 시각이 벌써 지났구나. 시각을 다시 알려 주렴."}],
                p.model_dump(mode="json"),
            )
        pool, widened = [], False
        for theme in s.themes:
            found, w = self._search(ctx, theme, start)
            widened |= w
            for place in found:
                if place["ref"] not in {x["ref"] for x in pool}:
                    # 저장 검색어: 지명이 있으면 '애월 빵집'처럼 붙여 둔다(화면이 다시 열 때 이 말로 다시 찾음).
                    # 무장애 여행지는 그 여행지 이름으로 다시 찾는다
                    query = place.get("query") or (
                        f"{s.location_text} {theme}" if s.location_text else theme
                    )
                    pool.append({**place, "theme": theme, "query": query[:40]})
        if not pool:
            return present.no_candidates(ctx, p, self.policy.version)
        if s.theme_counts:  # "빵집 2곳, 카페 1곳" (LLM 해석, 검증됨)
            # 개수를 말하지 않은 종류("카페도")는 1곳
            theme_quota = {t: s.theme_counts.get(t, 1) for t in s.themes}
            s = s.model_copy(update={"stop_count": sum(theme_quota.values())})
        else:  # 분류별 개수가 없으면 총 개수를 분류 수로 나눠 올림(분류마다 최소 1곳)
            quota = math.ceil((s.stop_count or len(s.themes)) / len(s.themes))
            theme_quota = dict.fromkeys(s.themes, quota)
        chosen, obs = plan_greedy(
            start,
            end,
            pool,
            dist,
            m_per_min,
            round_trip=s.round_trip,
            stop_count=s.stop_count,
            budget_min=budget_min,
            stay_min=s.stay_minutes or 0,
            theme_quota=theme_quota,
            max_stops=self.policy.max_stops,
        )
        if not chosen:
            return present.nothing_fits(ctx, p, obs, self.policy.version)
        stops = [{**c, "fixed": False, "completed": False} for c in chosen]
        leg_list = legs(start, end, stops, dist, m_per_min)
        check = validate(
            stops,
            leg_list,
            stay_min=s.stay_minutes or 0,
            budget_min=budget_min,
            fixed_refs=set(),
            excluded_refs=set(),
            completed_refs=set(),
        )
        if not check["publishable"]:
            return present.validation_failed(ctx, p, check, self.policy.version)
        # 여유 시간으로 받은 예산도 고칠 때 지킬 수 있게 "몇 시까지"로 바꿔 저장한다
        deadline = s.return_by or (
            (self.clock() + timedelta(minutes=budget_min)).isoformat() if budget_min else None
        )
        body = self._trip_body(p, s.model_copy(update={"return_by": deadline}), stops, mobility,
                               start, ctx)
        return present.itinerary(
            ctx,
            p,
            stops,
            leg_list,
            check,
            obs,
            widened,
            s,
            mobility,
            self.policy.version,
            created=True,
            commit=body,
            next_day=(ctx.get("days_after") or {}).get("left", [None])[0]
            if (ctx.get("days_after") or {}).get("left")
            else None,
        )

    def _trip_body(self, p, s: Slots, stops, mobility, start, ctx, base: dict | None = None):
        base = base or {}
        title = base.get("title") or (
            (f"{s.day_no}일차 · " if s.day_no else "") + "·".join(s.themes) + " 코스"
        )[:60]
        places = [
            {"source": "kakao", "ref": x["ref"], "name": x["query"], "category": x["theme"]}
            for x in stops
        ]
        return {
            **base,
            "title": title,
            "start_date": base.get("start_date") or (s.date or self.clock().date().isoformat()),
            "end_date": base.get("end_date"),
            "places": places,
            "route_choices": [RoutePreference.SHORTEST.value] * max(0, len(places) - 1)
            if not base
            else _fit(base.get("route_choices", []), len(places)),
            "prefer_cctv": base.get("prefer_cctv"),
            "mobility": mobility.value,
            "agent": {
                "anchor_text": s.location_text,
                # 기준 지명으로 고른 카카오 장소 번호: 이름이 여러 곳에 맞아도 같은 곳으로 다시 찾는다
                "anchor_ref": start.get("ref") if s.location_text else None,
                # 지금 위치에서 시작한 일정만: 고칠 때 같은 출발·복귀점을 쓰려고 약 10m 단위로 저장
                # (사용자 자신의 위치, 카카오 자료 아님. 여행을 지우면 함께 지워짐)
                "start_point": None
                if s.location_text or start.get("label") not in ("지금 위치", "지도 가운데")
                else {"lon": round(start["lon"], 4), "lat": round(start["lat"], 4)},
                "destination_text": None if s.round_trip else s.destination_text,
                "round_trip": s.round_trip,
                "return_by": s.return_by,
                "stay_minutes": s.stay_minutes,
                "themes": s.themes,
                "stops": [
                    {
                        "ref": x["ref"],
                        "query": x["query"],
                        "theme": x["theme"],
                        "fixed": bool(x.get("fixed")),
                        "completed": bool(x.get("completed")),
                        # 무장애 조사 자료의 접근성 요약(공공자료, 카카오 자료 아님)
                        **({"access": x["access"]} if x.get("access") else {}),
                    }
                    for x in stops
                ],
                "excluded_refs": sorted(
                    set((base.get("agent") or {}).get("excluded_refs", []))
                    | set(ctx.get("excluded", []))
                ),
            },
        }

    def _plan_modify(self, p: ParseResult, ctx) -> dict:
        if not ctx["trip"]:
            raise NeedInput(
                [
                    {
                        "text": "고칠 여행 일정이 없구나. 먼저 일정을 만들어 보렴. 예: 애월에서 빵집 두 곳 들르는 코스 짜줘"
                    }
                ],
                None,
            )
        body, _version, _ = ctx["trip"]
        if any(pl["source"] != "kakao" for pl in body.get("places", [])):
            raise NeedInput(
                [{"text": "직접 고른 공공시설이 들어 있는 일정은 여행 화면에서 고쳐 주렴."}], None
            )
        refs = [pl["ref"] for pl in body.get("places", [])]
        ctx.setdefault("orig_refs", refs)
        if refs != ctx["orig_refs"]:  # 그사이 장소 목록이 바뀜: 번호가 달라져 같은 변경을 적용할 수 없음
            raise _StaleTrip()
        agent = body.get("agent") or {}
        meta = {m["ref"]: m for m in agent.get("stops", [])}
        stops = [
            {
                "ref": pl["ref"],
                "query": pl["name"],
                "theme": pl.get("category") or pl["name"],
                "fixed": meta.get(pl["ref"], {}).get("fixed", False),
                "completed": meta.get(pl["ref"], {}).get("completed", False),
                "access": meta.get(pl["ref"], {}).get("access"),
            }
            for pl in body.get("places", [])
            if pl["source"] == "kakao"
        ]
        if not p.requested_changes:
            raise NeedInput(
                [{"text": "일정을 어떻게 바꿔 줄까? 예: 2번 빼줘, 1번 다녀왔어, 카페 먼저"}],
                p.model_dump(mode="json"),
            )
        s = Slots(
            location_text=p.slots.location_text or agent.get("anchor_text"),
            destination_text=agent.get("destination_text"),
            round_trip=agent.get("round_trip", True),
            return_by=p.slots.return_by or agent.get("return_by"),
            stay_minutes=agent.get("stay_minutes"),
            themes=agent.get("themes", []),
        )
        excluded = set(agent.get("excluded_refs", []))
        for c in p.requested_changes:
            if c.op in ("exclude", "keep", "complete"):
                idx = len(stops) - 1 if c.last else (c.target or 0) - 1
                if not 0 <= idx < len(stops):
                    raise NeedInput(
                        [
                            {
                                "text": f"일정에 {c.target}번 장소가 없구나. 1~{len(stops)}번 중에서 골라 주렴."
                            }
                        ],
                        None,
                    )
                if c.op == "exclude":
                    excluded.add(stops.pop(idx)["ref"])
                elif c.op == "keep":
                    stops[idx]["fixed"] = True
                else:
                    stops[idx]["completed"] = True
        ctx["excluded"] = sorted(excluded)
        mobility = self._mobility(p, ctx)
        m_per_min = self._speed(mobility)
        dist = self._dist_fn(ctx, mobility)
        p_for_point = p.model_copy(update={"slots": s})
        saved_start = agent.get("start_point")
        if agent.get("anchor_ref") and not p.slots.location_text:
            ctx["state"].setdefault("picked", {})["location_text"] = agent["anchor_ref"]
        if saved_start and not p.slots.location_text:
            start = {**saved_start, "label": "출발한 곳"}
        else:
            start = self._point(p_for_point, ctx)
        end = start if s.round_trip else self._point(p_for_point, ctx, text_slot="destination_text")
        self._locate(ctx, stops, start)
        for c in p.requested_changes:
            if c.op in ("prioritize", "add_theme") and c.theme:
                self._add_theme(ctx, stops, c, start, end, dist, excluded, s)
        leg_list = legs(start, end, stops, dist, m_per_min)
        budget_min = None
        if s.return_by:  # 이미 다녀온 곳은 지난 시간이므로 지금부터 남은 일정만 본다
            budget_min = (datetime.fromisoformat(s.return_by) - self.clock()).total_seconds() / 60
        check = validate(
            stops,
            leg_list,
            stay_min=s.stay_minutes or 0,
            budget_min=budget_min,
            fixed_refs={x["ref"] for x in stops if x["fixed"]},
            excluded_refs=excluded,
            completed_refs={x["ref"] for x in stops if x["completed"]},
        )
        if "over_time" in check["errors"]:
            return present.over_time(
                ctx, p, stops, leg_list, check, budget_min, self.policy.version
            )
        if not check["publishable"]:
            return present.validation_failed(ctx, p, check, self.policy.version)
        new_body = self._trip_body(p, s, stops, mobility, start, ctx, base=body)
        return present.itinerary(
            ctx,
            p,
            stops,
            leg_list,
            check,
            {},
            False,
            s,
            mobility,
            self.policy.version,
            created=False,
            commit=new_body,
        )

    def _locate(self, ctx, stops: list[dict], anchor: dict) -> None:
        """저장된 장소(ID·검색어)를 기준 위치 주변에서 다시 검색해 지금 좌표·이름을 얻는다.
        찾지 못한 장소는 일정에 그대로 두고 구간 시간을 '확인 못 함'으로 둔다."""
        by_query: dict[str, list[dict]] = {}
        for st in stops:
            # 만들 때 찾은 검색어는 분류(theme). 같은 기준점에서 거리순으로 찾으면 처음 고른 곳이
            # 같은 순서 안에 다시 나온다(만들 때 반경은 최대 20km라 같은 최대 반경으로)
            query = relocate_query(st)
            if st.get("theme") == BARRIER_FREE_THEME:  # 이름 그대로 만들 때와 같은 검색으로
                args = {"query": query}
                by_query.setdefault(query, ctx["runner"].call("search_places", args)["places"])
            elif query not in by_query:
                args = {
                    "query": query,
                    "near": {"lon": anchor["lon"], "lat": anchor["lat"]},
                    "radius_m": KAKAO_MAX_RADIUS_M,
                    "category": THEME_CATEGORY.get(query),
                }
                by_query[query] = ctx["runner"].call("search_places", {**args, "only_kind": True})[
                    "places"
                ]
            hit = [x for x in by_query[query] if x["ref"] == st["ref"]]
            if not hit and not THEME_CATEGORY.get(query):  # 종류 거르기 전에 만든 여행
                hit = [x for x in ctx["runner"].call("search_places", args)["places"]
                       if x["ref"] == st["ref"]]
            if hit:
                st.update(
                    {k: hit[0][k] for k in ("name", "category", "address", "lon", "lat", "url")}
                )
            else:
                ctx["observations"].append("unlocated")

    def _add_theme(self, ctx, stops, c, start, end, dist, excluded, s) -> None:
        remaining = [x for x in stops if not x["completed"]]
        existing = [x for x in remaining if x["theme"] == c.theme]
        if c.op == "prioritize" and existing:
            stops.remove(existing[0])
            first_open = next((i for i, x in enumerate(stops) if not x["completed"]), len(stops))
            stops.insert(first_open, existing[0])
            return
        done = [x for x in stops if x["completed"] and "lon" in x]
        here = done[-1] if done else start
        found, _ = self._search(ctx, c.theme, here)
        taken = {x["ref"] for x in stops} | excluded
        # 무장애 여행지는 여행지 이름으로 다시 찾으므로 후보의 이름 검색어를 지킨다
        pool = [{**f, "theme": c.theme, "query": f.get("query") or c.theme}
                for f in found if f["ref"] not in taken]
        if not pool:
            ctx["observations"].append("theme_not_found")
            return
        # 시간 제한 없이 우회가 가장 적은 한 곳(시간 초과는 뒤의 검증이 따로 막는다)
        picked, _ = plan_greedy(
            here,
            end,
            pool,
            dist,
            1.0,
            round_trip=True,
            stop_count=1,
            budget_min=None,
            stay_min=0,
            theme_quota={c.theme: 1},
            max_stops=1,
        )
        if not picked:
            ctx["observations"].append("theme_not_found")
            return
        new = {**picked[0], "fixed": False, "completed": False}
        first_open = next((i for i, x in enumerate(stops) if not x["completed"]), len(stops))
        stops.insert(first_open if c.op == "prioritize" else len(stops), new)


def relocate_query(stop: dict) -> str:
    """저장된 장소를 다시 찾을 검색어: 만들 때 쓴 분류(theme), 없으면 저장된 검색어.
    무장애 여행지는 분류로 찾을 수 없어(카카오 분류 아님) 저장된 여행지 이름으로."""
    if stop.get("theme") == BARRIER_FREE_THEME:
        return stop["query"]
    return stop.get("theme") or stop["query"]


def locate_trip(kakao, body: dict) -> dict[str, dict]:
    """AI가 만든 여행의 장소를 만들 때와 같은 기준(기준점 둘레 거리순, 같은 검색어)으로 다시 찾는다.
    플래너 화면이 쓴다(이름·좌표는 응답에만, 저장 안 함). 기준점을 정할 수 없으면 빈 사전."""
    meta = body.get("agent") or {}
    anchor = meta.get("start_point")
    if anchor is None and meta.get("anchor_text"):
        text, ref = meta["anchor_text"], meta.get("anchor_ref")
        at = None if ref else kakao.address(text)
        if at:
            anchor = {"lon": at[0], "lat": at[1]}
        else:
            found = kakao.keyword(text)
            pick = [p for p in found if p["ref"] == ref] if ref else (
                found if len(found) == 1 else [p for p in found if p["name"] == text])
            anchor = {"lon": pick[0]["lon"], "lat": pick[0]["lat"]} if len(pick) == 1 else None
    if anchor is None:
        return {}
    stops = {s["ref"]: s for s in meta.get("stops", [])}
    out, by_query = {}, {}
    for pl in body.get("places", []):
        st = stops.get(pl["ref"])
        if pl.get("source") != "kakao" or st is None:
            continue
        query = relocate_query(st)
        if st.get("theme") == BARRIER_FREE_THEME:  # 무장애 여행지: 이름 그대로(만들 때와 같은 검색)
            by_query.setdefault(query, kakao.keyword(query))
        elif query not in by_query:
            at = (anchor["lon"], anchor["lat"])
            code = THEME_CATEGORY.get(query)
            if code:
                by_query[query] = kakao.category(code, at, KAKAO_MAX_RADIUS_M)
            else:  # 만들 때와 같은 검색(최대 45곳에서 종류 거르기) + 거르기 전에 만든 여행용 첫 쪽
                every, _ = kakao.search_all(query=query, near=at, radius_m=KAKAO_MAX_RADIUS_M)
                by_query[query] = [p for p in every if is_kind(p, query)] + kakao.keyword(
                    query, at, KAKAO_MAX_RADIUS_M)
        hit = [x for x in by_query[query] if x["ref"] == pl["ref"]]
        if hit:
            out[pl["ref"]] = hit[0]
    return out


def _fit(choices: list, n: int) -> list:
    need = max(0, n - 1)
    return (list(choices) + [RoutePreference.SHORTEST.value] * need)[:need]
