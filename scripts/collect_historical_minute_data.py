"""
1년치 과거 분봉 데이터 수집 스크립트
KIS API inquire-time-dailychartprice (TR: FHKST03010230) 사용
- 1회 호출: 최대 120건 (120분)
- 하루 전체: 약 4회 호출 (09:00~15:30 = 390분)
- API 제한: 초당 20건 (60ms 간격)

사용법:
  python scripts/collect_historical_minute_data.py --start 20250301 --end 20260223
  python scripts/collect_historical_minute_data.py --start 20250301 --end 20260223 --max-stocks 50
  python scripts/collect_historical_minute_data.py --resume  # 중단된 지점부터 재개
"""
import sys
import os
import time
import json
import argparse
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.kis_api_manager import KISAPIManager
from api.kis_chart_api import get_inquire_time_dailychartprice, get_div_code_for_stock
from db.postgres_manager import PostgresManager
from utils.logger import setup_logger

logger = setup_logger("HistoricalCollector")

PROGRESS_FILE = Path("cache/collection_progress.json")


def get_trading_dates(start_date: str, end_date: str, pg: PostgresManager) -> List[str]:
    """PG daily_candles에서 실제 거래일 목록 추출"""
    try:
        rows = pg.execute_query(
            "SELECT DISTINCT candle_date FROM daily_candles "
            "WHERE candle_date >= %s AND candle_date <= %s "
            "ORDER BY candle_date",
            (f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}",
             f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}")
        )
        if rows:
            return [r[0].strftime('%Y%m%d') for r in rows]
    except Exception as e:
        logger.warning(f"PG 거래일 조회 실패: {e}")

    # fallback: 주말 제외한 날짜 생성
    dates = []
    start = datetime.strptime(start_date, '%Y%m%d')
    end = datetime.strptime(end_date, '%Y%m%d')
    current = start
    while current <= end:
        if current.weekday() < 5:  # 월~금
            dates.append(current.strftime('%Y%m%d'))
        current += timedelta(days=1)
    return dates


def get_universe_stocks(pg: PostgresManager) -> List[Dict]:
    """Universe 종목 목록 (PG daily_candles에서 최근 활성 종목)"""
    try:
        rows = pg.execute_query("""
            SELECT DISTINCT stock_code FROM daily_candles
            WHERE candle_date >= (SELECT MAX(candle_date) - INTERVAL '7 days' FROM daily_candles)
            ORDER BY stock_code
        """)
        if rows:
            return [{'stock_code': r[0]} for r in rows]
    except Exception as e:
        logger.warning(f"PG 종목 조회 실패: {e}")

    # fallback: 최신 universe 파일
    data_dir = Path("data")
    universe_files = sorted(data_dir.glob("universe_*.json"), reverse=True)
    if universe_files:
        with open(universe_files[0], 'r', encoding='utf-8') as f:
            data = json.load(f)
        stocks = []
        for item in data.get('stocks', data) if isinstance(data, dict) else data:
            if isinstance(item, dict):
                stocks.append({'stock_code': item.get('stock_code', item.get('code', ''))})
            elif isinstance(item, str):
                stocks.append({'stock_code': item})
        return stocks
    return []


def collect_day_minute_data(stock_code: str, date_str: str) -> Optional[pd.DataFrame]:
    """특정 종목/날짜의 전체 분봉 수집 (120건씩 연속 호출)"""
    all_data = []
    input_hour = "160000"  # 장 마감부터 역순으로
    div_code = get_div_code_for_stock(stock_code)
    max_calls = 5  # 안전장치

    for call_idx in range(max_calls):
        try:
            result = get_inquire_time_dailychartprice(
                div_code=div_code,
                stock_code=stock_code,
                input_date=date_str,
                input_hour=input_hour,
                past_data_yn="Y"
            )

            if result is None:
                break

            _, chart_df = result
            if chart_df is None or chart_df.empty:
                break

            all_data.append(chart_df)

            # 가장 이른 시간 확인 → 09:00 이전이면 종료
            if 'stck_cntg_hour' in chart_df.columns:
                earliest = chart_df['stck_cntg_hour'].min()
                if isinstance(earliest, str) and earliest <= "090000":
                    break
                # 다음 호출을 위해 가장 이른 시간 설정
                input_hour = earliest
            elif 'datetime' in chart_df.columns:
                earliest_dt = pd.to_datetime(chart_df['datetime']).min()
                if earliest_dt.hour < 9 or (earliest_dt.hour == 9 and earliest_dt.minute == 0):
                    break
                input_hour = earliest_dt.strftime('%H%M%S')
            else:
                break

            time.sleep(0.08)  # API rate limit

        except Exception as e:
            logger.warning(f"  {stock_code} {date_str} 호출 {call_idx} 오류: {e}")
            break

    if not all_data:
        return None

    combined = pd.concat(all_data, ignore_index=True)

    # 중복 제거
    if 'datetime' in combined.columns:
        combined = combined.drop_duplicates(subset=['datetime']).sort_values('datetime').reset_index(drop=True)
    elif 'stck_bsop_date' in combined.columns and 'stck_cntg_hour' in combined.columns:
        combined = combined.drop_duplicates(
            subset=['stck_bsop_date', 'stck_cntg_hour']
        ).sort_values(['stck_bsop_date', 'stck_cntg_hour']).reset_index(drop=True)

    return combined


def save_to_pg(pg: PostgresManager, stock_code: str, date_str: str, df: pd.DataFrame):
    """분봉 데이터를 PG에 저장"""
    try:
        pg.save_minute_candles(stock_code, date_str, df)
    except Exception as e:
        logger.debug(f"  PG 저장 실패 (무시): {e}")


