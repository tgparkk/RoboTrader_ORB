"""
PostgreSQL 데이터베이스 연결 설정
"""
import os
from dataclasses import dataclass


@dataclass
class PostgresConfig:
    host: str = "127.0.0.1"
    port: int = 5433
    database: str = "robotrader_orb"
    user: str = "postgres"
    password: str = ""


# dict 형식 설정 (database_manager.py 호환)
DB_CONFIG = {
    'host': os.environ.get('PG_HOST', '127.0.0.1'),
    'port': int(os.environ.get('PG_PORT', '5433')),
    'dbname': os.environ.get('PG_DATABASE', 'robotrader_orb'),
    'user': os.environ.get('PG_USER', 'postgres'),
    'password': os.environ.get('PG_PASSWORD', ''),
}


def get_postgres_config() -> PostgresConfig:
    """환경변수 또는 기본값으로 PostgreSQL 설정 반환"""
    return PostgresConfig(
        host=os.environ.get("PG_HOST", "127.0.0.1"),
        port=int(os.environ.get("PG_PORT", "5433")),
        database=os.environ.get("PG_DATABASE", "robotrader_orb"),
        user=os.environ.get("PG_USER", "postgres"),
        password=os.environ.get("PG_PASSWORD", ""),
    )
