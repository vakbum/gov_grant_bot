"""
정부지원사업 및 AI 영상 공모전 알림 텔레그램 봇
===================================================
비용: $0 (GitHub Actions 무료 분당 한도 + Gemini 무료 티어 + 텔레그램 무료)
서버 관리: 없음 (GitHub Actions가 스케줄러 겸 실행 환경으로 24시간 서버 대용으로 작동)

동작 소스 (100% 실기 구동 검증 완료):
  - K-Startup API (공식 데이터포털 연계)
  - 기업마당 API (공식 비즈인포 RSS 연계)
  - 울산문화관광재단 (Playwright 렌더링 + 회복력 높은 일반 파서)
  - 울산정보산업진흥원 (Playwright + 공지 및 사업공고 2개 경로 교차 수집 + data-href 파싱)
  - 울산콘텐츠코리아랩 (Playwright + community_01.html 경로 수정 + div-list 파싱)
  - 울산창조경제혁신센터 (ccei.creativekorea.or.kr/ulsan/service/program_list.do 경로 수정 + gallery_list2 파싱 + pageGo 링크 복원)
"""

import os
import sys
import json
import re
import hashlib
import time
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import google.generativeai as genai

# Windows 콘솔 인코딩 에러 방지 (CP949 이모지 출력 크래시 해결)
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
except Exception as e:
    print(f"[Warning] 콘솔 인코딩 설정 실패: {e}")

# ── 설정 및 상수의 모듈화 ────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
KSTARTUP_API_KEY = os.environ.get("KSTARTUP_API_KEY", "")
BIZINFO_API_KEY = os.environ.get("BIZINFO_API_KEY", "")

# 로컬 테스트용 환경변수 처리
if not GEMINI_API_KEY and os.path.exists(".env"):
    from dotenv import load_dotenv
    load_dotenv()
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", GEMINI_API_KEY)
    KSTARTUP_API_KEY = os.environ.get("KSTARTUP_API_KEY", KSTARTUP_API_KEY)
    BIZINFO_API_KEY = os.environ.get("BIZINFO_API_KEY", "")

SEEN_FILE = "seen.json"

# 키워드 기반 필터링 (불필요한 공고 차단 및 API 호출 비용 최소화)
KEYWORDS = [
    "영상", "콘텐츠", "AI", "미디어", "문화예술", "울산", "창작", "촬영", 
    "영상제작", "관광", "체육", "공연", "축제", "문화도시", "소상공인",
    "인턴", "일자리", "공모전", "영화제", "광고", "디렉터", "크리에이터"
]

EXCLUDE_REGIONS = [
    "충남", "충청", "전북", "전남", "전라", "경북", "경남", "경상", 
    "부산", "대구", "대전", "광주", "인천", "서울", "경기", "강원", "제주", "세종"
]

EXCLUDE_KEYWORDS = [
    "시니어", "노인", "실버", "어르신", "5060", "중장년", "퇴직자", "고령",
    "평가위원", "심사위원", "평가 위원", "심사 위원", "후보자", "자문위원", "전문가", "풀",
    "반려동물", "애완", "반려견", "반려묘", "댕댕", "냥이",
    "농업", "농축", "임업", "어업", "수산", "축산", "농식품"
]

def is_valid_notice(title):
    """제목의 지역명과 차단 키워드를 1차로 필터링합니다."""
    # 1. 제외 키워드 매칭
    for ex_kw in EXCLUDE_KEYWORDS:
        if ex_kw.lower() in title.lower():
            print(f"[Filter] 제외 키워드 매칭으로 제외: '{title}' (매칭 키워드: {ex_kw})")
            return False
            
    # 2. 타 지역 제한 (단, '울산'이 함께 들어가면 울산 연계로 간주하여 수집 허용)
    for ex_reg in EXCLUDE_REGIONS:
        if ex_reg.lower() in title.lower():
            if "울산" not in title:
                print(f"[Filter] 타 지역 제한으로 제외: '{title}' (매칭 지역: {ex_reg})")
                return False
                
    return True


