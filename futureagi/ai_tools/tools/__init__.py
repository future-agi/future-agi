# Import the native tool modules to trigger @register_tool decorators.
# This is called from AiToolsConfig.ready().
#
# Everything that proxies a public REST endpoint now comes from the
# OpenAPI-generated MCP catalog (see ai_tools.generated). Only tools that have
# no API equivalent stay here: workspace administration, LLM-driven analysis,
# documentation, web/knowledge-base search, and Falcon's discovery helpers.

# Context / discovery tools (3)
from ai_tools.tools.context import read_schema  # noqa: F401
from ai_tools.tools.context import read_taxonomy  # noqa: F401
from ai_tools.tools.context import search  # noqa: F401

# Documentation tools (3)
from ai_tools.tools.docs import ask_docs  # noqa: F401
from ai_tools.tools.docs import get_page  # noqa: F401
from ai_tools.tools.docs import search_docs  # noqa: F401

# Agent-driven evaluation (1)
from ai_tools.tools.evaluations import evaluate_with_agent  # noqa: F401

# Trace analysis pipeline and visualization (7)
from ai_tools.tools.tracing import analyze_error_cluster  # noqa: F401
from ai_tools.tools.tracing import analyze_project_traces  # noqa: F401
from ai_tools.tools.tracing import explore_trace  # noqa: F401
from ai_tools.tools.tracing import read_trace_span  # noqa: F401
from ai_tools.tools.tracing import render_widget  # noqa: F401
from ai_tools.tools.tracing import submit_trace_finding  # noqa: F401
from ai_tools.tools.tracing import submit_trace_scores  # noqa: F401

# Organization, workspace, member and API key administration (17)
from ai_tools.tools.users import add_workspace_member  # noqa: F401
from ai_tools.tools.users import create_api_key  # noqa: F401
from ai_tools.tools.users import create_workspace  # noqa: F401
from ai_tools.tools.users import deactivate_user  # noqa: F401
from ai_tools.tools.users import get_organization  # noqa: F401
from ai_tools.tools.users import get_user  # noqa: F401
from ai_tools.tools.users import get_user_permissions  # noqa: F401
from ai_tools.tools.users import invite_users  # noqa: F401
from ai_tools.tools.users import list_api_keys  # noqa: F401
from ai_tools.tools.users import list_org_members  # noqa: F401
from ai_tools.tools.users import list_organizations  # noqa: F401
from ai_tools.tools.users import list_users  # noqa: F401
from ai_tools.tools.users import list_workspace_members  # noqa: F401
from ai_tools.tools.users import remove_user  # noqa: F401
from ai_tools.tools.users import revoke_api_key  # noqa: F401
from ai_tools.tools.users import update_user_role  # noqa: F401
from ai_tools.tools.users import update_workspace  # noqa: F401

# Web, knowledge base and trace exploration (3)
from ai_tools.tools.web import brave_search  # noqa: F401
from ai_tools.tools.web import kb_search  # noqa: F401
from ai_tools.tools.web import trace_explorer  # noqa: F401
