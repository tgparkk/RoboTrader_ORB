"""
2025년 1월 22일 매매 분석 스크립트
"""
import psycopg2
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
import re
from collections import defaultdict

# PostgreSQL 접속 정보
DB_CONN_PARAMS = dict(host='172.23.208.1', port=5433, dbname='robotrader_orb', user='postgres')
LOG_PATH = "logs/trading_20260122.log"
TARGET_DATE = "2026-01-22"

def analyze_database_trades():
    """데이터베이스에서 매매 기록 분석"""
    print("=" * 80)
    print("[데이터베이스 매매 기록 분석]")
    print("=" * 80)
    
    try:
        conn = psycopg2.connect(**DB_CONN_PARAMS)
        
        # 실거래 기록 조회 (날짜 필터링)
        query_real = f"""
            SELECT 
                id,
                stock_code,
                stock_name,
                action,
                quantity,
                price,
                timestamp,
                strategy,
                reason,
                profit_loss,
                profit_rate,
                buy_record_id
            FROM real_trading_records
            WHERE timestamp LIKE '{TARGET_DATE}%'
            ORDER BY timestamp ASC
        """
        
        df_real = pd.read_sql_query(query_real, conn)
        
        # 가상 매매 기록 조회 (날짜 필터링)
        query_virtual = f"""
            SELECT 
                id,
                stock_code,
                stock_name,
                action,
                quantity,
                price,
                timestamp,
                strategy,
                reason,
                profit_loss,
                profit_rate,
                buy_record_id
            FROM virtual_trading_records
            WHERE timestamp LIKE '{TARGET_DATE}%' AND is_test = 1
            ORDER BY timestamp ASC
        """
        
        df_virtual = pd.read_sql_query(query_virtual, conn)
        
        conn.close()
        
        # 실거래 분석
        print("\n[실거래 기록]")
        if not df_real.empty:
            print(f"총 {len(df_real)}건의 거래 기록")
            print("\n매수 기록:")
            buys = df_real[df_real['action'] == 'BUY']
            if not buys.empty:
                for _, row in buys.iterrows():
                    print(f"  - {row['timestamp']} | {row['stock_code']}({row['stock_name']}) | "
                          f"{row['quantity']}주 @{row['price']:,.0f}원 | 전략: {row['strategy']} | 사유: {row['reason']}")
            
            print("\n매도 기록:")
            sells = df_real[df_real['action'] == 'SELL']
            if not sells.empty:
                total_profit = 0
                for _, row in sells.iterrows():
                    profit = row['profit_loss'] if pd.notna(row['profit_loss']) else 0
                    rate = row['profit_rate'] if pd.notna(row['profit_rate']) else 0
                    total_profit += profit
                    print(f"  - {row['timestamp']} | {row['stock_code']}({row['stock_name']}) | "
                          f"{row['quantity']}주 @{row['price']:,.0f}원 | "
                          f"손익: {profit:+,.0f}원 ({rate:+.2f}%) | 사유: {row['reason']}")
                
                print(f"\n총 손익: {total_profit:+,.0f}원")
                print(f"평균 수익률: {sells['profit_rate'].mean():.2f}%")
        else:
            print("실거래 기록이 없습니다.")
        
        # 가상 매매 분석
        print("\n[가상 매매 기록]")
        if not df_virtual.empty:
            print(f"총 {len(df_virtual)}건의 거래 기록")
            print("\n매수 기록:")
            buys = df_virtual[df_virtual['action'] == 'BUY']
            if not buys.empty:
                for _, row in buys.iterrows():
                    print(f"  - {row['timestamp']} | {row['stock_code']}({row['stock_name']}) | "
                          f"{row['quantity']}주 @{row['price']:,.0f}원 | 전략: {row['strategy']} | 사유: {row['reason']}")
            
            print("\n매도 기록:")
            sells = df_virtual[df_virtual['action'] == 'SELL']
            if not sells.empty:
                total_profit = 0
                for _, row in sells.iterrows():
                    profit = row['profit_loss'] if pd.notna(row['profit_loss']) else 0
                    rate = row['profit_rate'] if pd.notna(row['profit_rate']) else 0
                    total_profit += profit
                    print(f"  - {row['timestamp']} | {row['stock_code']}({row['stock_name']}) | "
                          f"{row['quantity']}주 @{row['price']:,.0f}원 | "
                          f"손익: {profit:+,.0f}원 ({rate:+.2f}%) | 사유: {row['reason']}")
                
                print(f"\n총 손익: {total_profit:+,.0f}원")
                print(f"평균 수익률: {sells['profit_rate'].mean():.2f}%")
        else:
            print("가상 매매 기록이 없습니다.")
        
        return df_real, df_virtual
        
    except Exception as e:
        print(f"[오류] 데이터베이스 분석 오류: {e}")
        return None, None

