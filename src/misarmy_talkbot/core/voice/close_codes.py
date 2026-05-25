import os


def watchdog_interval() -> float:
    return float(os.getenv('WS_WATCHDOG_INTERVAL_SECONDS', '5'))


def unhealthy_grace() -> float:
    return float(os.getenv('WS_UNHEALTHY_GRACE_SECONDS', '5'))


def library_grace_s() -> float:
    return int(os.getenv('WS_LIBRARY_GRACE_MS', '1500')) / 1000.0


def wait_4014_s() -> float:
    return int(os.getenv('WS_4014_WAIT_MS', '1500')) / 1000.0


def rate_limit_backoff_s() -> float:
    return int(os.getenv('WS_RATE_LIMIT_BACKOFF_MS', '5000')) / 1000.0


def voice_connect_timeout() -> float:
    return float(os.getenv('VOICE_CONNECT_TIMEOUT', '30'))
