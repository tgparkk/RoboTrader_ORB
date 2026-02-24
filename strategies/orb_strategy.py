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

    후보 종목 선정 (08:55~08:59):
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

    def __init__(self, config: ORBStrategyConfig = None, logger: Any = None, pg_manager=None):
        super().__init__(config or DEFAULT_ORB_CONFIG, logger)
        self.orb_data = {}  # {code: {'high': float, 'low': float, 'avg_volume': float, ...}}
        self._orb_data_date = None  # 날짜 변경 감지용
        self.pg = pg_manager

    def _ensure_daily_reset(self):
        """매일 장 시작 시 orb_data 초기화 (전날 데이터 사용 방지)"""
        from utils.korean_time import now_kst
        today = now_kst().date()
        if self._orb_data_date != today:
            if self._orb_data_date is not None and self.orb_data:
                if self.logger:
                    self.logger.info(
                        f"[ORB 전략] 🔄 날짜 변경 감지 ({self._orb_data_date} → {today}), "
                        f"orb_data 초기화 ({len(self.orb_data)}개 종목 제거)"
                    )
            self.orb_data = {}
            self._orb_data_date = today

    async def select_daily_candidates(
        self,
        universe,
        api_client: Any,
        **kwargs
    ) -> List[CandidateStock]:
        """
        일간 후보 종목 선정 (08:55~08:59 실행)

        Args:
            universe: 종목 유니버스 - DataFrame 또는 List[dict]
                      [{'code': '005930', 'name': '삼성전자', 'market': 'KOSPI', ...}]
            api_client: API 클라이언트
            **kwargs: 추가 파라미터

        Returns:
            후보 종목 리스트
        """
        import time
        start_time = time.time()

        candidates = []

        # 스크리닝 통계 카운터
        stats = {
            'total': 0,
            'invalid_format': 0,
            'price_fetch_failed': 0,
            'zero_price': 0,
            'daily_data_insufficient': 0,
            'gap_out_of_range': 0,
            'volume_insufficient': 0,
            'atr_invalid': 0,
            'selected': 0,
            'api_calls': 0,
            # 갭 상승 분포 통계
            'gap_dist': {
                'negative': 0,      # 갭 하락
                'flat': 0,          # 0~0.3%
                'small': 0,         # 0.3~1%
                'medium': 0,        # 1~2%
                'large': 0,         # 2~3%
                'too_large': 0,     # 3% 이상
            },
        }

        # DataFrame인 경우 List[dict]로 변환
        if hasattr(universe, 'to_dict'):
            universe = universe.to_dict('records')
            if self.logger:
                self.logger.info(f"[ORB 전략] DataFrame → List[dict] 변환 완료")

        stats['total'] = len(universe)

        if self.logger:
            self.logger.info(f"[ORB 전략] 후보 종목 선정 시작 - Universe: {len(universe)}개")

        for stock in universe:
            try:
                # Handle both dict and string formats for stock
                if isinstance(stock, dict):
                    code = stock.get('code')
                    name = stock.get('name', 'Unknown')
                    market = stock.get('market', 'Unknown')
                else:
                    code = str(stock)
                    name = 'Unknown'
                    market = 'Unknown'

                if not code or len(code) < 6:
                    stats['invalid_format'] += 1
                    continue

                # 1. 현재가 정보 조회
                stats['api_calls'] += 1
                price_data = api_client.get_current_price(code)
                if not price_data:
                    stats['price_fetch_failed'] += 1
                    continue

                current_price = getattr(price_data, 'current_price', 0)
                if current_price == 0:
                    stats['zero_price'] += 1
                    continue

                # 2. 일봉 데이터 조회 (최근 30일)
                stats['api_calls'] += 1
                daily_data = api_client.get_ohlcv_data(code, "D", 30)
                if daily_data is None or (hasattr(daily_data, 'empty') and daily_data.empty) or len(daily_data) < 15:
                    stats['daily_data_insufficient'] += 1
                    continue

                # 3. 후보 종목 평가
                candidate, reject_reason = await self._evaluate_candidate_with_reason(
                    code, name, market, price_data, daily_data
                )

                # 갭 분포 통계 수집 (평가 후 metadata에서 갭 비율 추출)
                if candidate and 'gap_ratio' in candidate.metadata:
                    gap_ratio = candidate.metadata['gap_ratio']
                    if gap_ratio < 0:
                        stats['gap_dist']['negative'] += 1
                    elif gap_ratio < 0.003:
                        stats['gap_dist']['flat'] += 1
                    elif gap_ratio < 0.01:
                        stats['gap_dist']['small'] += 1
                    elif gap_ratio < 0.02:
                        stats['gap_dist']['medium'] += 1
                    elif gap_ratio < 0.03:
                        stats['gap_dist']['large'] += 1
                    else:
                        stats['gap_dist']['too_large'] += 1
                elif reject_reason == 'gap':
                    # 거절된 경우에도 갭 비율을 알아야 하는데, 현재는 반환되지 않음
                    # 대신 일봉 데이터로 직접 계산
                    if hasattr(daily_data, 'iloc'):
                        df_temp = daily_data
                    else:
                        df_temp = pd.DataFrame([
                            {'stck_clpr': d.close_price} for d in daily_data
                        ])
                    close_col_temp = 'close' if 'close' in df_temp.columns else 'stck_clpr'
                    prev_close_temp = float(df_temp.iloc[-1][close_col_temp])
                    current_price_temp = getattr(price_data, 'current_price', prev_close_temp)
                    gap_ratio_temp = (current_price_temp - prev_close_temp) / prev_close_temp if prev_close_temp > 0 else 0

                    if gap_ratio_temp < 0:
                        stats['gap_dist']['negative'] += 1
                    elif gap_ratio_temp < 0.003:
                        stats['gap_dist']['flat'] += 1
                    elif gap_ratio_temp < 0.01:
                        stats['gap_dist']['small'] += 1
                    elif gap_ratio_temp < 0.02:
                        stats['gap_dist']['medium'] += 1
                    elif gap_ratio_temp < 0.03:
                        stats['gap_dist']['large'] += 1
                    else:
                        stats['gap_dist']['too_large'] += 1

                if candidate:
                    candidates.append(candidate)
                    stats['selected'] += 1
                    if self.logger:
                        self.logger.info(
                            f"[ORB 전략] ✅ 후보 선정: {name}({code}) - "
                            f"점수: {candidate.score}, 이유: {candidate.reason}"
                        )
                else:
                    # 탈락 사유 카운트
                    if reject_reason == 'gap':
                        stats['gap_out_of_range'] += 1
                    elif reject_reason == 'volume':
                        stats['volume_insufficient'] += 1
                    elif reject_reason == 'atr':
                        stats['atr_invalid'] += 1

            except Exception as e:
                stock_code = stock.get('code', 'unknown') if isinstance(stock, dict) else str(stock)
                if self.logger:
                    self.logger.warning(f"[ORB 전략] 종목 분석 실패 {stock_code}: {e}")
                continue

        elapsed_time = time.time() - start_time

        # 스크리닝 요약 통계 로그 (INFO 레벨)
        if self.logger:
            self.logger.info(f"[ORB 전략] 후보 종목 선정 완료: {len(candidates)}개")

            # 갭 분포 통계
            gap_dist = stats['gap_dist']
            gap_dist_summary = (
                f"\n📈 갭 상승 분포:\n"
                f"  - 갭 하락: {gap_dist['negative']}개\n"
                f"  - 보합권(0~0.3%): {gap_dist['flat']}개\n"
                f"  - 소폭상승(0.3~1%): {gap_dist['small']}개\n"
                f"  - 중간상승(1~2%): {gap_dist['medium']}개\n"
                f"  - 큰상승(2~3%): {gap_dist['large']}개\n"
                f"  - 과도상승(3%+): {gap_dist['too_large']}개"
            )

            self.logger.info(
                f"[ORB 전략] 📊 스크리닝 통계 (소요시간: {elapsed_time:.1f}초, API호출: {stats['api_calls']}회):\n"
                f"  - 전체: {stats['total']}개\n"
                f"  - 잘못된 형식: {stats['invalid_format']}개\n"
                f"  - 가격조회 실패: {stats['price_fetch_failed']}개\n"
                f"  - 가격 0원: {stats['zero_price']}개\n"
                f"  - 일봉 부족(<15일): {stats['daily_data_insufficient']}개\n"
                f"  - 갭 범위 벗어남: {stats['gap_out_of_range']}개\n"
                f"  - 거래대금 부족: {stats['volume_insufficient']}개\n"
                f"  - ATR 비정상: {stats['atr_invalid']}개\n"
                f"  - ✅ 선정: {stats['selected']}개\n"
                f"{gap_dist_summary}"
            )

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
        후보 종목 평가 (기존 호환성 유지용 wrapper)

        Returns:
            CandidateStock 또는 None
        """
        candidate, _ = await self._evaluate_candidate_with_reason(
            code, name, market, price_data, daily_data
        )
        return candidate

    async def _evaluate_candidate_with_reason(
        self,
        code: str,
        name: str,
        market: str,
        price_data: Any,
        daily_data: Any
    ) -> tuple:
        """
        후보 종목 평가 (탈락 사유 포함)

        검증 항목:
        - 갭 (0.3~3% 상승)
        - 거래대금 (100억 이상)
        - ATR 유효성

        Returns:
            (CandidateStock, None) 또는 (None, reject_reason)
            reject_reason: 'gap', 'volume', 'atr', None
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

        # 컬럼명 호환성 처리 (API 전처리로 인해 컬럼명이 변경될 수 있음)
        close_col = 'close' if 'close' in df.columns else 'stck_clpr'
        high_col = 'high' if 'high' in df.columns else 'stck_hgpr'
        low_col = 'low' if 'low' in df.columns else 'stck_lwpr'
        vol_col = 'volume' if 'volume' in df.columns else 'acml_vol'

        # 전일 종가
        prev_close = float(df.iloc[-1][close_col])  # 가장 최근 일봉 종가
        current_price = getattr(price_data, 'current_price', prev_close)

        # 🆕 장전(08:55~09:00) 예상체결가 활용 로직
        is_pre_market = False
        from utils.korean_time import now_kst
        current_time = now_kst().time()

        # 장 시작 전이고 현재가가 전일 종가와 같다면 (아직 시가 미형성)
        if time(8, 55) <= current_time < time(9, 0) and current_price == prev_close:
            try:
                # 예상체결가 조회 시도
                from api.kis_market_api import get_expected_price_info
                # api_client 객체가 아니라 모듈 함수를 직접 사용 (api_client에 메서드가 없을 수 있음)
                # 하지만 api_client가 KISAPIManager 인스턴스라면 거기에도 메서드를 추가하는 게 좋겠지만,
                # 여기서는 직접 임포트해서 사용
                expected_info = get_expected_price_info(code)

                if expected_info and expected_info['expected_price'] > 0:
                    current_price = expected_info['expected_price']
                    is_pre_market = True
                    if self.logger:
                        self.logger.debug(f"[ORB 전략] 🕒 {code}: 장전 예상체결가 사용 ({current_price:,.0f}원)")
            except ImportError:
                pass
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[ORB 전략] 예상체결가 조회 오류 {code}: {e}")

        # A. 갭 확인 (전일 종가 대비 현재가/예상가)
        gap_ratio = (current_price - prev_close) / prev_close if prev_close > 0 else 0

        # 갭 방향 확인
        if self.config.gap_direction == "up" and gap_ratio < 0:
            return (None, 'gap')  # 하락 갭은 제외
        elif self.config.gap_direction == "down" and gap_ratio > 0:
            return (None, 'gap')  # 상승 갭은 제외

        # 🆕 월요일 갭 조건 완화 (주말 후 시장 특성 반영)
        min_gap_threshold = self.config.min_gap_ratio
        abs_gap = abs(gap_ratio)  # 월요일 조건 확인 전에 미리 계산
        if self.config.enable_monday_relaxation:
            from utils.korean_time import now_kst
            current_weekday = now_kst().weekday()  # 0=월요일, 6=일요일
            if current_weekday == 0:  # 월요일
                min_gap_threshold = self.config.monday_min_gap_ratio
                if self.logger and abs_gap >= self.config.monday_min_gap_ratio:
                    self.logger.debug(
                        f"[ORB 전략] 📅 월요일 갭 조건 완화 적용: {code} "
                        f"(갭 {gap_ratio:.2%}, 기준 {min_gap_threshold:.2%})"
                    )

        # 갭 크기 확인
        if abs_gap < min_gap_threshold or abs_gap > self.config.max_gap_ratio:
            if self.logger:
                self.logger.debug(
                    f"[ORB 전략] ❌ {code}: 갭 범위 벗어남 ({gap_ratio:.2%})"
                )
            return (None, 'gap')

        score += self.config.score_weights['valid_gap']
        reasons.append(f"적절한 갭 ({gap_ratio:+.2%})")

        # B. 거래대금 확인
        volume_amount = getattr(price_data, 'volume_amount', 0)

        # 🆕 거래대금 데이터 검증: API 파싱 실패 시 폴백 로직
        if volume_amount == 0 or volume_amount < 1e6:  # 100만원 미만이면 비정상
            # 1차 폴백: volume * current_price로 계산
            volume = getattr(price_data, 'volume', 0)
            if volume > 0 and current_price > 0:
                volume_amount = volume * current_price
                if self.logger:
                    self.logger.debug(
                        f"[ORB 전략] ⚠️ {code}: 거래대금 필드 없음, volume×price로 계산 ({volume_amount/1e9:.1f}억)"
                    )

            # 2차 폴백: 5일 평균 거래대금 사용 (장전 시점에 유용)
            if volume_amount < 1e6:
                recent_5d = df.tail(5)
                volume_amount = (
                    recent_5d[vol_col].astype(float) *
                    recent_5d[close_col].astype(float)
                ).mean()

                if self.logger:
                    self.logger.debug(
                        f"[ORB 전략] ⚠️ {code}: 당일 거래대금 산출 불가, 5일 평균 사용 ({volume_amount/1e9:.1f}억)"
                    )

        if volume_amount < self.config.min_trading_amount:
            if self.logger:
                self.logger.debug(
                    f"[ORB 전략] ❌ {code}: 거래대금 부족 ({volume_amount/1e9:.1f}억)"
                )
            return (None, 'volume')

        # 5일 평균 거래대금
        recent_5d = df.tail(5)
        avg_amount_5d = (
            recent_5d[vol_col].astype(float) *
            recent_5d[close_col].astype(float)
        ).mean()

        if avg_amount_5d < self.config.min_avg_trading_amount_5d:
            if self.logger:
                self.logger.debug(
                    f"[ORB 전략] ❌ {code}: 5일 평균 거래대금 부족 ({avg_amount_5d/1e9:.1f}억)"
                )
            return (None, 'volume')

        score += self.config.score_weights['sufficient_trading_amount']
        reasons.append(f"충분한 거래대금 ({volume_amount/1e9:.1f}억)")

        # C. ATR 계산 (컬럼명 전달)
        atr = self._calculate_atr(df, self.config.atr_period, high_col, low_col, close_col)
        if atr == 0 or atr > prev_close * 0.1:  # ATR이 종가의 10% 초과 시 제외
            if self.logger:
                self.logger.debug(
                    f"[ORB 전략] ❌ {code}: ATR 비정상 ({atr:,.0f}원)"
                )
            return (None, 'atr')

        score += self.config.score_weights['valid_atr']
        reasons.append(f"ATR {atr:,.0f}원")

        # 후보 종목 생성
        return (CandidateStock(
            code=code,
            name=name,
            market=market,
            score=score,
            reason=", ".join(reasons),
            prev_close=prev_close,
            metadata={
                'gap_ratio': gap_ratio,
                'atr': atr,
                'avg_volume_5d': recent_5d[vol_col].astype(float).mean()
            }
        ), None)

    def _calculate_atr(
        self,
        df: pd.DataFrame,
        period: int = 14,
        high_col: str = 'stck_hgpr',
        low_col: str = 'stck_lwpr',
        close_col: str = 'stck_clpr'
    ) -> float:
        """
        ATR (Average True Range) 계산

        Args:
            df: 일봉 DataFrame
            period: ATR 계산 기간 (기본 14일)
            high_col: 고가 컬럼명
            low_col: 저가 컬럼명
            close_col: 종가 컬럼명

        Returns:
            ATR 값
        """
        if len(df) < period:
            return 0.0

        df = df.copy()
        df['high'] = df[high_col].astype(float)
        df['low'] = df[low_col].astype(float)
        df['close'] = df[close_col].astype(float)

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
            # 0. 날짜 변경 시 orb_data 초기화
            self._ensure_daily_reset()

            # 1. 시간 확인
            from utils.korean_time import now_kst
            now = now_kst().time()
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
                # DataFrame인 경우: 컬럼명 호환성 처리
                vol_col = 'volume' if 'volume' in minute_data.columns else 'acml_vol'
                if vol_col not in minute_data.columns:
                    if self.logger:
                        self.logger.debug(f"[ORB 전략] ❌ {code}: 거래량 컬럼 없음 ({minute_data.columns.tolist()})")
                    return None
                current_volume = float(minute_data.iloc[-1][vol_col])
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

            volume_ratio = current_volume / orb_avg_volume if orb_avg_volume > 0 else 0
            # 🆕 confidence에 거래량 배수 반영 (높을수록 우선순위 높음)
            confidence = min(1.0, volume_ratio / 10.0)  # 10배 이상이면 confidence=1.0

            return BuySignal(
                code=code,
                reason=f"ORB 고가 돌파 (거래량 {volume_ratio:.1f}배)",
                confidence=confidence,
                metadata={
                    'orb_high': orb_high,
                    'orb_low': orb_low,
                    'range_size': range_size,
                    'stop_loss': orb_low,
                    'take_profit': take_profit_price,
                    'entry_price': current_price,
                    'volume_ratio': volume_ratio
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
            from utils.korean_time import now_kst
            now = now_kst().time()
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

            # 5. 시간 기반 트레일링 스탑
            if getattr(self.config, 'enable_time_trailing', False) and entry_price > 0:
                profit_pct = (current_price - entry_price) / entry_price * 100

                trailing_start = time.fromisoformat(getattr(self.config, 'trailing_start_time', '11:00'))
                breakeven_time = time.fromisoformat(getattr(self.config, 'breakeven_time', '13:00'))
                tighten_time = time.fromisoformat(getattr(self.config, 'tighten_time', '14:00'))

                # breakeven 평가를 tighten보다 먼저 실행 (우선순위 보장)
                breakeven_str = getattr(self.config, 'breakeven_time', '13:00')
                tighten_str = getattr(self.config, 'tighten_time', '14:00')

                # breakeven_time 이후: 본전 이하이면 즉시 청산
                if now >= breakeven_time and profit_pct <= 0:
                    if self.logger:
                        self.logger.info(
                            f"[ORB 전략] ⏰ 시간 트레일링: {code} {breakeven_str} 본전 스탑 "
                            f"@ {current_price:,.0f}원 (수익: {profit_pct:.2f}%)"
                        )
                    return SellSignal(
                        code=code,
                        reason=f"{breakeven_str} 본전 스탑 (수익 {profit_pct:.2f}%)",
                        signal_type="time_trailing",
                        confidence=0.9,
                        metadata={'profit_pct': profit_pct, 'trailing_type': 'breakeven'}
                    )

                # tighten_time 이후: 익절선을 현재 수익의 50%로 좁힘
                if now >= tighten_time and profit_pct > 0:
                    tighten_price = entry_price * (1 + profit_pct / 100 * 0.5)
                    if current_price <= tighten_price or profit_pct < 0.3:
                        if self.logger:
                            self.logger.info(
                                f"[ORB 전략] ⏰ 시간 트레일링: {code} {tighten_str} 익절선 축소 청산 "
                                f"@ {current_price:,.0f}원 (수익: {profit_pct:.2f}%)"
                            )
                        return SellSignal(
                            code=code,
                            reason=f"{tighten_str} 익절선 축소 (수익 {profit_pct:.2f}%)",
                            signal_type="time_trailing",
                            confidence=0.9,
                            metadata={'profit_pct': profit_pct, 'trailing_type': 'tighten'}
                        )

                # 11:00 이후: 수익 +1% 이상이면 +0.5% 트레일링 스탑
                if now >= trailing_start and profit_pct >= 1.0:
                    trailing_stop_price = entry_price * 1.005
                    if current_price <= trailing_stop_price:
                        if self.logger:
                            self.logger.info(
                                f"[ORB 전략] ⏰ 시간 트레일링: {code} 11시 트레일링 스탑 "
                                f"@ {current_price:,.0f}원 (수익: {profit_pct:.2f}%)"
                            )
                        return SellSignal(
                            code=code,
                            reason=f"11시 트레일링 스탑 (수익 {profit_pct:.2f}%)",
                            signal_type="time_trailing",
                            confidence=0.85,
                            metadata={'profit_pct': profit_pct, 'trailing_type': 'trailing_stop'}
                        )

            return None

        except Exception as e:
            if self.logger:
                self.logger.warning(f"[ORB 전략] 매도 신호 생성 실패 {code}: {e}")
            return None

    async def calculate_orb_range(
        self,
        code: str,
        minute_1_data: Any,
        stock_name: str = ''
    ) -> bool:
        """
        ORB 레인지 계산 (09:00~09:10)

        Args:
            code: 종목 코드
            minute_1_data: 1분봉 데이터 (09:00~09:10 구간)
            stock_name: 종목명

        Returns:
            계산 성공 여부
        """
        try:
            # 날짜 변경 시 orb_data 초기화 (전날 데이터 사용 방지)
            self._ensure_daily_reset()
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

            # 컬럼명 호환성 처리 (API 전처리로 인해 컬럼명이 변경될 수 있음)
            # stck_hgpr → high, stck_lwpr → low, acml_vol → volume
            high_col = 'high' if 'high' in df.columns else 'stck_hgpr'
            low_col = 'low' if 'low' in df.columns else 'stck_lwpr'
            vol_col = 'volume' if 'volume' in df.columns else 'acml_vol'

            # 필수 컬럼 확인
            if high_col not in df.columns or low_col not in df.columns:
                if self.logger:
                    self.logger.error(
                        f"[ORB 전략] ❌ {code}: 필수 컬럼 없음 (컬럼: {df.columns.tolist()})"
                    )
                return False

            # ORB 고가/저가
            orb_high = df[high_col].astype(float).max()
            orb_low = df[low_col].astype(float).min()
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
            avg_volume = df[vol_col].astype(float).mean() if vol_col in df.columns else 0

            # ORB 데이터 저장 (메모리)
            self.orb_data[code] = {
                'high': orb_high,
                'low': orb_low,
                'range_size': range_size,
                'range_ratio': range_ratio,
                'avg_volume': avg_volume
            }

            # PostgreSQL에도 저장
            if self.pg:
                try:
                    from utils.korean_time import now_kst
                    target_price = orb_high + (range_size * self.config.take_profit_multiplier)
                    self.pg.save_orb_range(
                        stock_code=code,
                        stock_name=stock_name,
                        trading_date=now_kst().strftime('%Y%m%d'),
                        orb_data={
                            'orb_high': float(orb_high),
                            'orb_low': float(orb_low),
                            'range_size': float(range_size),
                            'range_ratio': float(range_ratio),
                            'avg_volume': float(avg_volume),
                            'target_price': float(target_price),
                            'stop_price': float(orb_low),
                            'is_valid': True,
                            'calculated_at': now_kst(),
                        }
                    )
                except Exception as pg_e:
                    if self.logger:
                        self.logger.warning(f"[ORB 전략] PG ORB 저장 실패: {pg_e}")

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
