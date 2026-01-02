"""
후보 종목 선정 기준 설정

전략에 맞게 이 값들을 수정하여 사용하세요.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class CandidateSelectionConfig:
    """후보 종목 선정 기준"""

    # 기본 필터 조건
    min_trading_amount: int = 5_000_000_000  # 최소 거래대금 (50억)
    min_avg_trading_amount_5d: int = 5_000_000_000  # 5일 평균 거래대금 (50억)

    # 신고가 근처 판단 기준
    new_high_threshold: float = 0.98  # 98% 이상이면 신고가 근처로 판단

    # Envelope 설정
    envelope_ma_period: int = 10  # 이동평균선 기간
    envelope_upper_ratio: float = 1.10  # 상한선 비율 (MA × 1.10)

    # 거래량 급증 기준
    volume_surge_threshold_high: float = 3.0  # 평균 대비 3배
    volume_surge_threshold_mid: float = 2.0   # 평균 대비 2배
    volume_avg_period: int = 20  # 거래량 평균 계산 기간

    # 급등주 제외 기준
    max_open_gap_ratio: float = 0.07   # 시가 갭상승 7% 이상 제외
    max_close_change_ratio: float = 0.10  # 종가 상승 10% 이상 제외

    # 당일 상승 기준
    intraday_rise_threshold: float = 0.03  # 시가 대비 종가 3% 이상

    # 점수 체계
    min_score: int = 50  # 최소 통과 점수

    # 점수 가중치
    score_weights: Dict[str, int] = None

    def __post_init__(self):
        """점수 가중치 초기화"""
        if self.score_weights is None:
            self.score_weights = {
                # 신고가 근처 (기간별 차등)
                'new_high_200d': 25,
                'new_high_100d': 20,
                'new_high_other': 15,

                # 기술적 지표
                'envelope_breakout': 15,
                'positive_candle': 10,
                'above_mid_price': 10,

                # 거래량
                'volume_surge_3x': 25,
                'volume_surge_2x': 15,
                'sufficient_trading_amount': 15,

                # 당일 흐름
                'intraday_rise_3pct': 20,
            }


# 기본 설정 인스턴스
DEFAULT_CANDIDATE_SELECTION_CONFIG = CandidateSelectionConfig()
