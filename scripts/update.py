"""
포장재 법규 모니터 - 매일 자동 업데이트 스크립트

하는 일
  1) 공식 API(미국 연방관보, 한국 국가법령정보센터)에서 최근 문서 후보를 모읍니다.
  2) Claude API + 웹 검색으로 5개국(KR/US/EU/JP/CN)의 포장재 법규 변경을 찾습니다.
  3) 기존 데이터(data/regulations.json)와 비교해 신규·개정·폐지·삭제·일정변경·입법예고를 반영합니다.
  4) 변경 이력(data/changes.json)과 점검 기록(data/status.json)을 저장합니다.
  5) 변경이 있으면 Slack으로 알립니다.

환경변수
  ANTHROPIC_API_KEY  (필수) Claude API 키
  CLAUDE_MODEL       (선택) 기본값 claude-sonnet-5-5
  SLACK_WEBHOOK_URL  (선택) Slack Incoming Webhook 주소
  LAW_OC             (선택) 국가법령정보센터 Open API 인증값(OC)
  LOOKBACK_DAYS      (선택) 며칠 전까지의 변경을 찾을지, 기본 14
  COUNTRIES          (선택) 점검할 국가, 기본 "KR,US,EU,JP,CN"
  DRY_RUN            (선택) "1"이면 파일 저장·Slack 전송 없이 결과만 출력
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REGS_FILE = DATA / "regulations.json"
CHANGES_FILE = DATA / "changes.json"
STATUS_FILE = DATA / "status.json"

MODEL = os.environ.get("CLAUDE_MODEL") or "claude-sonnet-5-5"
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS") or "14")
COUNTRIES = [c.strip().upper() for c in (os.environ.get("COUNTRIES") or "KR,US,EU,JP,CN").split(",") if c.strip()]
DRY_RUN = os.environ.get("DRY_RUN") == "1"

CATEGORIES = [
    "EPR·생산자책임", "식품접촉물질", "유해물질(PFAS·중금속)", "재활용성·설계", "재생원료",
    "과대포장·감량", "표시·라벨링", "일회용 플라스틱", "재사용·리필",
]
STATUSES = ["시행", "시행예정", "입법예고", "폐지"]
ACTION_TO_TYPE = {
    "new": "신규", "amend": "개정", "repeal": "폐지",
    "delete": "삭제", "schedule": "일정변경", "draft": "입법예고",
}

COUNTRY_INFO = {
    "KR": {
        "name": "한국",
        "sources": "국가법령정보센터(law.go.kr), 국민참여입법센터(opinion.lawmaking.go.kr), 기후에너지환경부(mcee.go.kr), "
                   "식품의약품안전처(mfds.go.kr), 한국환경공단, 순환경제공제조합, 주요 언론",
        "lang": "한국어 검색어(예: 포장재 개정, 자원재활용법 입법예고, 과대포장 고시, 식품용 기구 용기 포장 기준 개정)",
    },
    "US": {
        "name": "미국",
        "sources": "Federal Register, FDA, FTC, EPA, CalRecycle, Oregon DEQ, Colorado CDPHE, 각 주 의회, "
                   "Sustainable Packaging Coalition, 주요 로펌 뉴스레터",
        "lang": "영어 검색어(예: packaging EPR law, PFAS packaging ban, recyclability labeling, food contact FDA)",
    },
    "EU": {
        "name": "유럽연합",
        "sources": "EUR-Lex, European Commission(environment.ec.europa.eu, food.ec.europa.eu), Official Journal, "
                   "European Parliament, ECHA, 주요 로펌·업계 매체",
        "lang": "영어 검색어(예: PPWR implementing act, delegated act packaging, food contact materials regulation amendment)",
    },
    "JP": {
        "name": "일본",
        "sources": "e-Gov 법령검색(laws.e-gov.go.jp), e-Gov 퍼블릭코멘트, 환경성(env.go.jp), 경제산업성(meti.go.jp), "
                   "소비자청(caa.go.jp), 후생노동성, 일본용기포장리사이클협회",
        "lang": "일본어와 영어 검색어(예: 容器包装 改正, プラスチック資源循環 省令, 食品用器具 容器包装 ポジティブリスト 改正)",
    },
    "CN": {
        "name": "중국",
        "sources": "국가시장감독관리총국(samr.gov.cn), 국가표준위원회(std.samr.gov.cn), 국가위생건강위원회(nhc.gov.cn), "
                   "국가우정국(spb.gov.cn), 국가발전개혁위원회, 생태환경부, 중국정부망(gov.cn)",
        "lang": "중국어와 영어 검색어(예: 过度包装 国家标准 发布, 食品接触材料 GB 4806 修订, 快递包装 新规, 塑料污染治理)",
    },
}


# ---------------------------------------------------------------- 파일 입출력
def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date().isoformat()  # 한국 시간 기준 날짜


# ---------------------------------------------------------------- 공식 API 후보 수집
def fetch_federal_register(days: int) -> list[dict]:
    """미국 연방관보(Federal Register) API: 키 없이 사용 가능."""
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    out: list[dict] = []
    for term in ["packaging", "food contact substance", "PFAS packaging"]:
        params = [
            ("conditions[term]", term),
            ("conditions[publication_date][gte]", since),
            ("per_page", "20"),
            ("order", "newest"),
        ]
        for f in ["title", "html_url", "publication_date", "type", "abstract", "agency_names"]:
            params.append(("fields[]", f))
        try:
            r = requests.get("https://www.federalregister.gov/api/v1/documents.json", params=params, timeout=30)
            r.raise_for_status()
            for d in r.json().get("results", []):
                out.append({
                    "title": d.get("title"), "url": d.get("html_url"), "date": d.get("publication_date"),
                    "type": d.get("type"), "agency": ", ".join(d.get("agency_names") or []),
                    "abstract": (d.get("abstract") or "")[:400],
                })
        except Exception as e:  # 실패해도 Claude 웹 검색으로 계속 진행
            print(f"[US] Federal Register 조회 실패({term}): {e}")
    seen, uniq = set(), []
    for d in out:
        if d["url"] and d["url"] not in seen:
            seen.add(d["url"]); uniq.append(d)
    return uniq[:30]


def fetch_korea_law(days: int) -> list[dict]:
    """국가법령정보센터 Open API (open.law.go.kr에서 OC 신청 필요). LAW_OC가 없으면 건너뜁니다."""
    oc = os.environ.get("LAW_OC")
    if not oc:
        return []
    since = (dt.date.today() - dt.timedelta(days=days)).strftime("%Y%m%d")
    out: list[dict] = []
    for target in ["law", "admrul"]:  # law: 법령, admrul: 행정규칙(고시 등)
        for q in ["포장", "재활용", "용기"]:
            try:
                r = requests.get("https://www.law.go.kr/DRF/lawSearch.do",
                                 params={"OC": oc, "target": target, "type": "JSON", "query": q, "display": "50", "sort": "ddes"},
                                 timeout=30)
                r.raise_for_status()
                body = r.json()
                root = body.get("LawSearch") or body.get("AdmRulSearch") or {}
                items = root.get("law") or root.get("admrul") or []
                if isinstance(items, dict):
                    items = [items]
                for it in items:
                    date = str(it.get("공포일자") or it.get("발령일자") or "")
                    if date and date >= since:
                        out.append({
                            "title": it.get("법령명한글") or it.get("행정규칙명"),
                            "kind": it.get("제개정구분명") or it.get("제개정구분") or "",
                            "promulgated": date, "effective": str(it.get("시행일자") or ""),
                            "agency": it.get("소관부처명") or "",
                            "url": "https://www.law.go.kr" + (it.get("법령상세링크") or it.get("행정규칙상세링크") or ""),
                        })
            except Exception as e:
                print(f"[KR] 국가법령정보센터 조회 실패({target}/{q}): {e}")
    seen, uniq = set(), []
    for d in out:
        k = (d["title"], d["promulgated"])
        if k not in seen:
            seen.add(k); uniq.append(d)
    return uniq[:40]


# ---------------------------------------------------------------- Claude 호출
def build_prompt(country: str, regs: list[dict], changes: list[dict], candidates: list[dict]) -> str:
    info = COUNTRY_INFO[country]
    existing = [
        {k: r.get(k) for k in ["id", "title", "original", "status", "effective", "nextDate", "nextLabel"]}
        for r in regs if r.get("country") == country
    ]
    cutoff = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    recent = [
        {k: c.get(k) for k in ["regId", "type", "date", "summary"]}
        for c in changes if c.get("country") == country and (c.get("date") or "") >= cutoff
    ]
    since = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    return f"""당신은 포장재 규제 모니터링 담당자입니다. 오늘은 {today()}입니다.
