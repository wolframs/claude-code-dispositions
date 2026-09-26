import time
import urllib.request
from . import config

def fetch(url):
    last_err = None
    for attempt in range(config.RETRY_LIMIT):
        try:
            with urllib.request.urlopen(url, timeout=config.TIMEOUT_S) as resp:
                return resp.read()
        except Exception as err:  # noqa: BLE001 - single retry funnel
            last_err = err
            time.sleep(config.RETRY_BACKOFF_S)
    raise last_err
