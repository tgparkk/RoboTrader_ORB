# 전략 시스템 사용 가이드

RoboTrader는 **전략 패턴**을 사용하여 후보 종목 선정 및 매매 판단 로직을 쉽게 교체할 수 있습니다.

---

## 목차

1. [전략 구조 개요](#전략-구조-개요)
2. [설정 파일에서 전략 지정](#설정-파일에서-전략-지정)
3. [후보 선정 전략 작성](#후보-선정-전략-작성)
4. [매매 판단 전략 작성](#매매-판단-전략-작성)
5. [전략 등록 및 사용](#전략-등록-및-사용)
6. [예제: 나만의 전략 만들기](#예제-나만의-전략-만들기)

---

## 전략 구조 개요

### 전략 분리

RoboTrader는 두 가지 전략을 독립적으로 관리합니다:

1. **후보 선정 전략** (`CandidateSelectionStrategy`)
   - 역할: 매수 후보 종목 스크리닝
   - 위치: `strategies/candidate_strategy.py`
   - 기본 구현: `MomentumCandidateStrategy` (모멘텀 기반)

2. **매매 판단 전략** (`TradingStrategy`)
   - 역할: 매수/매도 신호 생성
   - 위치: `strategies/trading_strategy.py`
   - 기본 구현: 없음 (사용자가 구현 필요)

### 디렉토리 구조

```
RoboTrader_orb/
├── strategies/
│   ├── __init__.py
│   ├── candidate_strategy.py          # 후보 선정 전략 인터페이스
│   ├── momentum_candidate_strategy.py # 모멘텀 기반 후보 선정 (기본)
│   ├── trading_strategy.py            # 매매 전략 인터페이스
│   └── strategy_factory.py            # 전략 로딩 팩토리
├── config/
│   ├── trading_config.json            # 전략 설정
│   └── candidate_selection_config.py  # 후보 선정 기준 설정
└── core/
    ├── candidate_selector.py          # 전략 사용
    └── trading_decision_engine.py     # 전략 사용
```

---

## 설정 파일에서 전략 지정

[config/trading_config.json](config/trading_config.json)

```json
{
  "strategy": {
    "candidate_selection": "momentum",  // 후보 선정 전략 이름
    "trading_decision": "",             // 매매 전략 이름 (비어있으면 손익비만 사용)
    "parameters": {
      // 전략별 파라미터 (선택사항)
    }
  }
}
```

### 설정 예시

```json
{
  "strategy": {
    "candidate_selection": "momentum",
    "trading_decision": "orb",
    "parameters": {
      "orb_period_minutes": 30,
      "breakout_threshold": 0.01
    }
  }
}
```

---

## 후보 선정 전략 작성

### 1. 베이스 클래스 상속

```python
# strategies/my_candidate_strategy.py

from typing import Optional, Any
from .candidate_strategy import CandidateSelectionStrategy, CandidateStock


class MyCandidateStrategy(CandidateSelectionStrategy):
    """나만의 후보 선정 전략"""

    def __init__(self, config: Any = None, logger: Any = None):
        super().__init__(config, logger)

    async def evaluate_stock(
        self,
        code: str,
        name: str,
        market: str,
        price_data: Any,
        daily_data: Any,
        weekly_data: Any
    ) -> Optional[CandidateStock]:
        """
        종목을 평가하여 후보로 적합한지 판단

        Returns:
            CandidateStock 객체 또는 None
        """
        # 1. 기본 조건 체크
        current_price = price_data.current_price

        if current_price < 1000:  # 예: 1000원 이상만
            return None

        # 2. 점수 계산
        score = 0
        reasons = []

        # 예: 거래대금 체크
        volume_amount = getattr(price_data, 'volume_amount', 0)
        if volume_amount >= 10_000_000_000:  # 100억 이상
            score += 50
            reasons.append("거래대금 100억 이상")

        # 3. 최소 점수 미달 시 제외
        if score < 50:
            return None

        # 4. 후보 종목 반환
        return CandidateStock(
            code=code,
            name=name,
            market=market,
            score=score,
            reason=", ".join(reasons),
            prev_close=0.0  # 필요 시 계산
        )
```

### 2. 전략 등록

[strategies/strategy_factory.py](strategies/strategy_factory.py)

```python
def register_default_strategies():
    """기본 전략 등록"""
    from .momentum_candidate_strategy import MomentumCandidateStrategy
    from .my_candidate_strategy import MyCandidateStrategy  # 추가

    # 후보 선정 전략
    StrategyFactory.register_candidate_strategy('momentum', MomentumCandidateStrategy)
    StrategyFactory.register_candidate_strategy('my_strategy', MyCandidateStrategy)  # 추가
```

### 3. 설정 파일에서 사용

```json
{
  "strategy": {
    "candidate_selection": "my_strategy"  // 내 전략 사용
  }
}
```

---

## 매매 판단 전략 작성

### 1. 베이스 클래스 상속

```python
# strategies/my_trading_strategy.py

from typing import Optional, Any
from .trading_strategy import TradingStrategy, BuySignal, SellSignal


class MyTradingStrategy(TradingStrategy):
    """나만의 매매 전략"""

    def __init__(self, config: Any = None, logger: Any = None):
        super().__init__(config, logger)

    async def generate_buy_signal(
        self,
        code: str,
        minute_data: Any,
        current_price: float,
        **kwargs
    ) -> Optional[BuySignal]:
        """
        매수 신호 생성

        Returns:
            BuySignal 객체 또는 None
        """
        # 예: 최근 3분봉이 연속 상승하면 매수
        if len(minute_data) < 3:
            return None

        recent_3 = minute_data['close'].tail(3).tolist()
        if recent_3[0] < recent_3[1] < recent_3[2]:
            return BuySignal(
                code=code,
                reason="3분봉 연속 상승",
                confidence=0.8,
                metadata={'pattern': '3-candle-up'}
            )

        return None

    async def generate_sell_signal(
        self,
        code: str,
        position: Any,
        minute_data: Any,
        current_price: float,
        **kwargs
    ) -> Optional[SellSignal]:
        """
        매도 신호 생성

        Returns:
            SellSignal 객체 또는 None
        """
        # 예: 최근 캔들이 고가 대비 5% 하락하면 매도
        if len(minute_data) < 1:
            return None

        last_candle = minute_data.iloc[-1]
        high = float(last_candle['high'])
        close = float(last_candle['close'])

        if (high - close) / high >= 0.05:
            return SellSignal(
                code=code,
                reason="고가 대비 5% 하락",
                signal_type="pattern",
                confidence=0.7
            )

        return None
```

### 2. 전략 등록

```python
def register_default_strategies():
    """기본 전략 등록"""
    from .my_trading_strategy import MyTradingStrategy  # 추가

    # 매매 전략
    StrategyFactory.register_trading_strategy('my_trading', MyTradingStrategy)
```

### 3. 설정 파일에서 사용

```json
{
  "strategy": {
    "candidate_selection": "momentum",
    "trading_decision": "my_trading"  // 내 매매 전략 사용
  }
}
```

---

## 전략 등록 및 사용

### 자동 등록 (권장)

`strategy_factory.py`의 `register_default_strategies()` 함수에 추가:

```python
def register_default_strategies():
    from .my_candidate_strategy import MyCandidateStrategy
    from .my_trading_strategy import MyTradingStrategy

    StrategyFactory.register_candidate_strategy('my_candidate', MyCandidateStrategy)
    StrategyFactory.register_trading_strategy('my_trading', MyTradingStrategy)
```

### 수동 등록

필요 시 런타임에 직접 등록:

```python
from strategies import StrategyFactory
from my_module import CustomStrategy

StrategyFactory.register_candidate_strategy('custom', CustomStrategy)
```

---

## 예제: 나만의 전략 만들기

### 시나리오: 가치주 선정 전략

**요구사항:**
- PER 10 이하
- PBR 1 이하
- 거래대금 50억 이상

**구현:**

```python
# strategies/value_candidate_strategy.py

from typing import Optional, Any
from .candidate_strategy import CandidateSelectionStrategy, CandidateStock


class ValueCandidateStrategy(CandidateSelectionStrategy):
    """가치주 선정 전략"""

    async def evaluate_stock(
        self,
        code: str,
        name: str,
        market: str,
        price_data: Any,
        daily_data: Any,
        weekly_data: Any
    ) -> Optional[CandidateStock]:
        """가치주 평가"""

        # 1. 거래대금 체크
        volume_amount = getattr(price_data, 'volume_amount', 0)
        if volume_amount < 5_000_000_000:
            return None

        # 2. PER, PBR 체크 (API에서 가져온다고 가정)
        per = getattr(price_data, 'per', 999)
        pbr = getattr(price_data, 'pbr', 999)

        if per > 10 or pbr > 1:
            return None

        # 3. 점수 계산
        score = 100
        reasons = ["PER 10 이하", "PBR 1 이하", "거래대금 50억 이상"]

        return CandidateStock(
            code=code,
            name=name,
            market=market,
            score=score,
            reason=", ".join(reasons),
            prev_close=0.0
        )
```

**등록:**

```python
# strategies/strategy_factory.py

def register_default_strategies():
    from .value_candidate_strategy import ValueCandidateStrategy

    StrategyFactory.register_candidate_strategy('value', ValueCandidateStrategy)
```

**사용:**

```json
{
  "strategy": {
    "candidate_selection": "value"
  }
}
```

---

## 설정 커스터마이징

### 후보 선정 기준 조정

[config/candidate_selection_config.py](config/candidate_selection_config.py)를 수정하여 모멘텀 전략의 기준을 조정할 수 있습니다:

```python
from config.candidate_selection_config import CandidateSelectionConfig

# 커스텀 설정
custom_config = CandidateSelectionConfig(
    min_trading_amount=10_000_000_000,  # 100억으로 상향
    new_high_threshold=0.95,             # 신고가 기준 완화
    min_score=60                         # 점수 기준 강화
)

# CandidateSelector 생성 시 전달
selector = CandidateSelector(
    config=trading_config,
    api_manager=api_manager,
    selection_config=custom_config,      # 커스텀 설정 사용
    strategy_name="momentum"
)
```

---

## 주요 개념 정리

### CandidateStock

후보 종목 정보:
- `code`: 종목 코드
- `name`: 종목명
- `market`: 시장 구분
- `score`: 선정 점수
- `reason`: 선정 이유
- `prev_close`: 전날 종가

### BuySignal

매수 신호:
- `code`: 종목 코드
- `reason`: 매수 사유
- `confidence`: 신호 강도 (0~1)
- `metadata`: 추가 정보 (딕셔너리)

### SellSignal

매도 신호:
- `code`: 종목 코드
- `reason`: 매도 사유
- `signal_type`: 신호 타입 (`'stop_loss'`, `'take_profit'`, `'pattern'`, `'time_based'`)
- `confidence`: 신호 강도 (0~1)
- `metadata`: 추가 정보 (딕셔너리)

---

## FAQ

**Q1. 여러 전략을 동시에 사용할 수 있나요?**

현재는 후보 선정 1개, 매매 판단 1개만 설정 가능합니다. 여러 전략을 결합하려면 새로운 전략 클래스를 만들어 내부에서 여러 전략을 호출하는 방식을 사용하세요.

**Q2. 전략을 동적으로 변경할 수 있나요?**

설정 파일을 수정한 후 시스템을 재시작하면 새 전략이 로드됩니다.

**Q3. 기본 모멘텀 전략을 수정하고 싶어요.**

[strategies/momentum_candidate_strategy.py](strategies/momentum_candidate_strategy.py) 파일을 직접 수정하거나, 이 파일을 복사하여 새 전략을 만드세요.

**Q4. 매매 전략 없이 손익비만 사용할 수 있나요?**

네, `trading_decision`을 빈 문자열로 두면 기본 손절/익절만 작동합니다.

---

## 다음 단계

1. [strategies/](strategies/) 폴더에서 예제 전략 확인
2. 나만의 전략 작성
3. `strategy_factory.py`에 등록
4. `trading_config.json`에서 활성화
5. 백테스트로 검증

**템플릿을 활용하여 나만의 트레이딩 전략을 구현해보세요!**
