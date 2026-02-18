"""
주간 Universe 업데이트 스크립트

네이버 금융에서 시가총액 상위 종목 크롤링:
- KOSPI: 상위 200개
- KOSDAQ: 상위 100개
- 총 300개 종목

실행: 매주 금요일 장마감 후 또는 주말
"""

import sys
from pathlib import Path

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
from datetime import datetime
from utils.korean_time import now_kst
from utils.logger import setup_logger

logger = setup_logger(__name__)


def crawl_naver_finance_market(market_code: int, target_count: int) -> list:
    """
    네이버 금융에서 특정 시장의 상위 종목 크롤링

    Args:
        market_code: 0=KOSPI, 1=KOSDAQ
        target_count: 목표 종목 수

    Returns:
        종목 리스트 (dict)
    """
    market_name = 'KOSPI' if market_code == 0 else 'KOSDAQ'
    print(f"[INFO] {market_name} 상위 {target_count}개 크롤링 시작...")

    stocks = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    page = 1
    max_pages = (target_count // 50) + 2  # 페이지당 50개, 여유분

    while len(stocks) < target_count and page <= max_pages:
        url = f'https://finance.naver.com/sise/sise_market_sum.naver?sosok={market_code}&page={page}'

        try:
            print(f"  페이지 {page} 크롤링 중...")
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            response.encoding = 'euc-kr'  # 네이버 금융 인코딩

            soup = BeautifulSoup(response.text, 'html.parser')
            table = soup.select_one('table.type_2')

            if not table:
                print(f"  [WARN] 페이지 {page}: 테이블을 찾을 수 없습니다.")
                # HTML 저장 (디버깅용)
                with open(f'debug_page_{market_name}_{page}.html', 'w', encoding='utf-8') as f:
                    f.write(response.text)
                print(f"  [DEBUG] HTML 저장: debug_page_{market_name}_{page}.html")
                break

            rows = table.select('tr')
            page_count = 0

            for row in rows:
                cols = row.select('td')
                if len(cols) < 2:
                    continue

                # 종목명과 코드 (2번째 td)
                name_cell = cols[1].select_one('a')
                if not name_cell:
                    continue

                stock_name = name_cell.text.strip()
                href = name_cell.get('href', '')

                # 코드 추출
                if 'code=' not in href:
                    continue
                stock_code = href.split('code=')[-1].split('&')[0]

                # 우선주 제외
                if '우' in stock_name or '전환' in stock_name or stock_code.endswith('5'):
                    continue

                # 시가총액 (7번째 td, 0부터 시작하면 6번)
                if len(cols) > 6:
                    market_cap_text = cols[6].text.strip().replace(',', '')
                    try:
                        market_cap = int(market_cap_text) if market_cap_text else 0
                    except:
                        market_cap = 0
                else:
                    market_cap = 0

                if market_cap == 0:
                    continue

                stocks.append({
                    'code': stock_code,
                    'name': stock_name,
                    'market': market_name,
                    'market_cap': market_cap,
                    'rank': len(stocks) + 1
                })

                page_count += 1

                if len(stocks) >= target_count:
                    break

            print(f"  페이지 {page}: {page_count}개 종목 수집 (누적: {len(stocks)}개)")

            # 다음 페이지로
            page += 1
            time.sleep(0.5)  # 서버 부하 방지

        except requests.exceptions.RequestException as e:
            print(f"  [ERROR] 페이지 {page} 크롤링 오류: {e}")
            break
        except Exception as e:
            print(f"  [ERROR] 페이지 {page} 파싱 오류: {e}")
            import traceback
            traceback.print_exc()
            break

    print(f"[OK] {market_name} 크롤링 완료: {len(stocks)}개")
    return stocks[:target_count]  # 정확히 target_count개만 반환


def save_weekly_universe(kospi_count: int = 200, kosdaq_count: int = 100):
    """
    주간 Universe 저장

    Args:
        kospi_count: KOSPI 종목 수 (기본 200)
        kosdaq_count: KOSDAQ 종목 수 (기본 100)
    """
    print("=" * 60)
    print("[INFO] 주간 Universe 업데이트 시작")
    print("=" * 60)

    # 1. KOSPI 상위 200개
    kospi_stocks = crawl_naver_finance_market(market_code=0, target_count=kospi_count)

    # 2. KOSDAQ 상위 100개
    kosdaq_stocks = crawl_naver_finance_market(market_code=1, target_count=kosdaq_count)

    # 3. 통합
    all_stocks = kospi_stocks + kosdaq_stocks

    print(f"\n[INFO] 수집 결과:")
    print(f"  - KOSPI: {len(kospi_stocks)}개")
    print(f"  - KOSDAQ: {len(kosdaq_stocks)}개")
    print(f"  - 총계: {len(all_stocks)}개")

    if len(all_stocks) == 0:
        print("\n[ERROR] 수집된 종목이 없습니다. 크롤링 실패.")
        return None

    # 4. DataFrame 변환
    df = pd.DataFrame(all_stocks)

    # 5. 파일 저장
    today = now_kst().strftime('%Y%m%d')

    # JSON 저장
    json_path = project_root / 'data' / f'universe_{today}.json'
    df.to_json(json_path, orient='records', force_ascii=False, indent=2)
    print(f"\n[OK] JSON 저장: {json_path}")

    # CSV 저장 (백업용)
    csv_path = project_root / 'data' / f'universe_{today}.csv'
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"[OK] CSV 저장: {csv_path}")

    # 6. 요약 통계
    print(f"\n[INFO] Universe 요약:")
    print(f"  시가총액 범위:")
    print(f"    - 최대: {df['market_cap'].max():,}억원 ({df.loc[df['market_cap'].idxmax(), 'name']})")
    print(f"    - 최소: {df['market_cap'].min():,}억원 ({df.loc[df['market_cap'].idxmin(), 'name']})")
    print(f"    - 평균: {df['market_cap'].mean():,.0f}억원")

    print("\n" + "=" * 60)
    print("[OK] 주간 Universe 업데이트 완료!")
    print("=" * 60)

    return df


