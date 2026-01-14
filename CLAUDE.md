# RoboTrader_orb 시스템 가이드

이 문서는 RoboTrader_orb 시스템 사용 및 운영 방법을 설명합니다.

---

## 시스템 개요

**RoboTrader_orb**는 ORB (Opening Range Breakout) 전략을 구현한 자동매매 시스템입니다.

### 핵심 컴포넌트
- **Universe 관리**: 주간 시가총액 상위 300개 종목 (KOSPI 200 + KOSDAQ 100)
- **후보 종목 선정**: 장전(08:30~08:50) 갭상승 종목 필터링
- **ORB 레인지 계산**: 09:00~09:10 구간 고가/저가 수집
- **매매 판단**: ORB 돌파 여부 + 거래량 확인
- **주문 관리**: 주문 실행, 체결 확인, 정정/취소
- **리스크 관리**: 손절(ORB 저가), 익절(ORB 고가 + range×2)
- **모니터링**: 텔레그램 알림, 로깅

---

## 일일 운영 프로세스

### 1. 장전 준비 (07:00~08:30)

#### 시스템 시작
```bash
python main.py
```

#### 자동 실행 작업
- API 인증 초기화
- DB에서 전일 후보 종목 복원 (있는 경우)
- 텔레그램 연결

### 2. 장전 후보 종목 선정 (08:30~08:50)

**자동 실행**: 시스템이 08:30에 자동으로 Universe에서 후보 종목 선정

#### 선정 기준
- Universe 300개 종목 대상
- 갭 상승: 전일 종가 대비 0.3~3% 상승
- 거래대금: 100억 이상 (전일 기준)
- ATR 유효성: 14일 ATR 계산 가능

#### 결과 저장
- DB `candidate_stocks` 테이블에 저장
- 로그: `logs/trading_YYYYMMDD.log`

### 3. 장 시작 (09:00~09:10)

#### ORB 레인지 계산
- **데이터**: 1분봉 (09:00~09:10)
- **계산**: 10분 구간 고가/저가
- **검증**: 레인지가 가격의 0.3~2% 범위인지 확인

#### 목표가/손절가 설정
```
목표가 = ORB 고가 + (레인지 크기 × 2)
손절가 = ORB 저가
```

### 4. 장 중 매매 (09:10~15:00)

#### 매수 조건
- ORB 고가 돌파 (3분봉 종가 기준)
- 거래량 1.5배 이상 (ORB 구간 평균 대비)
- 3분봉 완성 + 10초 시점에 판단

#### 매도 조건
- **익절**: 목표가 도달
- **손절**: ORB 저가 이탈
- **시간**: 15:00 장마감 일괄 청산

### 5. 장 마감 (15:00~)

#### 자동 실행 작업
- 보유 포지션 일괄 청산
- 당일 거래 데이터 DB 저장
- 텔레그램 일일 리포트 발송

---

## 주간 작업

### Universe 업데이트 (매주 금요일 또는 주말)

```bash
python scripts/update_weekly_universe.py
```

**결과 파일**: `data/universe_YYYYMMDD.json`

#### 업데이트 내용
- KOSPI 시가총액 상위 200개
- KOSDAQ 시가총액 상위 100개
- 네이버 금융 크롤링 기반

---

## 로그 분석

### 로그 파일 위치
- **거래 로그**: `logs/trading_YYYYMMDD.log`
- **패턴 데이터**: `pattern_data_log/pattern_data_YYYYMMDD.jsonl` (활성화 시)
- **캐시 데이터**: `cache/minute_data/종목코드_YYYYMMDD.pkl`

### 주요 분석 명령어

#### 후보 종목 선정 확인
```bash
grep "후보 선정" logs/trading_20260114.log
grep "갭" logs/trading_20260114.log | head -20
```

#### ORB 레인지 계산 확인
```bash
grep "ORB 레인지 계산" logs/trading_20260114.log
grep "목표가\|손절가" logs/trading_20260114.log
```

#### 매수 신호 확인
```bash
grep "매수 신호 발생" logs/trading_20260114.log
grep "ORB 고가 돌파" logs/trading_20260114.log
```

#### 체결 확인
```bash
grep "체결" logs/trading_20260114.log
grep "수익률" logs/trading_20260114.log
```

---

## 핵심 개념

### 타이밍

#### 분봉 완성 시간
- **1분봉**: 라벨 시간 + 1분 (예: 09:00 캔들 → 09:01:00 완성)
- **3분봉**: 라벨 시간 + 3분 (예: 09:00 캔들 → 09:03:00 완성)
- **판단 시점**: 캔들 완성 + 10초 (예: 09:03:10)

#### 중요 시간대
- **08:30**: 후보 종목 선정 시작
- **09:00**: 장 시작, ORB 레인지 수집 시작
- **09:10**: ORB 레인지 완성, 매수 신호 감지 시작
- **15:00**: 장마감 일괄 청산

### 데이터 소스

#### 실시간 데이터
- KIS API를 통한 실시간 시세 수신
- 30초마다 업데이트
- 1분봉 → 3분봉 변환 (floor 방식)

#### 캐시 데이터
- 장 마감 후 확정 데이터 저장
- 시뮬레이션/분석 용도
- 위치: `cache/minute_data/`

### ORB 전략 파라미터