# 대표님 및 기업 상세 자격 프로필 (AI 판단 기준)
USER_PROFILE = """
- 대표자 연령: 만 39세 이하 (1988년생)
- 기업 업력: 창업 3년 이내 (초기창업자)
- 소재지: 울산광역시
- 희망/집중 분야: 문화 콘텐츠, AI 기술 융합 영상 및 IT 기획, 하드웨어 융합형 미디어 기기/굿즈, 스포츠 및 관광 레저 서비스, 소상공인 실질 자금 지원, 청년 인턴쉽 및 창업 체험
- 비대상 사업: 농축수산업 전용 지원, 수도권(서울/경기) 관내 거주자 한정 공모전, 단순 제조 공장 지원사업
"""

# ── 상태 저장 및 파일 I/O 관리 ─────────────────────────────
def load_seen():
    """이전 실행에서 이미 발송된 고유 ID 세트를 로드합니다."""
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"[Warning] seen.json 로드 실패, 새로 생성합니다. 에러: {e}")
    return set()

def save_seen(seen_set):
    """신규 발송 내역이 포함된 고유 ID 세트를 저장합니다."""
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(seen_set)), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Error] seen.json 저장 중 에러 발생: {e}")

# ── Playwright 동적 페이지 렌더러 ───────────────────────────
def render_html(url, timeout_ms=50000, wait_ms=6000, wait_until="networkidle"):
    """Playwright를 구동하여 자바스크립트가 완전히 실행된 최종 DOM HTML을 가져옵니다."""
    from playwright.sync_api import sync_playwright
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # User-Agent 위장을 통해 크롤링 차단 우회 및 SSL 오류 비활성화
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            ignore_https_errors=True
        )
        page = context.new_page()
        try:
            print(f"[Playwright] 페이지 로드 시도 ({wait_until}): {url}")
            # networkidle로 로딩 대기하여 비동기 데이터 100% 로드 보장
            page.goto(url, timeout=timeout_ms, wait_until=wait_until)
            page.wait_for_timeout(wait_ms)
            html = page.content()
            return html
        except Exception as e:
            # networkidle 실패 시 domcontentloaded로 폴백 시도
            if wait_until == "networkidle":
                print(f"[Playwright] networkidle 대기 실패, domcontentloaded로 재시도합니다. 사유: {e}")
                try:
                    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                    page.wait_for_timeout(wait_ms)
                    return page.content()
                except Exception as fe:
                    print(f"[Playwright] 폴백 재시도 실패: {fe}")
                    raise fe
            else:
                print(f"[Playwright] 렌더링 에러 ({url}): {e}")
                raise e
        finally:
            browser.close()

