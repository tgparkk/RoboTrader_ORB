"""
매매 판단 엔진 - 전략 패턴 적용

설정 파일에서 지정한 전략을 사용하여 매매 판단을 수행합니다.
"""
from typing import Tuple, Dict, Optional
from strategies.strategy_factory import StrategyFactory
from strategies.trading_strategy import TradingStrategy
from utils.logger import setup_logger


class TradingDecisionEngine:
    """매매 판단 엔진 (전략 패턴 적용)"""

    def __init__(
        self,
        db_manager=None,
        telegram_integration=None,
        trading_manager=None,
        api_manager=None,
        intraday_manager=None,
        strategy_name: str = None,
        strategy_config: Dict = None
    ):
        self.logger = setup_logger(__name__)
        self.db_manager = db_manager
        self.telegram = telegram_integration
        self.trading_manager = trading_manager
        self.api_manager = api_manager
        self.intraday_manager = intraday_manager

        # 가상매매 관리자
        from core.virtual_trading_manager import VirtualTradingManager
        self.virtual_trading = VirtualTradingManager(
            db_manager=db_manager,
            api_manager=api_manager
        )

        # 전략 로드 (전략이 등록되어 있으면)
        self.strategy: Optional[TradingStrategy] = None
        if strategy_name:
            self.strategy = StrategyFactory.create_trading_strategy(
                name=strategy_name,
                config=strategy_config,
                logger=self.logger
            )
            if self.strategy:
                self.logger.info(f"🧠 매매 판단 엔진 초기화 완료 (전략: {strategy_name})")
            else:
                self.logger.warning(f"⚠️ 전략 '{strategy_name}' 로드 실패. 기본 손익비 로직만 사용.")
        else:
            self.logger.info("🧠 매매 판단 엔진 초기화 완료 (전략 없음, 기본 손익비만 사용)")

    async def analyze_buy_decision(self, trading_stock, data) -> Tuple[bool, str, dict]:
        """
        매수 판단

        전략이 설정되어 있으면 전략 사용, 없으면 False 반환

        Returns:
            (매수여부, 사유, {'buy_price': 가격, 'quantity': 수량, 'max_buy_amount': 최대 투자금})
        """
        buy_info = {'buy_price': 0, 'quantity': 0, 'max_buy_amount': 0}

        # 전략이 없으면 매수하지 않음
        if self.strategy is None:
            return False, "전략 미설정", buy_info

        try:
            # 현재가 추출
            current_price = float(data['close'].iloc[-1]) if data is not None and len(data) > 0 else 0

            if current_price <= 0:
                return False, "유효하지 않은 가격", buy_info

            # 전략에 매수 신호 요청
            buy_signal = await self.strategy.generate_buy_signal(
                code=trading_stock.stock_code,
                minute_data=data,
                current_price=current_price,
                trading_stock=trading_stock
            )

            if buy_signal is None:
                return False, "매수 신호 없음", buy_info

            # 매수 정보 구성
            buy_info['buy_price'] = current_price

            # 수량 계산 (가상거래 모드 여부에 따라 다른 로직 적용)
            max_buy_amount = self._calculate_max_buy_amount()
            quantity = self._calculate_quantity(current_price, max_buy_amount)

            buy_info['quantity'] = quantity
            buy_info['max_buy_amount'] = max_buy_amount

            return True, buy_signal.reason, buy_info

        except Exception as e:
            self.logger.error(f"매수 판단 실패 ({trading_stock.stock_code}): {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False, f"분석 오류: {e}", buy_info

    def _calculate_max_buy_amount(self) -> float:
        """
        최대 매수 가능 금액 계산

        가상거래 모드: 가상 잔고에서 종목당 투자 금액 반환
        실거래 모드: 실제 계좌 잔고에서 계산

        Returns:
            float: 최대 매수 가능 금액
        """
        try:
            # 가상거래 관리자에서 종목당 투자 금액 가져오기
            max_amount = self.virtual_trading.virtual_investment_amount
            virtual_balance = self.virtual_trading.virtual_balance

            # 가상 잔고보다 투자 금액이 크면 잔고만큼만 사용
            return min(max_amount, virtual_balance)

        except Exception as e:
            self.logger.error(f"최대 매수 금액 계산 오류: {e}")
            return 0

    def _calculate_quantity(self, price: float, max_buy_amount: float) -> int:
        """
        매수 수량 계산

        Args:
            price: 주가
            max_buy_amount: 최대 매수 가능 금액

        Returns:
            int: 매수 수량
        """
        try:
            if price <= 0 or max_buy_amount <= 0:
                return 0

            # 최대 수량 계산
            quantity = int(max_buy_amount / price)

            return max(0, quantity)

        except Exception as e:
            self.logger.error(f"수량 계산 오류: {e}")
            return 0

    async def analyze_sell_decision(self, trading_stock, data) -> Tuple[bool, str]:
        """
        매도 판단

        1. 기본 손절/익절 체크 (우선순위)
        2. 전략의 매도 신호 체크 (있으면)

        Returns:
            (매도여부, 사유)
        """
        if data is None or len(data) < 1:
            return False, "데이터 부족"

        current_price = float(data['close'].iloc[-1])

        # 1. 기본 손절/익절 체크 (우선순위)
        if trading_stock.stop_loss_price and current_price <= trading_stock.stop_loss_price:
            return True, f"손절 ({trading_stock.stop_loss_price:,.0f}원)"

        if trading_stock.profit_target_price and current_price >= trading_stock.profit_target_price:
            return True, f"익절 ({trading_stock.profit_target_price:,.0f}원)"

        # 2. 전략의 매도 신호 체크
        if self.strategy:
            try:
                sell_signal = await self.strategy.generate_sell_signal(
                    code=trading_stock.stock_code,
                    position=trading_stock,
                    minute_data=data,
                    current_price=current_price
                )

                if sell_signal:
                    return True, sell_signal.reason

            except Exception as e:
                self.logger.error(f"전략 매도 판단 실패 ({trading_stock.stock_code}): {e}")

        return False, ""

    async def execute_virtual_buy(self, trading_stock, data, reason: str, buy_price: float = None, quantity: int = None):
        """
        가상 매수 실행 (실제 주문 제외, 모든 로직 실행)

        Args:
            trading_stock: 거래 종목 정보
            data: 3분봉 데이터
            reason: 매수 사유
            buy_price: 매수 가격 (None이면 현재가 사용)
            quantity: 매수 수량 (None이면 자동 계산)

        Returns:
            int: 매수 기록 ID (성공시) 또는 None (실패시)
        """
        try:
            # 가격 결정
            if buy_price is None:
                current_price = float(data['close'].iloc[-1]) if data is not None and len(data) > 0 else 0
            else:
                current_price = buy_price

            if current_price <= 0:
                self.logger.error(f"❌ 가상 매수 실패: 유효하지 않은 가격 ({current_price})")
                return None

            # 수량 결정
            if quantity is None:
                quantity = self.virtual_trading.get_max_quantity(current_price)

            if quantity <= 0:
                self.logger.error(f"❌ 가상 매수 실패: 유효하지 않은 수량 ({quantity})")
                return None

            # 가상 매수 실행 및 DB 기록
            buy_id = self.virtual_trading.execute_virtual_buy(
                stock_code=trading_stock.stock_code,
                stock_name=trading_stock.stock_name,
                price=current_price,
                quantity=quantity,
                strategy="ORB",
                reason=reason
            )

            if buy_id:
                # 포지션 정보 업데이트 (손절/익절가 계산)
                if trading_stock.position:
                    trading_stock.position.avg_price = current_price
                    trading_stock.position.quantity = quantity

                    # 손절가/익절가 계산 (전략에서 가져온 값 또는 기본 비율 사용)
                    if hasattr(trading_stock, 'stop_loss_price') and trading_stock.stop_loss_price:
                        pass  # 이미 설정됨
                    else:
                        # 기본 손절가 (2.5% 손실)
                        trading_stock.stop_loss_price = current_price * 0.975

                    if hasattr(trading_stock, 'profit_target_price') and trading_stock.profit_target_price:
                        pass  # 이미 설정됨
                    else:
                        # 기본 익절가 (3.5% 수익)
                        trading_stock.profit_target_price = current_price * 1.035

                self.logger.info(f"✅ 가상 매수 성공: {trading_stock.stock_code}({trading_stock.stock_name}) "
                               f"{quantity}주 @{current_price:,.0f}원 - {reason}")
                return buy_id
            else:
                self.logger.warning(f"⚠️ 가상 매수 DB 저장 실패: {trading_stock.stock_code}")
                return None

        except Exception as e:
            self.logger.error(f"❌ 가상 매수 실행 오류 ({trading_stock.stock_code}): {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None

    async def execute_virtual_sell(self, trading_stock, data, reason: str):
        """
        가상 매도 실행 (실제 주문 제외, 모든 로직 실행)

        Args:
            trading_stock: 거래 종목 정보
            data: 데이터 (사용 안 함, 호환성 유지용)
            reason: 매도 사유

        Returns:
            bool: 성공 여부
        """
        try:
            # 현재가 조회
            current_price_info = self.intraday_manager.get_cached_current_price(trading_stock.stock_code)
            if not current_price_info:
                self.logger.error(f"❌ 가상 매도 실패: 현재가 조회 실패 ({trading_stock.stock_code})")
                return False

            current_price = float(current_price_info.current_price)

            # DB에서 가상 매수 기록 조회
            if not self.db_manager:
                self.logger.error(f"❌ 가상 매도 실패: DB 매니저 없음")
                return False

            # 직접 SQL 쿼리로 미체결 포지션 조회
            import sqlite3
            with sqlite3.connect(self.db_manager.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, price, quantity
                    FROM virtual_trading_records
                    WHERE stock_code = ? AND action = 'BUY'
                    AND id NOT IN (
                        SELECT buy_record_id FROM virtual_trading_records
                        WHERE action = 'SELL' AND buy_record_id IS NOT NULL
                    )
                    ORDER BY timestamp ASC
                    LIMIT 1
                ''', (trading_stock.stock_code,))

                buy_record = cursor.fetchone()

            if not buy_record:
                self.logger.warning(f"⚠️ 가상 매도 실패: 매수 기록 없음 ({trading_stock.stock_code})")
                return False

            buy_id, buy_price, quantity = buy_record

            # 가상 매도 실행 및 DB 기록
            success = self.virtual_trading.execute_virtual_sell(
                stock_code=trading_stock.stock_code,
                stock_name=trading_stock.stock_name,
                price=current_price,
                quantity=quantity,
                strategy="ORB",
                reason=reason,
                buy_record_id=buy_id
            )

            if success:
                profit = (current_price - buy_price) * quantity
                profit_rate = ((current_price - buy_price) / buy_price) * 100

                self.logger.info(f"✅ 가상 매도 성공: {trading_stock.stock_code}({trading_stock.stock_name}) "
                               f"{quantity}주 @{current_price:,.0f}원 "
                               f"(수익: {profit:,.0f}원, {profit_rate:+.2f}%) - {reason}")
                return True
            else:
                self.logger.warning(f"⚠️ 가상 매도 DB 저장 실패: {trading_stock.stock_code}")
                return False

        except Exception as e:
            self.logger.error(f"❌ 가상 매도 실행 오류 ({trading_stock.stock_code}): {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
