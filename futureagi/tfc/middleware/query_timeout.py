import time
from functools import wraps

import structlog
from django.db import connection

logger = structlog.get_logger(__name__)


class QueryTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Set query timeout
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout TO '30s'")

        start_time = time.time()
        response = self.get_response(request)
        execution_time = time.time() - start_time

        if execution_time > 10:  # Log slow queries
            logger.warning(
                f"Slow query detected: {request.path} took {execution_time:.2f}s"
            )

        return response


def monitor_query_performance(func):
    """Decorator to monitor query performance"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        initial_queries = len(connection.queries)

        result = func(*args, **kwargs)

        end = time.time()
        final_queries = len(connection.queries)

        if end - start > 3.0:  # Log queries taking more than 1 second
            logger.warning(
                f"Slow operation in {func.__name__}: "
                f"Time: {end - start:.2f}s, "
                f"Queries: {final_queries - initial_queries}"
            )

        return result

    return wrapper
