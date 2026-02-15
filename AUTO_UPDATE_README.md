# ✅ Universe 자동 업데이트 기능 추가 완료!

## 🎯 변경 사항

### 1. `scripts/update_weekly_universe.py` - 새로운 함수 추가

```python
# 새로 추가된 함수들
get_universe_age_days()          # Universe 파일 나이 계산
needs_update(max_age_days=7)     # 업데이트 필요 여부 체크
auto_update_if_needed(...)       # 자동 업데이트 실행
```

### 2. `main.py` - 초기화 시 자동 체크

```python
# import 추가
from scripts.update_weekly_universe import auto_update_if_needed

# initialize() 함수에 자동 업데이트 로직 추가 (line 174)
```

---

## 🚀 이제 이렇게 동작합니다

### 시나리오 1: Universe가 최신 상태 (7일 미만)

```
> run_robotrader.bat

🚀 주식 단타 거래 시스템 초기화 시작
📅 Universe 업데이트 체크 중...
✅ Universe 최신 상태: 3일 경과 (최대 7일)
📅 오늘 거래시간 정보:
...
```

→ **바로 진행**, 업데이트 하지 않음

---

### 시나리오 2: Universe가 오래됨 (7일 이상)

```
> run_robotrader.bat

🚀 주식 단타 거래 시스템 초기화 시작
📅 Universe 업데이트 체크 중...
📅 Universe 업데이트 필요: 16일 경과 (최대 7일)
🔄 자동 업데이트 시작...

============================================================
[INFO] 주간 Universe 업데이트 시작
============================================================

[INFO] KOSPI 상위 200개 크롤링 시작...
[OK] KOSPI 200개 수집 완료

[INFO] KOSDAQ 상위 100개 크롤링 시작...
[OK] KOSDAQ 100개 수집 완료

✅ data/universe_20260119.json 저장 완료

============================================================
[OK] 주간 Universe 업데이트 완료!
============================================================

✅ Universe 자동 업데이트 완료!
📅 오늘 거래시간 정보:
...
```

→ **자동 업데이트 후 진행**

---

### 시나리오 3: 업데이트 실패 (네트워크 오류 등)

```
> run_robotrader.bat

🚀 주식 단타 거래 시스템 초기화 시작
📅 Universe 업데이트 체크 중...
📅 Universe 업데이트 필요: 16일 경과 (최대 7일)
🔄 자동 업데이트 시작...
❌ Universe 자동 업데이트 실패: Connection timeout
⚠️ Universe 자동 업데이트 체크 실패: ...
⚠️ 기존 Universe 파일로 계속 진행합니다.
📅 오늘 거래시간 정보:
...
```

→ **기존 파일로 계속 진행** (안전)

---

## ⚙️ 설정 변경 (선택사항)

### 업데이트 주기 변경

**파일:** `main.py` (line 176 근처)

```python
# 현재 설정 (7일)
auto_update_if_needed(max_age_days=7, kospi_count=200, kosdaq_count=100)

# ↓ 변경 예시 ↓

# 3일마다 업데이트 (더 자주)
auto_update_if_needed(max_age_days=3, kospi_count=200, kosdaq_count=100)

# 14일마다 업데이트 (덜 자주)
auto_update_if_needed(max_age_days=14, kospi_count=200, kosdaq_count=100)
```

### 종목 수 변경

```python
# KOSPI 150개, KOSDAQ 50개로 축소
auto_update_if_needed(max_age_days=7, kospi_count=150, kosdaq_count=50)

# KOSPI 100개, KOSDAQ 200개로 변경
auto_update_if_needed(max_age_days=7, kospi_count=100, kosdaq_count=200)
```

---

## 📝 참고 사항

### 장점
✅ 수동으로 Universe 업데이트할 필요 없음  
✅ 항상 최신 시가총액 상위 종목으로 거래  
✅ 업데이트 실패 시 기존 파일로 안전하게 계속 진행  
✅ 프로그램 시작 시 자동으로 체크

### 주의사항
⚠️ 업데이트 시 네이버 금융 크롤링 (인터넷 연결 필요)  
⚠️ 업데이트는 약 2~5분 소요 (종목 수에 따라)  
⚠️ 프로그램 시작 시에만 체크 (실행 중에는 자동 업데이트 안 됨)

### 수동 업데이트 (여전히 가능)
```bash
python scripts/update_weekly_universe.py
```

---

## 🔧 자동 업데이트 비활성화

원하지 않으면 `main.py`에서 주석 처리:

```python
# # 0. Universe 자동 업데이트 체크 (7일 경과 시)
# self.logger.info("📅 Universe 업데이트 체크 중...")
# try:
#     auto_update_if_needed(max_age_days=7, kospi_count=200, kosdaq_count=100)
# except Exception as e:
#     self.logger.warning(f"⚠️ Universe 자동 업데이트 체크 실패: {e}")
#     self.logger.warning("⚠️ 기존 Universe 파일로 계속 진행합니다.")
```

---

## 📚 상세 문서

더 자세한 내용은 `docs/auto_universe_update.md` 참고

---

**구현 완료일:** 2026-01-19  
**기본 설정:** 7일마다 자동 업데이트  
**상태:** 즉시 사용 가능 ✅
