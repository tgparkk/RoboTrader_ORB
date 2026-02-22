"""
주식 단타 거래 시스템 메인 실행 파일
"""
import asyncio
import signal
import sys
import os
from datetime import datetime, time
from pathlib import Path
import pandas as pd

# 프로젝트 경로 추가
sys.path.append(str(Path(__file__).parent))

from core.models import TradingConfig, StockState
from core.data_collector import RealTimeDataCollector
from core.order_manager import OrderManager
from core.telegram_integration import TelegramIntegration
from core.candidate_selector import CandidateSelector, CandidateStock
from core.intraday_stock_manager import IntradayStockManager
from core.trading_stock_manager import TradingStockManager
from core.trading_decision_engine import TradingDecisionEngine
from core.fund_manager import FundManager
from db.database_manager import DatabaseManager
from api.kis_api_manager import KISAPIManager
from config.settings import load_trading_config
from utils.logger import setup_logger
from utils.korean_time import now_kst, get_market_status, is_market_open, KST
from config.market_hours import MarketHours
from scripts.collect_extended_data import ExtendedDataCollector
from scripts.update_weekly_universe import auto_update_if_needed
# from post_market_chart_generator import PostMarketChartGenerator  # 파일 없음


class DayTradingBot:
    """주식 단타 거래 봇"""
    
    def __init__(self):
        try:
            self.logger = setup_logger(__name__)
            self.is_running = False
            self.pid_file = Path("bot.pid")
            self._last_eod_liquidation_date = None  # 장마감 일괄청산 실행 일자
            
            # 프로세스 중복 실행 방지
            self._check_duplicate_process()
            
            # 설정 초기화
            self.config = self._load_config()
            
            # 핵심 모듈 초기화
            self.api_manager = KISAPIManager()
            self.telegram = TelegramIntegration(trading_bot=self)
            self.data_collector = RealTimeDataCollector(self.config, self.api_manager)
            self.order_manager = OrderManager(self.config, self.api_manager, self.telegram)
            self.candidate_selector = CandidateSelector(
                self.config,
                self.api_manager,
                strategy_name="orb"
            )
            
            # PostgreSQL 초기화
            try:
                from db.postgres_manager import PostgresManager
                self.pg_manager = PostgresManager()
            except Exception as pg_err:
                self.logger.warning(f"PostgreSQL 연결 실패 (pkl fallback): {pg_err}")
                self.pg_manager = None

            # TelegramIntegration에 pg_manager 연결
            if self.pg_manager:
                self.telegram.pg = self.pg_manager

            self.intraday_manager = IntradayStockManager(self.api_manager, pg_manager=self.pg_manager)  # 🆕 장중 종목 관리자

            self.trading_manager = TradingStockManager(
                self.intraday_manager, self.data_collector, self.order_manager, self.telegram
            )  # 🆕 거래 상태 통합 관리자

            self.db_manager = DatabaseManager()
            
            self.decision_engine = TradingDecisionEngine(
                db_manager=self.db_manager,
                telegram_integration=self.telegram,
                trading_manager=self.trading_manager,
                api_manager=self.api_manager,
                intraday_manager=self.intraday_manager,
                strategy_name="orb"
            )  # 🆕 매매 판단 엔진
    
            # 🆕 TradingStockManager에 decision_engine 연결 (쿨다운 설정용)
            self.trading_manager.set_decision_engine(self.decision_engine)

            # PostgreSQL 연결을 전략에도 전달
            if self.pg_manager and self.decision_engine.strategy:
                self.decision_engine.strategy.pg = self.pg_manager
    
            self.fund_manager = FundManager()  # 🆕 자금 관리자
            self.chart_generator = None  # 🆕 장 마감 후 차트 생성기 (지연 초기화)
            
            # 🆕 과거 데이터 수집기 (기존 매니저 주입)
            self.extended_collector = ExtendedDataCollector(
                api_manager=self.api_manager,
                db_manager=self.db_manager
            )
            self._last_extended_collection_date = None # 🆕 마지막 수집 날짜
            
            
            # 신호 핸들러 등록
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
            
        except Exception as e:
            print(f"CRITICAL ERROR in DayTradingBot.__init__: {e}")
            import traceback
            traceback.print_exc()
            raise e

    def _round_to_tick(self, price: float) -> float:
        """KRX 정확한 호가단위에 맞게 반올림 - kis_order_api 함수 사용"""
        try:
            from api.kis_order_api import _round_to_krx_tick
            
            if price <= 0:
                return 0.0
            
            original_price = price
            rounded_price = _round_to_krx_tick(price)
            
            # 로깅으로 가격 조정 확인
            if abs(rounded_price - original_price) > 0:
                self.logger.debug(f"💰 호가단위 조정: {original_price:,.0f}원 → {rounded_price:,.0f}원")
            
            return float(rounded_price)
            
        except Exception as e:
            self.logger.error(f"❌ 호가단위 조정 오류: {e}")
            return float(int(price))


    
    def _check_duplicate_process(self):
        """프로세스 중복 실행 방지"""
        try:
            if self.pid_file.exists():
                # 기존 PID 파일 읽기
                existing_pid = int(self.pid_file.read_text().strip())
                
                # Windows에서 프로세스 존재 여부 확인
                try:
                    import psutil
                    if psutil.pid_exists(existing_pid):
                        process = psutil.Process(existing_pid)
                        if 'python' in process.name().lower() and 'main.py' in ' '.join(process.cmdline()):
                            self.logger.error(f"이미 봇이 실행 중입니다 (PID: {existing_pid})")
                            print(f"오류: 이미 거래 봇이 실행 중입니다 (PID: {existing_pid})")
                            print("기존 프로세스를 먼저 종료해주세요.")
                            sys.exit(1)
                except ImportError:
                    # psutil이 없는 경우 간단한 체크
                    self.logger.warning("psutil 모듈이 없어 정확한 중복 실행 체크를 할 수 없습니다")
                except:
                    # 기존 PID가 존재하지 않으면 PID 파일 삭제
                    self.pid_file.unlink(missing_ok=True)
            
            # 현재 프로세스 PID 저장
            current_pid = os.getpid()
            self.pid_file.write_text(str(current_pid))
            self.logger.info(f"프로세스 PID 등록: {current_pid}")
            
        except Exception as e:
            self.logger.warning(f"중복 실행 체크 중 오류: {e}")
    
    def _load_config(self) -> TradingConfig:
        """거래 설정 로드"""
        config = load_trading_config()
        self.logger.info(f"거래 설정 로드 완료: 후보종목 {len(config.data_collection.candidate_stocks)}개")
        return config
    
    def _signal_handler(self, signum, frame):
        """시그널 핸들러 (Ctrl+C 등)"""
        self.logger.info(f"종료 신호 수신: {signum}")
        self.is_running = False
    
    async def initialize(self) -> bool:
        """시스템 초기화"""
        try:
            self.logger.info("🚀 주식 단타 거래 시스템 초기화 시작")

            # 0. Universe 자동 업데이트 체크 (7일 경과 시 자동 업데이트)
            self.logger.info("📅 Universe 업데이트 체크 중...")
            try:
                auto_update_if_needed(max_age_days=7, kospi_count=200, kosdaq_count=100)
            except Exception as e:
                self.logger.warning(f"⚠️ Universe 자동 업데이트 체크 실패: {e}")
                self.logger.warning("⚠️ 기존 Universe 파일로 계속 진행합니다.")

            # 1. 오늘 거래시간 정보 출력 (특수일 확인)
            today_info = MarketHours.get_today_info('KRX')
            self.logger.info(f"📅 오늘 거래시간 정보:\n{today_info}")

            # 2. API 초기화
            self.logger.info("📡 API 매니저 초기화 시작...")
            if not self.api_manager.initialize():
                self.logger.error("❌ API 초기화 실패")
                return False
            self.logger.info("✅ API 매니저 초기화 완료")

            # 2.5. 자금 관리자 초기화 (API 초기화 후)
            # 🆕 가상 매매 모드일 경우 강제로 1000만원 설정
            use_virtual = self.config.risk_management.use_virtual_trading if hasattr(self.config.risk_management, 'use_virtual_trading') else False
            
            if use_virtual:
                self.logger.info("💰 가상 매매 모드: 초기 자금을 10,000,000원으로 고정합니다.")
                self.fund_manager.update_total_funds(10000000)
                # 가상 거래 매니저 잔고도 강제 설정
                if hasattr(self.decision_engine, 'virtual_trading'):
                    self.decision_engine.virtual_trading.virtual_balance = 10000000
                    self.decision_engine.virtual_trading.initial_balance = 10000000
                    self.decision_engine.virtual_trading.virtual_investment_amount = 1000000  # 종목당 100만원
            else:
                balance_info = self.api_manager.get_account_balance()
                if balance_info:
                    total_funds = float(balance_info.account_balance) if hasattr(balance_info, 'account_balance') else 10000000
                    self.fund_manager.update_total_funds(total_funds)
                    self.logger.info(f"💰 자금 관리자 초기화 완료: {total_funds:,.0f}원")
                else:
                    self.logger.warning("⚠️ 잔고 조회 실패 - 기본값 1천만원으로 설정")
                    self.fund_manager.update_total_funds(10000000)

            # 2.6. 가상거래 잔고 초기화 (API 초기화 후) - 위에서 처리했으므로 실거래 모드에서만 로깅
            if not use_virtual and (self.config.risk_management.use_virtual_trading if hasattr(self.config.risk_management, 'use_virtual_trading') else False):
                 # 설정 파일엔 켜져있으나 위 로직에서 use_virtual이 False인 경우 (거의 없음)
                 pass

            # 3. 시장 상태 확인
            market_status = get_market_status()
            self.logger.info(f"📊 현재 시장 상태: {market_status}")
            
            # 4. 텔레그램 초기화
            await self.telegram.initialize()
            
            # 5. DB에서 오늘 날짜의 후보 종목 복원
            await self._restore_todays_candidates()
            
            # 6. 미청산 가상 포지션 복원
            await self._restore_open_virtual_positions()
            
            self.logger.info("✅ 시스템 초기화 완료")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ 시스템 초기화 실패: {e}")
            return False
    
    async def run_daily_cycle(self):
        """일일 거래 사이클 실행"""
        try:
            self.is_running = True
            self.logger.info("📈 일일 거래 사이클 시작")
            
            # 병렬 실행할 태스크들
            tasks = [
                self._data_collection_task(),
                self._order_monitoring_task(),
                self.trading_manager.start_monitoring(),
                self._trading_decision_task(),
                self._system_monitoring_task(),
                self._telegram_task()
            ]
            
            # 모든 태스크 실행
            await asyncio.gather(*tasks, return_exceptions=True)
            
        except Exception as e:
            self.logger.error(f"❌ 일일 거래 사이클 실행 중 오류: {e}")
        finally:
            await self.shutdown()
    
    async def _data_collection_task(self):
        """데이터 수집 태스크"""
        try:
            self.logger.info("📊 데이터 수집 태스크 시작")
            await self.data_collector.start_collection()
        except Exception as e:
            self.logger.error(f"❌ 데이터 수집 태스크 오류: {e}")
            await self.telegram.notify_critical(
                "데이터 수집 태스크 중단",
                f"오류: {e}",
                "시스템 재시작 필요"
            )
    
    async def _order_monitoring_task(self):
        """주문 모니터링 태스크"""
        try:
            self.logger.info("🔍 주문 모니터링 태스크 시작")
            await self.order_manager.start_monitoring()
        except Exception as e:
            self.logger.error(f"❌ 주문 모니터링 태스크 오류: {e}")
            await self.telegram.notify_critical(
                "주문 모니터링 태스크 중단",
                f"오류: {e}",
                "미체결 주문 수동 확인 필요"
            )
    
    async def _trading_decision_task(self):
        """매매 의사결정 태스크"""
        try:
            self.logger.info("🤖 매매 의사결정 태스크 시작")

            orb_range_calculated = False  # ORB 레인지 계산 완료 플래그

            while self.is_running:
                if not is_market_open():
                    await asyncio.sleep(60)  # 장 마감 시 1분 대기
                    continue

                current_time = now_kst()

                # 🆕 P1-1: 15:15 Failsafe Sweep — DB에서 미청산 BUY 직접 조회 후 강제 청산
                if current_time.hour == 15 and current_time.minute == 15:
                    if not hasattr(self, '_failsafe_sweep_done'):
                        await self._failsafe_sweep_unclosed_buys()
                        self._failsafe_sweep_done = True

                # 🚨 장마감 시간 시장가 일괄매도 체크 (한 번만 실행) - 동적 시간 적용
                if MarketHours.is_eod_liquidation_time('KRX', current_time):
                    if not hasattr(self, '_eod_liquidation_done'):
                        await self._execute_end_of_day_liquidation()
                        self._eod_liquidation_done = True

                    # 청산 시간 이후에는 매매 판단 건너뛰고 모니터링만 계속
                    # (장마감 후 데이터 저장을 위해 루프 계속 실행)
                    await asyncio.sleep(5)
                    continue

                # 🆕 ORB 레인지 계산 (09:10 이후 한 번만 실행)
                if not orb_range_calculated and current_time.time() >= time(9, 10):
                    await self._calculate_orb_ranges()
                    orb_range_calculated = True

                # 매매 판단 시스템 실행 (5초 주기)
                # 자금 관리자 업데이트 (가상거래 모드 분기)
                use_virtual = self.config.risk_management.use_virtual_trading if hasattr(self.config.risk_management, 'use_virtual_trading') else False

                if use_virtual:
                    # 가상거래 모드: 가상 잔고 사용 (1000만원 고정 로직 유지)
                    virtual_manager = self.decision_engine.virtual_trading
                    virtual_balance = virtual_manager.get_virtual_balance()
                    # 실계좌 동기화 로직 제거됨 (초기화 시 1000만원 설정값 유지)
                    self.fund_manager.update_total_funds(virtual_balance)
                    self.logger.debug(f"💰 가상거래 잔고: {virtual_balance:,.0f}원")
                else:
                    # 실거래 모드: 실시간 잔고 조회
                    balance_info = self.api_manager.get_account_balance()
                    if balance_info:
                        self.fund_manager.update_total_funds(float(balance_info.account_balance))

                # 현재 가용 자금 계산 (총 자금의 10% 기준)
                fund_status = self.fund_manager.get_status()
                current_available_funds = fund_status['available_funds']
                max_investment_per_stock = fund_status['total_funds'] * 0.1  # 종목당 최대 10%

                self.logger.debug(f"💰 현재 자금 상황: 가용={current_available_funds:,.0f}원, 종목당최대={max_investment_per_stock:,.0f}원")

                await self._execute_trading_decision(current_available_funds)
                await asyncio.sleep(5)  # 5초 주기
                
        except Exception as e:
            self.logger.error(f"❌ 매매 의사결정 태스크 오류: {e}")
            await self.telegram.notify_critical(
                "매매 의사결정 태스크 중단",
                f"오류: {e}",
                "매매 판단 불가 — 시스템 재시작 필요"
            )
    
    async def _execute_trading_decision(self, available_funds: float = None):
        """매매 판단 시스템 실행 (매도 판단 + 포지션 동기화)

        Args:
            available_funds: 사용 가능한 자금 (미리 계산된 값) - 현재 미사용
        """
        try:
            # TradingStockManager에서 관리 중인 종목들 확인
            from core.models import StockState

            selected_stocks = self.trading_manager.get_stocks_by_state(StockState.SELECTED)
            positioned_stocks = self.trading_manager.get_stocks_by_state(StockState.POSITIONED)
            buy_pending_stocks = self.trading_manager.get_stocks_by_state(StockState.BUY_PENDING)
            sell_pending_stocks = self.trading_manager.get_stocks_by_state(StockState.SELL_PENDING)
            completed_stocks = self.trading_manager.get_stocks_by_state(StockState.COMPLETED)

            self.logger.info(
                f"📦 종목 상태 현황:\n"
                f"  - SELECTED: {len(selected_stocks)}개 (매수 대기)\n"
                f"  - COMPLETED: {len(completed_stocks)}개 (재거래 가능)\n"
                f"  - BUY_PENDING: {len(buy_pending_stocks)}개 (매수 주문 중)\n"
                f"  - POSITIONED: {len(positioned_stocks)}개 (보유중)\n"
                f"  - SELL_PENDING: {len(sell_pending_stocks)}개 (매도 주문 중)"
            )

            # 매수 주문 중인 종목 상세 정보
            if buy_pending_stocks:
                for stock in buy_pending_stocks:
                    self.logger.info(f"  📊 매수 체결 대기: {stock.stock_code}({stock.stock_name}) - 주문ID: {stock.current_order_id}")

            # 🆕 매수 판단은 _update_intraday_data()에서 데이터 업데이트 직후 실행됨 (3분봉 + 10초 타이밍)
            # 이 함수에서는 매도 판단과 포지션 동기화만 수행

            # 🔧 긴급 포지션 동기화 (주석 처리됨 - 필요시 활성화)
            await self.emergency_sync_positions()

            # 실제 거래 모드: 실제 포지션만 매도 판단
            if positioned_stocks:
                self.logger.debug(f"💰 매도 판단 대상 {len(positioned_stocks)}개 종목: {[f'{s.stock_code}({s.stock_name})' for s in positioned_stocks]}")
                for trading_stock in positioned_stocks:
                    # 실제 포지션인지 확인
                    if trading_stock.position and trading_stock.position.quantity > 0:
                        await self._analyze_sell_decision(trading_stock)
                    else:
                        self.logger.warning(f"⚠️ {trading_stock.stock_code} 포지션 정보 없음 (매도 판단 건너뜀)")
            else:
                self.logger.debug("📊 매도 판단 대상 종목 없음 (POSITIONED 상태 종목 없음)")

        except Exception as e:
            self.logger.error(f"❌ 매매 판단 시스템 오류: {e}")
    
    async def _analyze_buy_decision(self, trading_stock, available_funds: float = None):
        """매수 판단 분석 (완성된 3분봉만 사용)

        Args:
            trading_stock: 거래 대상 주식
            available_funds: 사용 가능한 자금 (미리 계산된 값)
        """
        try:
            stock_code = trading_stock.stock_code
            stock_name = trading_stock.stock_name

            self.logger.debug(f"🔍 매수 판단 시작: {stock_code}({stock_name})")

            # 추가 안전 검증: 현재 보유 중인 종목인지 다시 한번 확인
            positioned_stocks = self.trading_manager.get_stocks_by_state(StockState.POSITIONED)
            if any(pos_stock.stock_code == stock_code for pos_stock in positioned_stocks):
                self.logger.info(f"⚠️ 보유 중인 종목 매수 신호 무시: {stock_code}({stock_name})")
                return

            # 🆕 최대 동시 보유 종목 수 제한
            buy_pending_stocks = self.trading_manager.get_stocks_by_state(StockState.BUY_PENDING)
            current_position_count = len(positioned_stocks) + len(buy_pending_stocks)
            from config.orb_strategy_config import DEFAULT_ORB_CONFIG
            max_positions = DEFAULT_ORB_CONFIG.max_positions
            if current_position_count >= max_positions:
                self.logger.info(f"⚠️ 최대 보유 종목 수({max_positions})에 도달, 매수 스킵: {stock_code}")
                return

            # 🆕 25분 매수 쿨다운 확인
            if trading_stock.is_buy_cooldown_active():
                remaining_minutes = trading_stock.get_remaining_cooldown_minutes()
                self.logger.debug(f"⚠️ {stock_code}: 매수 쿨다운 활성화 (남은 시간: {remaining_minutes}분)")
                return

            # 🆕 당일 재진입 제한 확인 (1회만 허용)
            if not trading_stock.can_buy_today():
                self.logger.debug(f"⚠️ {stock_code}: 당일 재진입 제한 (매수 {trading_stock.daily_buy_count}회 완료)")
                return

            # 🆕 [지영] 일일 손실 한도 체크 — 한도 도달 시 신규 매수 차단
            if self.decision_engine.virtual_trading.is_daily_loss_limit_reached():
                pnl_summary = self.decision_engine.virtual_trading.get_daily_pnl_summary()
                self.logger.warning(
                    f"🚨 {stock_code}: 일일 손실 한도 도달로 매수 차단 "
                    f"(누적손실: {pnl_summary['realized_loss']:,.0f}원, "
                    f"한도: {pnl_summary['loss_limit']:,.0f}원)"
                )
                return

            # 🆕 타이밍 체크는 _update_intraday_data()에서 이미 수행됨 (3분봉 완성 + 10초 후)
            # 여기서는 종목별 매수 판단만 수행

            # 분봉 데이터 가져오기
            combined_data = self.intraday_manager.get_combined_chart_data(stock_code)
            if combined_data is None:
                self.logger.debug(f"❌ {stock_code} 1분봉 데이터 없음 (None)")
                return
            if len(combined_data) < 15:
                self.logger.debug(f"❌ {stock_code} 1분봉 데이터 부족: {len(combined_data)}개 (최소 15개 필요) - 실시간 데이터 대기 중")
                # 실시간 환경에서는 메모리에 있는 데이터만 사용 (캐시 파일 체크 불필요)
                return
            
            # 🆕 3분봉 변환 시 완성된 봉만 자동 필터링됨 (TimeFrameConverter에서 처리)
            from core.timeframe_converter import TimeFrameConverter

            data_3min = TimeFrameConverter.convert_to_3min_data(combined_data)

            if data_3min is None or len(data_3min) < 5:
                self.logger.debug(f"❌ {stock_code} 3분봉 데이터 부족: {len(data_3min) if data_3min is not None else 0}개 (최소 5개 필요)")
                return

            # 🆕 3분봉 품질 검증: 경고만 표시 (시뮬레이션과 동일하게 차단하지 않음)
            if not data_3min.empty and len(data_3min) >= 2:
                data_3min_copy = data_3min.copy()
                data_3min_copy['datetime'] = pd.to_datetime(data_3min_copy['datetime'])

                # 1. 시간 간격 검증 (3분봉 연속성)
                time_diffs = data_3min_copy['datetime'].diff().dt.total_seconds().fillna(0) / 60
                invalid_gaps = time_diffs[1:][(time_diffs[1:] != 3.0) & (time_diffs[1:] != 0.0)]

                if len(invalid_gaps) > 0:
                    gap_indices = invalid_gaps.index.tolist()
                    gap_times = [data_3min_copy.loc[idx, 'datetime'].strftime('%H:%M') for idx in gap_indices]
                    self.logger.warning(f"⚠️ {stock_code} 3분봉 불연속 구간 발견: {', '.join(gap_times)} (간격: {invalid_gaps.values} 분) - 경고만, 진행")

                # 2. 🆕 각 3분봉의 구성 분봉 개수 검증 (HTS 분봉 누락 감지)
                if 'candle_count' in data_3min_copy.columns:
                    incomplete_candles = data_3min_copy[data_3min_copy['candle_count'] < 3]
                    if not incomplete_candles.empty:
                        for idx, row in incomplete_candles.iterrows():
                            candle_time = row['datetime'].strftime('%H:%M')
                            count = int(row['candle_count'])
                            self.logger.warning(f"⚠️ {stock_code} 3분봉 내부 누락: {candle_time} ({count}/3개 분봉) - HTS 분봉 누락 가능성")

                # 3. 09:00 시작 확인
                first_time = data_3min_copy['datetime'].iloc[0]
                if first_time.hour == 9 and first_time.minute not in [0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30]:
                    self.logger.warning(f"⚠️ {stock_code} 첫 3분봉이 정규 시간이 아님: {first_time.strftime('%H:%M')} (09:00, 09:03, 09:06... 중 하나여야 함) - 경고만, 진행")

            # 🆕 이미 매수 진행 중이거나 포지션 보유 중이면 매수 판단 건너뛰기
            if trading_stock.state in (StockState.BUY_PENDING, StockState.POSITIONED, StockState.SELL_CANDIDATE, StockState.SELL_PENDING):
                return  # 매수 불가 상태 - 중복 매수 방지

            # 매매 판단 엔진으로 매수 신호 확인 (완성된 3분봉 데이터 사용)
            buy_signal, buy_reason, buy_info = await self.decision_engine.analyze_buy_decision(trading_stock, data_3min)

            self.logger.debug(f"💡 {stock_code} 매수 판단 결과: signal={buy_signal}, reason='{buy_reason}'")
            if buy_signal and buy_info:
                self.logger.debug(f"💰 {stock_code} 매수 정보: 가격={buy_info['buy_price']:,.0f}원, 수량={buy_info['quantity']:,}주, 투자금={buy_info['max_buy_amount']:,.0f}원")


            if buy_signal and buy_info.get('quantity', 0) > 0:
                self.logger.info(f"🚀 {stock_code}({stock_name}) 매수 신호 발생: {buy_reason}")

                # 🆕 매수 전 자금 확인 (전달받은 available_funds 활용)
                if available_funds is not None:
                    # 전달받은 가용 자금 기준으로 종목당 최대 투자 금액 계산 (10%)
                    fund_status = self.fund_manager.get_status()
                    max_buy_amount = min(available_funds, fund_status['total_funds'] * 0.1)
                else:
                    # 기존 방식 (fallback)
                    max_buy_amount = self.fund_manager.get_max_buy_amount(stock_code)

                required_amount = buy_info['buy_price'] * buy_info['quantity']

                if required_amount > max_buy_amount:
                    self.logger.warning(f"⚠️ {stock_code} 자금 부족: 필요={required_amount:,.0f}원, 가용={max_buy_amount:,.0f}원")
                    # 가용 자금에 맞게 수량 조정
                    if max_buy_amount > 0:
                        adjusted_quantity = int(max_buy_amount / buy_info['buy_price'])
                        if adjusted_quantity > 0:
                            buy_info['quantity'] = adjusted_quantity
                            self.logger.info(f"💰 {stock_code} 수량 조정: {adjusted_quantity}주 (투자금: {adjusted_quantity * buy_info['buy_price']:,.0f}원)")
                        else:
                            self.logger.warning(f"❌ {stock_code} 매수 포기: 최소 1주도 매수 불가")
                            return
                    else:
                        self.logger.warning(f"❌ {stock_code} 매수 포기: 가용 자금 없음")
                        return

                # 🆕 매수 전 종목 상태 확인
                current_stock = self.trading_manager.get_trading_stock(stock_code)
                if current_stock:
                    self.logger.debug(f"🔍 매수 전 상태 확인: {stock_code} 현재상태={current_stock.state.value}")
                
                # 가상거래 모드 확인
                use_virtual_trading = self.config.risk_management.use_virtual_trading if hasattr(self.config.risk_management, 'use_virtual_trading') else False

                if use_virtual_trading:
                    # [가상매매 모드]
                    try:
                        # 가상 매수 실행 및 DB 기록
                        buy_record_id = await self.decision_engine.execute_virtual_buy(
                            trading_stock,
                            data_3min,
                            buy_reason,
                            buy_price=buy_info['buy_price'],
                            quantity=buy_info['quantity']
                        )

                        if buy_record_id:
                            # 상태를 POSITIONED로 반영하여 이후 매도 판단 루프에 포함
                            self.trading_manager._change_stock_state(stock_code, StockState.POSITIONED, "가상 매수 체결")

                            # 가상 포지션 정보 설정 (매도 시 매수 기록 추적용)
                            trading_stock.set_virtual_buy_info(
                                buy_record_id, buy_info['buy_price'], buy_info['quantity']
                            )
                            if not trading_stock.position:
                                # Position 객체가 없으면 여기서 확인 (execute_virtual_buy에서 생성했어야 함)
                                self.logger.warning(f"⚠️ {stock_code} 가상 매수 후 포지션 객체 없음 (버그 가능성)")

                            # 🆕 가상 잔고를 fund_manager에 동기화 (가상/실거래 통합 관리)
                            virtual_balance = self.decision_engine.virtual_trading.get_virtual_balance()
                            self.fund_manager.update_total_funds(virtual_balance)

                            # 🆕 [지영] 트레일링 스탑용 ORB 메타데이터 설정 + buy_record_id 저장
                            signal_meta = buy_info.get('signal_metadata', {})
                            trading_stock.metadata = {
                                'buy_record_id': buy_record_id,  # 매도 시 매수 기록 추적용
                                'entry_price': buy_info['buy_price'],
                                'stop_loss': signal_meta.get('stop_loss', 0) or getattr(trading_stock, 'stop_loss_price', 0) or 0,
                                'take_profit': signal_meta.get('take_profit', 0) or getattr(trading_stock, 'profit_target_price', 0) or 0,
                                'orb_high': signal_meta.get('orb_high', 0),
                                'orb_low': signal_meta.get('orb_low', 0),
                            }

                            self.logger.info(f"🔥 가상 매수 완료: {stock_code}({stock_name}) "
                                           f"{buy_info['quantity']}주 @{buy_info['buy_price']:,.0f}원 - {buy_reason}")

                            # 🆕 당일 매수 횟수 증가 (재진입 제한용)
                            trading_stock.increment_daily_buy_count()
                        else:
                            self.logger.warning(f"⚠️ 가상 매수 실패: {stock_code}({stock_name})")
                    except Exception as e:
                        self.logger.error(f"❌ 가상 매수 처리 오류: {e}")
                        import traceback
                        self.logger.error(traceback.format_exc())
                else:
                    # [실제 매매 모드]
                    try:
                        buy_success = await self.decision_engine.execute_real_buy(
                            trading_stock,
                            buy_reason,
                            buy_info['buy_price'],
                            buy_info['quantity']
                        )
                        # 상태는 execute_buy_order 내부에서 자동 변경 (SELECTED -> BUY_PENDING -> POSITIONED)

                        if buy_success:
                            # 실거래 모드에서도 트레일링 스탑용 ORB 메타데이터 설정
                            signal_meta = buy_info.get('signal_metadata', {})
                            trading_stock.metadata = {
                                'entry_price': buy_info['buy_price'],
                                'stop_loss': signal_meta.get('stop_loss', 0) or getattr(trading_stock, 'stop_loss_price', 0) or 0,
                                'take_profit': signal_meta.get('take_profit', 0) or getattr(trading_stock, 'profit_target_price', 0) or 0,
                                'orb_high': signal_meta.get('orb_high', 0),
                                'orb_low': signal_meta.get('orb_low', 0),
                            }

                            self.logger.info(f"🔥 실제 매수 주문 완료: {stock_code}({stock_name}) - {buy_reason}")

                            # 당일 매수 횟수 증가 (재진입 제한용)
                            trading_stock.increment_daily_buy_count()
                        else:
                            self.logger.warning(f"⚠️ 실제 매수 주문 실패: {stock_code}({stock_name})")
                    except Exception as e:
                        self.logger.error(f"❌ 실제 매수 처리 오류: {e}")
                    
            else:
                #self.logger.debug(f"📊 {stock_code}({stock_name}) 매수 신호 없음")
                pass
                        
        except Exception as e:
            self.logger.error(f"❌ {trading_stock.stock_code} 매수 판단 오류: {e}")
            import traceback
            self.logger.error(f"상세 오류 정보: {traceback.format_exc()}")
    
    async def _analyze_sell_decision(self, trading_stock):
        """매도 판단 분석 (간단한 손절/익절 로직)"""
        try:
            stock_code = trading_stock.stock_code
            stock_name = trading_stock.stock_name

            current_price_info = self.intraday_manager.get_cached_current_price(stock_code)
            if current_price_info is None:
                self.logger.debug(f"📊 매도 판단 스킵: {stock_code}({stock_name}) 현재가 없음 (캐시 미갱신 또는 미수집)")
                return

            current_price = current_price_info.get('current_price') or 0.0
            if current_price <= 0:
                self.logger.debug(f"📊 매도 판단 스킵: {stock_code}({stock_name}) 현재가 유효하지 않음 ({current_price})")
                return

            data = pd.DataFrame({'close': [float(current_price)]})

            sell_signal, sell_reason = await self.decision_engine.analyze_sell_decision(trading_stock, data)
            
            if sell_signal:
                # 🆕 매도 전 종목 상태 확인
                self.logger.debug(f"🔍 매도 전 상태 확인: {stock_code} 현재상태={trading_stock.state.value}")
                if trading_stock.position:
                    self.logger.debug(f"🔍 포지션 정보: {trading_stock.position.quantity}주 @{trading_stock.position.avg_price:,.0f}원")
                
                # 매도 후보로 변경
                success = self.trading_manager.move_to_sell_candidate(stock_code, sell_reason)
                if success:
                    # 가상거래 모드 확인
                    use_virtual_trading = self.config.risk_management.use_virtual_trading if hasattr(self.config.risk_management, 'use_virtual_trading') else False

                    if use_virtual_trading:
                        # [가상매매 모드]
                        try:
                            sell_success = await self.decision_engine.execute_virtual_sell(trading_stock, None, sell_reason)
                            if sell_success:
                                # 🆕 가상 잔고를 fund_manager에 동기화 (가상/실거래 통합 관리)
                                virtual_balance = self.decision_engine.virtual_trading.get_virtual_balance()
                                self.fund_manager.update_total_funds(virtual_balance)

                                self.logger.info(f"📉 가상 매도 완료: {stock_code}({stock_name}) - {sell_reason}")

                                # 🆕 [지영] 일일 손실 한도 도달 시 텔레그램 긴급 알림
                                if self.decision_engine.virtual_trading.is_daily_loss_limit_reached():
                                    pnl = self.decision_engine.virtual_trading.get_daily_pnl_summary()
                                    await self.telegram.notify_urgent_signal(
                                        f"🚨 일일 손실 한도 도달!\n"
                                        f"누적 손실: {pnl['realized_loss']:,.0f}원\n"
                                        f"누적 수익: {pnl['realized_profit']:,.0f}원\n"
                                        f"순 손익: {pnl['net_pnl']:,.0f}원\n"
                                        f"→ 당일 신규 매수 중단됨"
                                    )

                                # 상태를 COMPLETED로 변경하여 거래 종료
                                self.trading_manager._change_stock_state(stock_code, StockState.COMPLETED, "가상 매도 체결")
                            else:
                                self.logger.warning(f"⚠️ 가상 매도 실패: {stock_code}({stock_name})")
                        except Exception as e:
                            self.logger.error(f"❌ 가상 매도 처리 오류: {e}")
                            import traceback
                            self.logger.error(traceback.format_exc())
                    else:
                        # [실제 매매 모드]
                        try:
                            sell_success = await self.decision_engine.execute_real_sell(trading_stock, sell_reason)
                            if sell_success:
                                self.logger.info(f"📉 실제 매도 주문 완료: {stock_code}({stock_name}) - {sell_reason}")
                            else:
                                self.logger.warning(f"⚠️ 실제 매도 주문 실패: {stock_code}({stock_name}) - {sell_reason}")
                                # 손절 매도 실패 시 긴급 알림
                                if "손절" in sell_reason or "stop_loss" in sell_reason.lower():
                                    await self.telegram.notify_urgent_signal(
                                        f"🚨 긴급: 손절 매도 실패!\n"
                                        f"종목: {stock_code}({stock_name})\n"
                                        f"사유: {sell_reason}\n"
                                        f"→ VI 발동 또는 네트워크 장애 의심\n"
                                        f"→ 수동 매도 확인 필요"
                                    )
                        except Exception as e:
                            self.logger.error(f"❌ 실제 매도 처리 오류: {e}")
                            if "손절" in sell_reason or "stop_loss" in sell_reason.lower():
                                await self.telegram.notify_urgent_signal(
                                    f"🚨 긴급: 손절 매도 예외 발생!\n"
                                    f"종목: {stock_code}({stock_name})\n"
                                    f"사유: {sell_reason}\n"
                                    f"오류: {e}\n"
                                    f"→ 수동 매도 확인 필요"
                                )
        except Exception as e:
            self.logger.error(f"❌ {trading_stock.stock_code} 매도 판단 오류: {e}")
    
    # 가상매매 포지션 분석 함수 비활성화 (실제 매매 모드)
    # async def _analyze_virtual_positions_for_sell(self):
    #     """DB에서 미체결 가상 포지션을 조회하여 매도 판단 (signal_replay 방식)"""
    #     pass
    
    async def _telegram_task(self):
        """텔레그램 태스크"""
        try:
            self.logger.info("📱 텔레그램 태스크 시작")
            
            # 텔레그램 봇 폴링과 주기적 상태 알림을 병렬 실행
            telegram_tasks = [
                self.telegram.start_telegram_bot(),
                self.telegram.periodic_status_task()
            ]
            
            await asyncio.gather(*telegram_tasks, return_exceptions=True)
            
        except Exception as e:
            self.logger.error(f"❌ 텔레그램 태스크 오류: {e}")
    
    async def _system_monitoring_task(self):
        """시스템 모니터링 태스크"""
        try:
            self.logger.info("🔥 DEBUG: _system_monitoring_task 시작됨")  # 디버깅용
            self.logger.info("📡 시스템 모니터링 태스크 시작")

            last_api_refresh = now_kst()
            last_market_check = now_kst()
            last_intraday_update = now_kst()  # 🆕 장중 데이터 업데이트 시간
            last_premarket_selection_date = None  # 🆕 장전 후보 종목 선정 날짜
            # last_chart_generation = datetime(2000, 1, 1, tzinfo=KST)  # 🆕 장 마감 후 차트 생성 시간 (주석처리)
            # chart_generation_count = 0  # 🆕 차트 생성 횟수 카운터 (주석처리)
            # last_chart_reset_date = now_kst().date()  # 🆕 차트 카운터 리셋 기준 날짜 (주석처리)

            self.logger.info("🔥 DEBUG: while 루프 진입 시도")  # 디버깅용
            while self.is_running:
                #self.logger.info(f"🔥 DEBUG: while 루프 실행 중 - is_running: {self.is_running}")  # 디버깅용
                current_time = now_kst()

                # API 24시간마다 재초기화
                if (current_time - last_api_refresh).total_seconds() >= 86400:  # 24시간
                    await self._refresh_api()
                    last_api_refresh = current_time

                # 🆕 장전 후보 종목 선정 (08:55~08:59 구간, 하루 1회)
                current_date = current_time.date()
                is_premarket_time = (current_time.hour == 8 and 55 <= current_time.minute <= 59)
                if is_premarket_time and last_premarket_selection_date != current_date:
                    self.logger.info("🔍 장전 후보 종목 선정 시작 (08:55~08:59)")
                    await self._select_premarket_candidates()
                    last_premarket_selection_date = current_date
                    self.logger.info("✅ 장전 후보 종목 선정 완료")

                # 🆕 장중 종목 실시간 데이터 업데이트 (매분 13~45초 사이에 실행)
                # 13~45초 구간에서는 이전 실행으로부터 최소 13초 이상 간격만 유지
                if 13 <= current_time.second <= 45 and (current_time - last_intraday_update).total_seconds() >= 13:
                    # 장중이거나 장마감 후 10분 구간에서는 실행 (데이터 저장 위해) - 동적 시간 적용
                    market_hours = MarketHours.get_market_hours('KRX', current_time)
                    market_close = market_hours['market_close']
                    close_hour = market_close.hour
                    close_minute = market_close.minute

                    is_after_close_window = (current_time.hour == close_hour and
                                            close_minute <= current_time.minute <= close_minute + 10)

                    if is_market_open() or is_after_close_window:
                        await self._update_intraday_data()
                        last_intraday_update = current_time
                
                # 🆕 과거 후보 종목 데이터 추가 수집 (15:45 실행)
                if current_time.hour == 15 and current_time.minute >= 45:
                    current_date = current_time.date()
                    if self._last_extended_collection_date != current_date:
                        self.logger.info("🕒 15:45 정기 작업: 과거 후보 종목 데이터 추가 수집 시작")
                        await self.extended_collector.collect_data()
                        self._last_extended_collection_date = current_date
                        self.logger.info("✅ 15:45 정기 작업 완료")

                # 장마감 청산 로직 제거: 15:00 시장가 매도로 대체됨
                
                # 🆕 차트 생성 카운터 매일 리셋 (주석처리)
                # current_date = current_time.date()
                # if current_date != last_chart_reset_date:
                #     chart_generation_count = 0  # 새로운 날이면 카운터 리셋
                #     last_chart_reset_date = current_date
                #     self.logger.info(f"📅 새로운 날 - 차트 생성 카운터 리셋 ({current_date})")

                # 🆕 장 마감 후 차트 생성 (16:00~24:00 시간대에 실행) - 주석처리
                # current_hour = current_time.hour
                # is_chart_time = (16 <= current_hour <= 23) and current_time.weekday() < 5  # 평일 16~24시
                # if is_chart_time and chart_generation_count < 2:  # 16~24시 시간대에만, 최대 2번
                #     if (current_time - last_chart_generation).total_seconds() >= 1 * 60:  # 1분 간격으로 체크
                #         #self.logger.info(f"🔥 DEBUG: 차트 생성 실행 시작 ({chart_generation_count + 1}/2)")  # 디버깅용
                #         await self._generate_post_market_charts()
                #         #self.logger.info(f"🔥 DEBUG: 차트 생성 실행 완료 ({chart_generation_count + 1}/2)")  # 디버깅용
                #         last_chart_generation = current_time
                #         chart_generation_count += 1
                #
                #         if chart_generation_count >= 1:
                #             self.logger.info("✅ 장 마감 후 차트 생성 완료 (1회 실행 완료)")
                
                # 시스템 모니터링 루프 대기 (5초 주기)
                await asyncio.sleep(5)  
                
                # 30분마다 시스템 상태 로깅
                if (current_time - last_market_check).total_seconds() >= 30 * 60:  # 30분
                    await self._log_system_status()
                    last_market_check = current_time
                
        except Exception as e:
            self.logger.error(f"❌ 시스템 모니터링 태스크 오류: {e}")
            await self.telegram.notify_critical(
                "시스템 모니터링 태스크 중단",
                f"오류: {e}",
                "장마감 청산 모니터링 불가 — 확인 필요"
            )

    async def _liquidate_all_positions_end_of_day(self):
        """장 마감 직전 보유 포지션 전량 시장가 일괄 청산"""
        try:
            from core.models import StockState
            positioned_stocks = self.trading_manager.get_stocks_by_state(StockState.POSITIONED)
            
            # 실제 매매 모드: 실제 포지션만 처리
            if not positioned_stocks:
                self.logger.info("📦 장마감 일괄청산: 보유 포지션 없음")
                return
                
            self.logger.info(f"🛎️ 장마감 일괄청산 시작: {len(positioned_stocks)}종목")
            
            # 실제 포지션 매도
            for trading_stock in positioned_stocks:
                try:
                    if not trading_stock.position or trading_stock.position.quantity <= 0:
                        continue
                    stock_code = trading_stock.stock_code
                    quantity = int(trading_stock.position.quantity)
                    # 가격 산정: 가능한 경우 최신 분봉 종가, 없으면 현재가 조회
                    sell_price = 0.0
                    combined_data = self.intraday_manager.get_combined_chart_data(stock_code)
                    if combined_data is not None and len(combined_data) > 0:
                        sell_price = float(combined_data['close'].iloc[-1])
                    else:
                        price_obj = self.api_manager.get_current_price(stock_code)
                        if price_obj:
                            sell_price = float(price_obj.current_price)
                    sell_price = self._round_to_tick(sell_price)
                    # 상태 전환 후 시장가 매도 주문 실행
                    moved = self.trading_manager.move_to_sell_candidate(stock_code, "장마감 일괄청산")
                    if moved:
                        await self.trading_manager.execute_sell_order(
                            stock_code, quantity, sell_price, "장마감 일괄청산", market=True
                        )
                        self.logger.info(
                            f"🧹 장마감 청산 주문: {stock_code} {quantity}주 시장가 @{sell_price:,.0f}원"
                        )
                except Exception as se:
                    self.logger.error(f"❌ 장마감 청산 개별 처리 오류({trading_stock.stock_code}): {se}")
            
            # 가상 포지션 매도 처리 제거 (실제 매매 모드)
            
            self.logger.info("✅ 장마감 일괄청산 요청 완료")
            
        except Exception as e:
            self.logger.error(f"❌ 장마감 일괄청산 오류: {e}")
            await self.telegram.notify_critical(
                "장마감 일괄청산 오류",
                f"오류: {e}",
                "보유 포지션 수동 청산 필요"
            )
    
    async def _execute_end_of_day_liquidation(self):
        """장마감 시간 모든 보유 종목 시장가 일괄매도 (동적 시간 적용). 가상거래 시 가상 매도만 수행."""
        try:
            from core.models import StockState

            current_time = now_kst()
            market_hours = MarketHours.get_market_hours('KRX', current_time)
            eod_hour = market_hours['eod_liquidation_hour']
            eod_minute = market_hours['eod_liquidation_minute']

            positioned_stocks = self.trading_manager.get_stocks_by_state(StockState.POSITIONED)

            if not positioned_stocks:
                self.logger.info(f"📦 {eod_hour}:{eod_minute:02d} 시장가 매도: 보유 포지션 없음")
                return

            use_virtual = (
                self.config.risk_management.use_virtual_trading
                if hasattr(self.config.risk_management, 'use_virtual_trading')
                else False
            )

            if use_virtual:
                self.logger.info(f"🚨 {eod_hour}:{eod_minute:02d} 가상 일괄청산 시작: {len(positioned_stocks)}종목")
                failed_virtual = []
                for trading_stock in positioned_stocks:
                    try:
                        if not trading_stock.position or trading_stock.position.quantity <= 0:
                            continue
                        stock_code = trading_stock.stock_code
                        stock_name = trading_stock.stock_name
                        reason = f"{eod_hour}:{eod_minute:02d} 시장가 일괄청산"
                        moved = self.trading_manager.move_to_sell_candidate(stock_code, reason)
                        if moved:
                            # 최대 3회 재시도
                            ok = False
                            for attempt in range(3):
                                ok = await self.decision_engine.execute_virtual_sell(trading_stock, None, reason)
                                if ok:
                                    break
                                self.logger.warning(f"⚠️ 가상 일괄청산 실패 ({attempt+1}/3): {stock_code}({stock_name})")
                                if attempt < 2:
                                    await asyncio.sleep(1)
                            if ok:
                                virtual_balance = self.decision_engine.virtual_trading.get_virtual_balance()
                                self.fund_manager.update_total_funds(virtual_balance)
                                self.logger.info(f"📉 가상 일괄청산: {stock_code}({stock_name}) - {reason}")
                                self.trading_manager._change_stock_state(stock_code, StockState.COMPLETED, "가상 일괄청산 체결")
                            else:
                                failed_virtual.append(f"{stock_code}({stock_name})")
                    except Exception as se:
                        self.logger.error(f"❌ {eod_hour}:{eod_minute:02d} 가상 청산 개별 오류({trading_stock.stock_code}): {se}")
                        failed_virtual.append(f"{trading_stock.stock_code}")

                if failed_virtual:
                    await self.telegram.notify_critical(
                        "가상 일괄청산 일부 실패",
                        f"실패 종목: {', '.join(failed_virtual)}",
                        "미청산 포지션 확인 필요"
                    )
                self.logger.info(f"✅ {eod_hour}:{eod_minute:02d} 가상 일괄청산 완료")

                # 🆕 [민수] 장 마감 후 메모리 정리
                self.order_manager.cleanup_completed_orders()
                return

            self.logger.info(f"🚨 {eod_hour}:{eod_minute:02d} 시장가 일괄매도 시작: {len(positioned_stocks)}종목")

            failed_stocks = []
            for trading_stock in positioned_stocks:
                try:
                    if not trading_stock.position or trading_stock.position.quantity <= 0:
                        continue

                    stock_code = trading_stock.stock_code
                    stock_name = trading_stock.stock_name
                    quantity = int(trading_stock.position.quantity)
                    current_price = 0.0
                    reason = f"{eod_hour}:{eod_minute:02d} 시장가 일괄매도"

                    moved = self.trading_manager.move_to_sell_candidate(stock_code, reason)
                    if moved:
                        # 최대 3회 재시도
                        sell_success = False
                        for attempt in range(3):
                            try:
                                sell_success = await self.trading_manager.execute_sell_order(
                                    stock_code, quantity, current_price, reason, market=True
                                )
                                if sell_success:
                                    self.logger.info(f"🚨 {reason}: {stock_code}({stock_name}) {quantity}주 시장가 주문")
                                    break
                                else:
                                    self.logger.warning(f"⚠️ {reason} 실패 ({attempt+1}/3): {stock_code}({stock_name})")
                                    if attempt < 2:
                                        await asyncio.sleep(2)
                            except Exception as retry_e:
                                self.logger.error(f"❌ {reason} 오류 ({attempt+1}/3): {stock_code} - {retry_e}")
                                if attempt < 2:
                                    await asyncio.sleep(2)

                        if not sell_success:
                            failed_stocks.append(f"{stock_code}({stock_name}) {quantity}주")

                except Exception as se:
                    self.logger.error(f"❌ {eod_hour}:{eod_minute:02d} 시장가 매도 개별 처리 오류({trading_stock.stock_code}): {se}")
                    failed_stocks.append(f"{trading_stock.stock_code}")

            if failed_stocks:
                await self.telegram.notify_critical(
                    "장마감 청산 일부 실패",
                    f"실패 종목: {', '.join(failed_stocks)}",
                    "수동 매도 확인 필요"
                )

            self.logger.info(f"✅ {eod_hour}:{eod_minute:02d} 시장가 일괄매도 요청 완료")

        except Exception as e:
            self.logger.error(f"❌ 장마감 시장가 매도 오류: {e}")
            await self.telegram.notify_critical(
                "장마감 일괄 청산 실패",
                f"오류: {e}",
                "보유 포지션 수동 청산 필요"
            )
    
    async def _failsafe_sweep_unclosed_buys(self):
        """P1-1: 15:15 Failsafe Sweep — DB에서 미청산 BUY 레코드 직접 조회 후 강제 청산.
        메모리 포지션과 무관하게, DB 기준으로 미청산 건을 모두 찾아 매도 처리한다."""
        try:
            if not self.db_manager:
                self.logger.warning("⚠️ Failsafe sweep: DB 매니저 없음, 스킵")
                return

            from utils.korean_time import now_kst
            today = now_kst().strftime('%Y-%m-%d')

            conn = self.db_manager._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT b.id, b.stock_code, b.stock_name, b.price, b.quantity
                    FROM virtual_trading_records b
                    WHERE b.action = 'BUY' AND b.is_test = true
                      AND b.timestamp::date = %s::date
                      AND NOT EXISTS (
                          SELECT 1 FROM virtual_trading_records s
                          WHERE s.action = 'SELL' AND s.buy_record_id = b.id
                      )
                ''', (today,))
                unclosed = cursor.fetchall()
            finally:
                self.db_manager._put_connection(conn)

            if not unclosed:
                self.logger.info("✅ 15:15 Failsafe sweep: 미청산 BUY 레코드 없음")
                return

            self.logger.warning(f"🚨 15:15 Failsafe sweep: 미청산 BUY {len(unclosed)}건 발견, 강제 청산 시작")

            for buy_id, stock_code, stock_name, buy_price, qty in unclosed:
                try:
                    # 현재가 조회
                    sell_price = 0.0
                    current_price_info = self.intraday_manager.get_cached_current_price(stock_code)
                    if current_price_info:
                        sell_price = float(current_price_info.get('current_price') or 0)
                    if sell_price <= 0:
                        price_obj = self.api_manager.get_current_price(stock_code)
                        if price_obj:
                            sell_price = float(price_obj.current_price)
                    if sell_price <= 0:
                        sell_price = float(buy_price)  # 최후 fallback: 매수가

                    # DB에 직접 SELL 기록
                    success = self.decision_engine.virtual_trading.execute_virtual_sell(
                        stock_code=stock_code,
                        stock_name=stock_name or f"Stock_{stock_code}",
                        price=sell_price,
                        quantity=qty,
                        strategy="ORB",
                        reason="15:15 failsafe sweep (DB 미청산 강제 청산)",
                        buy_record_id=buy_id
                    )

                    if success:
                        profit = (sell_price - float(buy_price)) * qty
                        self.decision_engine.virtual_trading.record_trade_pnl(profit)
                        self.logger.info(
                            f"🧹 Failsafe 청산: {stock_code}({stock_name}) "
                            f"{qty}주 @{sell_price:,.0f}원 (buy_id={buy_id}, 손익={profit:+,.0f}원)"
                        )

                        # 메모리 포지션도 정리 (있는 경우)
                        from core.models import StockState
                        trading_stock = self.trading_manager.trading_stocks.get(stock_code)
                        if trading_stock and trading_stock.state != StockState.COMPLETED:
                            self.trading_manager._change_stock_state(
                                stock_code, StockState.COMPLETED, "15:15 failsafe sweep"
                            )
                    else:
                        self.logger.error(f"❌ Failsafe 청산 실패: {stock_code} buy_id={buy_id}")

                except Exception as e:
                    self.logger.error(f"❌ Failsafe 청산 개별 오류 ({stock_code}, buy_id={buy_id}): {e}")

            self.logger.info(f"✅ 15:15 Failsafe sweep 완료")

            # 텔레그램 알림
            await self.telegram.notify_system_status(
                f"🧹 15:15 Failsafe sweep: {len(unclosed)}건 미청산 BUY 강제 청산 완료"
            )

        except Exception as e:
            self.logger.error(f"❌ 15:15 Failsafe sweep 오류: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    async def _log_system_status(self):
        """시스템 상태 로깅"""
        try:
            current_time = now_kst()
            market_status = get_market_status()
            
            # 주문 요약
            order_summary = self.order_manager.get_order_summary()
            
            # 데이터 수집 상태
            candidate_stocks = self.data_collector.get_candidate_stocks()
            data_counts = {stock.code: len(stock.ohlcv_data) for stock in candidate_stocks}
            
            self.logger.info(
                f"📊 시스템 상태 [{current_time.strftime('%H:%M:%S')}]\n"
                f"  - 시장 상태: {market_status}\n"
                f"  - 미체결 주문: {order_summary['pending_count']}건\n"
                f"  - 완료 주문: {order_summary['completed_count']}건\n"
                f"  - 데이터 수집: {data_counts}"
            )
            
        except Exception as e:
            self.logger.error(f"❌ 시스템 상태 로깅 오류: {e}")
    
    async def _refresh_api(self):
        """API 재초기화"""
        try:
            self.logger.info("🔄 API 24시간 주기 재초기화 시작")
            
            # API 매니저 재초기화
            if not self.api_manager.initialize():
                self.logger.error("❌ API 재초기화 실패")
                await self.telegram.notify_critical(
                    "API 재초기화 실패",
                    "API 인증 토큰 갱신에 실패했습니다.\n주문/시세 조회 불가 상태.",
                    "key.ini 확인 및 수동 재시작"
                )
                return False
                
            self.logger.info("✅ API 재초기화 완료")
            await self.telegram.notify_system_status("API 재초기화 완료")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ API 재초기화 오류: {e}")
            await self.telegram.notify_critical(
                "API 재초기화 오류",
                f"오류: {e}",
                "네트워크 상태 확인 및 수동 재시작"
            )
            return False
    
    async def _restore_todays_candidates(self):
        """DB에서 오늘 날짜의 후보 종목 복원"""
        try:
            # 오늘 날짜
            today = now_kst().strftime('%Y-%m-%d')

            conn = self.db_manager._get_connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute('''
                        SELECT DISTINCT stock_code, stock_name, score, reasons
                        FROM candidate_stocks
                        WHERE DATE(selection_date) = %s
                        ORDER BY score DESC
                    ''', (today,))

                    rows = cursor.fetchall()
            finally:
                self.db_manager._put_connection(conn)
            
            if not rows:
                self.logger.info(f"📊 오늘({today}) 후보 종목 없음")
                return
            
            self.logger.info(f"🔄 오늘({today}) 후보 종목 {len(rows)}개 복원 시작")
            
            restored_count = 0
            for row in rows:
                stock_code = row[0]
                stock_name = row[1] or f"Stock_{stock_code}"
                score = row[2] or 0.0
                reason = row[3] or "DB 복원"
                
                # 전날 종가 조회
                prev_close = 0.0
                try:
                    daily_data = self.api_manager.get_ohlcv_data(stock_code, "D", 7)
                    if daily_data is not None and len(daily_data) >= 2:
                        if hasattr(daily_data, 'iloc'):
                            daily_data = daily_data.sort_values('stck_bsop_date')
                            last_date = daily_data.iloc[-1]['stck_bsop_date']
                            if isinstance(last_date, str):
                                from datetime import datetime
                                last_date = datetime.strptime(last_date, '%Y%m%d').date()
                            elif hasattr(last_date, 'date'):
                                last_date = last_date.date()
                            
                            if last_date == now_kst().date() and len(daily_data) >= 2:
                                prev_close = float(daily_data.iloc[-2]['stck_clpr'])
                            else:
                                prev_close = float(daily_data.iloc[-1]['stck_clpr'])
                except Exception as e:
                    self.logger.debug(f"⚠️ {stock_code} 전날 종가 조회 실패: {e}")
                
                # 거래 상태 관리자에 추가
                success = await self.trading_manager.add_selected_stock(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    selection_reason=f"DB복원: {reason} (점수: {score})",
                    prev_close=prev_close
                )
                
                if success:
                    restored_count += 1
            
            self.logger.info(f"✅ 오늘 후보 종목 {restored_count}/{len(rows)}개 복원 완료")
            
        except Exception as e:
            self.logger.error(f"❌ 오늘 후보 종목 복원 실패: {e}")

    async def _restore_open_virtual_positions(self):
        """미청산 가상매매 포지션을 POSITIONED 상태로 복원"""
        try:
            conn = self.db_manager._get_connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute('''
                        SELECT b.id, b.stock_code, b.stock_name, b.price, b.quantity, b.timestamp
                        FROM virtual_trading_records b
                        WHERE b.action='BUY' AND b.is_test=true
                          AND NOT EXISTS (
                            SELECT 1 FROM virtual_trading_records s 
                            WHERE s.action='SELL' AND s.buy_record_id=b.id
                          )
                        ORDER BY b.timestamp
                    ''')
                    rows = cursor.fetchall()
            finally:
                self.db_manager._put_connection(conn)

            if not rows:
                self.logger.info("📊 미청산 가상 포지션 없음")
                return

            self.logger.info(f"🔄 미청산 가상 포지션 {len(rows)}건 복원 시작")

            restored = 0
            for row in rows:
                buy_id, code, name, buy_price, qty, ts = row
                try:
                    # 종목을 SELECTED로 추가 후 POSITIONED로 전환
                    success = await self.trading_manager.add_selected_stock(
                        stock_code=code,
                        stock_name=name or f"Stock_{code}",
                        selection_reason=f"미청산 복원: {qty}주 @{buy_price:,.0f}원",
                        prev_close=float(buy_price)
                    )
                    if success:
                        # 가상 포지션 설정
                        trading_stock = self.trading_manager.trading_stocks.get(code)
                        if trading_stock:
                            from core.models import Position, StockState
                            trading_stock.position = Position(
                                stock_code=code,
                                quantity=qty,
                                avg_price=float(buy_price),
                            )
                            # buy_record_id는 매도 시 필요 — metadata에 저장
                            if not hasattr(trading_stock, 'metadata') or trading_stock.metadata is None:
                                trading_stock.metadata = {}
                            trading_stock.metadata['buy_record_id'] = buy_id
                            trading_stock.state = StockState.POSITIONED
                            restored += 1
                except Exception as e:
                    self.logger.warning(f"⚠️ {code}({name}) 포지션 복원 실패: {e}")

            self.logger.info(f"✅ 미청산 가상 포지션 {restored}/{len(rows)}건 복원 완료 (15:00 장마감 청산 대상)")

        except Exception as e:
            self.logger.error(f"❌ 미청산 가상 포지션 복원 실패: {e}")

    async def _select_premarket_candidates(self):
        """장전 후보 종목 선정 (08:55~08:59)"""
        try:
            self.logger.info("🔍 Universe 로드 중...")

            # 1. Universe 로드
            from scripts.update_weekly_universe import load_latest_universe
            universe = load_latest_universe()

            if universe is None or universe.empty:
                self.logger.error("❌ Universe 로드 실패 - Universe 파일이 없거나 비어있습니다")
                await self.telegram.notify_warning(
                    "Universe 로드 실패",
                    "Universe 파일이 없거나 비어있습니다.\n당일 후보 종목 선정 불가 — 수동 확인 필요"
                )
                return

            self.logger.info(f"✅ Universe 로드 완료: {len(universe)}개 종목")

            # 2. ORB 전략의 select_daily_candidates() 호출
            from strategies.orb_strategy import ORBStrategy
            from config.orb_strategy_config import DEFAULT_ORB_CONFIG

            orb_strategy = ORBStrategy(config=DEFAULT_ORB_CONFIG, logger=self.logger, pg_manager=self.pg_manager)

            self.logger.info("🔍 후보 종목 스크리닝 시작...")
            candidates = await orb_strategy.select_daily_candidates(
                universe=universe,
                api_client=self.api_manager
            )

            if not candidates:
                self.logger.info("📊 갭 상승 조건을 만족하는 후보 종목이 없습니다")
                return

            self.logger.info(f"✅ 후보 종목 {len(candidates)}개 선정 완료")

            # 3. 후보 종목을 거래 관리자에 추가
            added_count = 0
            for candidate in candidates:
                try:
                    stock_code = candidate.code
                    stock_name = candidate.name
                    score = candidate.score
                    reason = candidate.reason

                    # 전날 종가 조회
                    prev_close = 0.0
                    try:
                        daily_data = self.api_manager.get_ohlcv_data(stock_code, "D", 7)
                        if daily_data is not None and len(daily_data) >= 2:
                            if hasattr(daily_data, 'iloc'):
                                daily_data = daily_data.sort_values('stck_bsop_date')
                                last_date = daily_data.iloc[-1]['stck_bsop_date']
                                if isinstance(last_date, str):
                                    from datetime import datetime
                                    last_date = datetime.strptime(last_date, '%Y%m%d').date()
                                elif hasattr(last_date, 'date'):
                                    last_date = last_date.date()

                                if last_date == now_kst().date() and len(daily_data) >= 2:
                                    prev_close = float(daily_data.iloc[-2]['stck_clpr'])
                                else:
                                    prev_close = float(daily_data.iloc[-1]['stck_clpr'])
                    except Exception as e:
                        self.logger.debug(f"⚠️ {stock_code} 전날 종가 조회 실패: {e}")

                    # 거래 상태 관리자에 추가
                    success = await self.trading_manager.add_selected_stock(
                        stock_code=stock_code,
                        stock_name=stock_name,
                        selection_reason=f"장전선정: {reason} (점수: {score:.2f})",
                        prev_close=prev_close
                    )

                    if success:
                        added_count += 1
                        self.logger.info(f"  ✓ {stock_code}({stock_name}): {reason} (점수: {score:.2f})")

                except Exception as e:
                    self.logger.error(f"❌ 후보 종목 추가 실패 {candidate.code}: {e}")
                    continue

            # 4. DB에 저장
            if candidates:
                try:
                    self.db_manager.save_candidate_stocks(candidates)
                    self.logger.info(f"💾 후보 종목 DB 저장 완료: {len(candidates)}개")
                except Exception as db_err:
                    self.logger.error(f"❌ 후보 종목 DB 저장 오류: {db_err}")

            self.logger.info(f"✅ 장전 후보 종목 선정 완료: {added_count}/{len(candidates)}개 추가")

            # 5. 텔레그램 알림
            if added_count > 0:
                message = f"📊 장전 후보 종목 {added_count}개 선정 완료\n\n"
                for candidate in candidates[:10]:  # 상위 10개만
                    message += f"• {candidate.code}({candidate.name}): {candidate.reason}\n"
                if len(candidates) > 10:
                    message += f"\n... 외 {len(candidates) - 10}개"

                await self.telegram.notify_system_status(message)

        except Exception as e:
            self.logger.error(f"❌ 장전 후보 종목 선정 실패: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            await self.telegram.notify_warning(
                "장전 후보 종목 선정 실패",
                f"오류: {e}\n당일 매매 대상 종목 없음 — Universe 파일 확인"
            )

    async def _calculate_orb_ranges(self):
        """ORB 레인지 계산 (09:10 이후 실행)"""
        try:
            from core.models import StockState

            self.logger.info("📊 ORB 레인지 계산 시작 (09:00~09:10 구간)")

            # 선정된 종목 조회
            selected_stocks = self.trading_manager.get_stocks_by_state(StockState.SELECTED)

            if not selected_stocks:
                self.logger.warning("⚠️ ORB 레인지 계산: 선정된 종목 없음")
                return

            self.logger.info(f"🎯 ORB 레인지 계산 대상: {len(selected_stocks)}개 종목")

            # ORB 전략이 있는지 확인
            if not hasattr(self.decision_engine, 'strategy') or self.decision_engine.strategy is None:
                self.logger.error("❌ ORB 레인지 계산 실패: 전략 객체 없음")
                return

            strategy = self.decision_engine.strategy

            # 각 종목에 대해 ORB 레인지 계산
            success_count = 0
            failed_count = 0

            for trading_stock in selected_stocks:
                try:
                    stock_code = trading_stock.stock_code
                    stock_name = trading_stock.stock_name

                    # 09:00~09:10 구간 1분봉 데이터 조회
                    today = now_kst().strftime('%Y%m%d')
                    from api.kis_chart_api import get_full_trading_day_data_async

                    minute_1_data = await get_full_trading_day_data_async(
                        stock_code=stock_code,
                        target_date=today,
                        selected_time="091000",  # 09:10까지
                        start_time="090000"      # 09:00부터
                    )

                    if minute_1_data is None or (hasattr(minute_1_data, 'empty') and minute_1_data.empty):
                        self.logger.warning(f"⚠️ {stock_code}({stock_name}): 09:00~09:10 1분봉 데이터 없음")
                        failed_count += 1
                        continue

                    # ORB 레인지 계산
                    result = await strategy.calculate_orb_range(stock_code, minute_1_data)

                    if result:
                        success_count += 1
                    else:
                        failed_count += 1

                except Exception as e:
                    self.logger.error(f"❌ {trading_stock.stock_code} ORB 레인지 계산 오류: {e}")
                    failed_count += 1

            self.logger.info(
                f"✅ ORB 레인지 계산 완료: 성공 {success_count}개, 실패 {failed_count}개"
            )

        except Exception as e:
            self.logger.error(f"❌ ORB 레인지 계산 태스크 오류: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            await self.telegram.notify_warning(
                "ORB 레인지 계산 실패",
                f"오류: {e}\n당일 ORB 기반 매매 불가능 — 데이터 확인 필요"
            )

    async def _update_intraday_data(self):
        """장중 종목 실시간 데이터 업데이트 + 매수 판단 실행 (완성된 분봉만 수집)"""
        try:
            from utils.korean_time import now_kst
            from core.data_reconfirmation import reconfirm_intraday_data
            current_time = now_kst()

            # 🆕 완성된 봉만 수집하는 것을 로깅
            #self.logger.debug(f"🔄 실시간 데이터 업데이트 시작: {current_time.strftime('%H:%M:%S')} "
            #                f"(모든 관리 종목 - 재거래 대응)")

            # 모든 관리 종목의 실시간 데이터 업데이트 (재거래를 위해 COMPLETED, FAILED 상태도 포함)
            await self.intraday_manager.batch_update_realtime_data()

            # 🆕 데이터 수집 후 1초 대기 (데이터 안정화)
            await asyncio.sleep(1)

            # 🆕 최근 3분 데이터 재확인 (volume=0 but price changed 감지 및 재조회)
            updated_stocks = await reconfirm_intraday_data(
                self.intraday_manager,
                minutes_back=3
            )
            if updated_stocks:
                self.logger.info(f"🔄 데이터 재확인 완료: {len(updated_stocks)}개 종목 업데이트됨")

            # 🆕 3분봉 완성 + 10초 후 시점 체크
            # 3분봉 완성 시점: 매 3분마다 (09:00, 09:03, 09:06, ...)
            # 매수 판단 허용 시점: 각 3분봉 완성 후 10~59초 사이의 첫 번째 호출만
            minute_in_3min_cycle = current_time.minute % 3
            current_second = current_time.second

            # 3분봉 사이클의 첫 번째 분(0, 3, 6, 9...)이고 10초 이후일 때만 매수 판단
            is_3min_candle_completed = (minute_in_3min_cycle == 0 and current_second >= 10)

            if not is_3min_candle_completed:
                self.logger.debug(f"⏱️ 3분봉 미완성 또는 10초 미경과: {current_time.strftime('%H:%M:%S')} - 매수 판단 건너뜀")
                return

            # 🆕 데이터 업데이트 직후 매수 판단 실행 (3분봉 완성 + 10초 후)
            # 매수 중단 시간 전이고 SELECTED/COMPLETED 상태 종목만 매수 판단 - 동적 시간 적용
            should_stop_buy = MarketHours.should_stop_buying('KRX', current_time)

            if not should_stop_buy:
                # 가용 자금 계산 (가상거래 모드 분기)
                use_virtual = self.config.risk_management.use_virtual_trading if hasattr(self.config.risk_management, 'use_virtual_trading') else False

                if use_virtual:
                    # 가상거래 모드: 가상 잔고 사용
                    virtual_manager = self.decision_engine.virtual_trading
                    virtual_balance = virtual_manager.get_virtual_balance()
                    self.fund_manager.update_total_funds(virtual_balance)
                else:
                    # 실거래 모드: 실시간 잔고 조회
                    balance_info = self.api_manager.get_account_balance()
                    if balance_info:
                        self.fund_manager.update_total_funds(float(balance_info.account_balance))

                fund_status = self.fund_manager.get_status()
                available_funds = fund_status['available_funds']

                # SELECTED + COMPLETED 상태 종목 가져오기
                selected_stocks = self.trading_manager.get_stocks_by_state(StockState.SELECTED)
                completed_stocks = self.trading_manager.get_stocks_by_state(StockState.COMPLETED)
                buy_candidates = selected_stocks + completed_stocks

                if buy_candidates:
                    # 🆕 거래량 배수 기준 우선순위 정렬 (높은 순)
                    def _get_volume_ratio(ts):
                        """종목의 거래량 배수 추출 (ORB 데이터에서)"""
                        try:
                            if hasattr(ts, 'orb_data') and ts.orb_data and 'volume_ratio' in ts.orb_data:
                                return ts.orb_data['volume_ratio']
                        except Exception:
                            pass
                        return 0.0

                    buy_candidates.sort(key=_get_volume_ratio, reverse=True)

                    self.logger.info(f"🎯 3분봉 완성 후 매수 판단 실행: {current_time.strftime('%H:%M:%S')} - {len(buy_candidates)}개 종목 (거래량 우선순위)")

                    for trading_stock in buy_candidates:
                        await self._analyze_buy_decision(trading_stock, available_funds)

                        # 🆕 매수 후 가용 자금 갱신 (순차적 자금 관리)
                        fund_status = self.fund_manager.get_status()
                        available_funds = fund_status['available_funds']

        except Exception as e:
            self.logger.error(f"❌ 장중 종목 실시간 데이터 업데이트 오류: {e}")
            await self.telegram.notify_warning(
                "장중 데이터 업데이트 오류",
                f"오류: {e}\n매수 판단에 영향 가능 — 다음 주기에 자동 재시도"
            )
    
    async def _generate_post_market_charts(self):
        """장 마감 후 선정 종목 차트 생성 (15:30 이후)"""
        try:
            # 차트 생성기 지연 초기화 (파일 없음 - 주석처리)
            # if self.chart_generator is None:
            #     self.chart_generator = PostMarketChartGenerator()
            #     if not self.chart_generator.initialize():
            #         self.logger.error("❌ 차트 생성기 초기화 실패")
            #         return

            # PostMarketChartGenerator의 통합 메서드 호출 (파일 없음 - 주석처리)
            # results = await self.chart_generator.generate_post_market_charts_for_intraday_stocks(
            #     intraday_manager=self.intraday_manager,
            #     telegram_integration=self.telegram
            # )
            results = {'success': False}  # 임시
            
            # 결과 로깅
            if results.get('success', False):
                success_count = results.get('success_count', 0)
                total_stocks = results.get('total_stocks', 0)
                self.logger.info(f"🎯 장 마감 후 차트 생성 완료: {success_count}/{total_stocks}개 성공")
            else:
                message = results.get('message', '알 수 없는 오류')
                self.logger.info(f"ℹ️ 장 마감 후 차트 생성: {message}")
            
        except Exception as e:
            self.logger.error(f"❌ 장 마감 후 차트 생성 오류: {e}")
            await self.telegram.notify_error("Post Market Chart Generation", e)

    async def emergency_sync_positions(self):
        """긴급 포지션 동기화 - 매수가 기준 3%/2% 고정 비율"""
        try:
            self.logger.info("🔧 긴급 포지션 동기화 시작")

            # 실제 잔고 조회
            loop = asyncio.get_event_loop()
            balance = await loop.run_in_executor(
                None,
                self.api_manager.get_account_balance
            )
            if not balance or not balance.positions:
                self.logger.info("📊 보유 종목 없음")
                return

            held_stocks = {p['stock_code']: p for p in balance.positions if p.get('quantity', 0) > 0}

            self.logger.info(f"📊 실제 계좌 보유 종목: {list(held_stocks.keys())}")
            self.logger.info(f"📊 시스템 관리 종목: {list(self.trading_manager.trading_stocks.keys())}")

            # 시스템에서 누락된 포지션 찾기
            missing_positions = []
            unmanaged_stocks = []
            for code, balance_stock in held_stocks.items():
                if code in self.trading_manager.trading_stocks:
                    ts = self.trading_manager.trading_stocks[code]
                    if ts.state != StockState.POSITIONED:
                        missing_positions.append((code, balance_stock, ts))
                        self.logger.info(f"🔍 {code}: 보유중이지만 상태가 {ts.state.value} (복구 필요)")
                    else:
                        self.logger.info(f"✅ {code}: 정상 동기화됨 (상태: {ts.state.value})")
                else:
                    unmanaged_stocks.append((code, balance_stock))
                    self.logger.warning(f"⚠️ {code}: 보유중이지만 시스템에서 관리되지 않음")

            # 미관리 보유 종목을 시스템에 추가
            if unmanaged_stocks:
                self.logger.warning(f"🚨 미관리 보유 종목 발견: {[code for code, _ in unmanaged_stocks]}")
                for code, balance_stock in unmanaged_stocks:
                    try:
                        stock_name = balance_stock.get('stock_name', f'Stock_{code}')
                        quantity = balance_stock['quantity']
                        avg_price = balance_stock['avg_price']

                        self.logger.info(f"🔄 미관리 종목 시스템 추가: {code}({stock_name}) {quantity}주 @{avg_price:,.0f}")

                        # 거래 상태 관리자에 추가 (POSITIONED 상태로 즉시 설정)
                        success = await self.trading_manager.add_selected_stock(
                            stock_code=code,
                            stock_name=stock_name,
                            selection_reason=f"보유종목 자동복구 ({quantity}주 @{avg_price:,.0f})",
                            prev_close=avg_price  # 전날종가는 매수가로 대체
                        )

                        if success:
                            # 추가된 종목을 즉시 POSITIONED 상태로 설정
                            ts = self.trading_manager.get_trading_stock(code)
                            if ts:
                                ts.set_position(quantity, avg_price)
                                ts.clear_current_order()
                                ts.is_buying = False
                                ts.order_processed = True

                                self.trading_manager._change_stock_state(code, StockState.POSITIONED,
                                    f"미관리종목 복구: {quantity}주 @{avg_price:,.0f}원")

                                self.logger.info(f"✅ {code} 미관리 종목 복구 완료")

                                # missing_positions에도 추가하여 통합 처리
                                missing_positions.append((code, balance_stock, ts))

                    except Exception as e:
                        self.logger.error(f"❌ {code} 미관리 종목 복구 실패: {e}")

            if not missing_positions:
                self.logger.info("✅ 모든 포지션이 정상 동기화됨")
                return

            # 누락된 포지션들 복구
            for code, balance_stock, ts in missing_positions:
                # 포지션 복원
                quantity = balance_stock['quantity']
                avg_price = balance_stock['avg_price']
                ts.set_position(quantity, avg_price)
                ts.clear_current_order()
                ts.is_buying = False
                ts.order_processed = True

                buy_price = avg_price
                target_price = None
                stop_loss = None
                orb_source = "고정비율"

                # 🆕 [지영] PostgreSQL orb_ranges에서 ORB 기반 손익절가 복원 시도
                if self.pg_manager:
                    try:
                        from utils.korean_time import now_kst
                        today = now_kst().strftime('%Y-%m-%d')
                        orb_data = self.pg_manager.execute_query(
                            "SELECT orb_high, orb_low, range_size FROM orb_ranges "
                            "WHERE stock_code = %s AND trade_date = %s LIMIT 1",
                            (code, today)
                        )
                        if orb_data and len(orb_data) > 0:
                            orb_high = float(orb_data[0][0])
                            orb_low = float(orb_data[0][1])
                            range_size = float(orb_data[0][2])
                            from config.orb_strategy_config import DEFAULT_ORB_CONFIG
                            multiplier = DEFAULT_ORB_CONFIG.take_profit_multiplier
                            target_price = orb_high + (range_size * multiplier)
                            stop_loss = orb_low
                            orb_source = "ORB 레인지"
                            self.logger.info(f"✅ {code} ORB 데이터 복원: 고가={orb_high:,.0f}, 저가={orb_low:,.0f}, 레인지={range_size:,.0f}")
                    except Exception as orb_err:
                        self.logger.warning(f"⚠️ {code} ORB 데이터 조회 실패: {orb_err}")

                # ORB 데이터 없으면 기존 고정 비율 사용
                if target_price is None:
                    take_profit_ratio = self.config.risk_management.take_profit_ratio
                    stop_loss_ratio = self.config.risk_management.stop_loss_ratio
                    target_price = buy_price * (1 + take_profit_ratio)
                    stop_loss = buy_price * (1 - stop_loss_ratio)

                # 손익절가 설정
                ts.profit_target_price = target_price
                ts.stop_loss_price = stop_loss

                # 🆕 트레일링 스탑용 메타데이터 설정 (포지션 복구 시에도)
                ts.metadata = {
                    'entry_price': buy_price,
                    'stop_loss': stop_loss,
                    'take_profit': target_price,
                    'orb_high': orb_high if orb_source == "ORB 레인지" else 0,
                    'orb_low': orb_low if orb_source == "ORB 레인지" else 0,
                }

                # 상태 변경
                self.trading_manager._change_stock_state(code, StockState.POSITIONED,
                    f"잔고복구({orb_source}): {quantity}주 @{buy_price:,.0f}원")

                self.logger.info(f"✅ {code} 복구완료({orb_source}): 매수 {buy_price:,.0f} → "
                               f"목표 {target_price:,.0f} / 손절 {stop_loss:,.0f}")

            self.logger.info(f"🔧 총 {len(missing_positions)}개 종목 긴급 복구 완료")

            # 텔레그램 알림
            if missing_positions:
                message = f"🔧 포지션 동기화 복구\n"
                message += f"복구된 종목: {len(missing_positions)}개\n"
                for code, balance_stock, _ in missing_positions[:3]:  # 최대 3개만
                    quantity = balance_stock['quantity']
                    avg_price = balance_stock['avg_price']
                    message += f"- {code}: {quantity}주 @{avg_price:,.0f}원\n"
                await self.telegram.notify_system_status(message)

        except Exception as e:
            self.logger.error(f"❌ 긴급 포지션 동기화 실패: {e}")
            await self.telegram.notify_critical(
                "긴급 포지션 동기화 실패",
                f"오류: {e}\n실계좌 포지션과 내부 상태 불일치 가능",
                "HTS에서 보유 종목 수동 확인"
            )

    async def shutdown(self):
        """시스템 종료"""
        try:
            self.logger.info("🛑 시스템 종료 시작")
            
            # 데이터 수집 중단
            self.data_collector.stop_collection()
            
            # 주문 모니터링 중단
            self.order_manager.stop_monitoring()
            
            # 텔레그램 통합 종료
            await self.telegram.shutdown()
            
            # API 매니저 종료
            self.api_manager.shutdown()
            
            # PID 파일 삭제
            if self.pid_file.exists():
                self.pid_file.unlink()
                self.logger.info("PID 파일 삭제 완료")
            
            self.logger.info("✅ 시스템 종료 완료")
            
        except Exception as e:
            self.logger.error(f"❌ 시스템 종료 중 오류: {e}")


async def main():
    """메인 함수"""
    try:
        bot = DayTradingBot()
    except Exception as e:
        print(f"❌ DayTradingBot 생성 실패: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # 시스템 초기화
    if not await bot.initialize():
        print("❌ 시스템 초기화 실패로 종료")
        sys.exit(1)
    
    # 일일 거래 사이클 실행
    await bot.run_daily_cycle()


if __name__ == "__main__":
    try:
        # 로그 디렉토리 생성
        Path("logs").mkdir(exist_ok=True)
        
        # 메인 실행
        asyncio.run(main())
        
    except KeyboardInterrupt:
        print("\n사용자에 의해 중단되었습니다.")
    except Exception as e:
        print(f"시스템 오류: {e}")
        sys.exit(1)