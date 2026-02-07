"""
기존 pkl 캐시 파일을 PostgreSQL로 마이그레이션하는 스크립트

사용법:
    python scripts/migrate_pkl_to_postgres.py              # 전체 마이그레이션
    python scripts/migrate_pkl_to_postgres.py --minute      # 분봉만
    python scripts/migrate_pkl_to_postgres.py --daily       # 일봉만
    python scripts/migrate_pkl_to_postgres.py --dry-run     # 실제 저장 없이 확인만
"""
import sys
import os
import pickle
import time
import argparse
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.postgres_manager import PostgresManager


def migrate_minute_data(pg: PostgresManager, dry_run: bool = False) -> dict:
    """분봉 pkl 파일 → PostgreSQL 마이그레이션"""
    cache_dir = Path("cache/minute_data")
    stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0}

    pkl_files = sorted(cache_dir.glob("*.pkl"))
    stats["total"] = len(pkl_files)
    print(f"\n분봉 마이그레이션 시작: {stats['total']}개 파일")

    start_time = time.time()
    for i, pkl_file in enumerate(pkl_files, 1):
        parts = pkl_file.stem.split("_")
        if len(parts) != 2:
            stats["failed"] += 1
            continue

        stock_code, date_str = parts

        # 이미 DB에 있으면 스킵
        if not dry_run and pg.has_minute_candles(stock_code, date_str):
            stats["skipped"] += 1
            continue

        try:
            with open(pkl_file, "rb") as f:
                df = pickle.load(f)

            if not dry_run:
                result = pg.save_minute_candles(stock_code, date_str, df)
                if result:
                    stats["success"] += 1
                else:
                    stats["failed"] += 1
            else:
                stats["success"] += 1

        except Exception as e:
            print(f"  [FAIL] {pkl_file.name}: {e}")
            stats["failed"] += 1

        if i % 100 == 0:
            elapsed = time.time() - start_time
            rate = i / elapsed if elapsed > 0 else 0
            print(f"  진행: {i}/{stats['total']} ({rate:.1f} files/sec)")

    elapsed = time.time() - start_time
    print(f"분봉 완료: {elapsed:.1f}초, 성공={stats['success']}, 스킵={stats['skipped']}, 실패={stats['failed']}")
    return stats


def migrate_daily_data(pg: PostgresManager, dry_run: bool = False) -> dict:
    """일봉 pkl 파일 → PostgreSQL 마이그레이션"""
    cache_dir = Path("cache/daily")
    stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0}

    pkl_files = sorted(cache_dir.glob("*_daily.pkl"))
    stats["total"] = len(pkl_files)
    print(f"\n일봉 마이그레이션 시작: {stats['total']}개 파일")

    start_time = time.time()
    for i, pkl_file in enumerate(pkl_files, 1):
        stem = pkl_file.stem.replace("_daily", "")
        parts = stem.split("_")
        if len(parts) != 2:
            stats["failed"] += 1
            continue

        stock_code, date_str = parts

        # 이미 DB에 있으면 스킵
        if not dry_run and pg.has_daily_candles(stock_code, date_str):
            stats["skipped"] += 1
            continue

        try:
            with open(pkl_file, "rb") as f:
                df = pickle.load(f)

            if not dry_run:
                result = pg.save_daily_candles(stock_code, df)
                if result:
                    stats["success"] += 1
                else:
                    stats["failed"] += 1
            else:
                stats["success"] += 1

        except Exception as e:
            print(f"  [FAIL] {pkl_file.name}: {e}")
            stats["failed"] += 1

        if i % 100 == 0:
            elapsed = time.time() - start_time
            rate = i / elapsed if elapsed > 0 else 0
            print(f"  진행: {i}/{stats['total']} ({rate:.1f} files/sec)")

    elapsed = time.time() - start_time
    print(f"일봉 완료: {elapsed:.1f}초, 성공={stats['success']}, 스킵={stats['skipped']}, 실패={stats['failed']}")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="pkl → PostgreSQL 마이그레이션")
    parser.add_argument("--minute", action="store_true", help="분봉 데이터만")
    parser.add_argument("--daily", action="store_true", help="일봉 데이터만")
    parser.add_argument("--dry-run", action="store_true", help="실제 저장 없이 확인만")
    args = parser.parse_args()

    # 둘 다 지정 안 하면 전체 실행
    do_minute = args.minute or not (args.minute or args.daily)
    do_daily = args.daily or not (args.minute or args.daily)

    print("=" * 60)
    print("pkl → PostgreSQL 마이그레이션")
    if args.dry_run:
        print("  [DRY RUN] 실제 저장 없음")
    print("=" * 60)

    pg = PostgresManager()

    if do_minute:
        migrate_minute_data(pg, dry_run=args.dry_run)

    if do_daily:
        migrate_daily_data(pg, dry_run=args.dry_run)

    # 최종 통계
    print("\n" + "=" * 60)
    stats = pg.get_stats()
    print("PostgreSQL 최종 통계:")
    for table, count in stats.items():
        print(f"  {table}: {count:,}건")
    print("=" * 60)

    pg.close()
