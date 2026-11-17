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

from jeju_lifepath.agent import present
from jeju_lifepath.agent.itinerary import legs, plan_greedy, validate
from jeju_lifepath.agent.llm import llm_parse
from jeju_lifepath.agent.models import ParseResult, Slots, ToolError
from jeju_lifepath.agent.parse import LODGING, SEOUL, parse_rules
from jeju_lifepath.agent.services import AgentContext, build_tools
from jeju_lifepath.agent.tools import Budget, BudgetExceeded, ToolRunner
from jeju_lifepath.api.store import RunCancelled, Store, VersionConflict
from jeju_lifepath.geo.geodesy import haversine_m
from jeju_lifepath.routing.optimizer import SPEED_M_PER_MIN
from jeju_lifepath.schemas import MobilityProfile, RoutePreference

log = logging.getLogger(__name__)


# 지도를 아주 크게 확대했을 때도 이만큼은 찾는다(카카오 검색 최소 단위와 무관한 화면 기준값).
# shortcut: 근거 없는 값, 확대 화면에서 결과가 너무 적으면 조정
MIN_VIEW_RADIUS_M = 300
KAKAO_MAX_RADIUS_M = 20000  # 카카오 로컬 검색 반경 상한(API 규격)


class NeedInput(Exception):  # noqa: N818 - 흐름 제어용(오류 아님)
    def __init__(self, questions: list[dict], pending: dict | None = None):
        super().__init__("needs_input")
        self.questions = questions
        self.pending = pending


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
    ) -> dict:
        """screen: 사용자가 지금 보고 있는 화면({name, travel_stop?}). AI 해석에 함께 넘긴다.
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
                         map_view=map_view, screen=screen)

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
    ) -> dict:
        """pinned_version: 재개일 때 실행 당시 대상 여행 버전(None 여행이면 0). 대화의 현재 여행으로
        바꾸지 않고, 그사이 여행이 바뀌었으면 게시하지 않는다."""
        budget = Budget(self.policy)
        runner = ToolRunner(self.tools, self.store, user_id, run_id, budget, self.sleep)
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
        if pinned_version is None:
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
            parsed = saved_parse or self._parse(
                text, state, bool(trip), budget, present.screen_note(screen, trip)
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
                    "parse": parsed.model_dump(mode="json"),
                },
            )
            draft = self._dispatch(parsed, ctx)
            return self._publish(ctx, draft)
        except NeedInput as q:
            state["pending"] = q.pending
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
        # 대화 기록은 새로고침 후 화면 복원에만 쓴다(LLM에 보내지 않음). 횟수 제한 없이
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
        mine = {k: state.get(k) for k in ("pending", "options", "prefs", "picked")}
        added = state["history"][-2:]
        if ctx.get("trip_id"):
            mine["trip_id"] = ctx["trip_id"]
        for _ in range(3):
            found = self.store.get_conversation(ctx["user_id"], ctx["conv_id"])
            base, rev = found if found else ({"history": []}, 0)
            if rev == ctx.get("conv_rev", 0):
                merged = state
            else:
                merged = {**base, **{k: v for k, v in mine.items() if v is not None}}
                merged["history"] = [*base.get("history", []), *added]
            if self.store.save_conversation(ctx["user_id"], ctx["conv_id"], merged, rev):
                return
        log.warning("conversation state save kept losing the race")

    def _publish(self, ctx, draft: dict) -> dict:
        body = draft.pop("commit", None)
        state = ctx["state"]
        state["pending"] = None
        state["options"] = draft.pop("options_state", None)
        for k in ("mobility", "prefer_cctv"):
            v = getattr(ctx["parse"].slots, k)
            if v is not None:
                state["prefs"][k] = v.value if hasattr(v, "value") else v
        if body is None:
            draft.setdefault("trip_id", ctx["trip_id"])
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
        draft = {**draft, "trip_id": trip_id, "trip_version": version}
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
    def _parse(
        self, text: str, state: dict, has_trip: bool, budget: Budget, screen_note: str = ""
    ) -> ParseResult:
        rules = parse_rules(text, self.clock(), has_trip)
        if self.llm is None or budget.llm_calls >= self.policy.max_llm_calls:
            return rules
        budget.llm_calls += 1
        try:
            return llm_parse(self.llm, text, rules, screen_note)
        except (ToolError, ValueError) as e:
            log.info("llm parse fell back to rules: %s", type(e).__name__)
            rules.ambiguities = list(rules.ambiguities)
            return rules.model_copy(update={"parser": "rules"})

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
        answer_only = parsed.intent in ("chat", base.intent) or (
            base.intent == "plan_modify" and parsed.intent in ("chat", "plan_modify")
        )
        if not answer_only:
            return parsed
        merged = base.slots.model_dump()
        sources = {k: "previous" for k in base.slot_sources}
        for k, v in parsed.slots.model_dump().items():
            if k in parsed.slot_sources:
                merged[k] = v
                sources[k] = "explicit"
        if re.search(r"돌아|원점|제자리", text):
            merged["round_trip"] = True
        if parsed.slots.use_current_location:
            merged["location_text"] = None
        if re.fullmatch(r"\s*(걸어서|걸어|도보)\s*(로|요|갈게요|갈래요)?\s*", text):
            merged["transport"] = None
        changes = parsed.requested_changes or base.requested_changes
        return base.model_copy(
            update={
                "slots": Slots(**merged),
                "slot_sources": sources,
                "ambiguities": parsed.ambiguities
                if base.intent != "plan_modify" or not changes
                else [],
                "requested_changes": changes,
                "reference_time": parsed.reference_time,
            }
        )

    # dispatch -----------------------------------------------------------
    def _dispatch(self, p: ParseResult, ctx: dict) -> dict:
        if p.ambiguities:
            raise NeedInput([{"text": a} for a in p.ambiguities], p.model_dump(mode="json"))
        if p.intent == "unsupported" or (
            p.slots.transport in ("car", "bus", "taxi") and p.intent in ("route", "plan_create")
        ):
            return present.unsupported(ctx, p, self.policy.version)
        handler = {
            "guide": self._guide,
            "route": self._route,
            "plan_create": self._plan_create,
            "plan_modify": self._plan_modify,
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
        ask: str = "어디를 기준으로 찾을까요?",
    ) -> dict:
        """기준 위치. 사용자가 말한 지명을 먼저, 없으면 화면이 보낸 현재 위치. 둘 다 없으면 묻는다."""
        text = getattr(p.slots, text_slot)
        if text:
            if text in LODGING:
                raise NeedInput(
                    [{"text": f"{text} 이름이나 주소를 알려 주세요."}], self._without(p, text_slot)
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
        raise NeedInput([{"text": ask, "needs_location": True}], p.model_dump(mode="json"))

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
                return {"lon": match[0]["lon"], "lat": match[0]["lat"], "label": match[0]["name"]}
        if picked:  # 고른 후보가 다시 찾은 목록에 없으면 이름으로 다시 찾는다
            r2 = ctx["runner"].call("search_places", {"query": text})
            match = [o for o in r2["places"] if o["ref"] == picked]
            if match:
                return {"lon": match[0]["lon"], "lat": match[0]["lat"], "label": match[0]["name"]}
        if r["status"] == "found":
            return {"lon": r["lon"], "lat": r["lat"], "label": r["label"]}
        if r["status"] == "not_found":
            raise NeedInput(
                [{"text": f"'{text}'을(를) 찾지 못했어요. 다른 이름이나 주소로 알려 주세요."}],
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
                    "text": f"'{text}'(으)로 찾은 곳이 여러 곳이에요(입구·주차장 등). 어디로 할까요?",
                    "options": [
                        {"label": it["label"], "reply": f"{i + 1}번"} for i, it in enumerate(items)
                    ],
                }
            ],
            None,
        )

    def _search(self, ctx, query: str, near: dict) -> tuple[list[dict], bool]:
        """주변 검색. 0곳이면 반경을 한 번 넓힌다(조건은 그대로, 검색 전략만 변경)."""
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
        }
        found = ctx["runner"].call("search_places", args)["places"]
        if found or radius >= self.policy.widened_radius_m:
            return found, False
        ctx["observations"].append("widened")
        args["radius_m"] = self.policy.widened_radius_m
        return ctx["runner"].call("search_places", args)["places"], True

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
            p, ctx, ask="어디 근처에서 찾을까요? 지금 위치를 쓰거나 동네 이름을 알려 주세요."
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

    def _route(self, p: ParseResult, ctx) -> dict:
        origin = self._point(
            p, ctx, ask="어디에서 출발하세요? 지금 위치를 쓰거나 출발지 이름을 알려 주세요."
        )
        if not p.slots.destination_text:
            raise NeedInput([{"text": "어디로 가세요?"}], p.model_dump(mode="json"))
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
        return present.routes(ctx, p, origin, dest, mobility, r, self.policy.version)

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
        questions = []
        if not s.themes:
            questions.append({"text": "어떤 곳을 들르고 싶으세요? 예: 빵집, 카페, 바다"})
        if not (s.stop_count or s.theme_counts or s.free_minutes or s.return_by):
            questions.append({"text": "몇 곳을 들를까요? 아니면 몇 시까지 돌아와야 하나요?"})
        if not (s.destination_text or s.round_trip):
            questions.append(
                {
                    "text": "다 둘러본 뒤 어디로 가세요?",
                    "options": [{"label": "출발한 곳으로 돌아오기", "reply": "돌아올게요"}],
                }
            )
        if not (s.location_text or ctx["location"] or ctx.get("map_view")):
            questions.append(
                {
                    "text": "어디에서 출발하세요? 지금 위치를 쓰거나 출발지 이름을 알려 주세요.",
                    "needs_location": True,
                }
            )
        if questions:  # 관련 질문을 한 번에 묶어 묻는다(AG-107)
            raise NeedInput(questions, p.model_dump(mode="json"))
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
                [{"text": "돌아올 시각이 이미 지났어요. 시각을 다시 알려 주세요."}],
                p.model_dump(mode="json"),
            )
        pool, widened = [], False
        for theme in s.themes:
            found, w = self._search(ctx, theme, start)
            widened |= w
            for place in found:
                if place["ref"] not in {x["ref"] for x in pool}:
                    # 저장 검색어: 지명이 있으면 '애월 빵집'처럼 붙여 둔다(화면이 다시 열 때 이 말로 다시 찾음)
                    query = f"{s.location_text} {theme}" if s.location_text else theme
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
        )

    def _trip_body(self, p, s: Slots, stops, mobility, start, ctx, base: dict | None = None):
        base = base or {}
        title = base.get("title") or ("·".join(s.themes) + " 코스")[:60]
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
                        "text": "고칠 여행 일정이 없어요. 먼저 일정을 만들어 주세요. 예: 애월에서 빵집 두 곳 들르는 코스 짜줘"
                    }
                ],
                None,
            )
        body, _version, _ = ctx["trip"]
        if any(pl["source"] != "kakao" for pl in body.get("places", [])):
            raise NeedInput(
                [{"text": "직접 고른 공공시설이 들어 있는 일정은 여행 화면에서 고쳐 주세요."}], None
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
            }
            for pl in body.get("places", [])
            if pl["source"] == "kakao"
        ]
        if not p.requested_changes:
            raise NeedInput(
                [{"text": "일정을 어떻게 바꿀까요? 예: 2번 빼줘, 1번 다녀왔어, 카페 먼저"}],
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
                                "text": f"일정에 {c.target}번 장소가 없어요. 1~{len(stops)}번 중에서 골라 주세요."
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
            if st["query"] not in by_query:
                by_query[st["query"]] = ctx["runner"].call(
                    "search_places",
                    {
                        "query": st["query"],
                        "near": {"lon": anchor["lon"], "lat": anchor["lat"]},
                        # 만들 때 지도 범위로 최대 20km까지 찾았을 수 있어 같은 최대 반경으로
                        # (거리순이라 처음 고른 곳은 같은 기준점 주변 결과 안에 다시 나온다)
                        "radius_m": KAKAO_MAX_RADIUS_M,
                    },
                )["places"]
            hit = [x for x in by_query[st["query"]] if x["ref"] == st["ref"]]
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
        pool = [{**f, "theme": c.theme, "query": c.theme} for f in found if f["ref"] not in taken]
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


def _fit(choices: list, n: int) -> list:
    need = max(0, n - 1)
    return (list(choices) + [RoutePreference.SHORTEST.value] * need)[:need]
