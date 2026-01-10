"""
종목 데이터 분석 및 요약 모듈
수집된 분봉 데이터를 기반으로 통계 및 분석 정보를 생성합니다.
"""
import pandas as pd
from typing import Dict, Any, List, Optional
from utils.logger import setup_logger
from utils.korean_time import now_kst

class StockAnalyzer:
    """종목 데이터 분석 클래스"""
    
    def __init__(self):
        self.logger = setup_logger(__name__)

    def analyze_stock(self, 
                     stock_code: str,
                     stock_name: str,
                     selected_time: Any,
                     data_complete: bool,
                     last_update: Any,
                     historical_len: int,
                     realtime_len: int,
                     combined_data: pd.DataFrame) -> Dict[str, Any]:
        """
        단일 종목 상세 분석
        
        Args:
            stock_code: 종목코드
            stock_name: 종목명
            selected_time: 선정 시간
            data_complete: 데이터 수집 완료 여부
            last_update: 마지막 업데이트 시간
            historical_len: 과거 데이터 길이
            realtime_len: 실시간 데이터 길이
            combined_data: 결합된 분봉 데이터
            
        Returns:
            Dict: 분석 결과 딕셔너리
        """
        try:
            # 기본 정보
            analysis = {
                'stock_code': stock_code,
                'stock_name': stock_name,
                'selected_time': selected_time,
                'data_complete': data_complete,
                'last_update': last_update,
                'total_minutes': len(combined_data),
                'historical_minutes': historical_len,
                'realtime_minutes': realtime_len
            }
            
            # 가격 분석 (close 컬럼이 있는 경우)
            if 'close' in combined_data.columns and len(combined_data) > 0:
                prices = combined_data['close']
                
                first_price = float(prices.iloc[0]) if len(prices) > 0 else 0
                last_price = float(prices.iloc[-1]) if len(prices) > 0 else 0
                
                price_change = 0.0
                price_change_rate = 0.0
                
                if len(prices) > 1:
                    price_change = last_price - first_price
                    if first_price > 0:
                        price_change_rate = (price_change / first_price) * 100
                
                analysis.update({
                    'first_price': first_price,
                    'current_price': last_price,
                    'high_price': float(prices.max()),
                    'low_price': float(prices.min()),
                    'price_change': price_change,
                    'price_change_rate': price_change_rate
                })
            
            # 거래량 분석 (volume 컬럼이 있는 경우)
            if 'volume' in combined_data.columns:
                volumes = combined_data['volume']
                analysis.update({
                    'total_volume': int(volumes.sum()),
                    'avg_volume': int(volumes.mean()) if len(volumes) > 0 else 0,
                    'max_volume': int(volumes.max()) if len(volumes) > 0 else 0
                })
            
            return analysis
            
        except Exception as e:
            self.logger.error(f"❌ {stock_code} 분석 정보 생성 오류: {e}")
            return {}

    def create_summary_item(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """분석 결과에서 요약 리스트 아이템 생성"""
        try:
            if not analysis:
                return {}
                
            return {
                'stock_code': analysis.get('stock_code'),
                'stock_name': analysis.get('stock_name'),
                'selected_time': analysis.get('selected_time').strftime('%H:%M:%S') if hasattr(analysis.get('selected_time'), 'strftime') else str(analysis.get('selected_time')),
                'data_complete': analysis.get('data_complete'),
                'total_minutes': analysis.get('total_minutes'),
                'price_change_rate': analysis.get('price_change_rate', 0)
            }
        except Exception as e:
            self.logger.error(f"요약 아이템 생성 오류: {e}")
            return {}
