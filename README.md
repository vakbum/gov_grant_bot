# 정부지원사업 알림 텔레그램 봇

## 비용: $0
GitHub Actions 무료 분당 한도 + Gemini 무료 티어 + 텔레그램 무료. 서버 없음.

## 셋업 순서

### 1. 텔레그램
- @BotFather → `/newbot` → BOT_TOKEN 발급
- @userinfobot → CHAT_ID 확인
- 만든 봇 대화방 들어가서 Start 버튼 클릭 (필수)

### 2. Gemini API 키
- aistudio.google.com → Get API Key

### 3. K-Startup / 기업마당 API 키 (선택, 없어도 로컬 소스는 작동함)
- K-Startup: data.go.kr 에서 "창업진흥원_K-Startup" 검색 → 활용신청
- 기업마당: bizinfo.go.kr/uss/rss/bizinfoApi.do 안내 페이지에서 신청

### 4. GitHub 레포 세팅
1. Private 레포 생성
2. 이 폴더 전체(bot.py, .github/workflows/run.yml) 업로드
3. Settings → Secrets and variables → Actions → New repository secret
   - TELEGRAM_BOT_TOKEN
   - TELEGRAM_CHAT_ID
   - GEMINI_API_KEY
   - KSTARTUP_API_KEY (선택)
   - BIZINFO_API_KEY (선택)
4. Actions 탭에서 수동 실행(workflow_dispatch)으로 한 번 테스트
5. 이후 매일 09:00 / 18:00(KST) 자동 실행

## 지금 작동하는 소스
- K-Startup (오픈 API 연계)
- 기업마당 (공식 비즈인포 RSS 연계)
- 울산문화관광재단 (Playwright 동적 렌더링 및 파싱)
- 울산정보산업진흥원 (Playwright 동적 렌더링 + 회복 탄력적 파싱)
- 울산콘텐츠코리아랩 (Playwright 동적 렌더링 + User-Agent 위장 파싱)
- 울산스타트업허브/창조경제혁신센터 (Playwright 동적 렌더링 및 파싱)

## 회복 탄력적 파싱 엔진(Generic Table Parser) 도입
- 특정 사이트의 HTML 클래스명이나 CSS 구조가 미세하게 변경되더라도, 테이블의 행(`tr`) 구조 및 날짜 정규식 패턴을 역추적하여 공고 목록을 누락 없이 파싱할 수 있도록 보완했습니다.
- 신규 감지된 사이트의 경우 `bot.py` 하단 `sources` 딕셔너리에 간단히 `fetch_함수명`만 선언하여 손쉽게 모듈식 확장이 가능합니다.