def load_progress() -> Dict:
    """수집 진행 상황 로드"""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    return {'completed': {}, 'last_date': '', 'last_stock': ''}


def save_progress(progress: Dict):
    """수집 진행 상황 저장"""
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=2)


def is_already_collected(pg: PostgresManager, stock_code: str, date_str: str) -> bool:
    """이미 수집된 데이터인지 확인 (PG minute_candles 존재 여부)"""
    try:
        date_formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        rows = pg.execute_query(
            "SELECT 1 FROM minute_candles WHERE stock_code = %s AND candle_date = %s LIMIT 1",
            (stock_code, date_formatted)
        )
        return rows is not None and len(rows) > 0
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description='1년치 과거 분봉 데이터 수집')
    parser.add_argument('--start', type=str, default='20250301', help='시작 날짜 (YYYYMMDD)')
    parser.add_argument('--end', type=str, default='20260223', help='종료 날짜 (YYYYMMDD)')
    parser.add_argument('--max-stocks', type=int, default=0, help='수집할 최대 종목 수 (0=전체)')
    parser.add_argument('--resume', action='store_true', help='중단된 지점부터 재개')
    parser.add_argument('--dry-run', action='store_true', help='실제 API 호출 없이 규모만 확인')
    args = parser.parse_args()

    # PG 연결
    pg = PostgresManager()

    # API 초기화
    api = KISAPIManager()
    if not args.dry_run:
        if not api.initialize():
            logger.error("❌ API 초기화 실패")
            return

    # 거래일 목록
    trading_dates = get_trading_dates(args.start, args.end, pg)
    logger.info(f"📅 거래일: {len(trading_dates)}일 ({args.start} ~ {args.end})")

    # 종목 목록
    stocks = get_universe_stocks(pg)
    if args.max_stocks > 0:
        stocks = stocks[:args.max_stocks]
    logger.info(f"📊 종목: {len(stocks)}개")

    # 규모 산정
    total_combinations = len(trading_dates) * len(stocks)
    est_api_calls = total_combinations * 4  # 종목당 약 4회 호출
    est_hours = est_api_calls * 0.08 / 3600  # 0.08초 간격

    logger.info(f"📈 예상 규모: {total_combinations:,}건 (날짜×종목)")
    logger.info(f"📞 예상 API 호출: ~{est_api_calls:,}회")
    logger.info(f"⏱️  예상 소요: ~{est_hours:.1f}시간")

    if args.dry_run:
        logger.info("🔍 dry-run 모드 — 실제 수집하지 않습니다.")
        return

    # 진행 상황 로드
    progress = load_progress() if args.resume else {'completed': {}, 'last_date': '', 'last_stock': ''}
    completed_set: Dict[str, Set] = {}
    for date_key, stock_list in progress.get('completed', {}).items():
        completed_set[date_key] = set(stock_list)

    # 수집 시작
    total_collected = 0
    total_skipped = 0
    total_empty = 0
    start_time = time.time()

    try:
        for di, date_str in enumerate(trading_dates):
            date_completed = completed_set.get(date_str, set())
            date_new = 0

            for si, stock in enumerate(stocks):
                code = stock['stock_code']

                # 이미 완료된 건 스킵
                if code in date_completed or is_already_collected(pg, code, date_str):
                    total_skipped += 1
                    continue

                # API 수집
                df = collect_day_minute_data(code, date_str)

                if df is not None and not df.empty:
                    save_to_pg(pg, code, date_str, df)
                    total_collected += 1
                    date_new += 1
                else:
                    total_empty += 1

                # 진행 상황 업데이트
                if date_str not in completed_set:
                    completed_set[date_str] = set()
                completed_set[date_str].add(code)

                # 100건마다 진행 저장 + 로그
                if (total_collected + total_empty) % 100 == 0:
                    elapsed = time.time() - start_time
                    rate = (total_collected + total_empty + total_skipped) / max(elapsed, 1)
                    remaining = (total_combinations - total_collected - total_empty - total_skipped) / max(rate, 0.01)
                    logger.info(
                        f"📊 진행: [{di+1}/{len(trading_dates)}일] {date_str} "
                        f"수집={total_collected} 스킵={total_skipped} 빈값={total_empty} "
                        f"속도={rate:.1f}건/초 남은시간={remaining/60:.0f}분"
                    )
                    # 진행 저장
                    progress['completed'] = {k: list(v) for k, v in completed_set.items()}
                    progress['last_date'] = date_str
                    progress['last_stock'] = code
                    save_progress(progress)

            if date_new > 0:
                logger.info(f"✅ {date_str} 완료: {date_new}개 종목 수집")

    except KeyboardInterrupt:
        logger.info("⚠️ 사용자 중단 — 진행 상황 저장 중...")
    except Exception as e:
        logger.error(f"❌ 수집 오류: {e}")
    finally:
        # 최종 진행 저장
        progress['completed'] = {k: list(v) for k, v in completed_set.items()}
        save_progress(progress)

        elapsed = time.time() - start_time
        logger.info(f"\n{'='*60}")
        logger.info(f"📊 수집 완료 요약")
        logger.info(f"  수집: {total_collected}건")
        logger.info(f"  스킵(기존): {total_skipped}건")
        logger.info(f"  빈값: {total_empty}건")
        logger.info(f"  소요: {elapsed/60:.1f}분")
        logger.info(f"{'='*60}")


if __name__ == '__main__':
    main()
