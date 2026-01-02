"""
매매 판단 전략 인터페이스
"""

from abc import ABC, abstractmethod
from typing import Optional, Any
from dataclasses import dataclass


@dataclass
class BuySignal:
    """매수 신호"""
    code: str
    reason: str
    confidence: float = 1.0  # 신호 강도 (0~1)
    metadata: dict = None  # 추가 정보 (패턴명, 가격 등)

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class SellSignal:
    """매도 신호"""
    code: str
    reason: str
    signal_type: str  # 'stop_loss', 'take_profit', 'pattern', 'time_based'
    confidence: float = 1.0
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class TradingStrategy(ABC):
    """
    매매 판단 전략 베이스 클래스

    모든 매매 전략은 이 클래스를 상속받아 구현합니다.
    """

    def __init__(self, config: Any = None, logger: Any = None):
        """
        Args:
            config: 전략별 설정 객체
            logger: 로거 객체
        """
        self.config = config
        self.logger = logger

    @abstractmethod
    async def generate_buy_signal(
        self,
        code: str,
        minute_data: Any,
        current_price: float,
        **kwargs
    ) -> Optional[BuySignal]:
        """
        매수 신호 생성

        Args:
            code: 종목 코드
            minute_data: 분봉 데이터
            current_price: 현재가
            **kwargs: 전략별 추가 파라미터

        Returns:
            매수 신호 또는 None
        """
        pass

    @abstractmethod
    async def generate_sell_signal(
        self,
        code: str,
        position: Any,
        minute_data: Any,
        current_price: float,
        **kwargs
    ) -> Optional[SellSignal]:
        """
        매도 신호 생성

        Args:
            code: 종목 코드
            position: 포지션 정보
            minute_data: 분봉 데이터
            current_price: 현재가
            **kwargs: 전략별 추가 파라미터

        Returns:
            매도 신호 또는 None
        """
        pass

    def get_strategy_name(self) -> str:
        """전략 이름 반환"""
        return self.__class__.__name__

    def validate_signal(self, signal: Any) -> bool:
        """
        신호 유효성 검증 (선택적 구현)

        Args:
            signal: BuySignal 또는 SellSignal

        Returns:
            유효 여부
        """
        return True