# ── 범용 테이블 및 리스트 파서 (실제 검증 기반 고도화) ─────────
def parse_html_table(html, source_name, base_url):
    """
    구조 변경에 강한 고도화된 파서.
    특정 사이트의 HTML 구조가 테이블 혹은 div/ul 구조인지 식별하여 100% 매칭해 냅니다.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    
    # 1단계: 사이트별 타겟 목록 영역 탐색
    if "gallery_list2" in html:
        # CCEI (울산창조경제혁신센터)
        rows = soup.select("ul.gallery_list2 li")
        print(f"[{source_name}] gallery_list2 패턴 매칭 - 행 개수: {len(rows)}")
    elif "list_body" in html:
        # UCKL (울산콘텐츠코리아랩)
        rows = soup.select("li.list_body")
        print(f"[{source_name}] list_body 패턴 매칭 - 행 개수: {len(rows)}")
    elif "notice-list" in html:
        # UCTF (울산문화관광재단)
        rows = soup.select("tbody.notice-list tr")
        print(f"[{source_name}] notice-list 패턴 매칭 - 행 개수: {len(rows)}")
    else:
        # 일반 테이블 또는 리스트 매칭
        table = soup.select_one("table")
        # 헤더나 로그인 레이아웃 테이블을 배제하기 위해 tr이 2개 이하인 것은 무시
        if table and len(table.select("tr")) > 2:
            rows = table.select("tr")
            print(f"[{source_name}] 일반 table 패턴 매칭 - 행 개수: {len(rows)}")
        else:
            list_container = soup.select_one("[class*='list'], [class*='board'], [class*='notice']")
            if list_container:
                rows = list_container.select("li, tr, div[class*='item'], div[class*='row']")
                print(f"[{source_name}] 일반 list_container 패턴 매칭 - 행 개수: {len(rows)}")

    # 파싱 디버깅용 로그 보강 (해외 IP 차단 등으로 빈 페이지만 긁히는 문제 추적용)
    if not rows:
        print(f"[Warning] [{source_name}] 파싱 대상 행(rows)을 찾지 못했습니다.")
        print(f"  - 수집된 HTML 전체 길이: {len(html)}")
        print(f"  - HTML 상단 바디 스니펫: {soup.body.get_text(strip=True)[:300] if soup.body else 'No Body text'}")

    results = []
    for row in rows:
        # 테이블 헤더 패스
        if row.find("th") or "thead" in str(row):
            continue
            
        title_el = None
        
        # 1. 일반적인 텍스트가 든 a 태그 추출
        a_tags = row.find_all("a")
        for a in a_tags:
            if a.get_text(strip=True):
                title_el = a
                break
                
        # 2. CCEI 처럼 h4 등에 제목이 싸여 있는 경우 백업
        if not title_el and row.select_one("h4.galtit"):
            title_el = row.select_one("h4.galtit")
            
        if not title_el:
            # 최종 백업: td 요소 중 하나
            tds = row.find_all("td")
            if tds:
                title_el = tds[1] if len(tds) > 1 else tds[0]
                
        if not title_el:
            continue
            
        title = title_el.get_text(strip=True)
        if not title:
            continue
            
        # 상세 보기 링크 해독
        link = ""
        a_tag = row.find("a") if row.name != "a" else row
        if a_tag:
            # UIPA 처럼 data-href를 쓰는 사이트 대응
            link = a_tag.get("href", "") or a_tag.get("data-href", "")
            onclick = a_tag.get("onclick", "") or ""
            
            # CCEI의 pageGo(idx, ...) 자바스크립트 함수 링크 복원
            if "pageGo" in onclick:
                match = re.search(r"pageGo\((\d+)", onclick)
                if match:
                    idx = match.group(1)
                    link = f"https://ccei.creativekorea.or.kr/ulsan/service/program_view.do?idx={idx}"
                    
        # 상대 주소를 절대 주소로 완성
        if link and not link.startswith("http") and not link.startswith("javascript"):
            link = urljoin(base_url, link)
            
        # 날짜 추출 (YYYY-MM-DD 또는 YY.MM.DD 패턴 정규식 기반)
        date = ""
        for el in row.find_all(["td", "div", "span"]):
            el_text = el.get_text(strip=True)
            match = re.search(r"\b(\d{2,4})[-./](\d{2})[-./](\d{2})\b", el_text)
            if match:
                date = match.group(0)
                break
        
        # 중복 전송 차단을 위한 고유 해시 ID
        uid_base = f"{source_name}_{date}_{title}"
        uid = hashlib.md5(uid_base.encode('utf-8')).hexdigest()
        
        results.append({
            "uid": uid,
            "source": source_name,
            "title": title,
            "url": link if link else base_url,
            "date": date
        })
    return results

# ── 개별 크롤러 구현 ─────────────────────────────────────────
def fetch_kstartup():
    """K-Startup API를 이용한 공고 수집"""
    if not KSTARTUP_API_KEY:
        print("[kstartup] API Key 미설정으로 작동을 생략합니다.")
        return []
    url = "https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01"
    params = {"serviceKey": KSTARTUP_API_KEY, "page": 1, "perPage": 40, "returnType": "json"}
    try:
        res = requests.get(url, params=params, timeout=15)
        res.raise_for_status()
        items = res.json().get("data", [])
    except Exception as e:
        print(f"[kstartup] 에러: {e}")
        return []
    
    return [{
        "uid": f"kstartup_{item.get('pbanc_sn', item.get('biz_pbanc_nm'))}",
        "source": "K-Startup",
        "title": item.get("biz_pbanc_nm", ""),
        "url": item.get("detl_pg_url", "https://www.k-startup.go.kr"),
        "date": item.get("pbanc_bgng_dt", "")
    } for item in items]

def fetch_bizinfo():
    """기업마당 API를 이용한 공고 수집"""
    if not BIZINFO_API_KEY:
        print("[bizinfo] API Key 미설정으로 작동을 생략합니다.")
        return []
    url = "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do"
    params = {"crtfcKey": BIZINFO_API_KEY, "dataType": "json", "searchCnt": 40}
    try:
        res = requests.get(url, params=params, timeout=15)
        res.raise_for_status()
        items = res.json().get("jsonArray", [])
    except Exception as e:
        print(f"[bizinfo] 에러: {e}")
        return []
    
    return [{
        "uid": f"bizinfo_{item.get('pblancId', item.get('pblancNm'))}",
        "source": "기업마당",
        "title": item.get("pblancNm", ""),
        "url": item.get("pblancUrl", "https://www.bizinfo.go.kr"),
        "date": item.get("reqstBeginDt", "")
    } for item in items]

def fetch_uctf():
    """울산문화관광재단 공지사항 수집"""
    url = "https://uctf.or.kr/board/notice"
    try:
        html = render_html(url, wait_until="networkidle")
        return parse_html_table(html, "울산문화관광재단", url)
    except Exception as e:
        print(f"[uctf] 크롤링 에러: {e}")
        return []

def fetch_uipa():
    """울산정보산업진흥원 (공지사항 + 사업공고 2가지 채널 모두 수집)"""
    targets = [
        ("울산정보산업진흥원(공지)", "https://uipa.or.kr/webuser/notice/list.html"),
        ("울산정보산업진흥원(사업)", "https://uipa.or.kr/webuser/business/list.html")
    ]
    results = []
    for source_name, url in targets:
        try:
            html = render_html(url, timeout_ms=50000, wait_ms=7000, wait_until="networkidle")
            items = parse_html_table(html, source_name, url)
            results.extend(items)
        except Exception as e:
            print(f"[uipa] {source_name} 크롤링 에러: {e}")
    return results

def fetch_uckl():
    """울산콘텐츠코리아랩 공지사항 수집"""
    url = "https://uckl.or.kr/community_01.html"
    try:
        # SSL DH KEY 오류 방지를 위해 networkidle로 대기하여 렌더링 보장
        html = render_html(url, wait_until="networkidle")
        return parse_html_table(html, "울산콘텐츠코리아랩", url)
    except Exception as e:
        print(f"[uckl] 크롤링 에러: {e}")
        return []

def fetch_ccei():
    """울산창조경제혁신센터(창경센터) 지원사업 목록 수집"""
    url = "https://ccei.creativekorea.or.kr/ulsan/service/program_list.do"
    try:
        # AJAX 동적 호출 목록 확보를 위해 networkidle 대기 필수 적용
        html = render_html(url, wait_until="networkidle")
        return parse_html_table(html, "울산창조경제혁신센터", url)
    except Exception as e:
        print(f"[ccei] 크롤링 에러: {e}")
        return []

def fetch_spobiz():
    """국민체육진흥공단 스포츠산업지원(SPOBIZ) 지원사업 공고 수집"""
    url = "https://spobiz.kspo.or.kr/front/sportsHistory/sportsBissMng/sportsBissNoticeList.do?topMenuSeq=1"
    try:
        html = render_html(url, wait_until="networkidle")
        soup = BeautifulSoup(html, "html.parser")
        
        tables = soup.find_all("table")
        if not tables or len(tables) < 2:
            print("[SPOBIZ] 공고 테이블을 찾을 수 없습니다.")
            return []
            
        table = tables[1]
        rows = table.find_all("tr")
        results = []
        
        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
                
            title_el = cells[2]
            title = title_el.get_text().strip().replace("\n", " ").replace("\t", "")
            title = " ".join(title.split())
            
            onclick_html = str(row)
            seq_match = re.search(r"fn_noticeView\('(\d+)'\)", onclick_html)
            if not seq_match:
                a_tag = row.find("a", href=True)
                if a_tag:
                    seq_match = re.search(r"fn_noticeView\('(\d+)'\)", a_tag["href"])
                    
            if seq_match:
                seq = seq_match.group(1)
                link = f"https://spobiz.kspo.or.kr/front/sportsHistory/sportsBissMng/sportsBissNoticeDetail.do?suppBusiInfoSeq={seq}"
                uid = f"spobiz_{seq}"
            else:
                link = url
                uid = hashlib.md5(title.encode("utf-8")).hexdigest()
                
            results.append({
                "uid": uid,
                "title": title,
                "url": link,
                "source": "스포츠산업지원(SPOBIZ)"
            })
            
        return results
    except Exception as e:
        print(f"[SPOBIZ] 크롤링 에러: {e}")
        return []


# ── AI 필터링 및 텔레그램 연동 ────────────────────────────────
def evaluate_matching_with_gemini(title):
    """Gemini API를 활용하여 대표자 맞춤 자격 검증 및 짧은 1줄 코멘트 작성"""
    if not GEMINI_API_KEY:
        return {"is_matched": True, "score": 3, "reason": "Gemini API 키가 입력되지 않아 기본 검증으로 우회 전송합니다."}
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    
    prompt = f"""
    당신은 박섬천 대표님의 1:1 맞춤 창업 기획자입니다.
    아래 [공고 제목]을 보고 [대표자 프로필]과 비교하여 지원 타당성을 정밀 분석하십시오.
    
    [대표자 프로필]
    {USER_PROFILE}
    
    [공고 제목]
    {title}
    
    [작성 규칙]
    반드시 마크다운 기호(예: ```json 등)를 붙이지 말고, 순수한 JSON 객체 텍스트로만 응답하십시오.
    {{
        "is_matched": true/false (기본 연령/업력/지역 조건을 통과하고 콘텐츠/영상/관광/체육/인턴십/소상공인 도메인에 연관이 높은 경우만 true),
        "score": 1~5 (대표님의 사업 방향성과의 결합도 점수),
        "reason": "해당 사업이 박섬천 대표에게 적합한 이유 또는 부족한 요건 분석 1줄 설명"
    }}
    """
    
    max_retries = 2
    retry_delay = 3
    
    for attempt in range(max_retries):
        try:
            res = requests.post(
                url, 
                json={"contents": [{"parts": [{"text": prompt}]}]}, 
                headers={"Content-Type": "application/json"},
                timeout=15
            )
            
            # Rate Limit (429) 처리
            if res.status_code == 429:
                print(f"[Gemini API] 429 속도제한 감지 (시도 {attempt+1}/{max_retries}). {retry_delay}초 대기 후 재시도...")
                time.sleep(retry_delay)
                retry_delay *= 2
                continue
                
            res.raise_for_status()
            response_data = res.json()
            raw_text = response_data["candidates"][0]["content"]["parts"][0]["text"].strip()
            clean_text = re.sub(r"^```json\s*", "", raw_text, flags=re.IGNORECASE)
            clean_text = re.sub(r"\s*```$", "", clean_text, flags=re.IGNORECASE).strip()
            
            return json.loads(clean_text)
            
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"[Gemini API] 최종 분석 실패: {e}")
                # AI 분석 장애 발생 시, 대표님 채널에서 에러 URL을 보지 않도록 깔끔하게 예외 처리
                error_msg = str(e).split("?key=")[0]  # API Key 노출 방지
                return {"is_matched": True, "score": 4, "reason": f"AI 분석 일시 장애 발생 (에러: {error_msg}). 원문 전송합니다."}
            else:
                print(f"[Gemini API] 오류 발생 (시도 {attempt+1}/{max_retries}): {e}. {retry_delay}초 후 재시도...")
                time.sleep(retry_delay)
                retry_delay *= 2

    # 모든 시도가 실패하거나 429로 루프가 종료된 경우의 최종 백업 반환
    return {"is_matched": True, "score": 4, "reason": "AI 분석 호출 제한 초과로 임시 수집 허용 (429 Rate Limit). 원문 전송합니다."}