대상 국가: {info['name']} ({country})

[임무]
{since} 이후 {info['name']}에서 발표·공포·시행·예고·폐지된 "포장재 관련" 법령, 시행령·규칙, 고시, 강제 표준, 가이드라인 변경을 웹 검색으로 찾으세요.
포장재 관련 범위: 포장 EPR·재활용 의무, 식품접촉 포장재, 포장 내 유해물질(PFAS·중금속·BPA 등), 재활용성·설계 기준, 재생원료 사용 의무,
과대포장·포장 감량, 재활용·환경 표시, 일회용 플라스틱, 재사용 포장.

[우선 확인할 출처] {info['sources']}
[검색 언어] {info['lang']}

[이미 등록된 법규] (변경이 이 중 하나에 해당하면 reg_id에 그 id를 쓰세요)
{json.dumps(existing, ensure_ascii=False)}

[이미 기록된 최근 변경] (같은 사건은 다시 보고하지 마세요)
{json.dumps(recent, ensure_ascii=False)}

[공식 API에서 받은 후보 문서] (관련 있는 것만 사용, 관련 없으면 무시)
{json.dumps(candidates, ensure_ascii=False)}

[규칙]
- 실제로 출처 페이지에서 확인한 사건만 보고하세요. 추측·전망·업계 의견은 제외합니다.
- 각 사건에는 확인한 출처 URL이 반드시 있어야 합니다. 가능하면 정부·공식 기관 URL을 쓰세요.
- 이미 기록된 변경과 같은 사건이면 제외하세요.
- 날짜는 YYYY-MM-DD. 모르면 빈 문자열.
- 변경이 없으면 events를 빈 배열로 두세요. 그것도 정상적인 결과입니다.

