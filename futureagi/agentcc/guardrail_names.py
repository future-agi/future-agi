"""The names the console gives guardrail checks, and what the gateway reads.

config_push turns a stored check into the gateway's shape with these tables,
and validators reads them to find the strings where the gateway refuses a
${NAME} in org config, so the two agree on what each check is. They live
outside config_push so validators can import them without an import cycle.
"""

# Map org-config rule names → gateway registry names
RULE_TO_REGISTRY = {
    "pii-detector": "pii-detection",
    "injection-detector": "prompt-injection",
    "secrets-detector": "secret-detection",
    # These are the same in both:
    "content-moderation": "content-moderation",
    "keyword-blocklist": "keyword-blocklist",
    "topic-restriction": "topic-restriction",
    "language-detection": "language-detection",
    "system-prompt-protection": "system-prompt-protection",
    "hallucination-detection": "hallucination-detection",
    "data-leakage-prevention": "data-leakage-prevention",
}

# External guardrail rules that need a "provider" key in their config
# for the gateway's dynamic factory to recognize them.
RULE_PROVIDER_DEFAULTS = {
    "futureagi-eval": "futureagi",
    "llama-guard": "llama_guard",
    "azure-content-safety": "azure_content_safety",
    "presidio-pii": "presidio",
    "lakera-guard": "lakera",
    "bedrock-guardrails": "bedrock_guardrails",
    "hiddenlayer-guard": "hiddenlayer",
    "aporia-guard": "aporia",
    "pangea-guard": "pangea",
    "dynamoai-guard": "dynamoai",
    "enkrypt-guard": "enkrypt",
    "ibm-ai-detector": "ibm_ai",
    "grayswan-guard": "grayswan",
    "lasso-guard": "lasso",
    "crowdstrike-aidr": "crowdstrike",
    "zscaler-guard": "zscaler",
    "tool-permissions": "tool_permissions",
    "mcp-security": "mcp_security",
}