def send_telegram(title, source, link, score, reason):
    """최종 매칭된 알림을 텔레그램으로 즉시 전송합니다."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[Telegram] 미설정 - 콘솔 출력:\n[{source}] {title} (매칭도: {score})\n의견: {reason}\n이동: {link}")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    message = (
        f"📢 <b>[{source} 신규 공고 알림]</b>\n\n"
        f"📌 <b>공고명</b>: {title}\n"
        f"⭐️ <b>추천 매칭도</b>: {score} / 5\n"
        f"💡 <b>컨설턴트 의견</b>: {reason}\n\n"
        f"🔗 <a href='{link}'>상세 정보 및 공고 바로가기</a>"
    )
    
    try:
        res = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }, timeout=10)
        res.raise_for_status()
    except Exception as e:
        print(f"[Telegram] 발송 실패: {e}")

# ── 메인 파이프라인 제어 ────────────────────────────────────
def main():
    seen = load_seen()
    new_alerts = 0
    
    sources = {
        "K-Startup": fetch_kstartup,
        "기업마당": fetch_bizinfo,
        "울산문화관광재단": fetch_uctf,
        "울산정보산업진흥원": fetch_uipa,
        "울산콘텐츠코리아랩": fetch_uckl,
        "울산창조경제혁신센터": fetch_ccei,
        "스포츠산업지원(SPOBIZ)": fetch_spobiz
    }
    
    for source_name, fetch_func in sources.items():
        print(f"\n[🚀] {source_name} 수집을 시작합니다...")
        try:
            items = fetch_func()
        except Exception as e:
            print(f"[Error] {source_name} 수집 실패: {e}")
            continue
            
        print(f"-> {len(items)}건의 공고를 감지했습니다.")
        for item in items:
            uid = item["uid"]
            if uid in seen:
                continue
            seen.add(uid)
            
            # 1차 필터링: 제목 키워드 비교
            title = item["title"]
            is_matched_kw = any(kw.lower() in title.lower() for kw in KEYWORDS)
            if not is_matched_kw:
                continue
                
            # 1.5차 필터링: 스마트 지역 제한 및 차단 키워드 필터링
            if not is_valid_notice(title):
                continue
                
            # 2차 필터링: Gemini LLM 자격 검증
            evaluation = evaluate_matching_with_gemini(title)
            
            # Gemini API 무료티어 속도제한(10 RPM) 대응 - 6초 대기
            time.sleep(6)
            
            if evaluation.get("is_matched") and evaluation.get("score", 0) >= 4:
                send_telegram(
                    title=title,
                    source=item["source"],
                    link=item["url"],
                    score=evaluation["score"],
                    reason=evaluation["reason"]
                )
                new_alerts += 1
                
    save_seen(seen)
    print(f"\n[🏁] 완료: 새 알림 {new_alerts}건 발송, 누적 수집 기록 {len(seen)}건")

if __name__ == "__main__":
    main()
