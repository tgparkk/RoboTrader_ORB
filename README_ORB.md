# RoboTrader_orb - ORB 전략 버전

ORB (Opening Range Breakout) 전략이 구현된 RoboTrader_orb 자동매매 시스템입니다.

---

## 빠른 시작

### 1. Universe 준비 (주간 1회)

매주 금요일 장마감 후 또는 주말에 실행:

```bash
python scripts/update_weekly_universe.py
```

결과 확인:
```bash
ls data/universe_*.json
# data/universe_20260103.json
```

### 2. 테스트 실행

```bash
python tests/test_orb_strategy.py
```

예상 출력:
```
============================================================
ORB 전략 테스트 시작
============================================================

[TEST 1] Universe 로드 테스트
[OK] Universe 로드 성공: 300개 종목

[TEST 2] ATR 계산 테스트
[OK] ATR 계산 성공: 2,250원

[TEST 3] ORB 레인지 계산 테스트
[OK] ORB 레인지 계산 성공

[TEST 4] 후보 종목 평가 테스트
[OK] 후보 종목 평가 성공

============================================================
[SUCCESS] 모든 테스트 통과!
```

### 3. 전략 설정 (옵션)

설정 파일: [`config/orb_strategy_config.py`](config/orb_strategy_config.py)

주요 파라미터:
- `min_gap_ratio`: 최소 갭 비율 (기본 0.3%)
- `max_gap_ratio`: 최대 갭 비율 (기본 3%)
- `orb_end_time`: ORB 종료 시간 (기본 09:10)
- `volume_surge_ratio`: 거래량 배수 (기본 1.5배)
- `take_profit_multiplier`: 익절 배수 (기본 2배)

---

## 전략 개요

### ORB (Opening Range Breakout) 전략이란?

장 초반 10분간(09:00~09:10) 형성된 가격 범위를 기준으로, 이를 돌파하는 순간 매수하는 전략입니다.

### 핵심 아이디어

1. **갭 상승 종목 선정**: 전일 종가 대비 0.3~3% 상승한 종목
2. **ORB 레인지**: 09:00~09:10 구간 고가/저가
3. **돌파 매수**: ORB 고가 돌파 + 거래량 1.5배 이상
4. **목표가**: ORB 고가 + (레인지 × 2)
5. **손절가**: ORB 저가

### 시간대별 작업

| 시간 | 작업 | 설명 |
|------|------|------|
| 08:30~08:50 | 후보 종목 선정 | Universe 300개 중 갭상승 종목 필터링 |
| 09:00~09:10 | ORB 레인지 계산 | 1분봉으로 고가/저가 수집 |
| 09:10~14:50 | 매수 신호 감지 | 3분봉으로 돌파 여부 확인 |
| 09:10~15:00 | 매도 신호 감지 | 손절/익절 조건 체크 |
| 15:00 | 장마감 청산 | 보유 포지션 일괄 청산 |

---

## 프로젝트 구조

```
RoboTrader_orb/
│
├── config/
│   └── orb_strategy_config.py      # ORB 전략 설정
│
├── strategies/
│   ├── trading_strategy.py         # 통합 전략 인터페이스
│   ├── orb_strategy.py             # ORB 전략 구현
│   └── strategy_factory.py         # 전략 팩토리
│
├── scripts/
│   └── update_weekly_universe.py   # Universe 업데이트
│
├── tests/
│   └── test_orb_strategy.py        # ORB 전략 테스트
│
├── data/
│   └── universe_YYYYMMDD.json      # 종목 유니버스
│
├── docs/
│   ├── orb_strategy_guide.md       # ORB 전략 상세 가이드
│   └── universe_management.md      # Universe 관리 가이드
│
└── README_ORB.md                   # 이 문서
```

---

## 주요 기능

### 1. 통합 전략 인터페이스

ORB 전략은 후보 종목 선정과 매매 판단을 하나의 전략 클래스에서 처리합니다.