def analyze_log_file():
    """로그 파일 분석"""
    print("\n" + "=" * 80)
    print("[로그 파일 분석]")
    print("=" * 80)
    
    if not Path(LOG_PATH).exists():
        print(f"[오류] 로그 파일을 찾을 수 없습니다: {LOG_PATH}")
        return
    
    try:
        # 주요 이벤트 패턴
        patterns = {
            '매수': r'(매수|BUY|buy)',
            '매도': r'(매도|SELL|sell)',
            '주문': r'(주문|ORDER|order)',
            '체결': r'(체결|FILLED|filled)',
            '에러': r'(ERROR|에러|오류|실패)',
            '신호': r'(신호|signal|SIGNAL)',
            '상태변경': r'(상태변경|state|STATE)',
        }
        
        event_counts = defaultdict(int)
        error_logs = []
        buy_logs = []
        sell_logs = []
        signal_logs = []
        state_changes = []
        
        with open(LOG_PATH, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                # 날짜 필터링 (1월 22일만)
                date_str = TARGET_DATE.replace('-', '')
                if date_str not in line[:10] and TARGET_DATE not in line:
                    continue
                
                # 이모지 제거 후 이벤트 카운트
                line_clean = re.sub(r'[^\x00-\x7F]+', '', line)  # ASCII만 남기기
                for event_type, pattern in patterns.items():
                    if re.search(pattern, line_clean, re.IGNORECASE):
                        event_counts[event_type] += 1
                
                # 에러 로그 수집
                if re.search(r'(ERROR|에러|오류|실패|Exception)', line, re.IGNORECASE):
                    error_logs.append(line.strip()[:200])  # 길이 제한
                
                # 매수 로그 수집
                if re.search(r'(매수|BUY|buy).*체결|체결.*매수', line, re.IGNORECASE):
                    buy_logs.append(line.strip()[:200])
                
                # 매도 로그 수집
                if re.search(r'(매도|SELL|sell).*체결|체결.*매도', line, re.IGNORECASE):
                    sell_logs.append(line.strip()[:200])
                
                # 신호 로그 수집
                if re.search(r'(매수신호|buy.*signal|signal.*buy)', line, re.IGNORECASE):
                    signal_logs.append(line.strip()[:200])
                
                # 상태 변경 로그 수집 (이모지 제거)
                if re.search(r'상태변경|state.*change', line, re.IGNORECASE):
                    state_changes.append(line.strip()[:200])
        
        print(f"\n[이벤트 통계]")
        for event_type, count in sorted(event_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"  {event_type}: {count}건")
        
        print(f"\n[매수 체결 로그] ({len(buy_logs)}건)")
        for log in buy_logs[:10]:  # 최대 10개만 표시
            try:
                print(f"  {log[:150]}")
            except:
                print(f"  [로그 출력 오류]")
        if len(buy_logs) > 10:
            print(f"  ... 외 {len(buy_logs) - 10}건")
        
        print(f"\n[매도 체결 로그] ({len(sell_logs)}건)")
        for log in sell_logs[:10]:  # 최대 10개만 표시
            try:
                print(f"  {log[:150]}")
            except:
                print(f"  [로그 출력 오류]")
        if len(sell_logs) > 10:
            print(f"  ... 외 {len(sell_logs) - 10}건")
        
        print(f"\n[매수 신호 로그] ({len(signal_logs)}건)")
        for log in signal_logs[:10]:  # 최대 10개만 표시
            try:
                print(f"  {log[:150]}")
            except:
                print(f"  [로그 출력 오류]")
        if len(signal_logs) > 10:
            print(f"  ... 외 {len(signal_logs) - 10}건")
        
        print(f"\n[상태 변경 로그] ({len(state_changes)}건)")
        for log in state_changes[:20]:  # 최대 20개만 표시
            try:
                print(f"  {log[:150]}")
            except:
                print(f"  [로그 출력 오류]")
        if len(state_changes) > 20:
            print(f"  ... 외 {len(state_changes) - 20}건")
        
        print(f"\n[에러 로그] ({len(error_logs)}건)")
        if error_logs:
            for log in error_logs[:20]:  # 최대 20개만 표시
                try:
                    print(f"  {log[:200]}")
                except:
                    print(f"  [로그 출력 오류]")
            if len(error_logs) > 20:
                print(f"  ... 외 {len(error_logs) - 20}건")
        else:
            print("  에러가 없습니다. [OK]")
        
    except Exception as e:
        print(f"[오류] 로그 파일 분석 오류: {e}")

def analyze_trading_summary(df_real, df_virtual):
    """매매 요약 분석"""
    print("\n" + "=" * 80)
    print("[매매 요약 분석]")
    print("=" * 80)
    
    # 실거래 요약
    if df_real is not None and not df_real.empty:
        real_sells = df_real[df_real['action'] == 'SELL']
        if not real_sells.empty:
            total_profit = real_sells['profit_loss'].sum()
            avg_profit_rate = real_sells['profit_rate'].mean()
            win_count = len(real_sells[real_sells['profit_loss'] > 0])
            loss_count = len(real_sells[real_sells['profit_loss'] <= 0])
            
            print("\n[실거래 요약]")
            print(f"  총 거래 건수: {len(real_sells)}건")
            print(f"  승률: {win_count}/{len(real_sells)} ({win_count/len(real_sells)*100:.1f}%)")
            print(f"  총 손익: {total_profit:+,.0f}원")
            print(f"  평균 수익률: {avg_profit_rate:.2f}%")
            print(f"  최대 수익: {real_sells['profit_loss'].max():+,.0f}원")
            print(f"  최대 손실: {real_sells['profit_loss'].min():+,.0f}원")
    
    # 가상 매매 요약
    if df_virtual is not None and not df_virtual.empty:
        virtual_sells = df_virtual[df_virtual['action'] == 'SELL']
        if not virtual_sells.empty:
            total_profit = virtual_sells['profit_loss'].sum()
            avg_profit_rate = virtual_sells['profit_rate'].mean()
            win_count = len(virtual_sells[virtual_sells['profit_loss'] > 0])
            loss_count = len(virtual_sells[virtual_sells['profit_loss'] <= 0])
            
            print("\n[가상 매매 요약]")
            print(f"  총 거래 건수: {len(virtual_sells)}건")
            print(f"  승률: {win_count}/{len(virtual_sells)} ({win_count/len(virtual_sells)*100:.1f}%)")
            print(f"  총 손익: {total_profit:+,.0f}원")
            print(f"  평균 수익률: {avg_profit_rate:.2f}%")
            print(f"  최대 수익: {virtual_sells['profit_loss'].max():+,.0f}원")
            print(f"  최대 손실: {virtual_sells['profit_loss'].min():+,.0f}원")

if __name__ == "__main__":
    print(f"\n{'='*80}")
    print(f"2025년 1월 22일 (목요일) 매매 분석 리포트")
    print(f"{'='*80}\n")
    
    # 데이터베이스 분석
    df_real, df_virtual = analyze_database_trades()
    
    # 로그 파일 분석
    analyze_log_file()
    
    # 요약 분석
    analyze_trading_summary(df_real, df_virtual)
    
    print("\n" + "=" * 80)
    print("[분석 완료]")
    print("=" * 80)
