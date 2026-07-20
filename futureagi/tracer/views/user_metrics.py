import math
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db import connection

class UserMetricsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, user_id, *args, **kwargs):
        query = """
            SELECT 
                COUNT(*) as trace_count,
                COUNT(CASE WHEN status = 'ERROR' THEN 1 END) as error_count,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) as p95_latency
            FROM tracer_observationspan
            WHERE user_id = %s AND parent_span_id IS NULL
        """
        
        try:
            with connection.cursor() as cursor:
                cursor.execute(query, [user_id])
                row = cursor.fetchone()
                
            trace_count = row[0] if row and row[0] is not None else 0
            error_count = row[1] if row and row[1] is not None else 0
            p95_latency = row[2] if row and row[2] is not None else 0
            
            error_rate = (error_count / trace_count * 100) if trace_count > 0 else 0
            
            # Ensure safe json response
            if not math.isfinite(error_rate):
                error_rate = 0
            if not math.isfinite(p95_latency):
                p95_latency = 0
                
            return Response({
                "trace_count": trace_count,
                "error_rate": round(error_rate, 2),
                "p95_latency": round(p95_latency, 2)
            })
            
        except Exception as e:
            return Response({
                "trace_count": 0,
                "error_rate": 0,
                "p95_latency": 0
            })