def load_latest_universe() -> pd.DataFrame:
    """
    가장 최신 Universe 로드

    Returns:
        Universe DataFrame
    """
    data_dir = project_root / 'data'
    universe_files = list(data_dir.glob('universe_*.json'))

    if not universe_files:
        logger.error("❌ Universe 파일이 없습니다. update_weekly_universe.py를 먼저 실행하세요.")
        return pd.DataFrame()

    # 날짜 추출 함수
    def get_date_from_filename(f: Path) -> str:
        try:
            # universe_20260103.json -> 20260103
            return f.stem.split('_')[1]
        except IndexError:
            return "00000000"

    # 가장 최신 파일 찾기
    latest_file = max(universe_files, key=get_date_from_filename)

    logger.info(f"📂 Universe 로드: {latest_file.name} (목록: {[f.name for f in universe_files]})")
    df = pd.read_json(latest_file)

    logger.info(f"  총 {len(df)}개 종목 (KOSPI: {len(df[df['market']=='KOSPI'])}개, KOSDAQ: {len(df[df['market']=='KOSDAQ'])}개)")

    return df


def get_universe_age_days() -> int:
    """
    가장 최신 Universe 파일의 생성 후 경과 일수 계산

    Returns:
        경과 일수 (파일이 없으면 999 반환)
    """
    data_dir = project_root / 'data'
    universe_files = list(data_dir.glob('universe_*.json'))

    if not universe_files:
        return 999  # 파일이 없으면 매우 큰 값 반환

    # 가장 최신 파일
    latest_file = max(universe_files, key=lambda f: f.stem.split('_')[1])
    
    # 파일명에서 날짜 추출 (universe_20260103.json)
    date_str = latest_file.stem.split('_')[1]  # '20260103'
    file_date = datetime.strptime(date_str, '%Y%m%d')
    
    # 현재 날짜와 비교
    today = now_kst()
    age_days = (today - file_date).days
    
    return age_days


def needs_update(max_age_days: int = 7) -> bool:
    """
    Universe 업데이트가 필요한지 확인

    Args:
        max_age_days: 최대 허용 일수 (기본 7일)

    Returns:
        True: 업데이트 필요, False: 업데이트 불필요
    """
    age = get_universe_age_days()
    return age >= max_age_days


def auto_update_if_needed(max_age_days: int = 7, kospi_count: int = 200, kosdaq_count: int = 100) -> bool:
    """
    필요 시 자동으로 Universe 업데이트

    Args:
        max_age_days: 최대 허용 일수
        kospi_count: KOSPI 종목 수
        kosdaq_count: KOSDAQ 종목 수

    Returns:
        True: 업데이트 실행됨, False: 업데이트 불필요
    """
    age = get_universe_age_days()
    
    if age >= max_age_days:
        logger.info(f"📅 Universe 업데이트 필요: {age}일 경과 (최대 {max_age_days}일)")
        logger.info("🔄 자동 업데이트 시작...")
        
        try:
            save_weekly_universe(kospi_count, kosdaq_count)
            logger.info("✅ Universe 자동 업데이트 완료!")
            return True
        except Exception as e:
            logger.error(f"❌ Universe 자동 업데이트 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    else:
        logger.info(f"✅ Universe 최신 상태: {age}일 경과 (최대 {max_age_days}일)")
        return False


if __name__ == '__main__':
    """
    실행 예시:

    # 기본 (KOSPI 200 + KOSDAQ 100)
    python scripts/update_weekly_universe.py

    # 커스텀 개수
    python scripts/update_weekly_universe.py 150 80
    """
    import sys

    kospi_count = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    kosdaq_count = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    try:
        save_weekly_universe(kospi_count, kosdaq_count)
    except KeyboardInterrupt:
        logger.info("\n\n⚠️ 사용자에 의해 중단되었습니다.")
    except Exception as e:
        logger.error(f"\n\n❌ 오류 발생: {e}")
        import traceback
        logger.error(traceback.format_exc())
