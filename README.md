# 포장재 법규 모니터 (자동 업데이트판)

미국·EU·일본·중국·한국의 포장재 법규를 **매일 오전 7시(한국 시간)** 자동 점검하고,
변경(신규·개정·폐지·삭제·일정변경·입법예고)이 있으면 **Slack으로 알리고 팀 대시보드에 기록**합니다.
서버를 따로 사지 않고 GitHub의 무료 기능(Actions, Pages)만으로 돌아갑니다.

```
매일 07:00  GitHub Actions 실행
   │
   ├─ 공식 API 조회 ── 미국 연방관보(Federal Register), 한국 국가법령정보센터(선택)
   ├─ Claude API + 웹 검색 ── 5개국 공식 기관·관보·업계 매체 검색
   ├─ 기존 데이터와 비교 ── 변경만 골라 data/*.json 에 반영 (중복 자동 제거)
   ├─ Slack 알림
   └─ 대시보드(GitHub Pages) 다시 배포
```

## 파일 구성

| 파일 | 역할 |
|---|---|
| `scripts/update.py` | 점검 프로그램 본체 |
| `.github/workflows/daily-update.yml` | 매일 자동 실행 설정 |
| `site/index.html` | 팀원이 보는 대시보드 (검색·필터·알림·CSV) |
| `data/regulations.json` | 법규 목록 (초기 34건 포함) |
| `data/changes.json` | 변경 이력 |
| `data/status.json` | 점검 기록 |

---

## 설치 순서 (약 30~40분)

### 1단계. 준비물 만들기

**① GitHub 계정** — https://github.com 에서 가입합니다. 회사 GitHub 조직이 있다면 그 안에 만드는 것이 좋습니다.

**② Claude API 키**
1. https://platform.claude.com 에 가입하고 결제 수단(크레딧)을 등록합니다.
2. API Keys 메뉴에서 키를 만들고 복사해 둡니다. (`sk-ant-`로 시작, 다시 볼 수 없으니 안전한 곳에 보관)
3. 웹 검색 기능은 조직 관리자가 Console에서 켜야 사용할 수 있습니다. 설정에서 웹 검색이 허용되어 있는지 확인하세요.

**③ Slack 알림 주소 (Incoming Webhook)**
1. https://api.slack.com/apps → *Create New App* → *From scratch* → 이름(예: 포장 법규 알림)과 워크스페이스 선택
2. 왼쪽 *Incoming Webhooks* → 켜기 → *Add New Webhook to Workspace* → 알림 받을 채널 선택
3. 생성된 `https://hooks.slack.com/services/...` 주소를 복사합니다.
   (회사 Slack 앱 설치에 관리자 승인이 필요할 수 있습니다.)

**④ (선택) 국가법령정보센터 Open API 인증값(OC)**
https://open.law.go.kr 에서 Open API 사용 신청을 하면 OC 값을 받습니다. 없어도 동작하며, 있으면 한국 법령 개정을 공식 데이터로 한 번 더 잡아냅니다.

### 2단계. 저장소(Repository) 만들고 파일 올리기

1. GitHub 오른쪽 위 **+ → New repository**
   - 이름: `packaging-reg-watch` (아무 이름 가능)
   - 공개 범위: 아래 '공개 범위 참고'를 읽고 선택
   - *Add a README file*은 체크하지 않습니다.
2. 만든 저장소 화면에서 **uploading an existing file** 링크를 눌러, 압축을 푼 폴더 안의 `data`, `scripts`, `site` 폴더와 `README.md`, `requirements.txt`를 끌어다 놓고 **Commit changes**를 누릅니다.
3. `.github` 폴더는 숨김 폴더라 끌어다 놓기에서 빠지는 경우가 많습니다. 다음처럼 직접 만드세요.
   - **Add file → Create new file**
   - 파일 이름 칸에 `.github/workflows/daily-update.yml` 입력 (슬래시를 치면 폴더가 자동 생성됨)
   - 압축 파일 안의 같은 파일 내용을 그대로 붙여넣고 **Commit changes**

> **공개 범위 참고**: GitHub Pages(대시보드)는 무료 플랜에서는 공개 저장소에서만 쓸 수 있고, 비공개 저장소에서 쓰려면 GitHub 유료 플랜이 필요합니다. 법규 정보 자체는 공개 정보라 공개 저장소도 무방하지만, 회사 내부 메모를 데이터에 넣을 계획이면 비공개 + 유료 플랜(또는 회사 조직 계정)을 쓰세요. 대시보드 없이 Slack 알림만 받는 것은 비공개 무료 저장소에서도 됩니다.

### 3단계. 비밀값 등록

저장소 **Settings → Secrets and variables → Actions**

*Secrets* 탭 → **New repository secret**

| 이름 | 값 | 필수 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 1단계 ②의 API 키 | 필수 |
| `SLACK_WEBHOOK_URL` | 1단계 ③의 주소 | 권장 |
| `LAW_OC` | 1단계 ④의 OC 값 | 선택 |

*Variables* 탭 → **New repository variable** (선택)

| 이름 | 값 |
|---|---|
| `SITE_URL` | 대시보드 주소 (5단계 후 입력, Slack 메시지에 링크로 붙음) |
| `CLAUDE_MODEL` | 사용할 모델. 비워두면 `claude-sonnet-5-5` |

