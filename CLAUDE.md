# RoboTrader_orb 시스템 가이드

이 문서는 RoboTrader_orb 시스템 사용 및 분석 방법을 설명합니다.

---

## 시스템 구조

RoboTrader_orb는 전략 독립적인 자동매매 시스템 템플릿입니다.

### 핵심 컴포넌트
- **데이터 수집**: 실시간 시세 데이터 수집 및 관리
- **종목 선정**: 후보 종목 스크리닝 및 필터링
- **매매 판단**: 사용자 정의 전략 기반 매매 신호 생성
- **주문 관리**: 주문 실행, 체결 확인, 정정/취소
- **리스크 관리**: 손절/익절, 포지션 관리
- **모니터링**: 텔레그램 알림, 로깅

---

## 실시간 거래 분석

### 로그 파일 위치
- **거래 로그**: `logs/trading_YYYYMMDD.log`
- **패턴 데이터**: `pattern_data_log/pattern_data_YYYYMMDD.jsonl` (전략별로 활성화 가능)

### 주요 분석 명령어
```bash
# 종목 선정 시점 확인
grep "종목코드" logs/trading_YYYYMMDD.log | grep "선정 완료"

# 매수 신호 발생 확인
grep "종목코드" logs/trading_YYYYMMDD.log | grep "매수 신호 발생"

# ML 필터 결과 확인 (ML 사용 시)
grep "종목코드" logs/trading_YYYYMMDD.log | grep "ML 필터"
```

---

## 시뮬레이션 분석 (전략별 구현 필요)

### 시뮬레이션 로그
- **순수 신호 로그**: `signal_replay_log/signal_new2_replay_YYYYMMDD_9_00_0.txt`
- **ML 필터 적용 로그**: `signal_replay_log_ml/signal_ml_replay_YYYYMMDD_9_00_0.txt`
- **데이터 소스**: `cache/minute_data/종목코드_YYYYMMDD.pkl` (확정된 캐시 데이터)

### 주의사항
- **selection_date 필터링**: 시뮬레이션에서 선정 시점 이전 신호는 차단됨
- **데이터 타임스탬프**: 캔들 완성 시간 기준 (예: 10:42 캔들 → 10:45:00 완성)

---

## 실시간 vs 시뮬레이션 차이 분석

### 3단계 검증 프로세스
1. **신호 시점 비교**: 실시간과 시뮬레이션에서 같은 시간에 신호가 발생했는가?
2. **selection_date 확인**: 시뮬레이션에서 필터링되었는가? (`completion_time < selection_date` 체크)
3. **데이터 일치 확인**: 캐시 데이터 vs 실시간 데이터 비교

### 주요 차이 원인
1. **selection_date 필터링**: 시뮬레이션에서만 선정 시점 이전 신호 차단
2. **데이터 불일치**: 실시간은 지속 업데이트, 캐시는 확정값
3. **전략 로직 차이**: 패턴 구조나 조건이 미묘하게 다를 경우

---

## 핵심 개념

### 타이밍
- **3분봉 completion_time**: 라벨 시간 + 3분 (예: 10:42 캔들 → 10:45:00 완성)
- **selection_date**: 종목 선정 완료 시간 (시뮬레이션 필터링 기준)

### ML 필터 (전략별 활성화)
- **ML 임계값**: 50% (기본값, `config/ml_settings.py`에서 조정 가능)
- **작동 방식**: 시뮬레이션에서만 적용 / 실시간은 별도 구현 필요

### 데이터 소스
- **실시간**: 지속 업데이트되는 데이터 (API 실시간 수신)
- **캐시**: 확정된 과거 데이터 (pkl 파일)
- **차이점**: 같은 시간대 캔들도 값이 다를 수 있음

---

## 전략 구현 가이드

RoboTrader_orb는 전략 독립적 템플릿입니다. 사용자가 직접 전략을 구현해야 합니다.

### 구현 위치
- **매매 판단 로직**: `core/trading_decision_engine.py`
  - `generate_buy_signal()`: 매수 신호 생성
  - `generate_sell_signal()`: 매도 신호 생성

### 설정 파일
- **전략 파라미터**: `config/trading_config.json` → `strategy` 섹션
- **리스크 관리**: `config/trading_config.json` → `risk_management` 섹션
- **후보 종목 조건**: `core/candidate_selector.py` (필요 시 수정)

---

## 추가 문서
- 상세 가이드: `ROBOTRADER_ANALYSIS_GUIDE.md` (프로젝트별로 작성 필요)
- 종목 상태 관리: `docs/stock_state_management.md`
