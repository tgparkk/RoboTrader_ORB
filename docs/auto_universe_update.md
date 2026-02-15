# Universe 자동 업데이트 기능

## 개요

RoboTrader 시스템이 시작될 때마다 Universe(거래 대상 종목 리스트)의 업데이트 시기를 자동으로 체크하고, 필요 시 자동으로 업데이트합니다.

## 동작 방식

### 1. 자동 체크 시점
- 프로그램 시작 시 (`run_robotrader.bat` 실행 시)
- `main.py`의 `initialize()` 함수에서 최우선 실행

### 2. 업데이트 조건
- **기본 설정:** Universe 파일이 **7일** 이상 경과한 경우
- 파일명에서 날짜를 추출하여 현재 날짜와 비교
  - 예: `universe_20260103.json` → 2026-01-03 생성
  - 현재: 2026-01-19 → 16일 경과 → **자동 업데이트 실행**

### 3. 업데이트 내용
- KOSPI 상위 200개 종목 (시가총액 기준)
- KOSDAQ 상위 100개 종목 (시가총액 기준)
- 총 300개 종목 자동 업데이트

## 구현 내역

### 추가된 함수 (`scripts/update_weekly_universe.py`)

```python
def get_universe_age_days() -> int:
    """Universe 파일 생성 후 경과 일수 계산"""

def needs_update(max_age_days: int = 7) -> bool:
    """업데이트 필요 여부 확인"""

def auto_update_if_needed(max_age_days: int = 7, 
                          kospi_count: int = 200, 
                          kosdaq_count: int = 100) -> bool:
    """필요 시 자동 업데이트 실행"""
```

### 수정된 파일 (`main.py`)

```python
# import 추가
from scripts.update_weekly_universe import auto_update_if_needed

# initialize() 함수 시작 부분에 추가
async def initialize(self) -> bool:
    """시스템 초기화"""
    try:
        self.logger.info("🚀 주식 단타 거래 시스템 초기화 시작")

        # 0. Universe 자동 업데이트 체크 (7일 경과 시)
        self.logger.info("📅 Universe 업데이트 체크 중...")
        try:
            auto_update_if_needed(max_age_days=7, kospi_count=200, kosdaq_count=100)
        except Exception as e:
            self.logger.warning(f"⚠️ Universe 자동 업데이트 체크 실패: {e}")
            self.logger.warning("⚠️ 기존 Universe 파일로 계속 진행합니다.")
        
        # ... 나머지 초기화 ...
```

## 로그 예시

### 업데이트 불필요한 경우
```
📅 Universe 업데이트 체크 중...
✅ Universe 최신 상태: 3일 경과 (최대 7일)
```

### 업데이트 필요한 경우
```
📅 Universe 업데이트 체크 중...
📅 Universe 업데이트 필요: 16일 경과 (최대 7일)
🔄 자동 업데이트 시작...

============================================================
[INFO] 주간 Universe 업데이트 시작
============================================================

[INFO] KOSPI 상위 200개 크롤링 시작...
[INFO] 페이지 1/5 처리 중... (현재 50개)
[INFO] 페이지 2/5 처리 중... (현재 100개)
...
[INFO] KOSDAQ 상위 100개 크롤링 시작...
...

============================================================
[OK] 주간 Universe 업데이트 완료!
============================================================

✅ Universe 자동 업데이트 완료!
```

### 업데이트 실패 시 (네트워크 오류 등)
```
📅 Universe 업데이트 체크 중...
📅 Universe 업데이트 필요: 16일 경과 (최대 7일)
🔄 자동 업데이트 시작...
❌ Universe 자동 업데이트 실패: Connection timeout
⚠️ 기존 Universe 파일로 계속 진행합니다.
```

## 설정 변경

### 업데이트 주기 변경

**파일:** `main.py` (line 176 근처)

```python
# 기본 (7일)
auto_update_if_needed(max_age_days=7, kospi_count=200, kosdaq_count=100)

# 3일로 변경
auto_update_if_needed(max_age_days=3, kospi_count=200, kosdaq_count=100)

# 14일로 변경 (더 느슨하게)
auto_update_if_needed(max_age_days=14, kospi_count=200, kosdaq_count=100)
```

### 종목 수 변경

```python
# KOSPI 150개, KOSDAQ 50개로 변경
auto_update_if_needed(max_age_days=7, kospi_count=150, kosdaq_count=50)

# KOSPI 100개, KOSDAQ 200개로 변경
auto_update_if_needed(max_age_days=7, kospi_count=100, kosdaq_count=200)
```

## 수동 업데이트

자동 업데이트와 별개로, 언제든지 수동으로 업데이트할 수 있습니다:

```bash
# 기본 (KOSPI 200 + KOSDAQ 100)
python scripts/update_weekly_universe.py

# 커스텀 개수
python scripts/update_weekly_universe.py 150 80
```

## 장점

1. **자동화:** 수동으로 업데이트를 실행할 필요 없음
2. **최신성:** 항상 최신 시가총액 상위 종목으로 거래
3. **안전성:** 업데이트 실패 시 기존 파일로 계속 진행
4. **투명성:** 로그로 업데이트 상태 명확히 확인 가능

## 주의사항

1. **네트워크 필요:** 자동 업데이트 시 네이버 금융 크롤링을 위해 인터넷 연결 필요
2. **시간 소요:** Universe 업데이트는 약 2~5분 소요 (종목 수에 따라 다름)
3. **실행 시점:** 프로그램 시작 시 체크하므로, 프로그램을 재시작해야 업데이트 적용됨

## 문제 해결

### Q1. 자동 업데이트를 비활성화하고 싶어요

**A:** `main.py`에서 해당 코드를 주석 처리하세요:

```python
# # 0. Universe 자동 업데이트 체크 (7일 경과 시)
# self.logger.info("📅 Universe 업데이트 체크 중...")
# try:
#     auto_update_if_needed(max_age_days=7, kospi_count=200, kosdaq_count=100)
# except Exception as e:
#     self.logger.warning(f"⚠️ Universe 자동 업데이트 체크 실패: {e}")
```

### Q2. Universe 파일이 없어요

**A:** 수동으로 한 번 생성하세요:

```bash
python scripts/update_weekly_universe.py
```

### Q3. 크롤링이 너무 느려요

**A:** 종목 수를 줄이세요:

```python
auto_update_if_needed(max_age_days=7, kospi_count=100, kosdaq_count=50)
```

---

**작성일:** 2026-01-19  
**버전:** 1.0
