"""
전략 팩토리

설정 파일에서 지정한 전략을 로드합니다.
"""

from typing import Optional, Any
from .candidate_strategy import CandidateSelectionStrategy
from .trading_strategy import TradingStrategy


class StrategyFactory:
    """전략 생성 팩토리"""

    # 등록된 후보 선정 전략
    _candidate_strategies = {}

    # 등록된 매매 전략
    _trading_strategies = {}

    @classmethod
    def register_candidate_strategy(cls, name: str, strategy_class: type):
        """
        후보 선정 전략 등록

        Args:
            name: 전략 이름 (설정 파일에서 사용)
            strategy_class: 전략 클래스
        """
        cls._candidate_strategies[name] = strategy_class

    @classmethod
    def register_trading_strategy(cls, name: str, strategy_class: type):
        """
        매매 전략 등록

        Args:
            name: 전략 이름 (설정 파일에서 사용)
            strategy_class: 전략 클래스
        """
        cls._trading_strategies[name] = strategy_class

    @classmethod
    def create_candidate_strategy(
        cls,
        name: str,
        config: Any = None,
        logger: Any = None
    ) -> Optional[CandidateSelectionStrategy]:
        """
        후보 선정 전략 생성

        Args:
            name: 전략 이름
            config: 전략 설정
            logger: 로거

        Returns:
            전략 인스턴스 또는 None
        """
        strategy_class = cls._candidate_strategies.get(name)
        if strategy_class is None:
            if logger:
                logger.warning(f"알 수 없는 후보 선정 전략: {name}")
            return None

        try:
            return strategy_class(config=config, logger=logger)
        except Exception as e:
            if logger:
                logger.error(f"후보 선정 전략 생성 실패 ({name}): {e}")
            return None

    @classmethod
    def create_trading_strategy(
        cls,
        name: str,
        config: Any = None,
        logger: Any = None
    ) -> Optional[TradingStrategy]:
        """
        매매 전략 생성

        Args:
            name: 전략 이름
            config: 전략 설정
            logger: 로거

        Returns:
            전략 인스턴스 또는 None
        """
        strategy_class = cls._trading_strategies.get(name)
        if strategy_class is None:
            if logger:
                logger.warning(f"알 수 없는 매매 전략: {name}")
            return None

        try:
            return strategy_class(config=config, logger=logger)
        except Exception as e:
            if logger:
                logger.error(f"매매 전략 생성 실패 ({name}): {e}")
            return None

    @classmethod
    def list_candidate_strategies(cls) -> list:
        """등록된 후보 선정 전략 목록 반환"""
        return list(cls._candidate_strategies.keys())

    @classmethod
    def list_trading_strategies(cls) -> list:
        """등록된 매매 전략 목록 반환"""
        return list(cls._trading_strategies.keys())


# 기본 전략 등록
def register_default_strategies():
    """기본 전략 등록"""
    from .momentum_candidate_strategy import MomentumCandidateStrategy
    from .orb_strategy import ORBStrategy

    # 후보 선정 전략 (레거시 - 하위 호환성)
    StrategyFactory.register_candidate_strategy('momentum', MomentumCandidateStrategy)

    # ORB 전략 (후보 선정 + 매매 판단 통합)
    StrategyFactory.register_candidate_strategy('orb', ORBStrategy)
    StrategyFactory.register_trading_strategy('orb', ORBStrategy)


# 모듈 로드 시 기본 전략 등록
register_default_strategies()
