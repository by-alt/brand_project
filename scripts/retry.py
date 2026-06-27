"""
Automatic retries with exponential backoff (scripts/retry.py).

A small, dependency-free decorator for the real-world failure mode of this pipeline:
a platform API hiccups or rate-limits a single request. Instead of failing the whole
run, we retry a few times with growing delays.

This is the genuinely useful core of "automatic retries" — it works on its own, with
no Celery/Redis required. (The Celery blueprint in scripts/tasks.py uses this same
idea at the task level for those who want a distributed queue later.)

Example:
    from scripts.retry import with_retries

    @with_retries(max_attempts=4, base_delay=2.0)
    def fetch_reviews_for(app_id):
        ...  # raises on transient network/rate-limit errors
"""
import time
import functools


def with_retries(max_attempts=3, base_delay=1.0, backoff=2.0,
                 exceptions=(Exception,), sleep=time.sleep, on_retry=None):
    """Retry the wrapped function on `exceptions`, with exponential backoff.

    Args:
        max_attempts: total tries before giving up (must be >= 1).
        base_delay:   seconds to wait before the first retry.
        backoff:      multiplier applied to the delay each subsequent retry.
        exceptions:   tuple of exception types that should trigger a retry.
        sleep:        sleep function — injectable so tests run instantly.
        on_retry:     optional callback(attempt, exc, delay) for logging.

    The final failure is re-raised, so callers still see genuine, persistent errors.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = base_delay
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break  # out of tries — re-raise below
                    if on_retry:
                        on_retry(attempt, exc, delay)
                    sleep(delay)
                    delay *= backoff
            raise last_exc
        return wrapper
    return decorator
