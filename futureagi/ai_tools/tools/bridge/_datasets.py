"""Bridge registration for DatasetView.

tracer/views/dataset.py is short enough to take the decorator inline, but
keeping it here for consistency with the legacy-file bridge pattern.
Standard CRUD names auto-generated: list_datasets, get_dataset,
create_dataset, update_dataset, delete_dataset.
"""

from ai_tools.drf_bridge import expose_to_mcp
from tracer.views.dataset import DatasetView

expose_to_mcp(category="datasets")(DatasetView)
