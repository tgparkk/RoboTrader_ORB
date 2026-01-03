"""
ORB (Opening Range Breakout) 전략

시간대별 데이터 요구사항:
- 1분봉: ORB 레인지 계산 (9:00~9:10)
- 3분봉: 매매 신호 판단 (노이즈 감소)
- 일봉: ATR 계산, 전일 종가 확인
"""

from typing import Optional, Any, List
from datetime import datetime, time, timedelta
import pandas as pd

from .trading_strategy import TradingStrategy, BuySignal, SellSignal, CandidateStock
from config.orb_strategy_config import DEFAULT_ORB_CONFIG, ORBStrategyConfig
from scripts.update_weekly_universe import load_latest_universe


class ORBStrategy(TradingStrategy):
    """
    ORB (Opening Range Breakout) 전략

    후보 종목 선정 (08:30~08:50):
    - Universe 로드 (주간 업데이트된 KOSPI 200 + KOSDAQ 100)
    - 갭 확인 (전일 종가 대비 0.3~3% 상승)
    - 거래대금 확인 (100억 이상)
    - ATR 계산 (14일)

    ORB 레인지 계산 (09:00~09:10):
    - 1분봉 데이터로 10분간 고가/저가 수집
    - 레인지 유효성 검증 (가격의 0.3~2%)

    매수 신호 (09:10~14:50):
    - ORB 고가 돌파
    - 거래량 1.5배 이상
    - 3분봉 데이터로 판단

    매도 신호:
    - 손절: ORB 저가
    - 익절: ORB 고가 + (range_size × 2)
    - 시간: 15:00 장마감 청산
    """

    def __init__(self, config: ORBStrategyConfig = None, logger: Any = None):
        super().__init__(config or DEFAULT_ORB_CONFIG, logger)
        self.orb_data = {}  # {code: {'high': float, 'low': float, 'avg_volume': float, ...}}

    async def select_daily_candidates(
        self,
        universe: List[dict],
        api_client: Any,
        **kwargs
    ) -> List[CandidateStock]:
        """
        일간 후보 종목 선정 (08:30~08:50 실행)

        Args:
            universe: 종목 유니버스 [{'code': '005930', 'name': '삼성전자', 'market': 'KOSPI', ...}]
            api_client: API 클라이언트
            **kwargs: 추가 파라미터

        Returns:
            후보 종목 리스트
        """
        candidates = []

        if self.logger:
            self.logger.info(f"[ORB 전략] 후보 종목 선정 시작 - Universe: {len(universe)}개")

        for stock in universe:
            try:
                code = stock['code']
                name = stock['name']
                market = stock['market']

                # 1. 현재가 정보 조회
                price_data = await api_client.get_current_price(code)
                if not price_data:
                    continue

                current_price = getattr(price_data, 'current_price', 0)
                if current_price == 0:
                    continue

                # 2. 일봉 데이터 조회 (최근 30일)
                daily_data = await api_client.get_daily_ohlcv(code, period=30)
                if not daily_data or len(daily_data) < 15:
                    continue

                # 3. 후보 종목 평가
                candidate = await self._evaluate_candidate(
                    code, name, market, price_data, daily_data
                )

                if candidate:
                    candidates.append(candidate)
                    if self.logger:
                        self.logger.info(
                            f"[ORB 전략] ✅ 후보 선정: {name}({code}) - "
                            f"점수: {candidate.score}, 이유: {candidate.reason}"
                        )

            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[ORB 전략] 종목 분석 실패 {stock.get('code', 'unknown')}: {e}")
                continue

        if self.logger:
            self.logger.info(f"[ORB 전략] 후보 종목 선정 완료: {len(candidates)}개")

        return candidates

    async def _evaluate_candidate(
        self,
        code: str,
        name: str,
        market: str,
        price_data: Any,
        daily_data: Any
    ) -> Optional[CandidateStock]:
        """
        후보 종목 평가

        검증 항목:
        - 갭 (0.3~3% 상승)
        - 거래대금 (100억 이상)
        - ATR 유효성
        """
        score = 0
        reasons = []

        # DataFrame 변환
        if hasattr(daily_data, 'empty'):
            df = daily_data
        else:
            # List인 경우 DataFrame으로 변환
            df = pd.DataFrame([
                {
                    'stck_clpr': data.close_price,
                    'stck_hgpr': data.high_price,
                    'stck_lwpr': data.low_price,
                    'acml_vol': data.volume
                }
                for data in daily_data
            ])

        # 전일 종가
        prev_close = float(df.iloc[-1]['stck_clpr'])  # 가장 최근 일봉 종가
        current_price = getattr(price_data, 'current_price', prev_close)

        # A. 갭 확인 (전일 종가 대비 현재가)
        gap_ratio = (current_price - prev_close) / prev_close if prev_close > 0 else 0

        # 갭 방향 확인
        if self.config.gap_direction == "up" and gap_ratio < 0:
            return None  # 하락 갭은 제외
        elif self.config.gap_direction == "down" and gap_ratio > 0:
            return None  # 상승 갭은 제외

        # 갭 크기 확인
        abs_gap = abs(gap_ratio)
        if abs_gap < self.config.min_gap_ratio or abs_gap > self.config.max_gap_ratio:
            if self.logger:
                self.logger.debug(
                    f"[ORB 전략] ❌ {code}: 갭 범위 벗어남 ({gap_ratio:.2%})"
                )
            return None

        score += self.config.score_weights['valid_gap']
        reasons.append(f"적절한 갭 ({gap_ratio:+.2%})")

        # B. 거래대금 확인
        volume_amount = getattr(price_data, 'volume_amount', 0)
        if volume_amount == 0:
            volume = getattr(price_data, 'volume', 0)
            volume_amount = volume * current_price

        if volume_amount < self.config.min_trading_amount:
            if self.logger:
                self.logger.debug(
                    f"[ORB 전략] ❌ {code}: 거래대금 부족 ({volume_amount/1e9:.1f}억)"
                )
            return None

        # 5일 평균 거래대금
        recent_5d = df.tail(5)
        avg_amount_5d = (
            recent_5d['acml_vol'].astype(float) *
            recent_5d['stck_clpr'].astype(float)
        ).mean()

        if avg_amount_5d < self.config.min_avg_trading_amount_5d:
            if self.logger:
                self.logger.debug(
                    f"[ORB 전략] ❌ {code}: 5일 평균 거래대금 부족 ({avg_amount_5d/1e9:.1f}억)"
                )
            return None

        score += self.config.score_weights['sufficient_trading_amount']
        reasons.append(f"충분한 거래대금 ({volume_amount/1e9:.1f}억)")

        # C. ATR 계산
        atr = self._calculate_atr(df, self.config.atr_period)
        if atr == 0 or atr > prev_close * 0.1:  # ATR이 종가의 10% 초과 시 제외
            if self.logger:
                self.logger.debug(
                    f"[ORB 전략] ❌ {code}: ATR 비정상 ({atr:,.0f}원)"
                )
            return None

        score += self.config.score_weights['valid_atr']
        reasons.append(f"ATR {atr:,.0f}원")

        # 후보 종목 생성
        return CandidateStock(
            code=code,
            name=name,
            market=market,
            score=score,
            reason=", ".join(reasons),
            prev_close=prev_close,
            metadata={
                'gap_ratio': gap_ratio,
                'atr': atr,
                'avg_volume_5d': recent_5d['acml_vol'].astype(float).mean()
            }
        )

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """
        ATR (Average True Range) 계산

        Args:
            df: 일봉 DataFrame (stck_hgpr, stck_lwpr, stck_clpr)
            period: ATR 계산 기간 (기본 14일)

        Returns:
            ATR 값
        """
        if len(df) < period:
            return 0.0

        df = df.copy()
        df['high'] = df['stck_hgpr'].astype(float)
        df['low'] = df['stck_lwpr'].astype(float)
        df['close'] = df['stck_clpr'].astype(float)

        # True Range 계산
        df['prev_close'] = df['close'].shift(1)
        df['tr1'] = df['high'] - df['low']
        df['tr2'] = abs(df['high'] - df['prev_close'])
        df['tr3'] = abs(df['low'] - df['prev_close'])
        df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)

        # ATR = TR의 이동평균
        atr = df['tr'].tail(period).mean()

        return atr

    async def generate_buy_signal(
        self,
        code: str,
        minute_data: Any,
        current_price: float,
        **kwargs
    ) -> Optional[BuySignal]:
        """
        매수 신호 생성

        조건:
        1. ORB 레인지 계산 완료 (09:10 이후)
        2. ORB 고가 돌파
        3. 거래량 1.5배 이상 (ORB 구간 평균 대비)
        4. 매수 시간 내 (09:10~14:50)

        Args:
            code: 종목 코드
            minute_data: 분봉 데이터 (3분봉)
            current_price: 현재가
            **kwargs: 추가 파라미터 (candidate_info 등)

        Returns:
            매수 신호 또는 None
        """
        try:
            # 1. 시간 확인
            now = datetime.now().time()
            buy_start = time.fromisoformat(self.config.buy_time_start)
            buy_end = time.fromisoformat(self.config.buy_time_end)

            if not (buy_start <= now <= buy_end):
                return None

            # 2. ORB 레인지 확인
            if code not in self.orb_data:
                if self.logger:
                    self.logger.debug(f"[ORB 전략] ❌ {code}: ORB 레인지 미계산")
                return None

            orb = self.orb_data[code]
            orb_high = orb.get('high', 0)
            orb_low = orb.get('low', 0)
            orb_avg_volume = orb.get('avg_volume', 0)

            if orb_high == 0 or orb_low == 0:
                return None

            # 3. ORB 고가 돌파 확인
            if current_price < orb_high * (1 + self.config.breakout_buffer):
                return None

            # 4. 거래량 확인 (현재 캔들)
            if hasattr(minute_data, 'empty') and not minute_data.empty:
                current_volume = float(minute_data.iloc[-1]['acml_vol'])
            elif hasattr(minute_data, '__iter__') and len(list(minute_data)) > 0:
                data_list = list(minute_data)
                current_volume = data_list[-1].volume
            else:
                return None

            if orb_avg_volume > 0 and current_volume < orb_avg_volume * self.config.volume_surge_ratio:
                if self.logger:
                    self.logger.debug(
                        f"[ORB 전략] ❌ {code}: 거래량 부족 "
                        f"({current_volume:,.0f} < {orb_avg_volume * self.config.volume_surge_ratio:,.0f})"
                    )
                return None

            # 5. 매수 신호 생성
            range_size = orb_high - orb_low
            take_profit_price = orb_high + (range_size * self.config.take_profit_multiplier)

            if self.logger:
                self.logger.info(
                    f"[ORB 전략] 🔔 매수 신호: {code} @ {current_price:,.0f}원 "
                    f"(ORB 고가: {orb_high:,.0f}원, 목표가: {take_profit_price:,.0f}원, "
                    f"손절가: {orb_low:,.0f}원)"
                )

            return BuySignal(
                code=code,
                reason=f"ORB 고가 돌파 (거래량 {current_volume/orb_avg_volume:.1f}배)",
                confidence=1.0,
                metadata={
                    'orb_high': orb_high,
                    'orb_low': orb_low,
                    'range_size': range_size,
                    'stop_loss': orb_low,
                    'take_profit': take_profit_price,
                    'entry_price': current_price,
                    'volume_ratio': current_volume / orb_avg_volume if orb_avg_volume > 0 else 0
                }
            )

        except Exception as e:
            if self.logger:
                self.logger.warning(f"[ORB 전략] 매수 신호 생성 실패 {code}: {e}")
            return None

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

        조건:
        1. 손절: ORB 저가 하회
        2. 익절: ORB 고가 + (range_size × 2) 도달
        3. 시간: 15:00 장마감 청산

        Args:
            code: 종목 코드
            position: 포지션 정보
            minute_data: 분봉 데이터
            current_price: 현재가
            **kwargs: 추가 파라미터

        Returns:
            매도 신호 또는 None
        """
        try:
            # 1. 시간 청산 확인
            now = datetime.now().time()
            liquidation_time = time.fromisoformat(self.config.liquidation_time)

            if now >= liquidation_time:
                return SellSignal(
                    code=code,
                    reason="장마감 청산",
                    signal_type="time_based",
                    confidence=1.0,
                    metadata={'liquidation_time': str(liquidation_time)}
                )

            # 2. 포지션 메타데이터에서 ORB 정보 추출
            metadata = getattr(position, 'metadata', {})
            if not metadata:
                # 메타데이터 없으면 기본 손절만 적용
                return None

            orb_low = metadata.get('stop_loss', 0)
            take_profit_price = metadata.get('take_profit', 0)
            entry_price = metadata.get('entry_price', current_price)

            # 3. 손절 확인
            if orb_low > 0 and current_price <= orb_low:
                loss_pct = (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0

                if self.logger:
                    self.logger.info(
                        f"[ORB 전략] 🔻 손절: {code} @ {current_price:,.0f}원 "
                        f"(손실: {loss_pct:.2f}%, ORB 저가: {orb_low:,.0f}원)"
                    )

                return SellSignal(
                    code=code,
                    reason=f"ORB 저가 하회 (손실 {loss_pct:.2f}%)",
                    signal_type="stop_loss",
                    confidence=1.0,
                    metadata={
                        'stop_loss': orb_low,
                        'loss_pct': loss_pct
                    }
                )

            # 4. 익절 확인
            if take_profit_price > 0 and current_price >= take_profit_price:
                profit_pct = (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0

                if self.logger:
                    self.logger.info(
                        f"[ORB 전략] 🎯 익절: {code} @ {current_price:,.0f}원 "
                        f"(수익: {profit_pct:.2f}%, 목표가: {take_profit_price:,.0f}원)"
                    )

                return SellSignal(
                    code=code,
                    reason=f"목표가 도달 (수익 {profit_pct:.2f}%)",
                    signal_type="take_profit",
                    confidence=1.0,
                    metadata={
                        'take_profit': take_profit_price,
                        'profit_pct': profit_pct
                    }
                )

            return None

        except Exception as e:
            if self.logger:
                self.logger.warning(f"[ORB 전략] 매도 신호 생성 실패 {code}: {e}")
            return None

    async def calculate_orb_range(
        self,
        code: str,
        minute_1_data: Any
    ) -> bool:
        """
        ORB 레인지 계산 (09:00~09:10)

        Args:
            code: 종목 코드
            minute_1_data: 1분봉 데이터 (09:00~09:10 구간)

        Returns:
            계산 성공 여부
        """
        try:
            # DataFrame 변환
            if hasattr(minute_1_data, 'empty'):
                df = minute_1_data
            else:
                df = pd.DataFrame([
                    {
                        'stck_hgpr': data.high_price,
                        'stck_lwpr': data.low_price,
                        'acml_vol': data.volume
                    }
                    for data in minute_1_data
                ])

            if df.empty or len(df) < 5:  # 최소 5개 캔들
                if self.logger:
                    self.logger.debug(f"[ORB 전략] ❌ {code}: 1분봉 데이터 부족 ({len(df)}개)")
                return False

            # ORB 고가/저가
            orb_high = df['stck_hgpr'].astype(float).max()
            orb_low = df['stck_lwpr'].astype(float).min()
            range_size = orb_high - orb_low

            # 레인지 유효성 검증
            mid_price = (orb_high + orb_low) / 2
            range_ratio = range_size / mid_price if mid_price > 0 else 0

            if range_ratio < self.config.min_range_ratio or range_ratio > self.config.max_range_ratio:
                if self.logger:
                    self.logger.debug(
                        f"[ORB 전략] ❌ {code}: 레인지 비율 벗어남 ({range_ratio:.2%})"
                    )
                return False

            # 평균 거래량 계산
            avg_volume = df['acml_vol'].astype(float).mean()

            # ORB 데이터 저장
            self.orb_data[code] = {
                'high': orb_high,
                'low': orb_low,
                'range_size': range_size,
                'range_ratio': range_ratio,
                'avg_volume': avg_volume
            }

            if self.logger:
                self.logger.info(
                    f"[ORB 전략] ✅ ORB 레인지 계산 완료: {code} - "
                    f"고가: {orb_high:,.0f}원, 저가: {orb_low:,.0f}원, "
                    f"레인지: {range_size:,.0f}원 ({range_ratio:.2%})"
                )

            return True

        except Exception as e:
            if self.logger:
                self.logger.warning(f"[ORB 전략] ORB 레인지 계산 실패 {code}: {e}")
            return False