[action 값]
new(등록되지 않은 새 법규) / amend(내용 개정) / repeal(폐지) / delete(목록에서 제외해야 할 항목, 예: 포장재와 무관하거나 다른 법으로 통합)
/ schedule(시행일·유예기간 등 일정 변경) / draft(입법예고·행정예고·의견수렴 단계)

[categories 값] 다음 중에서만 고르세요: {json.dumps(CATEGORIES, ensure_ascii=False)}
[status 값] 다음 중 하나: {json.dumps(STATUSES, ensure_ascii=False)}

[출력] 마지막 답변은 아래 JSON 하나만 출력하세요. 설명 문장이나 코드펜스 없이.
{{"events":[{{"action":"new|amend|repeal|delete|schedule|draft","reg_id":"기존 id 또는 null",
"title":"한국어 법규명","original":"원문 명칭·번호","authority":"소관 기관","status":"시행|시행예정|입법예고|폐지",
"effective":"YYYY-MM-DD","nextDate":"YYYY-MM-DD","nextLabel":"다음 일정 내용","categories":["..."],
"summary":"법규 요약(한국어 2~3문장)","requirements":["주요 요건"],"scope":"적용 대상","penalty":"제재",
"change_summary":"이번 변경 한 줄 요약(한국어)","change_detail":"세부 내용(한국어)","event_date":"YYYY-MM-DD",
"source_name":"출처 이름","source_url":"https://..."}}]}}
"""


def call_claude(prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}]
    text_parts: list[str] = []
    for _ in range(4):  # 서버 도구가 긴 작업을 나눠 돌려줄 때(pause_turn) 이어서 요청
        for attempt in range(3):
            try:
                resp = client.messages.create(model=MODEL, max_tokens=8000, messages=messages, tools=tools)
                break
            except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as e:
                wait = 20 * (attempt + 1)
                print(f"  API 일시 오류, {wait}초 후 재시도: {e}")
                time.sleep(wait)
        else:
            raise RuntimeError("Claude API 호출이 3회 실패했습니다.")
        text_parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        if resp.stop_reason == "pause_turn":
            messages = [messages[0], {"role": "assistant", "content": resp.content}]
            continue
        break
    return "\n".join(text_parts)


def parse_events(text: str) -> list[dict]:
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    else:
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("응답에서 JSON을 찾지 못했습니다.")
        t = t[start:end + 1]
    data = json.loads(t)
    events = data.get("events", [])
    if not isinstance(events, list):
        raise ValueError("events가 배열이 아닙니다.")
    return events


# ---------------------------------------------------------------- 검증·반영
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def norm(s) -> str:
    return re.sub(r"[\s()（）·,.\-]", "", str(s or "")).lower()


def clean_event(ev: dict, country: str, reg_ids: set[str], titles: dict | None = None) -> dict | None:
    action = str(ev.get("action", "")).strip().lower()
    if action not in ACTION_TO_TYPE:
        return None
    url = str(ev.get("source_url") or "")
    if not url.startswith(("http://", "https://")):
        return None  # 출처 없는 사건은 버립니다
    reg_id = ev.get("reg_id")
    if reg_id not in reg_ids:
        reg_id = None
    if reg_id is None and titles:
        reg_id = titles.get(norm(ev.get("title"))) or titles.get(norm(ev.get("original")))
    if action in ("amend", "repeal", "delete", "schedule") and reg_id is None:
        if action == "delete":
            return None
        action = "new"  # 기존 항목을 못 찾으면 신규로 등록
    for k in ("effective", "nextDate", "event_date"):
        v = str(ev.get(k) or "")
        ev[k] = v if DATE_RE.match(v) else ""
    ev["categories"] = [c for c in (ev.get("categories") or []) if c in CATEGORIES]
    if ev.get("status") not in STATUSES:
        ev["status"] = "입법예고" if action == "draft" else ("폐지" if action == "repeal" else "")
    ev["requirements"] = [str(x) for x in (ev.get("requirements") or []) if str(x).strip()][:10]
    ev["action"], ev["reg_id"], ev["country"] = action, reg_id, country
    if not str(ev.get("title") or "").strip() and reg_id is None:
        return None
    return ev


def event_key(ev: dict) -> str:
    base = f"{ev['country']}|{ev.get('reg_id') or ev.get('title')}|{ev['action']}|{ev.get('event_date')}|{ev.get('source_url')}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def new_reg_id(ev: dict, regs_by_id: dict) -> str:
    h = hashlib.sha1((ev.get("original") or ev.get("title") or "").encode("utf-8")).hexdigest()[:6]
    rid = f"{ev['country'].lower()}-auto-{h}"
    n = 2
    while rid in regs_by_id:
        rid = f"{ev['country'].lower()}-auto-{h}-{n}"; n += 1
    return rid


REG_FIELDS = ["title", "original", "authority", "status", "effective", "nextDate", "nextLabel",
              "categories", "summary", "requirements", "scope", "penalty"]


def apply_event(ev: dict, regs_by_id: dict, changes: list[dict]) -> dict | None:
    key = event_key(ev)
    if any(c.get("eventKey") == key for c in changes):
        return None  # 이미 반영된 사건
    if ev.get("reg_id") and ev.get("event_date") and any(
        c.get("regId") == ev["reg_id"] and c.get("type") == ACTION_TO_TYPE[ev["action"]] and c.get("date") == ev["event_date"]
        for c in changes
    ):
        return None  # 같은 법규·같은 유형·같은 날짜의 변경이 이미 있음
    if ev.get("reg_id") and any(
        c.get("regId") == ev["reg_id"] and c.get("sourceUrl") == ev["source_url"] for c in changes
    ):
        return None  # 같은 법규를 같은 출처로 이미 기록함
    ts = now_iso()
    source = {"name": ev.get("source_name") or ev["source_url"], "url": ev["source_url"]}
    action = ev["action"]
    reg_id = ev.get("reg_id")
    changed_fields: list[str] = []

    if action == "delete":
        reg = regs_by_id.pop(reg_id)
        title = reg.get("title", "")
    else:
        if reg_id is None:
            reg_id = new_reg_id(ev, regs_by_id)
            reg = {"id": reg_id, "country": ev["country"], "version": 0, "sources": []}
            regs_by_id[reg_id] = reg
        reg = regs_by_id[reg_id]
        for f in REG_FIELDS:
            v = ev.get(f)
            if v in (None, "", []):
                continue
            if reg.get(f) != v:
                reg[f] = v
                changed_fields.append(f)
        if action == "repeal":
            reg["status"] = "폐지"
        if action == "draft" and not reg.get("status"):
            reg["status"] = "입법예고"
        reg.setdefault("status", "시행")
        reg.setdefault("categories", [])
        if all(s.get("url") != source["url"] for s in reg.get("sources", [])):
            reg["sources"] = [source] + reg.get("sources", [])[:4]
        reg["verify"] = "확인 필요"  # 자동 수집 항목은 담당자 검토 전까지 '확인 필요'
        reg["updatedAt"] = ts
        reg["version"] = int(reg.get("version") or 0) + 1
        title = reg.get("title", "")

    change = {
        "id": f"c-{ts[:10].replace('-', '')}-{key}",
        "eventKey": key,
        "regId": reg_id,
        "regTitle": title,
        "country": ev["country"],
        "type": ACTION_TO_TYPE[action],
        "date": ev.get("event_date") or today(),
        "summary": ev.get("change_summary") or f"{ACTION_TO_TYPE[action]}: {title}",
        "detail": (ev.get("change_detail") or "") + (f" (변경 항목: {', '.join(changed_fields)})" if changed_fields and action != "new" else ""),
        "sourceName": source["name"],
        "sourceUrl": source["url"],
        "createdAt": ts,
        "by": "auto",
    }
    changes.insert(0, change)
    return change


# ---------------------------------------------------------------- 알림
FLAG = {"KR": "🇰🇷", "US": "🇺🇸", "EU": "🇪🇺", "JP": "🇯🇵", "CN": "🇨🇳"}


def notify_slack(new_changes: list[dict], errors: list[str]) -> None:
    hook = os.environ.get("SLACK_WEBHOOK_URL")
    if not hook or (not new_changes and not errors):
        return
    site = os.environ.get("SITE_URL", "")
    lines = []
    if new_changes:
        lines.append(f"*포장재 법규 변경 {len(new_changes)}건* ({today()})")
        for c in new_changes[:20]:
            lines.append(f"{FLAG.get(c['country'], '')} [{c['type']}] *{c['regTitle']}* — {c['summary']} <{c['sourceUrl']}|출처>")
        if len(new_changes) > 20:
            lines.append(f"…외 {len(new_changes) - 20}건")
        lines.append("_자동 수집 결과입니다. 원문 확인 후 업무에 반영하세요._")
    if errors:
        lines.append(f":warning: 점검 실패: {', '.join(errors)}")
    if site:
        lines.append(f"<{site}|대시보드 열기>")
    try:
        requests.post(hook, json={"text": "\n".join(lines)}, timeout=20).raise_for_status()
    except Exception as e:
        print(f"Slack 전송 실패: {e}")


def write_summary(new_changes: list[dict], errors: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"## 포장재 법규 점검 {today()}\n\n변경 {len(new_changes)}건\n\n")
        for c in new_changes:
            f.write(f"- {c['country']} [{c['type']}] {c['regTitle']}: {c['summary']} ([출처]({c['sourceUrl']}))\n")
        if errors:
            f.write(f"\n실패: {', '.join(errors)}\n")


# ---------------------------------------------------------------- 메인
def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY가 설정되지 않았습니다.")
        return 1
    regs = load_json(REGS_FILE, [])
    changes = load_json(CHANGES_FILE, [])
    status = load_json(STATUS_FILE, {"runs": []})
    regs_by_id = {r["id"]: r for r in regs}

    official = {"US": fetch_federal_register(LOOKBACK_DAYS), "KR": fetch_korea_law(LOOKBACK_DAYS)}
    new_changes: list[dict] = []
    errors: list[str] = []
    per_country: dict[str, int] = {}

    for country in COUNTRIES:
        if country not in COUNTRY_INFO:
            continue
        print(f"[{country}] 점검 시작")
        try:
            prompt = build_prompt(country, list(regs_by_id.values()), changes, official.get(country, []))
            events = parse_events(call_claude(prompt))
            count = 0
            for raw in events:
                titles = {}
                for r in regs_by_id.values():
                    if r.get("country") == country:
                        for k in (r.get("title"), r.get("original")):
                            if norm(k):
                                titles[norm(k)] = r["id"]
                ev = clean_event(dict(raw), country, set(regs_by_id), titles)
                if not ev:
                    continue
                ch = apply_event(ev, regs_by_id, changes)
                if ch:
                    new_changes.append(ch); count += 1
                    print(f"  + [{ch['type']}] {ch['regTitle']}: {ch['summary']}")
            per_country[country] = count
            print(f"[{country}] 완료: 변경 {count}건")
        except Exception as e:
            errors.append(country)
            per_country[country] = -1
            print(f"[{country}] 실패: {e}")

    status["lastCheck"] = now_iso()
    status["lastSummary"] = f"변경 {len(new_changes)}건" + (f", 실패 국가 {', '.join(errors)}" if errors else "")
    status["runs"] = ([{"at": status["lastCheck"], "changes": per_country, "errors": errors}] + status.get("runs", []))[:60]

    if DRY_RUN:
        print(json.dumps(new_changes, ensure_ascii=False, indent=2))
        return 0

    save_json(REGS_FILE, sorted(regs_by_id.values(), key=lambda r: (r.get("country", ""), r["id"])))
    save_json(CHANGES_FILE, changes[:2000])
    save_json(STATUS_FILE, status)
    notify_slack(new_changes, errors)
    write_summary(new_changes, errors)
    return 2 if len(errors) == len(COUNTRIES) else 0  # 전부 실패하면 워크플로를 실패로 표시


if __name__ == "__main__":
    sys.exit(main())
