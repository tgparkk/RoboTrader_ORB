# Universe 관리 가이드

## 개요

RoboTrader는 주간 단위로 거래 대상 종목 Universe를 관리합니다.

**구성**:
- KOSPI 상위 200개
- KOSDAQ 상위 100개
- **총 300개 종목**

---

## 1. Universe 업데이트

### 실행 시점
- **매주 금요일 장마감 후** (15:40 이후)
- 또는 **주말** (토요일/일요일)

### 실행 방법

```bash
# 기본 실행 (KOSPI 200 + KOSDAQ 100)
python scripts/update_weekly_universe.py

# 커스텀 개수 지정
python scripts/update_weekly_universe.py [KOSPI개수] [KOSDAQ개수]

# 예: KOSPI 150 + KOSDAQ 80
python scripts/update_weekly_universe.py 150 80
```

### 출력 파일
```
data/
├── universe_20260109.json   # JSON 형식 (시스템 사용)
└── universe_20260109.csv    # CSV 형식 (백업)
```

---

## 2. 크롤링 상세

### 네이버 금융 URL
- KOSPI: `https://finance.naver.com/sise/sise_market_sum.naver?sosok=0`
- KOSDAQ: `https://finance.naver.com/sise/sise_market_sum.naver?sosok=1`

### 수집 데이터
| 항목 | 설명 |
|------|------|
| code | 종목코드 (6자리) |
| name | 종목명 |
| market | KOSPI / KOSDAQ |
| market_cap | 시가총액 (억원) |
| rank | 시가총액 순위 |

### 제외 종목
- 우선주 (종목명에 '우' 포함 또는 코드 끝자리 5)
- 전환우선주 (종목명에 '전환' 포함)
- 시가총액 정보 없는 종목

---

## 3. Universe 로드

### 프로그램 내 사용

```python
from scripts.update_weekly_universe import load_latest_universe

# 최신 Universe 로드
universe = load_latest_universe()

# 결과: DataFrame
# - 총 300개 종목
# - 컬럼: code, name, market, market_cap, rank
```

### 전략에서 사용

```python
class ORBStrategy(TradingStrategy):
    async def select_daily_candidates(self, ...):
        # Universe 로드
        universe = load_latest_universe()

        # ORB 기준으로 필터링
        for stock in universe.to_dict('records'):
            if meets_orb_criteria(stock):
                candidates.append(stock)

        return candidates
```

---

## 4. 자동화 설정 (선택)

### Windows 작업 스케줄러

1. **작업 스케줄러 실행** (`taskschd.msc`)
2. **작업 만들기**
   - 이름: `RoboTrader Universe 업데이트`
   - 트리거: 매주 금요일 16:00
   - 작업:
     ```
     프로그램: python
     인수: d:\GIT\RoboTrader_orb\scripts\update_weekly_universe.py
     시작 위치: d:\GIT\RoboTrader_orb
     ```

### Python 스케줄러 (선택)

```python
# 프로그램 내 자동화
# main.py에 추가 가능

async def auto_update_universe():
    """금요일 15:40 이후 자동 업데이트"""
    from datetime import datetime
    from pathlib import Path

    now = datetime.now()

    # 금요일 체크
    if now.weekday() != 4:
        return

    # 15:40 이후
    if now.hour < 15 or (now.hour == 15 and now.minute < 40):
        return

    # 오늘 Universe 파일 확인
    today_file = Path(f'data/universe_{now.strftime("%Y%m%d")}.json')
    if today_file.exists():
        return

    # 업데이트 실행
    from scripts.update_weekly_universe import save_weekly_universe
    save_weekly_universe()
```

---

## 5. 검증 및 모니터링

### 수집 결과 확인

```bash
# 로그 확인
python scripts/update_weekly_universe.py

# 출력 예:
# ========================================================
# 📊 주간 Universe 업데이트 시작
# ========================================================
# 📊 KOSPI 상위 200개 크롤링 시작...
#   페이지 1 크롤링 중...
#   페이지 1: 50개 종목 수집 (누적: 50개)
#   ...
# ✅ KOSPI 크롤링 완료: 200개
#
# 📊 KOSDAQ 상위 100개 크롤링 시작...
# ✅ KOSDAQ 크롤링 완료: 100개
#
# 📈 수집 결과:
#   - KOSPI: 200개
#   - KOSDAQ: 100개
#   - 총계: 300개
```

### 데이터 품질 체크

```python
import pandas as pd

# Universe 로드
df = pd.read_json('data/universe_20260109.json')

# 기본 검증
assert len(df) == 300, "종목 수 불일치"
assert df['code'].nunique() == 300, "중복 종목 존재"
assert df['market_cap'].min() > 0, "시가총액 0인 종목 존재"

# 시장 구성 확인
print(df['market'].value_counts())
# KOSPI     200
# KOSDAQ    100
```

---

## 6. 문제 해결

### Q1. 크롤링 실패 (연결 오류)
- **원인**: 네트워크 문제 또는 네이버 금융 서버 부하
- **해결**: 잠시 후 재시도

### Q2. 종목 수 부족 (예: 250개만 수집)
- **원인**: 페이지 구조 변경 또는 파싱 오류
- **해결**:
  1. 로그 확인
  2. HTML 구조 변경 여부 확인
  3. 필요시 스크립트 수정

### Q3. 우선주가 포함됨
- **원인**: 네이버 금융 종목명 표기 변경
- **해결**: 필터링 로직 강화 (스크립트 수정)

### Q4. Universe 파일이 없음
- **원인**: 아직 한 번도 실행 안 함
- **해결**: `python scripts/update_weekly_universe.py` 실행

---

## 7. 수동 관리 (대안)

크롤링이 불안정한 경우 수동 관리 가능:

1. **네이버 금융 접속**
   - KOSPI: https://finance.naver.com/sise/sise_market_sum.naver?sosok=0
   - KOSDAQ: https://finance.naver.com/sise/sise_market_sum.naver?sosok=1

2. **엑셀 다운로드**
   - 페이지 하단 "엑셀 저장" 클릭

3. **데이터 정리**
   - KOSPI 200개 + KOSDAQ 100개 선택
   - 컬럼 정리: code, name, market, market_cap, rank

4. **JSON 변환**
   ```python
   import pandas as pd

   # CSV 로드
   df = pd.read_csv('manual_universe.csv')

   # JSON 저장
   df.to_json('data/universe_20260109.json',
              orient='records',
              force_ascii=False,
              indent=2)
   ```

---

## 8. 히스토리 관리

### 과거 Universe 보관
```bash
data/
├── universe_20260103.json
├── universe_20260110.json
├── universe_20260117.json
└── ...
```

### 보관 기간
- **권장**: 최소 4주 (1개월)
- **이유**: 백테스트 및 분석용

### 정리 스크립트 (선택)
```python
# 30일 이전 파일 삭제
from pathlib import Path
from datetime import datetime, timedelta

data_dir = Path('data')
cutoff_date = datetime.now() - timedelta(days=30)

for file in data_dir.glob('universe_*.json'):
    file_date_str = file.stem.split('_')[1]
    file_date = datetime.strptime(file_date_str, '%Y%m%d')

    if file_date < cutoff_date:
        file.unlink()
        print(f"삭제: {file.name}")
```
