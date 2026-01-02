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
            (매수여부, 사유, {'buy_price': 가격, 'quantity': 수량})
        """
        buy_info = {'buy_price': 0, 'quantity': 0, 'max_buy_amount': 0}

        # 전략이 없으면 매수하지 않음
        if self.strategy is None:
            return False, "전략 미설정", buy_info

        try:
            # 현재가 추출
            current_price = float(data['close'].iloc[-1]) if data is not None and len(data) > 0 else 0

            # 전략에 매수 신호 요청
            buy_signal = await self.strategy.generate_buy_signal(
                code=trading_stock.code,
                minute_data=data,
                current_price=current_price,
                trading_stock=trading_stock
            )

            if buy_signal is None:
                return False, "매수 신호 없음", buy_info

            # 매수 정보 구성
            buy_info['buy_price'] = current_price
            # 수량 계산은 기존 로직 사용 (리스크 관리와 연동)
            # TODO: 수량 계산 로직 구현 필요

            return True, buy_signal.reason, buy_info

        except Exception as e:
            self.logger.error(f"매수 판단 실패 ({trading_stock.code}): {e}")
            return False, f"분석 오류: {e}", buy_info

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
                    code=trading_stock.code,
                    position=trading_stock,
                    minute_data=data,
                    current_price=current_price
                )

                if sell_signal:
                    return True, sell_signal.reason

            except Exception as e:
                self.logger.error(f"전략 매도 판단 실패 ({trading_stock.code}): {e}")

        return False, ""
