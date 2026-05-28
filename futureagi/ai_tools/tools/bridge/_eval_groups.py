"""Bridge registration for EvalGroupView.

EvalGroupView is in a legacy file with pre-existing lint debt that would
block commits if touched. Decorator is applied programmatically here.
Tool names are auto-derived from the action verb + entity ('eval_group'):
list_eval_groups, get_eval_group, create_eval_group, update_eval_group,
delete_eval_group.
"""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.eval_group import EvalGroupView

expose_to_mcp(category="evaluations")(EvalGroupView)
