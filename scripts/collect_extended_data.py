"""
장 마감 후 과거 후보 종목들의 데이터 추가 수집 스크립트
목적: 선정된 이후 며칠간의 주가 흐름 분석(백테스팅)을 위해 최근 N일간 선정된 종목들의 오늘자 데이터를 수집합니다.
"""
import sys
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

# 프로젝트 루트 경로 추가
sys.path.append(str(Path(__file__).parent.parent))

from api.kis_api_manager import KISAPIManager
from db.database_manager import DatabaseManager
from utils.logger import setup_logger
from utils.korean_time import now_kst
from core.post_market_data_saver import PostMarketDataSaver
from api.kis_chart_api import get_full_trading_day_data_async

class ExtendedDataCollector:
    def __init__(self, api_manager=None, db_manager=None):
        self.logger = setup_logger("ExtendedDataCollector")
        self.api_manager = api_manager if api_manager else KISAPIManager()
        self.db_manager = db_manager if db_manager else DatabaseManager()
        self.data_saver = PostMarketDataSaver()
        self.days_to_track = 10  # 최근 10일간 선정된 종목 추적

    async def collect_data(self):
        """데이터 수집 실행"""
        try:
            self.logger.info("🚀 과거 후보 종목 데이터 추가 수집 시작")
            
            # 1. API 초기화
            if not self.api_manager.initialize():
                self.logger.error("❌ API 초기화 실패")
                return

            # 2. 최근 N일간 선정된 종목 리스트 DB에서 조회
            target_stocks = self._get_recent_candidate_stocks()
            if not target_stocks:
                self.logger.info("수집할 대상 종목이 없습니다.")
                return

            self.logger.info(f"📊 수집 대상: 총 {len(target_stocks)}개 종목 (최근 {self.days_to_track}일 선정)")

            # 3. 데이터 수집 및 저장
            success_count = 0
            today = now_kst().strftime("%Y%m%d")

            for stock_code, stock_name in target_stocks.items():
                try:
                    self.logger.info(f"🔄 [{stock_code}] {stock_name} 데이터 수집 중...")
                    
                    # 3-1. 일봉 데이터 저장 (기존 Saver 활용)
                    self.data_saver.save_daily_data([stock_code], target_date=today)
                    
                    # 3-2. 분봉 데이터 수집 및 저장
                    # 이미 저장된 파일이 있는지 확인
                    minute_file = self.data_saver.minute_cache_dir / f"{stock_code}_{today}.pkl"
                    if minute_file.exists():
                        self.logger.info(f"  ⏭️ 분봉 데이터 이미 존재 (스킵)")
                    else:
                        # API로 오늘자 전체 분봉 조회
                        minute_data = await get_full_trading_day_data_async(
                            stock_code=stock_code,
                            target_date=today
                        )
                        
                        if minute_data is not None and not minute_data.empty:
                            # 저장
                            import pickle
                            with open(minute_file, 'wb') as f:
                                pickle.dump(minute_data, f)
                            self.logger.info(f"  ✅ 분봉 데이터 저장 완료 ({len(minute_data)}건)")
                        else:
                            self.logger.warning(f"  ⚠️ 분봉 데이터 없음")

                    success_count += 1
                    await asyncio.sleep(0.5)  # API 호출 제한 고려

                except Exception as e:
                    self.logger.error(f"❌ {stock_code} 처리 중 오류: {e}")

            self.logger.info(f"🏁 데이터 추가 수집 완료: {success_count}/{len(target_stocks)}개 성공")

        except Exception as e:
            self.logger.error(f"❌ 전체 프로세스 오류: {e}")

    def _get_recent_candidate_stocks(self) -> dict:
        """DB에서 최근 N일간 선정된 종목 조회"""
        try:
            start_date = now_kst() - timedelta(days=self.days_to_track)
            
            # DB 연결 및 쿼리
            import sqlite3
            with sqlite3.connect(self.db_manager.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT DISTINCT stock_code, stock_name 
                    FROM candidate_stocks 
                    WHERE selection_date >= ?
                ''', (start_date.strftime('%Y-%m-%d %H:%M:%S'),))
                
                rows = cursor.fetchall()
                
            return {row[0]: row[1] for row in rows}

        except Exception as e:
            self.logger.error(f"DB 조회 실패: {e}")
            return {}

if __name__ == "__main__":
    collector = ExtendedDataCollector()
    asyncio.run(collector.collect_data())
