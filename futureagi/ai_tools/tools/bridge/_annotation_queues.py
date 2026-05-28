"""Bridge registration for AnnotationQueueViewSet.

AnnotationQueueViewSet lives in model_hub/views/annotation_queues.py — a
4500-line legacy file. Decorator applied programmatically. Tool names
auto-derived from action verb + 'annotation_queue':
list_annotation_queues, get_annotation_queue, create_annotation_queue,
update_annotation_queue, delete_annotation_queue.
"""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.annotation_queues import AnnotationQueueViewSet

expose_to_mcp(category="annotation_queues")(AnnotationQueueViewSet)
