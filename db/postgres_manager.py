"""
PostgreSQL 데이터베이스 관리 모듈
분봉/일봉 캐시, ORB 레인지, 일일 거래 요약 데이터 관리
"""
import psycopg2
import psycopg2.extras
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from config.db_config import get_postgres_config, PostgresConfig
from utils.logger import setup_logger
from utils.korean_time import now_kst


class PostgresManager:
    """PostgreSQL 데이터베이스 관리자"""

    def __init__(self, config: PostgresConfig = None):
        self.logger = setup_logger(__name__)
        self.config = config or get_postgres_config()
        self._conn = None
        self.logger.info(
            f"PostgreSQL 초기화: {self.config.host}:{self.config.port}/{self.config.database}"
        )

    def _get_conn(self):
        """연결 획득 (lazy, 자동 재연결)"""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                host=self.config.host,
                port=self.config.port,
                database=self.config.database,
                user=self.config.user,
                password=self.config.password,
                connect_timeout=10,
            )
            self._conn.autocommit = False
        return self._conn

    def close(self):
        """연결 종료"""
        if self._conn and not self._conn.closed:
            self._conn.close()

    # ============================================================
    # 범용 쿼리 실행
    # ============================================================

    def execute_query(self, query: str, params=None) -> list:
        """SQL 쿼리를 실행하고 결과(fetchall)를 반환한다."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(query, params)
            if cur.description:
                results = cur.fetchall()
            else:
                results = []
            conn.commit()
            return results
        except Exception as e:
            self.logger.error(f"execute_query 실패: {e}")
            if self._conn and not self._conn.closed:
                self._conn.rollback()
            raise

    # ============================================================
    # 분봉 데이터 (minute_candles)
    # ============================================================

    def save_minute_candles(self, stock_code: str, date_str: str, df: pd.DataFrame) -> bool:
        """분봉 데이터 저장 (UPSERT)"""
        try:
            if df is None or df.empty:
                return True

            conn = self._get_conn()
            cur = conn.cursor()

            # 기존 데이터 삭제 후 삽입 (날짜 단위)
            candle_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            cur.execute(
                "DELETE FROM minute_candles WHERE stock_code = %s AND candle_date = %s",
                (stock_code, candle_date),
            )

            rows = []
            for _, row in df.iterrows():
                # datetime 컬럼 처리
                if "datetime" in row and pd.notna(row["datetime"]):
                    dt = pd.Timestamp(row["datetime"])
                elif "date" in row and "time" in row:
                    dt = pd.Timestamp(f"{row['date'][:4]}-{row['date'][4:6]}-{row['date'][6:8]} "
                                      f"{row['time'][:2]}:{row['time'][2:4]}:{row['time'][4:6]}")
                else:
                    continue

                # 컬럼명 호환 (open/open_price 등)
                open_p = row.get("open", row.get("open_price", 0))
                high_p = row.get("high", row.get("high_price", 0))
                low_p = row.get("low", row.get("low_price", 0))
                close_p = row.get("close", row.get("close_price", 0))
                vol = row.get("volume", 0)
                amt = row.get("amount", 0)

                rows.append((
                    stock_code,
                    dt.date(),
                    dt.time(),
                    dt.to_pydatetime(),
                    float(open_p),
                    float(high_p),
                    float(low_p),
                    float(close_p),
                    int(vol),
                    float(amt),
                ))

            if rows:
                psycopg2.extras.execute_batch(
                    cur,
                    """INSERT INTO minute_candles
                       (stock_code, candle_date, candle_time, datetime,
                        open_price, high_price, low_price, close_price, volume, amount)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (stock_code, datetime) DO UPDATE SET
                        open_price=EXCLUDED.open_price, high_price=EXCLUDED.high_price,
                        low_price=EXCLUDED.low_price, close_price=EXCLUDED.close_price,
                        volume=EXCLUDED.volume, amount=EXCLUDED.amount""",
                    rows,
                    page_size=500,
                )

            conn.commit()
            self.logger.debug(f"[PG] {stock_code} 분봉 {len(rows)}건 저장 ({date_str})")
            return True

        except Exception as e:
            self.logger.error(f"[PG] 분봉 저장 실패 ({stock_code}, {date_str}): {e}")
            if self._conn and not self._conn.closed:
                self._conn.rollback()
            return False

    def get_minute_candles(self, stock_code: str, date_str: str) -> Optional[pd.DataFrame]:
        """분봉 데이터 조회 → DataFrame (기존 pkl 형식과 호환)"""
        try:
            conn = self._get_conn()
            candle_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

            df = pd.read_sql_query(
                """SELECT datetime, open_price as open, high_price as high,
                          low_price as low, close_price as close,
                          volume, amount
                   FROM minute_candles
                   WHERE stock_code = %s AND candle_date = %s
                   ORDER BY datetime""",
                conn,
                params=(stock_code, candle_date),
            )

            if df.empty:
                return None

            # pkl 형식 호환: date, time 컬럼 추가
            df["datetime"] = pd.to_datetime(df["datetime"])
            df["date"] = df["datetime"].dt.strftime("%Y%m%d")
            df["time"] = df["datetime"].dt.strftime("%H%M%S")

            self.logger.debug(f"[PG] {stock_code} 분봉 {len(df)}건 조회 ({date_str})")
            return df

        except Exception as e:
            self.logger.error(f"[PG] 분봉 조회 실패 ({stock_code}, {date_str}): {e}")
            return None

    def has_minute_candles(self, stock_code: str, date_str: str) -> bool:
        """분봉 데이터 존재 여부"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            candle_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM minute_candles WHERE stock_code=%s AND candle_date=%s)",
                (stock_code, candle_date),
            )
            return cur.fetchone()[0]
        except Exception as e:
            self.logger.error(f"[PG] 분봉 존재 확인 실패: {e}")
            return False

    # ============================================================
    # 일봉 데이터 (daily_candles)
    # ============================================================

    def save_daily_candles(self, stock_code: str, df: pd.DataFrame) -> bool:
        """일봉 데이터 저장 (UPSERT)"""
        try:
            if df is None or df.empty:
                return True

            conn = self._get_conn()
            cur = conn.cursor()

            rows = []
            for _, row in df.iterrows():
                # 일봉 컬럼명 호환 (API 원본 / 표준화)
                date_val = row.get("stck_bsop_date", row.get("candle_date", ""))
                if not date_val:
                    continue
                date_val = str(date_val)
                candle_date = f"{date_val[:4]}-{date_val[4:6]}-{date_val[6:8]}"

                open_p = float(row.get("stck_oprc", row.get("open_price", row.get("open", 0))))
                high_p = float(row.get("stck_hgpr", row.get("high_price", row.get("high", 0))))
                low_p = float(row.get("stck_lwpr", row.get("low_price", row.get("low", 0))))
                close_p = float(row.get("stck_clpr", row.get("close_price", row.get("close", 0))))
                vol = int(row.get("acml_vol", row.get("volume", 0)))
                amt = float(row.get("acml_tr_pbmn", row.get("trading_amount", 0)))
                prev_close = float(row.get("prev_close", 0))
                change_rate = float(row.get("prtt_rate", row.get("change_rate", 0)))

                rows.append((
                    stock_code, candle_date,
                    open_p, high_p, low_p, close_p,
                    vol, amt, prev_close, change_rate,
                ))

            if rows:
                psycopg2.extras.execute_batch(
                    cur,
                    """INSERT INTO daily_candles
                       (stock_code, candle_date, open_price, high_price, low_price, close_price,
                        volume, trading_amount, prev_close, change_rate)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (stock_code, candle_date) DO UPDATE SET
                        open_price=EXCLUDED.open_price, high_price=EXCLUDED.high_price,
                        low_price=EXCLUDED.low_price, close_price=EXCLUDED.close_price,
                        volume=EXCLUDED.volume, trading_amount=EXCLUDED.trading_amount,
                        prev_close=EXCLUDED.prev_close, change_rate=EXCLUDED.change_rate""",
                    rows,
                    page_size=500,
                )

            conn.commit()
            self.logger.debug(f"[PG] {stock_code} 일봉 {len(rows)}건 저장")
            return True

        except Exception as e:
            self.logger.error(f"[PG] 일봉 저장 실패 ({stock_code}): {e}")
            if self._conn and not self._conn.closed:
                self._conn.rollback()
            return False

    def get_daily_candles(self, stock_code: str, days: int = 100) -> Optional[pd.DataFrame]:
        """일봉 데이터 조회"""
        try:
            conn = self._get_conn()
            df = pd.read_sql_query(
                """SELECT candle_date, open_price, high_price, low_price, close_price,
                          volume, trading_amount, prev_close, change_rate
                   FROM daily_candles
                   WHERE stock_code = %s
                   ORDER BY candle_date DESC
                   LIMIT %s""",
                conn,
                params=(stock_code, days),
            )

            if df.empty:
                return None

            df["candle_date"] = pd.to_datetime(df["candle_date"])
            self.logger.debug(f"[PG] {stock_code} 일봉 {len(df)}건 조회")
            return df

        except Exception as e:
            self.logger.error(f"[PG] 일봉 조회 실패 ({stock_code}): {e}")
            return None

    def has_daily_candles(self, stock_code: str, date_str: str) -> bool:
        """일봉 데이터 존재 여부"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            candle_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM daily_candles WHERE stock_code=%s AND candle_date=%s)",
                (stock_code, candle_date),
            )
            return cur.fetchone()[0]
        except Exception as e:
            self.logger.error(f"[PG] 일봉 존재 확인 실패: {e}")
            return False

    # ============================================================
    # ORB 레인지 (orb_ranges)
    # ============================================================

    def save_orb_range(self, stock_code: str, stock_name: str,
                       trading_date: str, orb_data: dict) -> bool:
        """ORB 레인지 저장"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()

            td = trading_date
            if len(td) == 8:
                td = f"{td[:4]}-{td[4:6]}-{td[6:8]}"

            cur.execute(
                """INSERT INTO orb_ranges
                   (stock_code, stock_name, trading_date, orb_high, orb_low,
                    range_size, range_ratio, avg_volume, target_price, stop_price,
                    is_valid, calculated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (stock_code, trading_date) DO UPDATE SET
                    orb_high=EXCLUDED.orb_high, orb_low=EXCLUDED.orb_low,
                    range_size=EXCLUDED.range_size, range_ratio=EXCLUDED.range_ratio,
                    avg_volume=EXCLUDED.avg_volume, target_price=EXCLUDED.target_price,
                    stop_price=EXCLUDED.stop_price, is_valid=EXCLUDED.is_valid,
                    calculated_at=EXCLUDED.calculated_at""",
                (
                    stock_code,
                    stock_name,
                    td,
                    orb_data.get("orb_high", 0),
                    orb_data.get("orb_low", 0),
                    orb_data.get("range_size", 0),
                    orb_data.get("range_ratio", 0),
                    orb_data.get("avg_volume", 0),
                    orb_data.get("target_price", 0),
                    orb_data.get("stop_price", 0),
                    orb_data.get("is_valid", True),
                    orb_data.get("calculated_at", now_kst()),
                ),
            )

            conn.commit()
            self.logger.debug(f"[PG] ORB 저장: {stock_code} ({td})")
            return True

        except Exception as e:
            self.logger.error(f"[PG] ORB 저장 실패 ({stock_code}): {e}")
            if self._conn and not self._conn.closed:
                self._conn.rollback()
            return False

    def get_orb_range(self, stock_code: str, trading_date: str) -> Optional[dict]:
        """특정 종목의 ORB 레인지 조회"""
        try:
            conn = self._get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            td = trading_date
            if len(td) == 8:
                td = f"{td[:4]}-{td[4:6]}-{td[6:8]}"

            cur.execute(
                "SELECT * FROM orb_ranges WHERE stock_code=%s AND trading_date=%s",
                (stock_code, td),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        except Exception as e:
            self.logger.error(f"[PG] ORB 조회 실패 ({stock_code}): {e}")
            return None

    def get_orb_ranges_by_date(self, trading_date: str) -> pd.DataFrame:
        """날짜별 전체 ORB 레인지 조회"""
        try:
            conn = self._get_conn()
            td = trading_date
            if len(td) == 8:
                td = f"{td[:4]}-{td[4:6]}-{td[6:8]}"

            df = pd.read_sql_query(
                "SELECT * FROM orb_ranges WHERE trading_date=%s ORDER BY stock_code",
                conn,
                params=(td,),
            )
            return df

        except Exception as e:
            self.logger.error(f"[PG] ORB 날짜 조회 실패: {e}")
            return pd.DataFrame()

    # ============================================================
    # 일일 거래 요약 (daily_trading_summary)
    # ============================================================

    def save_daily_summary(self, trading_date: str, stats: dict) -> bool:
        """일일 거래 요약 저장/갱신"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()

            td = trading_date
            if len(td) == 8:
                td = f"{td[:4]}-{td[4:6]}-{td[6:8]}"

            cur.execute(
                """INSERT INTO daily_trading_summary
                   (trading_date, candidate_count, orb_valid_count,
                    total_buy_count, total_sell_count,
                    win_count, loss_count, win_rate, realized_pnl,
                    starting_capital, ending_capital, is_virtual, notes, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
                   ON CONFLICT (trading_date) DO UPDATE SET
                    candidate_count=EXCLUDED.candidate_count,
                    orb_valid_count=EXCLUDED.orb_valid_count,
                    total_buy_count=EXCLUDED.total_buy_count,
                    total_sell_count=EXCLUDED.total_sell_count,
                    win_count=EXCLUDED.win_count,
                    loss_count=EXCLUDED.loss_count,
                    win_rate=EXCLUDED.win_rate,
                    realized_pnl=EXCLUDED.realized_pnl,
                    starting_capital=EXCLUDED.starting_capital,
                    ending_capital=EXCLUDED.ending_capital,
                    is_virtual=EXCLUDED.is_virtual,
                    notes=EXCLUDED.notes,
                    updated_at=CURRENT_TIMESTAMP""",
                (
                    td,
                    stats.get("candidate_count", 0),
                    stats.get("orb_valid_count", 0),
                    stats.get("total_buy_count", 0),
                    stats.get("total_sell_count", 0),
                    stats.get("win_count", 0),
                    stats.get("loss_count", 0),
                    stats.get("win_rate", 0),
                    stats.get("realized_pnl", 0),
                    stats.get("starting_capital", 0),
                    stats.get("ending_capital", 0),
                    stats.get("is_virtual", True),
                    stats.get("notes", ""),
                ),
            )

            conn.commit()
            self.logger.debug(f"[PG] 일일 요약 저장: {td}")
            return True

        except Exception as e:
            self.logger.error(f"[PG] 일일 요약 저장 실패: {e}")
            if self._conn and not self._conn.closed:
                self._conn.rollback()
            return False

    def get_daily_summary(self, trading_date: str) -> Optional[dict]:
        """일일 거래 요약 조회"""
        try:
            conn = self._get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            td = trading_date
            if len(td) == 8:
                td = f"{td[:4]}-{td[4:6]}-{td[6:8]}"

            cur.execute(
                "SELECT * FROM daily_trading_summary WHERE trading_date=%s", (td,)
            )
            row = cur.fetchone()
            return dict(row) if row else None

        except Exception as e:
            self.logger.error(f"[PG] 일일 요약 조회 실패: {e}")
            return None

    def get_summary_history(self, days: int = 30) -> pd.DataFrame:
        """거래 요약 히스토리 조회"""
        try:
            conn = self._get_conn()
            cutoff = (now_kst() - timedelta(days=days)).strftime("%Y-%m-%d")

            df = pd.read_sql_query(
                """SELECT * FROM daily_trading_summary
                   WHERE trading_date >= %s
                   ORDER BY trading_date DESC""",
                conn,
                params=(cutoff,),
            )
            return df

        except Exception as e:
            self.logger.error(f"[PG] 요약 히스토리 조회 실패: {e}")
            return pd.DataFrame()

    # ============================================================
    # 유틸리티
    # ============================================================

    def cleanup_old_data(self, keep_days: int = 90):
        """오래된 데이터 정리"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cutoff = (now_kst() - timedelta(days=keep_days)).strftime("%Y-%m-%d")

            cur.execute("DELETE FROM minute_candles WHERE candle_date < %s", (cutoff,))
            minute_del = cur.rowcount
            cur.execute("DELETE FROM daily_candles WHERE candle_date < %s", (cutoff,))
            daily_del = cur.rowcount

            conn.commit()
            self.logger.info(
                f"[PG] 데이터 정리: 분봉 {minute_del}건, 일봉 {daily_del}건 삭제 ({keep_days}일 이전)"
            )

        except Exception as e:
            self.logger.error(f"[PG] 데이터 정리 실패: {e}")
            if self._conn and not self._conn.closed:
                self._conn.rollback()

    def get_stats(self) -> Dict[str, int]:
        """테이블별 레코드 수 조회"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            stats = {}
            for table in ["minute_candles", "daily_candles", "orb_ranges", "daily_trading_summary"]:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                stats[table] = cur.fetchone()[0]
            return stats
        except Exception as e:
            self.logger.error(f"[PG] 통계 조회 실패: {e}")
            return {}
