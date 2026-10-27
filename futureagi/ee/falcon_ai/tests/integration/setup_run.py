import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
logging.disable(logging.CRITICAL)
import django

django.setup()

# 1. Create ClickHouse table
from tracer.services.clickhouse.client import ClickHouseClient

ch = ClickHouseClient()
ch.execute(
    """
CREATE TABLE IF NOT EXISTS falcon_analysis_log (
    id UUID DEFAULT generateUUIDv4(),
    conversation_id UUID,
    trace_id UUID,
    project_id UUID,
    organization_id UUID,
    model String DEFAULT '',
    mode String DEFAULT '',
    skill_slug String DEFAULT '',
    input_tokens UInt32 DEFAULT 0,
    output_tokens UInt32 DEFAULT 0,
    tool_calls String DEFAULT '[]',
    response String DEFAULT '',
    errors_found UInt16 DEFAULT 0,
    overall_score Nullable(Float32),
    recommended_priority String DEFAULT '',
    started_at DateTime64(3) DEFAULT now64(),
    completed_at DateTime64(3) DEFAULT now64()
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(started_at)
ORDER BY (organization_id, project_id, trace_id, started_at)
"""
)
print("1. CH table created")

# 2. Reset project
from tracer.models.trace import Trace, TraceErrorAnalysisStatus
from tracer.models.trace_error_analysis import TraceErrorAnalysis, TraceErrorDetail

pid = "a5b0c2b1-aa2a-49c1-b896-2fba4a9121b9"
Trace.objects.filter(project_id=pid).update(
    error_analysis_status=TraceErrorAnalysisStatus.PENDING
)
TraceErrorDetail.objects.filter(analysis__project_id=pid).delete()
TraceErrorAnalysis.objects.filter(project_id=pid).delete()
print("2. DB reset")

# 3. Clean old hidden conversations
from ee.falcon_ai.models import Conversation

Conversation.objects.filter(title__startswith="Auto-analysis").delete()
print("3. Old conversations cleaned")

# 4. Clear CH logs
try:
    ch.execute("TRUNCATE TABLE falcon_analysis_log")
    print("4. CH logs cleared")
except Exception:
    print("4. CH logs table fresh")

print("READY")
