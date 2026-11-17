// Auto-routed request logs (GET /agentcc/request-logs/?tags=auto_requested:auto)
// from the local demo stack, 2026-10-20, trimmed to the fields the auto
// routing page reads.
export const AUTO_LOGS = {
  count: 13,
  results: [
    {
      id: "fa730417-cb68-4793-bf11-0ac1aa255c4c",
      request_id: "01M5DBMGCH17QN485471W5ZB4J",
      model: "mistralai/ministral-14b-2512",
      provider: "openrouter",
      started_at: "2026-10-20T19:11:58.353626Z",
      latency_ms: 14,
      cost: "0.000003",
      status_code: 200,
      is_error: false,
      metadata: {
        auto_model: "mistralai/ministral-14b-2512",
        auto_requested: "auto",
        auto_task_tier: "simple",
        auto_complexity: "3",
        auto_preference: "balanced",
        auto_eligible: "64",
        auto_considered: "64",
        auto_fallbacks: "anthropic/claude-haiku-5.5,openai/gpt-6-luna",
        auto_reason:
          "simple task (complexity 3), no special needs, balanced preference: 64 of 64 models eligible",
        auto_decision:
          '{"model":"mistralai/ministral-14b-2512","provider":"openrouter","task_tier":"simple","complexity":3,"preference":"balanced","considered":64,"ranked":[{"model":"mistralai/ministral-14b-2512","provider":"openrouter","tier":0.106,"score":0.994,"est_cost_usd":0.0001018},{"model":"anthropic/claude-haiku-5.5","provider":"openrouter","tier":0.106,"score":0.994,"est_cost_usd":0.00025089999999999997},{"model":"openai/gpt-6-luna","provider":"openrouter","tier":0.106,"score":0.994,"est_cost_usd":0.00025089999999999997},{"model":"openai/gpt-6-luna-pro","provider":"openrouter","tier":0.106,"score":0.994,"est_cost_usd":0.00025089999999999997},{"model":"mistralai/voxtral-small-24b-2507","provider":"openrouter","tier":0.062,"score":0.962,"est_cost_usd":0.00015089999999999998}],"rejected":[{"model":"amazon/nova-2-lite-v1","reason":"managed_model_not_allowed"},{"model":"amazon/nova-premier-v1","reason":"managed_model_not_allowed"},{"model":"cohere/command-a-plus","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v3.2","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-flash","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-flash-0731","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-flash-vision-exp","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-pro","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-pro-0813","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4.1-flash","reason":"managed_model_not_allowed"}],"fallbacks":["anthropic/claude-haiku-5.5","openai/gpt-6-luna"]}',
        billing_mode: "managed",
      },
    },
    {
      id: "4499a870-1ba1-4937-80ac-d791e6fa359f",
      request_id: "01M5CCM9JAPMSJBGXBDXF7D2BR",
      model: "mistralai/ministral-8b-2512",
      provider: "openrouter",
      started_at: "2026-10-20T10:10:05.514480Z",
      latency_ms: 8,
      cost: "0.000002",
      status_code: 200,
      is_error: false,
      metadata: {
        auto_model: "mistralai/ministral-8b-2512",
        auto_requested: "auto",
        auto_task_tier: "simple",
        auto_complexity: "5",
        auto_preference: "balanced",
        auto_eligible: "64",
        auto_considered: "64",
        auto_fallbacks:
          "mistralai/ministral-14b-2512,anthropic/claude-haiku-5.5",
        auto_reason:
          "simple task (complexity 5), no special needs, balanced preference: 64 of 64 models eligible",
        auto_decision:
          '{"model":"mistralai/ministral-8b-2512","provider":"openrouter","task_tier":"simple","complexity":5,"preference":"balanced","considered":64,"ranked":[{"model":"mistralai/ministral-8b-2512","provider":"openrouter","tier":0.062,"score":0.929,"est_cost_usd":0.000036599999999999995,"latency_ms":1,"success_rate":1},{"model":"mistralai/ministral-14b-2512","provider":"openrouter","tier":0.106,"score":0.927,"est_cost_usd":0.0000488,"latency_ms":2,"success_rate":1},{"model":"anthropic/claude-haiku-5.5","provider":"openrouter","tier":0.106,"score":0.927,"est_cost_usd":0.00010439999999999999,"latency_ms":2,"success_rate":1},{"model":"openai/gpt-6-luna-pro","provider":"openrouter","tier":0.106,"score":0.927,"est_cost_usd":0.00010439999999999999,"latency_ms":2,"success_rate":1},{"model":"mistralai/mistral-small-2603","provider":"openrouter","tier":0.148,"score":0.919,"est_cost_usd":0.0001266,"latency_ms":1,"success_rate":1}],"rejected":[{"model":"amazon/nova-2-lite-v1","reason":"managed_model_not_allowed"},{"model":"amazon/nova-premier-v1","reason":"managed_model_not_allowed"},{"model":"cohere/command-a-plus","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v3.2","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-flash","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-flash-0731","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-flash-vision-exp","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-pro","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-pro-0813","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4.1-flash","reason":"managed_model_not_allowed"}],"fallbacks":["mistralai/ministral-14b-2512","anthropic/claude-haiku-5.5"]}',
        billing_mode: "managed",
      },
    },
    {
      id: "85566ec0-96f3-4bc9-9ef0-30e397793faf",
      request_id: "01M5CCM9HB68W2FA3ZE4706B4D",
      model: "mistralai/ministral-8b-2512",
      provider: "openrouter",
      started_at: "2026-10-20T10:10:05.483879Z",
      latency_ms: 23,
      cost: "0.000002",
      status_code: 200,
      is_error: false,
      metadata: {
        auto_model: "mistralai/ministral-8b-2512",
        auto_requested: "auto",
        auto_task_tier: "simple",
        auto_complexity: "5",
        auto_preference: "balanced",
        auto_eligible: "64",
        auto_considered: "64",
        auto_fallbacks:
          "mistralai/ministral-14b-2512,anthropic/claude-haiku-5.5",
        auto_reason:
          "simple task (complexity 5), no special needs, balanced preference: 64 of 64 models eligible",
        auto_decision:
          '{"model":"mistralai/ministral-8b-2512","provider":"openrouter","task_tier":"simple","complexity":5,"preference":"balanced","considered":64,"ranked":[{"model":"mistralai/ministral-8b-2512","provider":"openrouter","tier":0.062,"score":0.929,"est_cost_usd":0.0000363,"latency_ms":1,"success_rate":1},{"model":"mistralai/ministral-14b-2512","provider":"openrouter","tier":0.106,"score":0.927,"est_cost_usd":0.0000484,"latency_ms":2,"success_rate":1},{"model":"anthropic/claude-haiku-5.5","provider":"openrouter","tier":0.106,"score":0.927,"est_cost_usd":0.0001042,"latency_ms":2,"success_rate":1},{"model":"openai/gpt-6-luna-pro","provider":"openrouter","tier":0.106,"score":0.927,"est_cost_usd":0.0001042,"latency_ms":2,"success_rate":1},{"model":"mistralai/mistral-small-2603","provider":"openrouter","tier":0.148,"score":0.919,"est_cost_usd":0.0001263,"latency_ms":1,"success_rate":1}],"rejected":[{"model":"amazon/nova-2-lite-v1","reason":"managed_model_not_allowed"},{"model":"amazon/nova-premier-v1","reason":"managed_model_not_allowed"},{"model":"cohere/command-a-plus","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v3.2","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-flash","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-flash-0731","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-flash-vision-exp","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-pro","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-pro-0813","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4.1-flash","reason":"managed_model_not_allowed"}],"fallbacks":["mistralai/ministral-14b-2512","anthropic/claude-haiku-5.5"]}',
        billing_mode: "managed",
      },
    },
    {
      id: "8e687ece-4e6f-4124-9458-0a50c8018f12",
      request_id: "01M5CCM9H0QYXQ1E74B7VC59T1",
      model: "mistralai/ministral-8b-2512",
      provider: "openrouter",
      started_at: "2026-10-20T10:10:05.472973Z",
      latency_ms: 5,
      cost: "0.000002",
      status_code: 200,
      is_error: false,
      metadata: {
        auto_model: "mistralai/ministral-8b-2512",
        auto_requested: "auto",
        auto_task_tier: "simple",
        auto_complexity: "5",
        auto_preference: "balanced",
        auto_eligible: "64",
        auto_considered: "64",
        auto_fallbacks:
          "mistralai/ministral-14b-2512,anthropic/claude-haiku-5.5",
        auto_reason:
          "simple task (complexity 5), no special needs, balanced preference: 64 of 64 models eligible",
        auto_decision:
          '{"model":"mistralai/ministral-8b-2512","provider":"openrouter","task_tier":"simple","complexity":5,"preference":"balanced","considered":64,"ranked":[{"model":"mistralai/ministral-8b-2512","provider":"openrouter","tier":0.062,"score":0.929,"est_cost_usd":0.00003615,"latency_ms":1,"success_rate":1},{"model":"mistralai/ministral-14b-2512","provider":"openrouter","tier":0.106,"score":0.927,"est_cost_usd":0.0000482,"latency_ms":2,"success_rate":1},{"model":"anthropic/claude-haiku-5.5","provider":"openrouter","tier":0.106,"score":0.927,"est_cost_usd":0.0001041,"latency_ms":2,"success_rate":1},{"model":"openai/gpt-6-luna-pro","provider":"openrouter","tier":0.106,"score":0.927,"est_cost_usd":0.0001041,"latency_ms":2,"success_rate":1},{"model":"mistralai/mistral-small-2603","provider":"openrouter","tier":0.148,"score":0.919,"est_cost_usd":0.00012615,"latency_ms":1,"success_rate":1}],"rejected":[{"model":"amazon/nova-2-lite-v1","reason":"managed_model_not_allowed"},{"model":"amazon/nova-premier-v1","reason":"managed_model_not_allowed"},{"model":"cohere/command-a-plus","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v3.2","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-flash","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-flash-0731","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-flash-vision-exp","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-pro","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4-pro-0813","reason":"managed_model_not_allowed"},{"model":"deepseek/deepseek-v4.1-flash","reason":"managed_model_not_allowed"}],"fallbacks":["mistralai/ministral-14b-2512","anthropic/claude-haiku-5.5"]}',
        billing_mode: "managed",
      },
    },
  ],
};
