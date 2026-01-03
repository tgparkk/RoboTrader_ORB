"""
ORB 전략 테스트 스크립트

이 스크립트는 ORB 전략의 핵심 기능을 테스트합니다:
1. Universe 로드
2. 후보 종목 선정
3. ORB 레인지 계산
4. ATR 계산
"""

import sys
from pathlib import Path

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import asyncio
import pandas as pd
from datetime import datetime
from utils.logger import setup_logger
from strategies.orb_strategy import ORBStrategy
from config.orb_strategy_config import DEFAULT_ORB_CONFIG
from scripts.update_weekly_universe import load_latest_universe


logger = setup_logger(__name__)


def test_universe_load():
    """Universe 로드 테스트"""
    print("\n" + "=" * 60)
    print("[TEST 1] Universe 로드 테스트")
    print("=" * 60)

    try:
        universe = load_latest_universe()

        if universe.empty:
            print("[ERROR] Universe 로드 실패: 데이터 없음")
            return False

        print(f"[OK] Universe 로드 성공: {len(universe)}개 종목")
        print(f"  - KOSPI: {len(universe[universe['market']=='KOSPI'])}개")
        print(f"  - KOSDAQ: {len(universe[universe['market']=='KOSDAQ'])}개")

        # 샘플 출력
        print("\n[샘플 종목 5개]")
        print(universe.head(5)[['code', 'name', 'market', 'market_cap', 'rank']])

        return True

    except Exception as e:
        print(f"[ERROR] Universe 로드 실패: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_atr_calculation():
    """ATR 계산 테스트"""
    print("\n" + "=" * 60)
    print("[TEST 2] ATR 계산 테스트")
    print("=" * 60)

    try:
        strategy = ORBStrategy(config=DEFAULT_ORB_CONFIG, logger=logger)

        # 샘플 일봉 데이터 생성
        sample_data = pd.DataFrame({
            'stck_hgpr': [51000, 52000, 51500, 53000, 52500, 54000, 53500, 55000, 54500, 56000,
                         55500, 57000, 56500, 58000, 57500, 59000],
            'stck_lwpr': [49000, 50000, 49500, 51000, 50500, 52000, 51500, 53000, 52500, 54000,
                         53500, 55000, 54500, 56000, 55500, 57000],
            'stck_clpr': [50000, 51000, 50500, 52000, 51500, 53000, 52500, 54000, 53500, 55000,
                         54500, 56000, 55500, 57000, 56500, 58000]
        })

        atr = strategy._calculate_atr(sample_data, period=14)

        print(f"[OK] ATR 계산 성공: {atr:,.0f}원")
        print(f"  - 데이터 개수: {len(sample_data)}일")
        print(f"  - 계산 기간: 14일")

        # ATR 유효성 검증
        last_close = float(sample_data.iloc[-1]['stck_clpr'])
        atr_ratio = atr / last_close

        print(f"  - 종가 대비 비율: {atr_ratio:.2%}")

        if atr > 0 and atr_ratio < 0.1:
            print("  [OK] ATR 유효성 검증 통과")
            return True
        else:
            print("  [ERROR] ATR 유효성 검증 실패")
            return False

    except Exception as e:
        print(f"[ERROR] ATR 계산 실패: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_orb_range_calculation():
    """ORB 레인지 계산 테스트"""
    print("\n" + "=" * 60)
    print("[TEST 3] ORB 레인지 계산 테스트")
    print("=" * 60)

    try:
        strategy = ORBStrategy(config=DEFAULT_ORB_CONFIG, logger=logger)

        # 샘플 1분봉 데이터 생성 (09:00~09:10, 10개 캔들)
        sample_1min_data = pd.DataFrame({
            'stck_hgpr': [50200, 50300, 50400, 50500, 50600, 50700, 50800, 50700, 50600, 50500],
            'stck_lwpr': [49800, 49900, 50000, 50100, 50200, 50300, 50400, 50300, 50200, 50100],
            'acml_vol': [10000, 12000, 11000, 13000, 11500, 12500, 14000, 13500, 12000, 11000]
        })

        # 비동기 함수 실행
        async def run_test():
            result = await strategy.calculate_orb_range(
                code='005930',
                minute_1_data=sample_1min_data
            )
            return result

        result = asyncio.run(run_test())

        if result:
            orb = strategy.orb_data['005930']
            print(f"[OK] ORB 레인지 계산 성공")
            print(f"  - ORB 고가: {orb['high']:,.0f}원")
            print(f"  - ORB 저가: {orb['low']:,.0f}원")
            print(f"  - 레인지 크기: {orb['range_size']:,.0f}원")
            print(f"  - 레인지 비율: {orb['range_ratio']:.2%}")
            print(f"  - 평균 거래량: {orb['avg_volume']:,.0f}주")

            # 목표가 계산
            take_profit = orb['high'] + (orb['range_size'] * 2)
            print(f"  - 손절가: {orb['low']:,.0f}원")
            print(f"  - 목표가: {take_profit:,.0f}원")

            return True
        else:
            print("[ERROR] ORB 레인지 계산 실패")
            return False

    except Exception as e:
        print(f"[ERROR] ORB 레인지 계산 실패: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_candidate_evaluation():
    """후보 종목 평가 테스트 (모의 데이터)"""
    print("\n" + "=" * 60)
    print("[TEST 4] 후보 종목 평가 테스트")
    print("=" * 60)

    try:
        strategy = ORBStrategy(config=DEFAULT_ORB_CONFIG, logger=logger)

        # 샘플 현재가 데이터 (모의)
        class MockPriceData:
            current_price = 70500
            volume = 5000000
            volume_amount = 0  # 계산됨

        # 샘플 일봉 데이터
        sample_daily_data = pd.DataFrame({
            'stck_clpr': [70000] * 15 + [70000, 69800, 69900, 70100, 70200,
                          70300, 70400, 70100, 70200, 70300, 70500, 70400,
                          70600, 70700, 70000],  # 30일
            'stck_hgpr': [71000] * 15 + [71000, 70800, 70900, 71100, 71200,
                          71300, 71400, 71100, 71200, 71300, 71500, 71400,
                          71600, 71700, 71000],
            'stck_lwpr': [69000] * 15 + [69000, 68800, 68900, 69100, 69200,
                          69300, 69400, 69100, 69200, 69300, 69500, 69400,
                          69600, 69700, 69000],
            'acml_vol': [3000000] * 30
        })

        # 비동기 함수 실행
        async def run_test():
            candidate = await strategy._evaluate_candidate(
                code='005930',
                name='삼성전자',
                market='KOSPI',
                price_data=MockPriceData(),
                daily_data=sample_daily_data
            )
            return candidate

        candidate = asyncio.run(run_test())

        if candidate:
            print(f"[OK] 후보 종목 평가 성공")
            print(f"  - 종목: {candidate.name} ({candidate.code})")
            print(f"  - 시장: {candidate.market}")
            print(f"  - 점수: {candidate.score}점")
            print(f"  - 이유: {candidate.reason}")
            print(f"  - 전일 종가: {candidate.prev_close:,.0f}원")
            print(f"  - 메타데이터:")
            for key, value in candidate.metadata.items():
                if isinstance(value, float):
                    if key == 'gap_ratio':
                        print(f"      {key}: {value:+.2%}")
                    else:
                        print(f"      {key}: {value:,.2f}")
                else:
                    print(f"      {key}: {value}")

            return True
        else:
            print("[ERROR] 후보 종목 평가 실패 (조건 미충족)")
            return False

    except Exception as e:
        print(f"[ERROR] 후보 종목 평가 실패: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """전체 테스트 실행"""
    print("\n" + "=" * 60)
    print("ORB 전략 테스트 시작")
    print("=" * 60)
    print(f"실행 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    results = []

    # 테스트 1: Universe 로드
    results.append(("Universe 로드", test_universe_load()))

    # 테스트 2: ATR 계산
    results.append(("ATR 계산", test_atr_calculation()))

    # 테스트 3: ORB 레인지 계산
    results.append(("ORB 레인지 계산", test_orb_range_calculation()))

    # 테스트 4: 후보 종목 평가
    results.append(("후보 종목 평가", test_candidate_evaluation()))

    # 결과 요약
    print("\n" + "=" * 60)
    print("테스트 결과 요약")
    print("=" * 60)

    for test_name, result in results:
        status = "[OK] 통과" if result else "[ERROR] 실패"
        print(f"{test_name:20s}: {status}")

    total_tests = len(results)
    passed_tests = sum(1 for _, result in results if result)

    print("\n" + "-" * 60)
    print(f"전체: {total_tests}개 테스트 중 {passed_tests}개 통과 ({passed_tests/total_tests*100:.1f}%)")
    print("=" * 60)

    if passed_tests == total_tests:
        print("\n[SUCCESS] 모든 테스트 통과!")
        return 0
    else:
        print(f"\n[WARNING] {total_tests - passed_tests}개 테스트 실패")
        return 1


if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
