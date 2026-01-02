"""
후보 종목 선정 전략 인터페이스
"""

from abc import ABC, abstractmethod
from typing import Optional, Any
from dataclasses import dataclass


@dataclass
class CandidateStock:
    """후보 종목 정보"""
    code: str
    name: str
    market: str
    score: float  # 선정 점수
    reason: str   # 선정 이유
    prev_close: float = 0.0  # 전날 종가 (일봉 기준)


class CandidateSelectionStrategy(ABC):
    """
    후보 종목 선정 전략 베이스 클래스

    모든 후보 종목 선정 전략은 이 클래스를 상속받아 구현합니다.
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
    async def evaluate_stock(
        self,
        code: str,
        name: str,
        market: str,
        price_data: Any,
        daily_data: Any,
        weekly_data: Any
    ) -> Optional[CandidateStock]:
        """
        종목을 평가하여 후보로 적합한지 판단

        Args:
            code: 종목 코드
            name: 종목명
            market: 시장 구분
            price_data: 현재가 데이터
            daily_data: 일봉 데이터
            weekly_data: 주봉 데이터

        Returns:
            후보 종목으로 선정되면 CandidateStock 객체, 아니면 None
        """
        pass

    def get_strategy_name(self) -> str:
        """전략 이름 반환"""
        return self.__class__.__name__
