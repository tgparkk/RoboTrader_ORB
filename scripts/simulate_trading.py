"""
매매 로직 시뮬레이션 스크립트
캐시된 분봉 데이터를 사용하여 변경된 로직 테스트
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pickle
from datetime import datetime, time, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import defaultdict


@dataclass
class SimPosition:
    """시뮬레이션 포지션"""
    stock_code: str
    stock_name: str
    quantity: int
    buy_price: float
    buy_time: datetime
    orb_high: float
    orb_low: float
    target_price: float
    stop_loss: float


@dataclass
class SimTrade:
    """시뮬레이션 거래 기록"""
    stock_code: str
    stock_name: str
    action: str  # BUY or SELL
    quantity: int
    price: float
    time: datetime
    reason: str
    profit_loss: float = 0.0
    profit_rate: float = 0.0


@dataclass
class SimConfig:
    """시뮬레이션 설정"""
    initial_balance: float = 10_000_000
    position_size: float = 1_000_000  # 종목당 투자금

    # 매수 조건
    volume_surge_ratio: float = 2.0  # 거래량 배수 (변경됨: 1.5 → 2.0)
    breakout_buffer: float = 0.0

    # 매도 조건
    take_profit_multiplier: float = 2.5  # 익절 배수 (변경됨: 2.0 → 2.5)

    # 재진입 제한
    daily_buy_limit: int = 1  # 당일 종목당 매수 제한 (변경됨: 무제한 → 1회)

    # 시간 설정
    orb_start: time = field(default_factory=lambda: time(9, 0))
    orb_end: time = field(default_factory=lambda: time(9, 10))
    buy_start: time = field(default_factory=lambda: time(9, 10))
    buy_end: time = field(default_factory=lambda: time(14, 50))
    liquidation_time: time = field(default_factory=lambda: time(15, 0))


class TradingSimulator:
    """매매 시뮬레이터"""

    def __init__(self, config: SimConfig, date_str: str):
        self.config = config
        self.date_str = date_str
        self.cache_dir = Path("cache/minute_data")

        self.balance = config.initial_balance
        self.positions: Dict[str, SimPosition] = {}
        self.trades: List[SimTrade] = []
        self.daily_buy_count: Dict[str, int] = defaultdict(int)
        self.orb_data: Dict[str, dict] = {}

    def load_minute_data(self, stock_code: str) -> Optional[pd.DataFrame]:
        """캐시된 분봉 데이터 로드"""
        file_path = self.cache_dir / f"{stock_code}_{self.date_str}.pkl"
        if not file_path.exists():
            return None

        with open(file_path, 'rb') as f:
            df = pickle.load(f)

        if df is None or df.empty:
            return None

        # datetime 컬럼 확인 및 변환
        if 'datetime' not in df.columns and 'stck_bsop_date' in df.columns:
            df['datetime'] = pd.to_datetime(
                df['stck_bsop_date'].astype(str) + ' ' + df['stck_cntg_hour'].astype(str).str.zfill(6),
                format='%Y%m%d %H%M%S'
            )

        df = df.sort_values('datetime').reset_index(drop=True)
        return df

    def calculate_orb_range(self, df: pd.DataFrame) -> Optional[dict]:
        """ORB 레인지 계산 (09:00~09:10)"""
        df_copy = df.copy()
        df_copy['time'] = pd.to_datetime(df_copy['datetime']).dt.time

        orb_data = df_copy[
            (df_copy['time'] >= self.config.orb_start) &
            (df_copy['time'] < self.config.orb_end)
        ]

        if len(orb_data) < 5:
            return None

        # 가격 컬럼 확인
        high_col = 'high' if 'high' in orb_data.columns else 'stck_hgpr'
        low_col = 'low' if 'low' in orb_data.columns else 'stck_lwpr'
        vol_col = 'volume' if 'volume' in orb_data.columns else 'cntg_vol'

        orb_high = float(orb_data[high_col].max())
        orb_low = float(orb_data[low_col].min())
        avg_volume = float(orb_data[vol_col].mean())

        range_size = orb_high - orb_low
        range_ratio = range_size / orb_high if orb_high > 0 else 0

        # 레인지 유효성 검사 (0.3% ~ 2.5%)
        if range_ratio < 0.003 or range_ratio > 0.025:
            return None

        return {
            'high': orb_high,
            'low': orb_low,
            'avg_volume': avg_volume,
            'range_size': range_size,
            'target_price': orb_high + (range_size * self.config.take_profit_multiplier),
            'stop_loss': orb_low
        }

    def convert_to_3min(self, df: pd.DataFrame) -> pd.DataFrame:
        """1분봉 → 3분봉 변환"""
        df_copy = df.copy()
        df_copy['datetime'] = pd.to_datetime(df_copy['datetime'])
        df_copy = df_copy.set_index('datetime')

        # 가격 컬럼 확인
        high_col = 'high' if 'high' in df_copy.columns else 'stck_hgpr'
        low_col = 'low' if 'low' in df_copy.columns else 'stck_lwpr'
        open_col = 'open' if 'open' in df_copy.columns else 'stck_oprc'
        close_col = 'close' if 'close' in df_copy.columns else 'stck_prpr'
        vol_col = 'volume' if 'volume' in df_copy.columns else 'cntg_vol'

        # 3분봉으로 리샘플링
        df_3min = df_copy.resample('3min', origin='start_day').agg({
            open_col: 'first',
            high_col: 'max',
            low_col: 'min',
            close_col: 'last',
            vol_col: 'sum'
        }).dropna()

        df_3min = df_3min.reset_index()
        df_3min.columns = ['datetime', 'open', 'high', 'low', 'close', 'volume']

        return df_3min

    def check_buy_signal(self, stock_code: str, candle: pd.Series, orb: dict) -> bool:
        """매수 신호 확인"""
        # 재진입 제한 체크
        if self.daily_buy_count[stock_code] >= self.config.daily_buy_limit:
            return False

        # 이미 보유 중이면 패스
        if stock_code in self.positions:
            return False

        # 잔고 체크
        if self.balance < self.config.position_size:
            return False

        close_price = float(candle['close'])
        volume = float(candle['volume'])

        # ORB 고가 돌파 확인
        orb_high = orb['high']
        if close_price <= orb_high * (1 + self.config.breakout_buffer):
            return False

        # 거래량 조건 확인
        avg_volume = orb['avg_volume']
        if avg_volume > 0 and volume < avg_volume * self.config.volume_surge_ratio:
            return False

        return True

    def check_sell_signal(self, stock_code: str, current_price: float, current_time: time) -> Tuple[bool, str]:
        """매도 신호 확인"""
        if stock_code not in self.positions:
            return False, ""

        position = self.positions[stock_code]

        # 시간 청산 (15:00)
        if current_time >= self.config.liquidation_time:
            return True, "15:00 시장가 일괄청산"

        # 익절
        if current_price >= position.target_price:
            return True, f"익절 ({position.target_price:,.0f}원)"

        # 손절
        if current_price <= position.stop_loss:
            return True, f"손절 ({position.stop_loss:,.0f}원)"

        return False, ""

    def execute_buy(self, stock_code: str, stock_name: str, price: float,
                   candle_time: datetime, orb: dict):
        """매수 실행"""
        quantity = int(self.config.position_size / price)
        if quantity <= 0:
            return

        total_cost = quantity * price
        self.balance -= total_cost

        self.positions[stock_code] = SimPosition(
            stock_code=stock_code,
            stock_name=stock_name,
            quantity=quantity,
            buy_price=price,
            buy_time=candle_time,
            orb_high=orb['high'],
            orb_low=orb['low'],
            target_price=orb['target_price'],
            stop_loss=orb['stop_loss']
        )

        self.daily_buy_count[stock_code] += 1

        self.trades.append(SimTrade(
            stock_code=stock_code,
            stock_name=stock_name,
            action='BUY',
            quantity=quantity,
            price=price,
            time=candle_time,
            reason=f"ORB 고가 돌파"
        ))

    def execute_sell(self, stock_code: str, price: float, candle_time: datetime, reason: str):
        """매도 실행"""
        if stock_code not in self.positions:
            return

        position = self.positions[stock_code]
        total_value = position.quantity * price
        profit_loss = total_value - (position.quantity * position.buy_price)
        profit_rate = (price - position.buy_price) / position.buy_price * 100

        self.balance += total_value

        self.trades.append(SimTrade(
            stock_code=stock_code,
            stock_name=position.stock_name,
            action='SELL',
            quantity=position.quantity,
            price=price,
            time=candle_time,
            reason=reason,
            profit_loss=profit_loss,
            profit_rate=profit_rate
        ))

        del self.positions[stock_code]

    def run(self, candidate_stocks: List[dict]) -> dict:
        """시뮬레이션 실행"""
        print(f"\n{'='*60}")
        print(f"시뮬레이션 시작: {self.date_str}")
        print(f"설정: 거래량 {self.config.volume_surge_ratio}배, "
              f"익절 {self.config.take_profit_multiplier}배, "
              f"재진입 {self.config.daily_buy_limit}회")
        print(f"{'='*60}\n")

        # 1. 모든 종목의 분봉 데이터 로드 및 ORB 계산
        stock_data = {}
        for stock in candidate_stocks:
            code = stock['stock_code']
            name = stock.get('stock_name', code)

            df = self.load_minute_data(code)
            if df is None:
                continue

            orb = self.calculate_orb_range(df)
            if orb is None:
                continue

            df_3min = self.convert_to_3min(df)
            if len(df_3min) < 5:
                continue

            stock_data[code] = {
                'name': name,
                'df_1min': df,
                'df_3min': df_3min,
                'orb': orb
            }
            self.orb_data[code] = orb

        print(f"ORB 레인지 계산 완료: {len(stock_data)}개 종목")

        # 2. 시간순으로 매매 시뮬레이션
        # 모든 3분봉 타임스탬프 수집
        all_timestamps = set()
        for data in stock_data.values():
            for ts in data['df_3min']['datetime']:
                all_timestamps.add(ts)

        sorted_timestamps = sorted(all_timestamps)

        for ts in sorted_timestamps:
            current_time = ts.time()

            # 매수 시간 체크
            if self.config.buy_start <= current_time <= self.config.buy_end:
                for code, data in stock_data.items():
                    df_3min = data['df_3min']
                    candles_up_to_now = df_3min[df_3min['datetime'] <= ts]

                    if len(candles_up_to_now) == 0:
                        continue

                    candle = candles_up_to_now.iloc[-1]

                    if self.check_buy_signal(code, candle, data['orb']):
                        self.execute_buy(
                            code, data['name'],
                            float(candle['close']),
                            ts, data['orb']
                        )

            # 매도 체크 (보유 중인 모든 포지션)
            for code in list(self.positions.keys()):
                if code not in stock_data:
                    continue

                df_3min = stock_data[code]['df_3min']
                candles_up_to_now = df_3min[df_3min['datetime'] <= ts]

                if len(candles_up_to_now) == 0:
                    continue

                current_price = float(candles_up_to_now.iloc[-1]['close'])

                should_sell, reason = self.check_sell_signal(code, current_price, current_time)
                if should_sell:
                    self.execute_sell(code, current_price, ts, reason)

        # 3. 15:00 일괄 청산
        for code in list(self.positions.keys()):
            if code in stock_data:
                df_3min = stock_data[code]['df_3min']
                if len(df_3min) > 0:
                    last_price = float(df_3min.iloc[-1]['close'])
                    self.execute_sell(code, last_price,
                                    df_3min.iloc[-1]['datetime'],
                                    "15:00 시장가 일괄청산")

        return self.get_results()

    def get_results(self) -> dict:
        """결과 반환"""
        buy_trades = [t for t in self.trades if t.action == 'BUY']
        sell_trades = [t for t in self.trades if t.action == 'SELL']

        total_profit = sum(t.profit_loss for t in sell_trades)

        # 수익/손실 거래 분리
        winning_trades = [t for t in sell_trades if t.profit_loss > 0]
        losing_trades = [t for t in sell_trades if t.profit_loss < 0]

        win_rate = len(winning_trades) / len(sell_trades) * 100 if sell_trades else 0

        return {
            'initial_balance': self.config.initial_balance,
            'final_balance': self.balance,
            'total_profit': total_profit,
            'profit_rate': total_profit / self.config.initial_balance * 100,
            'buy_count': len(buy_trades),
            'sell_count': len(sell_trades),
            'winning_count': len(winning_trades),
            'losing_count': len(losing_trades),
            'win_rate': win_rate,
            'trades': self.trades,
            'config': self.config
        }


def load_candidate_stocks(date_str: str) -> List[dict]:
    """DB에서 후보 종목 로드"""
    import sqlite3

    db_path = Path("data/robotrader.db")
    if not db_path.exists():
        print(f"DB 파일 없음: {db_path}")
        return []

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 날짜 형식 변환 (20260203 → 2026-02-03)
    formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    cursor.execute("""
        SELECT stock_code, stock_name
        FROM candidate_stocks
        WHERE date(selection_date) = ?
    """, (formatted_date,))

    rows = cursor.fetchall()
    conn.close()

    return [{'stock_code': row[0], 'stock_name': row[1]} for row in rows]


def print_results(results: dict, label: str):
    """결과 출력"""
    print(f"\n{'='*60}")
    print(f"[{label}] 시뮬레이션 결과")
    print(f"{'='*60}")
    print(f"설정: 거래량 {results['config'].volume_surge_ratio}배, "
          f"익절 {results['config'].take_profit_multiplier}배, "
          f"재진입 {results['config'].daily_buy_limit}회")
    print(f"-" * 60)
    print(f"초기 자금: {results['initial_balance']:,.0f}원")
    print(f"최종 잔고: {results['final_balance']:,.0f}원")
    print(f"총 손익: {results['total_profit']:+,.0f}원 ({results['profit_rate']:+.2f}%)")
    print(f"-" * 60)
    print(f"매수: {results['buy_count']}회")
    print(f"매도: {results['sell_count']}회")
    print(f"승리: {results['winning_count']}회")
    print(f"패배: {results['losing_count']}회")
    print(f"승률: {results['win_rate']:.1f}%")

    # 종목별 손익 집계
    if results['trades']:
        stock_pnl = defaultdict(float)
        stock_count = defaultdict(int)
        for t in results['trades']:
            if t.action == 'SELL':
                stock_pnl[f"{t.stock_code}({t.stock_name})"] += t.profit_loss
                stock_count[f"{t.stock_code}({t.stock_name})"] += 1

        print(f"\n[종목별 손익 TOP 5]")
        sorted_pnl = sorted(stock_pnl.items(), key=lambda x: x[1], reverse=True)
        for stock, pnl in sorted_pnl[:5]:
            cnt = stock_count[stock]
            print(f"  {stock}: {pnl:+,.0f}원 ({cnt}회)")

        print(f"\n[종목별 손실 TOP 5]")
        for stock, pnl in sorted_pnl[-5:]:
            if pnl < 0:
                cnt = stock_count[stock]
                print(f"  {stock}: {pnl:+,.0f}원 ({cnt}회)")


def get_available_dates() -> List[str]:
    """캐시된 데이터가 있는 날짜 목록 반환"""
    cache_dir = Path("cache/minute_data")
    if not cache_dir.exists():
        return []

    dates = set()
    for f in cache_dir.glob("*.pkl"):
        # 파일명: {stock_code}_{date}.pkl
        parts = f.stem.split('_')
        if len(parts) >= 2:
            dates.add(parts[-1])

    return sorted(dates)


def run_all_dates_comparison():
    """모든 날짜에 대해 변경 전/후 비교 실행"""
    dates = get_available_dates()
    print(f"\n테스트 가능한 날짜: {len(dates)}일")
    print(f"날짜 목록: {', '.join(dates)}")

    # 결과 집계용
    old_totals = {'profit': 0, 'buy_count': 0, 'win_count': 0, 'lose_count': 0, 'days': 0}
    new_totals = {'profit': 0, 'buy_count': 0, 'win_count': 0, 'lose_count': 0, 'days': 0}

    daily_results = []

    for date_str in dates:
        candidates = load_candidate_stocks(date_str)
        if not candidates:
            print(f"\n[{date_str}] 후보 종목 없음 - 건너뜀")
            continue

        print(f"\n{'='*70}")
        print(f"[{date_str}] 시뮬레이션 시작 (후보: {len(candidates)}개)")
        print(f"{'='*70}")

        # 변경 전 설정
        old_config = SimConfig(
            volume_surge_ratio=1.5,
            take_profit_multiplier=2.0,
            daily_buy_limit=999
        )
        sim_old = TradingSimulator(old_config, date_str)
        results_old = sim_old.run(candidates)

        # 변경 후 설정
        new_config = SimConfig(
            volume_surge_ratio=2.0,
            take_profit_multiplier=2.5,
            daily_buy_limit=1
        )
        sim_new = TradingSimulator(new_config, date_str)
        results_new = sim_new.run(candidates)

        # 일별 결과 저장
        daily_results.append({
            'date': date_str,
            'old': results_old,
            'new': results_new
        })

        # 집계
        old_totals['profit'] += results_old['total_profit']
        old_totals['buy_count'] += results_old['buy_count']
        old_totals['win_count'] += results_old['winning_count']
        old_totals['lose_count'] += results_old['losing_count']
        old_totals['days'] += 1

        new_totals['profit'] += results_new['total_profit']
        new_totals['buy_count'] += results_new['buy_count']
        new_totals['win_count'] += results_new['winning_count']
        new_totals['lose_count'] += results_new['losing_count']
        new_totals['days'] += 1

        # 일별 요약
        print(f"\n[{date_str}] 요약:")
        print(f"  변경 전: 손익 {results_old['total_profit']:+,.0f}원, 매수 {results_old['buy_count']}회, 승률 {results_old['win_rate']:.1f}%")
        print(f"  변경 후: 손익 {results_new['total_profit']:+,.0f}원, 매수 {results_new['buy_count']}회, 승률 {results_new['win_rate']:.1f}%")

    # 전체 결과 출력
    print(f"\n{'='*70}")
    print(f"전체 결과 집계 ({old_totals['days']}일)")
    print(f"{'='*70}")

    old_win_rate = old_totals['win_count'] / (old_totals['win_count'] + old_totals['lose_count']) * 100 if (old_totals['win_count'] + old_totals['lose_count']) > 0 else 0
    new_win_rate = new_totals['win_count'] / (new_totals['win_count'] + new_totals['lose_count']) * 100 if (new_totals['win_count'] + new_totals['lose_count']) > 0 else 0

    print(f"\n{'항목':<20} {'변경 전':>15} {'변경 후':>15} {'차이':>15}")
    print(f"-" * 65)
    print(f"{'총 손익':<20} {old_totals['profit']:>+15,.0f} {new_totals['profit']:>+15,.0f} {new_totals['profit'] - old_totals['profit']:>+15,.0f}")
    print(f"{'총 매수 횟수':<20} {old_totals['buy_count']:>15} {new_totals['buy_count']:>15} {new_totals['buy_count'] - old_totals['buy_count']:>+15}")
    print(f"{'총 승리':<20} {old_totals['win_count']:>15} {new_totals['win_count']:>15} {new_totals['win_count'] - old_totals['win_count']:>+15}")
    print(f"{'총 패배':<20} {old_totals['lose_count']:>15} {new_totals['lose_count']:>15} {new_totals['lose_count'] - old_totals['lose_count']:>+15}")
    print(f"{'전체 승률':<20} {old_win_rate:>14.1f}% {new_win_rate:>14.1f}% {new_win_rate - old_win_rate:>+14.1f}%")

    # 일별 상세 표
    print(f"\n\n{'='*70}")
    print("일별 상세 결과")
    print(f"{'='*70}")
    print(f"{'날짜':<12} {'변경전 손익':>12} {'변경후 손익':>12} {'변경전 매수':>10} {'변경후 매수':>10} {'변경전 승률':>10} {'변경후 승률':>10}")
    print(f"-" * 70)

    for r in daily_results:
        print(f"{r['date']:<12} {r['old']['total_profit']:>+12,.0f} {r['new']['total_profit']:>+12,.0f} "
              f"{r['old']['buy_count']:>10} {r['new']['buy_count']:>10} "
              f"{r['old']['win_rate']:>9.1f}% {r['new']['win_rate']:>9.1f}%")

    print(f"-" * 70)
    print(f"{'합계':<12} {old_totals['profit']:>+12,.0f} {new_totals['profit']:>+12,.0f} "
          f"{old_totals['buy_count']:>10} {new_totals['buy_count']:>10} "
          f"{old_win_rate:>9.1f}% {new_win_rate:>9.1f}%")


def main():
    """메인 함수"""
    import argparse

    parser = argparse.ArgumentParser(description='매매 로직 시뮬레이션')
    parser.add_argument('--date', type=str, default='20260203', help='시뮬레이션 날짜 (YYYYMMDD)')
    parser.add_argument('--compare', action='store_true', help='변경 전/후 비교')
    parser.add_argument('--all', action='store_true', help='모든 날짜 테스트')
    args = parser.parse_args()

    if args.all:
        run_all_dates_comparison()
        return

    date_str = args.date

    # 후보 종목 로드
    candidates = load_candidate_stocks(date_str)
    if not candidates:
        print(f"후보 종목 없음: {date_str}")
        return

    print(f"후보 종목: {len(candidates)}개")

    if args.compare:
        # 변경 전 설정으로 시뮬레이션
        old_config = SimConfig(
            volume_surge_ratio=1.5,  # 기존
            take_profit_multiplier=2.0,  # 기존
            daily_buy_limit=999  # 무제한
        )
        sim_old = TradingSimulator(old_config, date_str)
        results_old = sim_old.run(candidates)
        print_results(results_old, "변경 전 (1.5배/2.0배/무제한)")

        # 변경 후 설정으로 시뮬레이션
        new_config = SimConfig(
            volume_surge_ratio=2.0,  # 변경
            take_profit_multiplier=2.5,  # 변경
            daily_buy_limit=1  # 변경
        )
        sim_new = TradingSimulator(new_config, date_str)
        results_new = sim_new.run(candidates)
        print_results(results_new, "변경 후 (2.0배/2.5배/1회)")

        # 비교 출력
        print(f"\n{'='*60}")
        print("비교 분석")
        print(f"{'='*60}")
        print(f"{'항목':<20} {'변경 전':>15} {'변경 후':>15} {'차이':>15}")
        print(f"-" * 60)
        print(f"{'매수 횟수':<20} {results_old['buy_count']:>15} {results_new['buy_count']:>15} {results_new['buy_count'] - results_old['buy_count']:>+15}")
        print(f"{'총 손익':<20} {results_old['total_profit']:>+15,.0f} {results_new['total_profit']:>+15,.0f} {results_new['total_profit'] - results_old['total_profit']:>+15,.0f}")
        print(f"{'승률':<20} {results_old['win_rate']:>14.1f}% {results_new['win_rate']:>14.1f}% {results_new['win_rate'] - results_old['win_rate']:>+14.1f}%")

    else:
        # 현재 설정으로만 시뮬레이션
        config = SimConfig()
        sim = TradingSimulator(config, date_str)
        results = sim.run(candidates)
        print_results(results, "현재 설정")


if __name__ == "__main__":
    main()
