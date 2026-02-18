"""
로깅 시스템 설정
"""
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Union
import time

try:
    # 선택적: 한국시간 변환 지원
    from utils.korean_time import KST
except Exception:
    KST = None


def setup_logger(
    name: str,
    level: int = logging.DEBUG,
    file_path: Optional[Union[str, Path]] = None,
    use_kst: bool = False,
):
    """로거 설정

    Parameters:
    - name: 로거 이름
    - level: 로그 레벨
    - file_path: 지정 시 해당 경로로 파일 출력, 미지정 시 logs/trading_YYYYMMDD.log
    - use_kst: 로그 타임스탬프를 한국시간(KST)으로 변환해 표시
    """

    # 로그 디렉토리 및 파일 경로
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    if file_path is None:
        from utils.korean_time import now_kst
        today = now_kst().strftime("%Y%m%d")
        log_file = log_dir / f"trading_{today}.log"
    else:
        log_file = Path(file_path)
        if not log_file.parent.exists():
            log_file.parent.mkdir(parents=True, exist_ok=True)

    # 로거 생성/초기화
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    # 이미 핸들러가 있으면 제거 (중복 방지)
    if logger.handlers:
        logger.handlers.clear()

    # 포맷터 설정
    formatter = logging.Formatter(
        '%(asctime)s | %(name)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # KST 타임스탬프 변환기
    if use_kst and KST is not None:
        def _kst_converter(secs: float):
            try:
                return datetime.fromtimestamp(secs, KST).timetuple()
            except Exception:
                return time.localtime(secs)
        formatter.converter = _kst_converter  # type: ignore[attr-defined]

    # 파일 핸들러
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 콘솔 핸들러 (Windows cp949 인코딩 문제 방지)
    try:
        # UTF-8로 콘솔 출력 강제 설정 (이미 설정된 경우 건너뜀)
        if not hasattr(sys.stdout, 'encoding') or sys.stdout.encoding.lower() != 'utf-8':
            import io
            if isinstance(sys.stdout, io.TextIOWrapper) and sys.stdout.name == '<stdout>':
                 # detach()를 사용하면 기존 버퍼가 닫힐 위험이 있으므로, 안전하게 감싸기만 시도
                 # 하지만 여기서는 이미 래핑된 경우를 감지하기 어려우므로 간단히 encoding 체크만 수행
                 pass
            # 주의: sys.stdout 재설정은 프로세스당 한 번만 수행하는 것이 안전함.
            # 여기서는 로거 설정 때마다 호출되므로, 부작용 방지를 위해 주석 처리하거나 안전 장치 필요
            # sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            # sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
            pass 
    except Exception:
        pass  # 이미 설정되었거나 불가능한 경우 무시

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger