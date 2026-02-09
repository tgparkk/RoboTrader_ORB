"""
ORB (Opening Range Breakout) 전략 설정
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class ORBStrategyConfig:
    """ORB 전략 설정"""

    # ===== 후보 종목 선정 기준 =====

    # Universe 설정
    universe_file_pattern: str = "data/universe_*.json"  # Universe 파일 패턴

    # 거래대금 기준
    min_trading_amount: int = 10_000_000_000  # 최소 거래대금 (100억)
    min_avg_trading_amount_5d: int = 10_000_000_000  # 5일 평균 거래대금 (100억)

    # 갭 기준 (전일 종가 대비 당일 시가)
    min_gap_ratio: float = 0.003  # 최소 갭 0.3%
    max_gap_ratio: float = 0.03   # 최대 갭 3%
    gap_direction: str = "up"     # 갭 방향: "up" (상승), "down" (하락), "both" (양방향)

    # 주말 후 시장 대응 (월요일 완화 설정)
    enable_monday_relaxation: bool = True  # 월요일 갭 조건 완화 활성화
    monday_min_gap_ratio: float = 0.002    # 월요일 최소 갭 0.2% (평일 0.3%보다 완화)

    # ===== 오프닝 레인지 설정 =====

    # 시간 설정
    orb_start_time: str = "09:00"  # ORB 시작 시간
    orb_end_time: str = "09:10"    # ORB 종료 시간 (10분)

    # 레인지 검증 기준
    min_range_ratio: float = 0.003  # 최소 레인지 비율 (가격의 0.3%)
    max_range_ratio: float = 0.025  # 최대 레인지 비율 (가격의 2.5%)

    # ===== ATR 설정 =====

    atr_period: int = 14  # ATR 계산 기간 (14일)

    # ===== 매수 조건 =====

    # 거래량 조건
    volume_surge_ratio: float = 2.0  # ORB 구간 평균 거래량 대비 2.0배 (기존 1.5배에서 강화)

    # 브레이크아웃 확인
    breakout_buffer: float = 0.0  # 브레이크아웃 버퍼 (0%: 정확히 고가 돌파 시)

    # ===== 매도 조건 =====

    # 손절 기준
    stop_loss_type: str = "orb_low"  # "orb_low": ORB 저가, "atr": ATR 기반

    # 익절 기준
    take_profit_multiplier: float = 2.5  # ORB range_size × 2.5 (기존 2.0에서 상향)
    # 목표가 = ORB 고가 + (range_size × 2.5)

    # ===== 시간 제한 =====

    # 매수 시간 제한
    buy_time_start: str = "09:10"  # 매수 시작 시간 (ORB 종료 직후)
    buy_time_end: str = "14:50"    # 매수 종료 시간

    # 청산 시간
    liquidation_time: str = "15:00"  # 장마감 전 청산

    # ===== 기타 =====

    # ===== 포지션 제한 =====
    max_positions: int = 10  # 최대 동시 보유 종목 수
    position_priority: str = "volume_ratio"  # 우선순위 기준 (거래량 배수)

    # 로깅
    enable_pattern_logging: bool = True  # 패턴 데이터 로깅 활성화

    # 점수 체계 (후보 종목 선정용)
    min_score: int = 50  # 최소 통과 점수

    # 점수 가중치
    score_weights: Dict[str, int] = None

    def __post_init__(self):
        """점수 가중치 초기화"""
        if self.score_weights is None:
            self.score_weights = {
                # 갭 조건
                'valid_gap': 30,  # 적절한 갭 (0.3~3%)

                # 거래대금
                'sufficient_trading_amount': 20,  # 충분한 거래대금

                # ORB 레인지
                'valid_range': 30,  # 유효한 레인지 (0.3~2%)

                # ATR
                'valid_atr': 20,  # 유효한 ATR
            }


# 기본 설정 인스턴스
DEFAULT_ORB_CONFIG = ORBStrategyConfig()
