"""
매수 후보 종목 선정 모듈
"""
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from .models import Stock, TradingConfig
from api.kis_api_manager import KISAPIManager
from config.candidate_selection_config import DEFAULT_CANDIDATE_SELECTION_CONFIG, CandidateSelectionConfig
from strategies.candidate_strategy import CandidateStock
from strategies.strategy_factory import StrategyFactory
from utils.logger import setup_logger
from utils.korean_time import now_kst


class CandidateSelector:
    """매수 후보 종목 선정기 (전략 패턴 적용)"""

    def __init__(
        self,
        config: TradingConfig,
        api_manager: KISAPIManager,
        selection_config: CandidateSelectionConfig = None,
        strategy_name: str = "momentum"
    ):
        self.config = config
        self.api_manager = api_manager
        self.selection_config = selection_config or DEFAULT_CANDIDATE_SELECTION_CONFIG
        self.logger = setup_logger(__name__)

        # stock_list.json 파일 경로
        self.stock_list_file = Path(__file__).parent.parent / "stock_list.json"

        # 전략 로드
        self.strategy = StrategyFactory.create_candidate_strategy(
            name=strategy_name,
            config=self.selection_config,
            logger=self.logger
        )

        if self.strategy is None:
            self.logger.warning(f"전략 '{strategy_name}' 로드 실패. 기본 전략 사용.")
            # 기본 전략으로 폴백
            self.strategy = StrategyFactory.create_candidate_strategy(
                name="momentum",
                config=self.selection_config,
                logger=self.logger
            )
    
    async def select_daily_candidates(self, max_candidates: int = 5) -> List[CandidateStock]:
        """
        일일 매수 후보 종목 선정
        
        Args:
            max_candidates: 최대 후보 종목 수
            
        Returns:
            선정된 후보 종목 리스트
        """
        try:
            self.logger.info("🔍 일일 매수 후보 종목 선정 시작")
            
            # 1. 전체 종목 리스트 로드
            all_stocks = self._load_stock_list()
            if not all_stocks:
                self.logger.error("종목 리스트 로드 실패")
                return []
            
            self.logger.info(f"전체 종목 수: {len(all_stocks)}")
            
            # 2. 1차 필터링: 기본 조건 체크
            filtered_stocks = await self._apply_basic_filters(all_stocks)
            self.logger.info(f"1차 필터링 후: {len(filtered_stocks)}개 종목")
            
            # 3. 2차 필터링: 상세 분석
            candidate_stocks = await self._analyze_candidates(filtered_stocks)
            self.logger.info(f"2차 분석 후: {len(candidate_stocks)}개 후보")
            
            # 4. 점수 기준 정렬 및 상위 종목 선정
            candidate_stocks.sort(key=lambda x: x.score, reverse=True)
            selected_candidates = candidate_stocks[:max_candidates]
            
            self.logger.info(f"✅ 최종 선정된 후보 종목: {len(selected_candidates)}개")
            for candidate in selected_candidates:
                self.logger.info(f"  - {candidate.code}({candidate.name}): {candidate.score:.2f}점 - {candidate.reason}")
            
            return selected_candidates
            
        except Exception as e:
            self.logger.error(f"❌ 후보 종목 선정 실패: {e}")
            return []
    
    def _load_stock_list(self) -> List[Dict]:
        """stock_list.json 파일에서 종목 리스트 로드"""
        try:
            if not self.stock_list_file.exists():
                self.logger.error(f"종목 리스트 파일이 없습니다: {self.stock_list_file}")
                return []
            
            with open(self.stock_list_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            return data.get('stocks', [])
            
        except Exception as e:
            self.logger.error(f"종목 리스트 로드 실패: {e}")
            return []
    
    async def _apply_basic_filters(self, stocks: List[Dict]) -> List[Dict]:
        """
        1차 기본 필터링
        - KOSPI 종목만
        - 우선주 제외 
        - 기타 기본 조건
        """
        filtered = []
        excluded_counts = {
            'non_kospi': 0,
            'preferred': 0,
            'convertible': 0,
            'etf': 0,
            'passed': 0
        }
        
        for stock in stocks:
            try:
                code = stock.get('code', '')
                name = stock.get('name', '')
                
                # KOSPI 종목만
                if stock.get('market') != 'KOSPI':
                    excluded_counts['non_kospi'] += 1
                    continue
                
                # 우선주 제외 (종목코드 끝자리가 5인 경우나 이름에 '우' 포함)
                if code.endswith('5') or '우' in name:
                    excluded_counts['preferred'] += 1
                    continue
                
                # 전환우선주 제외
                if '전환' in name:
                    excluded_counts['convertible'] += 1
                    continue
                
                # ETF, ETN 제외
                if any(keyword in name.upper() for keyword in ['ETF', 'ETN']):
                    excluded_counts['etf'] += 1
                    continue
                
                excluded_counts['passed'] += 1
                filtered.append(stock)
                
            except Exception as e:
                self.logger.warning(f"기본 필터링 중 오류 {stock}: {e}")
                continue
        
        self.logger.info(f"1차 필터링 결과: "
                        f"비KOSPI({excluded_counts['non_kospi']}), "
                        f"우선주({excluded_counts['preferred']}), "
                        f"전환({excluded_counts['convertible']}), "
                        f"ETF({excluded_counts['etf']}), "
                        f"통과({excluded_counts['passed']})")
        
        return filtered
    
    async def _analyze_candidates(self, stocks: List[Dict]) -> List[CandidateStock]:
        """
        2차 상세 분석 및 후보 종목 선정
        """
        candidates = []
        
        # 병렬 처리를 위해 배치 단위로 처리
        batch_size = 20
        for i in range(0, len(stocks), batch_size):
            batch = stocks[i:i + batch_size]
            batch_candidates = await self._analyze_stock_batch(batch)
            candidates.extend(batch_candidates)
            
            # API 호출 제한 고려하여 잠시 대기
            if i + batch_size < len(stocks):
                await asyncio.sleep(1)
        
        return candidates
    
    async def _analyze_stock_batch(self, stocks: List[Dict]) -> List[CandidateStock]:
        """주식 배치 분석"""
        candidates = []
        
        for stock in stocks:
            try:
                candidate = await self._analyze_single_stock(stock)
                if candidate:
                    candidates.append(candidate)
                    
            except Exception as e:
                self.logger.warning(f"종목 분석 실패 {stock.get('code')}: {e}")
                continue
        
        return candidates
    
    async def _analyze_single_stock(self, stock: Dict) -> Optional[CandidateStock]:
        """
        개별 종목 분석 (전략 위임)

        전략 패턴을 사용하여 종목 평가를 전략 객체에 위임합니다.
        """
        try:
            code = stock['code']
            name = stock['name']
            market = stock['market']

            self.logger.debug(f"📊 종목 분석 시작: {code}({name})")

            # 현재가 및 기본 정보 조회
            price_data = self.api_manager.get_current_price(code)
            if price_data is None:
                self.logger.debug(f"❌ {code}: 현재가 데이터 없음")
                return None

            # 일봉 데이터 조회 (최대 100일)
            daily_data = self.api_manager.get_ohlcv_data(code, "D", 100)
            if daily_data is None:
                self.logger.debug(f"❌ {code}: 일봉 데이터 없음")
                return None

            # 주봉 데이터 조회 (200일 대상, 약 40주 = 280일)
            weekly_data = self.api_manager.get_ohlcv_data(code, "W", 280)
            if weekly_data is None:
                self.logger.debug(f"❌ {code}: 주봉 데이터 없음")
                return None
            
            # 전략을 사용하여 종목 평가
            candidate = await self.strategy.evaluate_stock(
                code=code,
                name=name,
                market=market,
                price_data=price_data,
                daily_data=daily_data,
                weekly_data=weekly_data
            )

            return candidate
            
        except Exception as e:
            self.logger.warning(f"종목 분석 실패 {stock.get('code')}: {e}")
            return None
    def update_candidate_stocks_in_config(self, candidates: List[CandidateStock]):
        """선정된 후보 종목을 데이터 컬렉터에 업데이트"""
        try:
            # 후보 종목 코드 리스트 생성
            candidate_codes = [candidate.code for candidate in candidates]
            
            # 설정에 업데이트
            self.config.data_collection.candidate_stocks = candidate_codes
            
            self.logger.info(f"후보 종목 설정 업데이트 완료: {len(candidate_codes)}개")
            
        except Exception as e:
            self.logger.error(f"후보 종목 설정 업데이트 실패: {e}")
    
    
    def get_condition_search_results(self, seq: str) -> Optional[List[Dict]]:
        """
        종목조건검색조회 실행 (장중 실행용)
        
        Args:
            seq: 조건검색 순번 (0부터 시작하는 문자열)
            
        Returns:
            조건검색 결과 종목 리스트 또는 None
        """
        try:
            from config.settings import HTS_ID
            from api.kis_market_api import get_psearch_result
            
            #self.logger.info(f"🔍 종목조건검색조회 실행: seq={seq}")
            
            # HTS_ID 확인
            if not HTS_ID:
                self.logger.error("❌ HTS_ID가 설정되지 않았습니다. config/key.ini를 확인해주세요.")
                return None
            
            # 종목조건검색조회 API 호출
            result_df = get_psearch_result(user_id=HTS_ID, seq=seq)
            
            if result_df is None:
                self.logger.error(f"❌ 종목조건검색조회 실패: seq={seq}")
                return None
            
            if result_df.empty:
                self.logger.info(f"ℹ️ 조건에 맞는 종목이 없습니다: seq={seq}")
                return []
            
            # DataFrame을 딕셔너리 리스트로 변환
            result_list = result_df.to_dict('records')
            
            #self.logger.debug(f"✅ 종목조건검색조회 성공: {len(result_list)}개 종목 발견 (seq={seq})")
            
            # 결과 요약 로그
            for i, stock in enumerate(result_list[:5]):  # 상위 5개만 로그
                code = stock.get('code', '')
                name = stock.get('name', '')
                price = stock.get('price', '')
                change_rate = stock.get('chgrate', '')
                
                self.logger.info(f"  {i+1}. {code}({name}): {price}원 ({change_rate}%)")
            
            if len(result_list) > 5:
                self.logger.info(f"  ... 외 {len(result_list) - 5}개 종목")
            
            return result_list
            
        except Exception as e:
            self.logger.error(f"❌ 종목조건검색조회 오류: {e}")
            return None
    
    
    def get_condition_search_candidates(self, seq: str, max_candidates: int = 10) -> Optional[List[Dict]]:
        """
        조건검색 결과 조회 (단순 조회만)
        
        Args:
            seq: 조건검색 순번
            max_candidates: 최대 후보 종목 수 (미사용, 호환성 유지용)
            
        Returns:
            조건검색 결과 종목 리스트 또는 None
        """
        try:
            # 1. 조건검색 결과 조회
            search_results = self.get_condition_search_results(seq)
            return search_results
            
        except Exception as e:
            self.logger.error(f"❌ 조건검색 결과 조회 실패: {e}")
            return None