### 4단계. 권한과 Pages 켜기

1. **Settings → Actions → General** 맨 아래 *Workflow permissions* → **Read and write permissions** 선택 → Save
2. **Settings → Pages** → *Build and deployment*의 Source를 **GitHub Actions**로 선택

### 5단계. 첫 실행

1. 저장소 위쪽 **Actions** 탭 → 왼쪽 *포장재 법규 매일 점검* → 오른쪽 **Run workflow** → Run
2. 5~15분 뒤 초록색 체크가 뜨면 성공입니다. 실행 결과를 누르면 요약에 찾은 변경 목록이 보입니다.
3. 대시보드 주소: `https://<GitHub아이디>.github.io/<저장소이름>/`
   이 주소를 3단계의 `SITE_URL` 변수에 넣고 팀원들에게 공유하세요.

이제 매일 오전 7시에 자동으로 돕니다.

---

## 팀 운영 방법

**매일**: Slack 알림 확인 → 링크로 원문 확인.

**검토 확정**: 자동 수집된 항목은 `검증 상태: 확인 필요`로 들어옵니다. 원문을 확인한 담당자가
대시보드의 **GitHub에서 수정** 버튼(또는 저장소의 `data/regulations.json` → 연필 아이콘)으로 들어가
해당 항목의 `"verify": "확인 필요"`를 `"verify": "출처 확인"`으로 바꾸고 저장합니다.
잘못 수집된 항목은 그 항목 블록을 지우면 됩니다. 저장하면 대시보드가 자동으로 다시 배포됩니다.
(편집하려면 저장소에 쓰기 권한이 있는 GitHub 계정이 필요합니다. 팀원은 Settings → Collaborators에서 초대합니다.)

**대시보드 알림**: 각 팀원의 브라우저가 '마지막 확인 시점'을 기억해 그 이후 변경을 배지로 보여줍니다.

---

## 비용

- GitHub Actions·Pages: 공개 저장소는 무료, 비공개 저장소도 무료 제공 시간 안에서 충분합니다.
- Claude API: 하루 5회 호출 × 웹 검색 최대 8회. 토큰 비용과 웹 검색 건당 비용이 듭니다.
  첫 주의 실제 청구액을 Console에서 확인하고, 줄이고 싶으면 `scripts/update.py`의 `max_uses`를 낮추거나
  점검 주기를 바꾸세요. 단가는 https://platform.claude.com 의 가격 안내를 확인하세요.

## 설정 바꾸기

| 바꾸고 싶은 것 | 위치 |
|---|---|
| 실행 시각 | `daily-update.yml`의 `cron: "0 22 * * *"` (UTC 기준, 22시 = 한국 07시) |
| 점검 국가 | 변수 `COUNTRIES` 추가 (예: `KR,EU`) |
| 검색 기간 | 변수 `LOOKBACK_DAYS` (기본 14일, 중복은 자동 제거) |
| 국가별 확인 출처·검색어 | `scripts/update.py`의 `COUNTRY_INFO` |
| 유형 분류 | `update.py`의 `CATEGORIES`와 `site/index.html`의 `CATS`를 같이 수정 |

## 알아둘 한계

- **AI 수집은 누락·오탐이 있을 수 있습니다.** 출처 URL이 없는 결과는 자동으로 버리지만, 반드시 사람이 원문을 확인하는 단계를 유지하세요.
- **공식 API가 있는 곳은 미국 연방관보와 한국 국가법령정보센터뿐**입니다(이 버전 기준). EU·일본·중국은 Claude 웹 검색으로 공식 사이트와 매체를 확인합니다. 특히 중국은 공개 API가 없어 상대적으로 놓칠 가능성이 큽니다.
- **미국 주 법은 연방관보에 실리지 않습니다.** 주 단위 변경(캘리포니아·오리건 등)은 웹 검색에 의존합니다.
- GitHub 예약 실행은 사용량이 많은 시간대에 수십 분 늦어질 수 있습니다.
- 공개 저장소에서 60일 동안 활동이 없으면 예약 실행이 멈출 수 있지만, 이 프로그램은 변경이 있을 때마다 커밋하므로 대개 유지됩니다. 멈추면 Actions 탭에서 다시 켜면 됩니다.

## 문제 해결

| 증상 | 확인할 것 |
|---|---|
| Actions가 빨간 X | 실행 로그에서 `ANTHROPIC_API_KEY가 설정되지 않았습니다` → 3단계 다시 확인 |
| `git push` 단계 실패 | 4단계 ① 쓰기 권한 |
| 대시보드 404 | 4단계 ② Pages 설정, 첫 배포 완료 여부 |
| 특정 국가만 계속 실패 | 로그의 `[CN] 실패: ...` 메시지. 응답 형식 오류면 다음 날 대개 해소됩니다 |
| Slack 알림 없음 | 변경이 0건이면 알림을 보내지 않습니다(정상). Webhook 주소 확인 |

## 내 컴퓨터에서 시험 실행 (선택)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
DRY_RUN=1 COUNTRIES=KR python scripts/update.py   # 파일 저장·Slack 전송 없이 결과만 출력
```
