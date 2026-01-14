"""
과거 분봉 데이터 수집 담당 클래스
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import pandas as pd
import threading

from utils.logger import setup_logger
from utils.korean_time import now_kst
from config.market_hours import MarketHours
from api.kis_chart_api import (
    get_inquire_time_itemchartprice,
    get_full_trading_day_data_async,
    get_div_code_for_stock
)
from core.intraday_data_utils import (
    calculate_time_range_minutes,
    validate_minute_data_continuity
)

class HistoricalDataCollector:
    """
    장중 선정된 종목의 과거 분봉 데이터(당일 08:00 ~ 선정시점)를 수집하는 클래스
    """
    def __init__(self, intraday_manager):
        """
        초기화
        
        Args:
            intraday_manager: IntradayStockManager 인스턴스 (메모리 저장소 접근용)
        """
        self.logger = setup_logger(__name__)
        self.manager = intraday_manager
        
    async def collect_historical_data(self, stock_code: str) -> bool:
        """
        당일 08:00부터 선정시점까지의 전체 분봉 데이터 수집
        
        Args:
            stock_code: 종목코드
            
        Returns:
            bool: 수집 성공 여부
        """
        try:
            with self.manager._lock:
                if stock_code not in self.manager.selected_stocks:
                    return False
                    
                stock_data = self.manager.selected_stocks[stock_code]
                selected_time = stock_data.selected_time
            
            self.logger.info(f"📈 {stock_code} 전체 거래시간 분봉 데이터 수집 시작")
            self.logger.info(f"   선정 시간: {selected_time.strftime('%H:%M:%S')}")

            # 🆕 동적 시장 거래시간 가져오기
            market_hours = MarketHours.get_market_hours('KRX', selected_time)
            market_open = market_hours['market_open']
            start_time_str = market_open.strftime('%H%M%S')

            # 당일 시장 시작시간부터 선정시점까지의 전체 거래시간 데이터 수집
            target_date = selected_time.strftime("%Y%m%d")
            target_hour = selected_time.strftime("%H%M%S")

            # 🔥 중요: 미래 데이터 수집 방지 - 선정 시점까지만 수집
            self.logger.info(f"📈 {stock_code} 과거 데이터 수집: {market_open.strftime('%H:%M')} ~ {selected_time.strftime('%H:%M:%S')}")

            historical_data = await get_full_trading_day_data_async(
                stock_code=stock_code,
                target_date=target_date,
                selected_time=target_hour,  # 선정 시점까지만!
                start_time=start_time_str  # 동적 시장 시작 시간
            )
            
            if historical_data is None or historical_data.empty:
                # 실패 시 1분씩 앞으로 이동하여 재시도
                try:
                    selected_time_dt = datetime.strptime(target_hour, "%H%M%S")
                    new_time_dt = selected_time_dt + timedelta(minutes=1)
                    new_target_hour = new_time_dt.strftime("%H%M%S")
                    
                    # 장 마감 시간(15:30) 초과 시 현재 시간으로 조정
                    if new_target_hour > "153000":
                        new_target_hour = now_kst().strftime("%H%M%S")
                    
                    self.logger.warning(f"🔄 {stock_code} 전체 데이터 조회 실패, 시간 조정하여 재시도: {target_hour} → {new_target_hour}")
                    
                    # 조정된 시간으로 재시도
                    historical_data = await get_full_trading_day_data_async(
                        stock_code=stock_code,
                        target_date=target_date,
                        selected_time=new_target_hour,
                        start_time=start_time_str  # 동적 시장 시작 시간 사용
                    )
                    
                    if historical_data is not None and not historical_data.empty:
                        # 성공 시 selected_time 업데이트
                        with self.manager._lock:
                            if stock_code in self.manager.selected_stocks:
                                new_selected_time = selected_time.replace(
                                    hour=new_time_dt.hour,
                                    minute=new_time_dt.minute,
                                    second=new_time_dt.second
                                )
                                self.manager.selected_stocks[stock_code].selected_time = new_selected_time
                                self.logger.info(f"✅ {stock_code} 시간 조정으로 전체 데이터 조회 성공, selected_time 업데이트: {new_selected_time.strftime('%H:%M:%S')}")
                    
                except Exception as e:
                    self.logger.error(f"❌ {stock_code} 전체 데이터 시간 조정 중 오류: {e}")
                
                if historical_data is None or historical_data.empty:
                    self.logger.error(f"❌ {stock_code} 당일 전체 분봉 데이터 조회 실패 (시간 조정 후에도 실패)")
                    # 실패 시 기존 방식으로 폴백
                    return await self._collect_historical_data_fallback(stock_code)
            
            # 🆕 당일 데이터만 필터링 (전날 데이터 혼입 방지 - 최우선)
            today_str = selected_time.strftime('%Y%m%d')
            before_count = len(historical_data)
            
            if 'date' in historical_data.columns:
                historical_data = historical_data[historical_data['date'].astype(str) == today_str].copy()
            elif 'datetime' in historical_data.columns:
                historical_data['date_str'] = pd.to_datetime(historical_data['datetime']).dt.strftime('%Y%m%d')
                historical_data = historical_data[historical_data['date_str'] == today_str].copy()
                if 'date_str' in historical_data.columns:
                    historical_data = historical_data.drop('date_str', axis=1)
            
            if before_count != len(historical_data):
                removed = before_count - len(historical_data)
                self.logger.warning(f"⚠️ {stock_code} 초기 수집 시 전날 데이터 {removed}건 제외: {before_count} → {len(historical_data)}건")
            
            if historical_data.empty:
                self.logger.error(f"❌ {stock_code} 당일 데이터 없음 (전날 데이터만 존재)")
                return await self._collect_historical_data_fallback(stock_code)
            
            # 데이터 정렬 및 정리 (시간 순서)
            if 'datetime' in historical_data.columns:
                historical_data = historical_data.sort_values('datetime').reset_index(drop=True)
                # 선정 시간을 timezone-naive로 변환하여 pandas datetime64[ns]와 비교
                selected_time_naive = selected_time.replace(tzinfo=None)
                filtered_data = historical_data[historical_data['datetime'] <= selected_time_naive].copy()
            elif 'time' in historical_data.columns:
                historical_data = historical_data.sort_values('time').reset_index(drop=True)
                # time 컬럼을 이용한 필터링
                selected_time_str = selected_time.strftime("%H%M%S")
                historical_data['time_str'] = historical_data['time'].astype(str).str.zfill(6)
                filtered_data = historical_data[historical_data['time_str'] <= selected_time_str].copy()
                if 'time_str' in filtered_data.columns:
                    filtered_data = filtered_data.drop('time_str', axis=1)
            else:
                # 시간 컬럼이 없으면 전체 데이터 사용
                filtered_data = historical_data.copy()

            # 🆕 1분봉 연속성 검증: 09:00부터 순서대로 1분 간격으로 있어야 함
            if not filtered_data.empty:
                validation_result = validate_minute_data_continuity(filtered_data, stock_code, self.logger)
                if not validation_result['valid']:
                    self.logger.error(f"❌ {stock_code} 1분봉 연속성 검증 실패: {validation_result['reason']}")
                    # 데이터가 불완전하면 폴백 방식으로 재시도
                    return await self._collect_historical_data_fallback(stock_code)
            
            daily_data = pd.DataFrame()  # 빈 DataFrame
            
            # 메모리에 저장
            with self.manager._lock:
                if stock_code in self.manager.selected_stocks:
                    self.manager.selected_stocks[stock_code].historical_data = filtered_data
                    self.manager.selected_stocks[stock_code].daily_data = daily_data  # 빈 DataFrame 저장
                    self.manager.selected_stocks[stock_code].data_complete = True
                    self.manager.selected_stocks[stock_code].last_update = now_kst()
            
            # 데이터 분석 및 로깅
            data_count = len(filtered_data)
            if data_count > 0:
                if 'time' in filtered_data.columns:
                    start_time = filtered_data.iloc[0].get('time', 'N/A')
                    end_time = filtered_data.iloc[-1].get('time', 'N/A')
                elif 'datetime' in filtered_data.columns:
                    start_dt = filtered_data.iloc[0].get('datetime')
                    end_dt = filtered_data.iloc[-1].get('datetime')
                    start_time = start_dt.strftime('%H%M%S') if start_dt else 'N/A'
                    end_time = end_dt.strftime('%H%M%S') if end_dt else 'N/A'
                else:
                    start_time = end_time = 'N/A'
                
                # 시간 범위 계산
                time_range_minutes = calculate_time_range_minutes(start_time, end_time)
                
                # 3분봉 변환 예상 개수 계산
                expected_3min_count = data_count // 3
                self.logger.info(f"   예상 3분봉: {expected_3min_count}개 (최소 3개 필요)")

                if expected_3min_count >= 3:
                    self.logger.info(f"   ✅ 신호 생성 조건 충족!")
                else:
                    self.logger.warning(f"   ⚠️ 3분봉 데이터 부족 위험: {expected_3min_count}/3")

                # 시장 시작시간부터 데이터가 시작되는지 확인
                if start_time and start_time >= start_time_str:
                    self.logger.info(f"   📊 정규장 데이터: {start_time}부터")
                
            else:
                self.logger.info(f"ℹ️ {stock_code} 선정 시점 이전 분봉 데이터 없음")
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ {stock_code} 전체 거래시간 분봉 데이터 수집 오류: {e}")
            # 오류 시 기존 방식으로 폴백
            return await self._collect_historical_data_fallback(stock_code)
            
    async def _collect_historical_data_fallback(self, stock_code: str) -> bool:
        """
        과거 분봉 데이터 수집 폴백 함수 (기존 방식)
        """
        try:
            with self.manager._lock:
                if stock_code not in self.manager.selected_stocks:
                    return False
                    
                stock_data = self.manager.selected_stocks[stock_code]
                selected_time = stock_data.selected_time
            
            self.logger.warning(f"🔄 {stock_code} 폴백 방식으로 과거 분봉 데이터 수집")
            
            # 선정 시간까지의 당일 분봉 데이터 조회 (기존 방식)
            target_hour = selected_time.strftime("%H%M%S")
            
            # 당일분봉조회 API 사용 (최대 30건)
            div_code = get_div_code_for_stock(stock_code)
            
            result = get_inquire_time_itemchartprice(
                div_code=div_code,
                stock_code=stock_code,
                input_hour=target_hour,
                past_data_yn="Y"
            )
            
            if result is None:
                # 실패 시 1분씩 앞으로 이동하여 재시도
                try:
                    selected_time_dt = datetime.strptime(target_hour, "%H%M%S")
                    new_time_dt = selected_time_dt + timedelta(minutes=1)
                    new_target_hour = new_time_dt.strftime("%H%M%S")
                    
                    # 장 마감 시간(15:30) 초과 시 현재 시간으로 조정
                    if new_target_hour > "153000":
                        new_target_hour = now_kst().strftime("%H%M%S")
                    
                    self.logger.warning(f"🔄 {stock_code} 조회 실패, 시간 조정하여 재시도: {target_hour} → {new_target_hour}")
                    
                    # 조정된 시간으로 재시도
                    result = get_inquire_time_itemchartprice(
                        div_code=div_code,
                        stock_code=stock_code,
                        input_hour=new_target_hour,
                        past_data_yn="Y"
                    )
                    
                    if result is not None:
                        # 성공 시 selected_time 업데이트
                        with self.manager._lock:
                            if stock_code in self.manager.selected_stocks:
                                new_selected_time = selected_time.replace(
                                    hour=new_time_dt.hour,
                                    minute=new_time_dt.minute,
                                    second=new_time_dt.second
                                )
                                self.manager.selected_stocks[stock_code].selected_time = new_selected_time
                                self.logger.info(f"✅ {stock_code} 시간 조정으로 조회 성공, selected_time 업데이트: {new_selected_time.strftime('%H:%M:%S')}")
                    
                except Exception as e:
                    self.logger.error(f"❌ {stock_code} 시간 조정 중 오류: {e}")
                
                if result is None:
                    self.logger.error(f"❌ {stock_code} 폴백 분봉 데이터 조회 실패 (시간 조정 후에도 실패)")
                    return False
            
            summary_df, chart_df = result
            
            if chart_df.empty:
                self.logger.warning(f"⚠️ {stock_code} 폴백 분봉 데이터 없음")
                # 빈 DataFrame이라도 저장
                with self.manager._lock:
                    if stock_code in self.manager.selected_stocks:
                        self.manager.selected_stocks[stock_code].historical_data = pd.DataFrame()
                        self.manager.selected_stocks[stock_code].data_complete = True
                return True
            
            # 선정 시점 이전 데이터만 필터링
            if 'datetime' in chart_df.columns:
                selected_time_naive = selected_time.replace(tzinfo=None)
                historical_data = chart_df[chart_df['datetime'] <= selected_time_naive].copy()
            else:
                historical_data = chart_df.copy()
            
            # 메모리에 저장
            with self.manager._lock:
                if stock_code in self.manager.selected_stocks:
                    self.manager.selected_stocks[stock_code].historical_data = historical_data
                    self.manager.selected_stocks[stock_code].data_complete = True
                    self.manager.selected_stocks[stock_code].last_update = now_kst()
            
            # 데이터 분석
            data_count = len(historical_data)
            if data_count > 0:
                start_time = historical_data.iloc[0].get('time', 'N/A') if 'time' in historical_data.columns else 'N/A'
                end_time = historical_data.iloc[-1].get('time', 'N/A') if 'time' in historical_data.columns else 'N/A'
                
                self.logger.info(f"✅ {stock_code} 폴백 분봉 수집 완료: {data_count}건 "
                               f"({start_time} ~ {end_time})")
                self.logger.warning(f"⚠️ 제한된 데이터 범위 (API 제한으로 최대 30분봉)")
            else:
                self.logger.info(f"ℹ️ {stock_code} 폴백 방식도 데이터 없음")
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ {stock_code} 폴백 분봉 데이터 수집 오류: {e}")
            return False