```python
class ORBStrategy(TradingStrategy):
    """
    ORB 전략 구현

    - select_daily_candidates(): 후보 종목 선정 (08:30~08:50)
    - calculate_orb_range(): ORB 레인지 계산 (09:00~09:10)
    - generate_buy_signal(): 매수 신호 생성 (09:10~14:50)
    - generate_sell_signal(): 매도 신호 생성 (포지션 보유 중)
    """
```

### 2. Universe 관리

매주 네이버 금융에서 시가총액 상위 종목 크롤링:
- KOSPI 상위 200개
- KOSDAQ 상위 100개
- 총 300개 종목

### 3. 데이터 분봉 전략

- **1분봉**: ORB 레인지 계산 (정확성)
- **3분봉**: 매매 신호 판단 (노이즈 감소)

---

## 설정 커스터마이징

### 갭 범위 조정

더 공격적인 전략 (큰 갭):
```python
min_gap_ratio: float = 0.005  # 0.5%
max_gap_ratio: float = 0.05   # 5%
```

보수적인 전략 (작은 갭):
```python
min_gap_ratio: float = 0.002  # 0.2%
max_gap_ratio: float = 0.02   # 2%
```

### ORB 시간 변경

더 긴 ORB 구간 (15분):
```python
orb_end_time: str = "09:15"
```

더 짧은 ORB 구간 (5분):
```python
orb_end_time: str = "09:05"
```

### 익절 배수 조정

공격적 익절 (1배):
```python
take_profit_multiplier: float = 1.0
```

보수적 익절 (3배):
```python
take_profit_multiplier: float = 3.0
```

---

## 예시: 하루 거래

### 종목: 삼성전자 (005930)

```
[08:40] 후보 선정
전일 종가: 70,000원
현재가: 70,500원
갭: +0.71%
→ 후보 선정 ✓

[09:10] ORB 레인지 완성
ORB 고가: 70,800원
ORB 저가: 70,300원
목표가: 71,800원 (70,800 + 500×2)
손절가: 70,300원

[09:42] 매수
종가: 70,850원 (돌파 ✓)
거래량: 1.6배 ✓
→ 매수 주문 체결

[10:15] 익절
종가: 71,850원 (목표가 도달 ✓)
→ 매도 주문 체결
수익: +1,000원 (+1.41%)
```

---

## 테스트 결과

테스트 실행:
```bash
python tests/test_orb_strategy.py
```

테스트 항목:
1. ✅ Universe 로드 (300개 종목)
2. ✅ ATR 계산 (14일 평균)
3. ✅ ORB 레인지 계산 (1분봉 10개)
4. ✅ 후보 종목 평가 (갭/거래대금/ATR)

---

## 문서

- **[ORB 전략 가이드](docs/orb_strategy_guide.md)**: 전략 상세 설명
- **[Universe 관리 가이드](docs/universe_management.md)**: Universe 업데이트 방법
- **[시스템 가이드](CLAUDE.md)**: 전체 시스템 구조 및 사용법

---

## 주의사항

### 데이터 타이밍
- **ORB 계산**: 반드시 1분봉 사용
- **매매 신호**: 3분봉 사용 (노이즈 감소)
- **캔들 완성 시간**: 라벨 + 봉 간격 (예: 09:42 → 09:45:00)

### Universe 관리
- **업데이트 주기**: 매주 1회 (금요일 또는 주말)
- **파일 확인**: `data/universe_YYYYMMDD.json` 존재 여부
- **수동 대응**: 크롤링 실패 시 수동 실행

### 리스크 관리
- **손절**: ORB 저가 (명확한 기준)
- **익절**: ORB 고가 + (range × 2)
- **시간**: 15:00 무조건 청산

---

## 라이선스

본 프로젝트는 교육 및 개인 투자 목적으로 작성되었습니다.
실전 투자 시 발생하는 손실에 대해 책임지지 않습니다.

---

## 참고 자료

- [GitHub Issue #37](https://github.com/tgparkk/RoboTrader/issues/37): ORB 전략 원본 스펙
- [네이버 금융](https://finance.naver.com/sise/sise_market_sum.naver): Universe 데이터 소스
