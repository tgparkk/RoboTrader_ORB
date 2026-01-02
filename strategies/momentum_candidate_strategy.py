"""
모멘텀 기반 후보 종목 선정 전략

기존 candidate_selector의 로직을 전략 패턴으로 분리
"""

from typing import Optional, Any
from .candidate_strategy import CandidateSelectionStrategy, CandidateStock
from config.candidate_selection_config import DEFAULT_CANDIDATE_SELECTION_CONFIG, CandidateSelectionConfig


class MomentumCandidateStrategy(CandidateSelectionStrategy):
    """
    모멘텀 기반 후보 종목 선정 전략

    - 신고가 근처 (기간별 차등 점수)
    - Envelope 상한선 돌파
    - 양봉 형성
    - 거래량 급증 (평균 대비 2~3배)
    - 중심가격 상회
    - 충분한 거래대금
    - 급등주 제외 (7% 갭상승, 10% 급등)
    - 당일 상승률 체크
    """

    def __init__(self, config: CandidateSelectionConfig = None, logger: Any = None):
        super().__init__(config or DEFAULT_CANDIDATE_SELECTION_CONFIG, logger)

    async def evaluate_stock(
        self,
        code: str,
        name: str,
        market: str,
        price_data: Any,
        daily_data: Any,
        weekly_data: Any
    ) -> Optional[CandidateStock]:
        """종목 평가"""
        try:
            # 기본 데이터 검증
            if not self._validate_data(code, daily_data, weekly_data):
                return None

            # 거래대금 검증
            volume_amount = self._calculate_volume_amount(price_data)
            if volume_amount < self.config.min_trading_amount:
                if self.logger:
                    self.logger.debug(f"❌ {code}: 거래대금 부족 ({volume_amount/1_000_000_000:.1f}억원)")
                return None

            if self.logger:
                self.logger.debug(f"✅ {code}: 기본 조건 통과 - 거래대금 {volume_amount/1_000_000_000:.1f}억원")

            # 점수 계산
            score = 0
            reasons = []
            today_close = price_data.current_price
            today_open = getattr(price_data, 'open_price', today_close)

            # A. 최고종가 체크
            score, reasons = self._check_new_high(code, today_close, weekly_data, score, reasons)

            # B. Envelope 상한선 돌파 체크
            if self._check_envelope_breakout(daily_data, today_close):
                score += self.config.score_weights['envelope_breakout']
                reasons.append("Envelope 상한선 돌파")

            # C. 양봉 체크
            if today_close > today_open:
                score += self.config.score_weights['positive_candle']
                reasons.append("양봉 형성")

            # D. 거래량 급증 체크
            score, reasons = self._check_volume_surge(price_data, daily_data, score, reasons)

            # E. 중심가격 위 체크
            if self._check_above_mid_price(price_data, today_close):
                score += self.config.score_weights['above_mid_price']
                reasons.append("중심가격 상회")

            # F. 5일 평균 거래대금 체크
            if self._check_sufficient_trading_amount(daily_data):
                score += self.config.score_weights['sufficient_trading_amount']
                reasons.append("충분한 거래대금")

            # G, H. 급등주 제외 조건
            if not self._check_not_sudden_surge(code, today_open, today_close, daily_data):
                return None

            # I. 시가대비 종가 상승 체크
            intraday_change = (today_close - today_open) / today_open if today_open > 0 else 0
            if intraday_change >= self.config.intraday_rise_threshold:
                score += self.config.score_weights['intraday_rise_3pct']
                reasons.append(f"당일 {self.config.intraday_rise_threshold:.0%} 이상 상승")

            if self.logger:
                self.logger.debug(f"📊 {code}: 최종 점수 {score}점 - {', '.join(reasons) if reasons else '조건 미충족'}")

            # 최소 점수 기준
            if score < self.config.min_score:
                if self.logger:
                    self.logger.debug(f"❌ {code}: 최소 점수 미달 ({score}점 < {self.config.min_score}점)")
                return None

            # 전날 종가 추출
            prev_close = self._get_prev_close(daily_data)

            return CandidateStock(
                code=code,
                name=name,
                market=market,
                score=score,
                reason=", ".join(reasons),
                prev_close=prev_close
            )

        except Exception as e:
            if self.logger:
                self.logger.warning(f"종목 분석 실패 {code}: {e}")
            return None

    def _validate_data(self, code: str, daily_data: Any, weekly_data: Any) -> bool:
        """데이터 유효성 검증"""
        # daily_data 검증
        if hasattr(daily_data, 'empty'):
            if daily_data.empty or len(daily_data) < 10:
                if self.logger:
                    self.logger.debug(f"❌ {code}: 일봉 데이터 부족 ({len(daily_data)}일)")
                return False
        elif len(daily_data) < 10:
            if self.logger:
                self.logger.debug(f"❌ {code}: 일봉 데이터 부족 ({len(daily_data)}일)")
            return False

        # weekly_data 검증
        if hasattr(weekly_data, 'empty'):
            if weekly_data.empty or len(weekly_data) < 5:
                if self.logger:
                    self.logger.debug(f"❌ {code}: 주봉 데이터 부족 ({len(weekly_data)}주)")
                return False
        elif len(weekly_data) < 5:
            if self.logger:
                self.logger.debug(f"❌ {code}: 주봉 데이터 부족 ({len(weekly_data)}주)")
            return False

        return True

    def _calculate_volume_amount(self, price_data: Any) -> float:
        """거래대금 계산"""
        volume_amount = getattr(price_data, 'volume_amount', 0)
        if volume_amount == 0:
            current_volume = getattr(price_data, 'volume', 0)
            current_price = getattr(price_data, 'current_price', 0)
            volume_amount = current_volume * current_price
        return volume_amount

    def _check_new_high(self, code: str, today_close: float, weekly_data: Any, score: int, reasons: list) -> tuple:
        """신고가 근처 체크"""
        # DataFrame인 경우 처리
        if hasattr(weekly_data, 'empty'):
            weekly_closes = weekly_data['stck_clpr'].astype(float).tolist()
        else:
            weekly_closes = [data.close_price for data in weekly_data]

        max_close_period = max(weekly_closes)
        weeks_available = len(weekly_closes)
        days_equivalent = weeks_available * 7  # 대략적인 일수 환산

        # 가능한 기간 내에서 신고가 근처인지 체크
        if today_close >= max_close_period * self.config.new_high_threshold:
            # 긴 기간일수록 더 높은 점수
            if days_equivalent >= 200:
                score += self.config.score_weights['new_high_200d']
                reasons.append(f"200일+ 신고가 근처")
            elif days_equivalent >= 100:
                score += self.config.score_weights['new_high_100d']
                reasons.append(f"100일+ 신고가 근처")
            else:
                score += self.config.score_weights['new_high_other']
                reasons.append(f"{days_equivalent}일 신고가 근처")

            if self.logger:
                self.logger.debug(f"✅ {code}: {days_equivalent}일 신고가 ({max_close_period:,.0f}원 대비 {today_close/max_close_period:.1%})")

        return score, reasons

    def _check_envelope_breakout(self, daily_data: Any, current_price: float) -> bool:
        """Envelope 상한선 돌파 체크"""
        try:
            # 10일 이동평균 계산
            if hasattr(daily_data, 'empty'):
                # DataFrame인 경우
                recent_10d = daily_data.tail(self.config.envelope_ma_period)
                ma10 = recent_10d['stck_clpr'].astype(float).mean()
            else:
                # List인 경우
                data_list = list(daily_data)
                recent_10d = data_list[-self.config.envelope_ma_period:]
                ma10 = sum([data.close_price for data in recent_10d]) / len(recent_10d)

            # Envelope 상한선
            upper_envelope = ma10 * self.config.envelope_upper_ratio

            return current_price >= upper_envelope

        except Exception:
            return False

    def _check_volume_surge(self, price_data: Any, daily_data: Any, score: int, reasons: list) -> tuple:
        """거래량 급증 체크"""
        if hasattr(daily_data, 'empty'):
            # DataFrame인 경우
            recent_vol = daily_data.tail(self.config.volume_avg_period)
            avg_volume = recent_vol['acml_vol'].astype(float).mean()
        else:
            # List인 경우
            recent_data = list(daily_data)[-self.config.volume_avg_period:]
            avg_volume = sum([data.volume for data in recent_data]) / len(recent_data)

        current_volume = getattr(price_data, 'volume', 0)
        if current_volume >= avg_volume * self.config.volume_surge_threshold_high:
            score += self.config.score_weights['volume_surge_3x']
            reasons.append("거래량 3배 급증")
        elif current_volume >= avg_volume * self.config.volume_surge_threshold_mid:
            score += self.config.score_weights['volume_surge_2x']
            reasons.append("거래량 2배 증가")

        return score, reasons

    def _check_above_mid_price(self, price_data: Any, today_close: float) -> bool:
        """중심가격 위 체크"""
        high_price = getattr(price_data, 'high_price', today_close)
        low_price = getattr(price_data, 'low_price', today_close)
        mid_price = (high_price + low_price) / 2
        return today_close > mid_price

    def _check_sufficient_trading_amount(self, daily_data: Any) -> bool:
        """5일 평균 거래대금 체크"""
        if hasattr(daily_data, 'empty'):
            # DataFrame인 경우
            recent_5d = daily_data.tail(5)
            volumes = recent_5d['acml_vol'].astype(float)
            closes = recent_5d['stck_clpr'].astype(float)
            avg_amount_5d = (volumes * closes).mean()
        else:
            # List인 경우
            recent_5d = list(daily_data)[-5:]
            avg_amount_5d = sum([data.volume * data.close_price for data in recent_5d]) / len(recent_5d)

        return avg_amount_5d >= self.config.min_avg_trading_amount_5d

    def _check_not_sudden_surge(self, code: str, today_open: float, today_close: float, daily_data: Any) -> bool:
        """급등주 제외 조건"""
        if hasattr(daily_data, 'empty'):
            # DataFrame인 경우
            if len(daily_data) >= 2:
                prev_close = float(daily_data.iloc[-2]['stck_clpr'])
                open_change = (today_open - prev_close) / prev_close if prev_close > 0 else 0
                close_change = (today_close - prev_close) / prev_close if prev_close > 0 else 0

                # 시가 갭상승 제외
                if open_change >= self.config.max_open_gap_ratio:
                    if self.logger:
                        self.logger.debug(f"❌ {code}: 시가 갭상승 제외 ({open_change:.1%})")
                    return False

                # 급등주 제외
                if close_change >= self.config.max_close_change_ratio:
                    if self.logger:
                        self.logger.debug(f"❌ {code}: 급등주 제외 ({close_change:.1%})")
                    return False
        else:
            # List인 경우
            data_list = list(daily_data)
            if len(data_list) >= 2:
                prev_close = data_list[-2].close_price
                open_change = (today_open - prev_close) / prev_close if prev_close > 0 else 0
                close_change = (today_close - prev_close) / prev_close if prev_close > 0 else 0

                # 시가 갭상승 제외
                if open_change >= self.config.max_open_gap_ratio:
                    if self.logger:
                        self.logger.debug(f"❌ {code}: 시가 갭상승 제외 ({open_change:.1%})")
                    return False

                # 급등주 제외
                if close_change >= self.config.max_close_change_ratio:
                    if self.logger:
                        self.logger.debug(f"❌ {code}: 급등주 제외 ({close_change:.1%})")
                    return False

        return True

    def _get_prev_close(self, daily_data: Any) -> float:
        """전날 종가 추출"""
        if hasattr(daily_data, 'empty') and len(daily_data) >= 2:
            return float(daily_data.iloc[-2]['stck_clpr'])
        else:
            data_list = list(daily_data)
            if len(data_list) >= 2:
                return data_list[-2].close_price
        return 0.0
