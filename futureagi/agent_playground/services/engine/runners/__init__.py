"""
Node Runners for Graph Execution Engine.

This package contains implementations of BaseNodeRunner for different node templates.
Runners self-register on module import.

Current runners:
    - llm_prompt: LLM completion using LiteLLM

Adding a new runner:
    1. Create a new file (e.g., http_request.py)
    2. Subclass BaseNodeRunner and implement the run() method
    3. Self-register at the bottom: register_runner("template_name", MyRunner())
    4. Import the module here to trigger registration
"""

# Import runner modules to trigger self-registration.
# Add new runners here as they are created.
import agent_playground.services.engine.runners.llm_prompt  # noqa: F401
import agent_playground.services.engine.runners.http_request  # noqa: F401