#### 기본 설정 (`config/orb_strategy_config.py`)
```python
min_gap_ratio = 0.003        # 최소 갭 0.3%
max_gap_ratio = 0.03         # 최대 갭 3%
orb_end_time = "09:10"       # ORB 종료 시간
volume_surge_ratio = 1.5     # 거래량 배수
take_profit_multiplier = 2.0 # 익절 배수
stop_loss_ratio = 1.0        # 손절 (ORB 저가)
```

---

## 전략 구현 위치

### 후보 종목 선정
- **파일**: `strategies/orb_strategy.py`
- **함수**: `select_daily_candidates()`
- **시간**: 08:30~08:50

### ORB 레인지 계산
- **파일**: `strategies/orb_strategy.py`
- **함수**: `calculate_orb_range()`
- **시간**: 09:10 (09:00~09:10 데이터 사용)

### 매수 신호 생성
- **파일**: `strategies/orb_strategy.py`
- **함수**: `generate_buy_signal()`
- **데이터**: 3분봉 + ORB 레인지

### 매도 신호 생성
- **파일**: `strategies/orb_strategy.py`
- **함수**: `generate_sell_signal()`
- **조건**: 익절/손절/시간

---

## 설정 파일

### 거래 설정
- **파일**: `config/trading_config.json`
- **항목**:
  - `risk_management.use_virtual_trading`: 가상거래 모드
  - `risk_management.max_position_count`: 최대 보유 종목 수
  - `risk_management.stop_loss_ratio`: 손절 비율
  - `risk_management.take_profit_ratio`: 익절 비율

### ORB 전략 설정
- **파일**: `config/orb_strategy_config.py`
- **항목**: 갭 범위, ORB 시간, 거래량 배수 등

### API 인증
- **파일**: `config/key.ini`
- **항목**: KIS API 키, 앱키, 계좌번호

---

## 데이터 처리

### 분봉 데이터 수집

#### 초기 수집 (종목 선정 시)
- **시점**: 후보 종목이 선정된 시각
- **범위**: 09:00부터 선정 시각까지
- **API**: `get_full_trading_day_data_async()`

#### 실시간 업데이트 (장중)
- **주기**: 30초마다
- **방식**: 증분 업데이트
- **검증**: 전날 데이터 자동 제거

### 3분봉 변환

#### Floor 방식
```python
# 09:00, 09:01, 09:02 → 09:00 캔들 (완성: 09:03:00)
# 09:03, 09:04, 09:05 → 09:03 캔들 (완성: 09:06:00)
df['floor_3min'] = df.index.floor('3min')
```

#### 완성 시점
- **현재 진행 중인 캔들 제외**
- **완성된 캔들만 사용**
- **판단 시점**: 완성 + 10초

---

## 가상거래 모드

### 설정
```json
{
  "risk_management": {
    "use_virtual_trading": true
  }
}
```

### 자금 관리
- **초기 자금**: 1,000만원 (고정)
- **종목당 투자**: 100만원
- **잔고 추적**: DB에 저장

### 체결 처리
- **매수**: 가상 잔고 차감, DB 기록
- **매도**: 가상 잔고 증가, 손익 계산
- **수익률**: 실시간 계산 및 로그 출력

---

## 문제 해결

### 후보 종목이 없음
```
📊 오늘(2026-01-14) 후보 종목 없음
```
**원인**: 갭 상승 조건을 만족하는 종목이 없음
**확인**: Universe 파일 존재 여부, API 응답 확인

### 3분봉 데이터 부족
```
⚠️ 3분봉 데이터 부족 위험: 0/5
```
**원인**: 장 초반(09:00~09:15) 데이터 미확보
**해결**: 최소 요구량 완화 또는 선정 시간 조정

### 전날 데이터 감지
```
🚨 실시간 업데이트에서 전날 데이터 24건 감지 및 제거
```
**상태**: 정상 작동 (자동 필터링)
**영향**: 없음

---

## 추가 문서

- **[README_ORB.md](README_ORB.md)**: ORB 전략 빠른 시작 가이드
- **[docs/orb_strategy_guide.md](docs/orb_strategy_guide.md)**: ORB 전략 상세 가이드
- **[docs/stock_state_management.md](docs/stock_state_management.md)**: 종목 상태 관리
- **[docs/universe_management.md](docs/universe_management.md)**: Universe 관리 가이드

---

## 주의사항

### 데이터 타이밍
- ORB 레인지는 반드시 **1분봉** 사용
- 매매 신호는 **3분봉** 사용 (노이즈 감소)
- 캔들 완성 시간 정확히 준수

### Universe 관리
- **업데이트 주기**: 매주 1회 필수
- **파일 확인**: `data/universe_YYYYMMDD.json` 존재 확인
- **크롤링 실패**: 수동 재실행

### 리스크 관리
- **손절**: ORB 저가에서 명확히 실행
- **익절**: 욕심 부리지 않고 목표가 도달 시 청산
- **시간 청산**: 15:00 일괄 청산 (예외 없음)

### 실전 투자 주의
- 가상거래로 충분히 테스트 후 실전 투자
- 소액으로 시작하여 점진적 확대
- 시장 상황에 따라 전략 파라미터 조정
- 손실 감내 범위 내에서만 투자

---

## 라이선스

본 프로젝트는 교육 및 개인 투자 목적으로 작성되었습니다.
실전 투자 시 발생하는 손실에 대해 책임지지 않습니다.
