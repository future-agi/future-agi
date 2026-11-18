"""
Metric configuration for Fix-Your-Agent system-level analysis.

``METRICS_CONFIG`` defines:
- Aggregate human benchmarks (``agg_*``), and
- Per-call / system-latency thresholds for deterministic issue groups.

Each metric may include:
- ``human_benchmark``: typical human-agent behaviour (label, unit, range)
- ``issue_group``: how we flag problematic calls
- ``sources``: external references that justify the chosen ranges/thresholds

When changing values, keep keys and units stable (code depends on them) and
prefer small, well-justified edits backed by sources where possible.
"""

METRICS_CONFIG = {
    # =========================
    # Aggregate (Human Benchmarks)
    # =========================
    # Chat benchmarks (used by chat human-comparison only)
    "agg_avg_latency_ms": {
        "label": "AVERAGE CHAT LATENCY",
        "description": "Average time (ms) the chat agent takes to respond after a customer message.",
        "human_benchmark": {
            "label": "AVERAGE CHAT RESPONSE LATENCY (MS)",
            "description": "In support chat, users typically expect responses within a few seconds; longer delays often feel unresponsive.",
            "unit": "ms",
            "range": "1000-3000 ms (reasonable); 3000-6000 ms (slow); >6000 ms (often feels unresponsive)",
            "sources": [
                "https://www.zendesk.com/blog/customer-service-response-times/",
            ],
        },
        "prefer_upper_outliers": True,
        "modalities": ["chat"],
    },
    "agg_turn_count": {
        "label": "AVERAGE TURN COUNT",
        "description": "Average number of customer/agent turns in a chat conversation.",
        "human_benchmark": {
            "label": "AVERAGE CHAT TURNS (COUNT)",
            "description": "Efficient support chats usually resolve within a moderate number of turns; very high turn counts often indicate confusion or repetition.",
            "unit": "count_per_call",
            "range": "6-12 turns typical; >15 turns often indicates friction or looping",
            "sources": [
                "https://www.zendesk.com/blog/customer-service-response-times/",
            ],
        },
        "prefer_upper_outliers": True,
        "modalities": ["chat"],
    },
    "agg_total_tokens": {
        "label": "AVERAGE TOTAL TOKENS",
        "description": "Average total tokens consumed per chat conversation (input + output).",
        "prefer_upper_outliers": True,
        "modalities": ["chat"],
    },
    "agg_input_tokens": {
        "label": "AVERAGE INPUT TOKENS",
        "description": "Average input tokens consumed per chat conversation.",
        "prefer_upper_outliers": True,
        "modalities": ["chat"],
    },
    "agg_output_tokens": {
        "label": "AVERAGE OUTPUT TOKENS",
        "description": "Average output tokens produced per chat conversation.",
        "prefer_upper_outliers": True,
        "modalities": ["chat"],
    },
    "agg_csat_score": {
        "label": "AVERAGE CSAT SCORE",
        "description": "Average chat CSAT score for the simulation (1-10).",
        "human_benchmark": {
            "label": "AVERAGE CHAT CSAT",
            "description": "Support chats with strong customer experience typically cluster toward the top end of the CSAT scale; lower averages suggest friction, confusion, or unresolved issues.",
            "unit": "csat_1_10",
            "range": "7.5-8.5 / 10 (good); >9.0 / 10 (exemplary); <7.0 / 10 (needs improvement)",
            "sources": [
                "https://www.zendesk.com/in/blog/customer-satisfaction-score/",
            ],
        },
        "prefer_upper_outliers": False,
        "modalities": ["chat"],
    },
    "agg_csat": {
        "label": "AVERAGE CSAT",
        "description": "Call-level satisfaction score (1-10) reflecting how satisfied the customer sounded with the overall experience with the voice AI agent.",
        "human_benchmark": {
            "label": "AVERAGE CSAT (1-10)",
            "description": "Typical call-center CSAT often considered 'good' around ~75-85% (convert to 7.5-8.5/10); >90% is often described as exemplary.",
            "unit": "csat_1_10",
            "range": "7.5-8.5 / 10 (good); >9.0 / 10 (exemplary)",
            "sources": [
                "https://www.zendesk.com/in/blog/customer-satisfaction-score/",
                "https://www.plivo.com/blog/contact-center-statistics-benchmarks-2025/",
            ],
        },
        "prefer_upper_outliers": False,
    },
    "agg_talk_ratio": {
        "label": "AVERAGE TALK RATIO",
        "description": "Relative speaking balance: >1 means the voice AI agent talks more than the customer; <1 means the customer talks more.",
        "human_benchmark": {
            "label": "AVERAGE TALK RATIO (AGENT/CUSTOMER)",
            "description": "For many service/sales calls, a balanced dialogue tends to have the agent speaking less than the customer; a widely-cited target is ~43% talk / 57% listen (ratio ≈ 0.75).",
            "unit": "ratio",
            "range": "0.7-1.1 (balanced); ~0.75 is a common target for sales-heavy calls",
            "sources": [
                "https://www.gong.io/blog/talk-to-listen-conversion-ratio",
                "https://aircall.io/blog/features/talk-to-listen-ratio/",
            ],
        },
        "prefer_upper_outliers": True,
    },
    "agg_agent_latency_ms": {
        "label": "AVERAGE AGENT LATENCY",
        "description": "Average time (ms) the voice AI agent takes to start speaking after the customer finishes a turn (reaction latency).",
        "human_benchmark": {
            "label": "AVERAGE AGENT REACTION LATENCY (MS)",
            "description": "Human turn-taking gaps are typically very short (modal ~200ms). In phone/support contexts, a practical 'human-like' target is still sub-second.",
            "unit": "ms",
            "range": "200-800 ms (human-like); 800-1200 ms (noticeable); >1200 ms (often feels laggy)",
            "sources": [
                "https://www.pnas.org/doi/10.1073/pnas.0903616106",
                "https://www.pnas.org/doi/10.1073/pnas.2116915119",
                "https://telnyx.com/resources/how-telnyx-fixed-voice-ai-latency-with-co-located-infrastructure",
            ],
        },
        "prefer_upper_outliers": True,
    },
    "agg_response_time_ms": {
        "label": "AVERAGE RESPONSE TIME",
        "description": "Average duration (ms) of each spoken response once the voice AI agent starts speaking (length of the agent's utterances).",
        "human_benchmark": {
            "label": "AVERAGE AGENT RESPONSE DURATION (MS)",
            "description": "Service agents often use slightly longer turns for explanations, but very long responses degrade turn-taking and feel monologue-like.",
            "unit": "ms",
            "range": "average response duration of 10-20 seconds is fine; But greater than 20000 ms (20 seconds) on average is a bit monologue-like",
            "sources": [
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC10077995/",
                "https://journalofcognition.org/articles/10.5334/joc.268",
            ],
        },
        "prefer_upper_outliers": True,
    },
    "agg_agent_talk_percentage": {
        "label": "AGENT TALK PERCENTAGE",
        "description": "Estimated percentage of total talk time across calls spoken by the voice AI agent.",
        "human_benchmark": {
            "label": "AGENT TALK PERCENTAGE (%)",
            "description": "A commonly cited high-performing pattern in sales-oriented calls is ~43% agent talk; sustained agent talk >65% is often linked to worse outcomes.",
            "unit": "percent",
            "range": "35-55% typical; >65% usually too agent-dominant",
            "sources": [
                "https://www.gong.io/blog/talk-to-listen-conversion-ratio",
                "https://aircall.io/blog/features/talk-to-listen-ratio/",
            ],
        },
        "prefer_upper_outliers": True,
    },
    "agg_customer_talk_percentage": {
        "label": "CUSTOMER TALK PERCENTAGE",
        "description": "Estimated percentage of total talk time across calls spoken by customers.",
        "human_benchmark": {
            "label": "CUSTOMER TALK PERCENTAGE (%)",
            "description": "In dialogue-like calls, customers commonly speak at least as much as the agent; a widely cited target is ~57% customer talk in many sales-heavy calls.",
            "unit": "percent",
            "range": "45-65% typical",
            "sources": [
                "https://www.gong.io/blog/talk-to-listen-conversion-ratio",
                "https://aircall.io/blog/features/talk-to-listen-ratio/",
            ],
        },
        "prefer_upper_outliers": False,
    },
    # NOTE: interruption *counts per call* are not commonly published as universal “benchmarks”.
    # The defaults below can be calliberated.
    "agg_user_interruption_count": {
        "label": "AVERAGE CUSTOMER INTERRUPTIONS",
        "description": "Average number of times per call that customers interrupt the voice AI agent.",
        "human_benchmark": {
            "label": "AVERAGE CUSTOMER INTERRUPTIONS PER CALL",
            "description": "Human conversation strongly minimizes overlap; frequent customer barge-ins often indicate latency or long turns.",
            "unit": "count_per_call",
            "range": "0-2 per call typical; >3 often indicates friction",
            "sources": [
                "https://www.pnas.org/doi/10.1073/pnas.0903616106",
                "https://journalofcognition.org/articles/10.5334/joc.268",
                "https://telnyx.com/resources/how-telnyx-fixed-voice-ai-latency-with-co-located-infrastructure",
            ],
        },
        "prefer_upper_outliers": True,
    },
    "agg_agent_interruption_count": {
        "label": "AVERAGE AGENT INTERRUPTIONS",
        "description": "Average number of times per call that the agent interrupts customers mid-utterance.",
        "human_benchmark": {
            "label": "AVERAGE AGENT INTERRUPTIONS PER CALL",
            "description": "Overlaps are typically kept low in efficient turn-taking; frequent agent barge-ins are perceived as 'talking over' the user.",
            "unit": "count_per_call",
            "range": "0-1 per call typical; >2 is usually disruptive",
            "sources": [
                "https://journalofcognition.org/articles/10.5334/joc.268",
            ],
        },
        "prefer_upper_outliers": True,
    },
    # =========================
    # Per-scenario Issue Groups (Industry / Voice-AI Standards)
    # =========================
    "csat": {
        "label": "CSAT",
        "description": "Call-level satisfaction score (1-10).",
        "issue_group": {
            "id": "low_csat",
            "description": "Flag calls where satisfaction is below commonly cited 'good' call-center ranges.",
            "unit": "score_1_10",
            "criteria": "<",
            "threshold": 7.5,  # aligns to ~75% 'good' CSAT guidance
            "category": "per_scenario",
            "sources": [
                "https://www.zendesk.com/in/blog/customer-satisfaction-score/",
                "https://www.plivo.com/blog/contact-center-statistics-benchmarks-2025/",
            ],
        },
        "prefer_upper_outliers": False,
        "modalities": ["voice"],
    },
    "csat_score": {
        "label": "CSAT SCORE",
        "description": "Chat satisfaction score (1-10).",
        "issue_group": {
            "id": "low_chat_csat_score",
            "description": "Flag chats where satisfaction is below commonly cited 'good' CSAT ranges.",
            "unit": "score_1_10",
            "criteria": "<",
            "threshold": 7.5,  # aligns to ~75% 'good' CSAT guidance
            "category": "per_scenario",
            "sources": [
                "https://www.zendesk.com/in/blog/customer-satisfaction-score/",
            ],
        },
        "prefer_upper_outliers": False,
        "modalities": ["chat"],
    },
    "avg_latency_ms": {
        "label": "CHAT LATENCY",
        "description": "Average time (ms) the chat agent takes to respond in a single conversation.",
        "issue_group": {
            "id": "high_chat_latency",
            "description": "Flag chats where response latency is high enough to feel unresponsive.",
            "unit": "ms",
            "criteria": ">",
            "threshold": 6000.0,
            "category": "per_scenario",
            "sources": [
                "https://www.zendesk.com/blog/customer-service-response-times/",
            ],
        },
        "prefer_upper_outliers": True,
        "modalities": ["chat"],
    },
    "turn_count": {
        "label": "TURN COUNT",
        "description": "Total number of customer/agent turns in a single chat conversation.",
        "issue_group": {
            "id": "high_chat_turn_count",
            "description": "Flag chats with a high turn count, which often indicates looping or friction.",
            "unit": "count",
            "criteria": ">",
            "threshold": 15.0,
            "category": "per_scenario",
            "sources": [
                "https://www.zendesk.com/blog/customer-service-response-times/",
            ],
        },
        "prefer_upper_outliers": True,
        "modalities": ["chat"],
    },
    "talk_ratio": {
        "label": "TALK RATIO",
        "description": "agent_talk_time / customer_talk_time.",
        "issue_group": {
            "id": "agent_dominates_call_talk_ratio",
            "description": "Flag calls where the agent heavily dominates the conversation.",
            "unit": "ratio",
            "criteria": ">",
            "threshold": 1.5,  # ~60% agent talk (agent/(agent+cust)); dominance warning well before 65%
            "category": "per_scenario",
            "sources": [
                "https://www.gong.io/blog/talk-to-listen-conversion-ratio",
                "https://aircall.io/blog/features/talk-to-listen-ratio/",
            ],
        },
        "prefer_upper_outliers": True,
    },
    "response_time_ms": {
        "label": "RESPONSE TIME",
        "description": "Average duration (ms) of each spoken response within a call.",
        "issue_group": {
            "id": "long_responses_per_call",
            "description": "Flag calls where the agent's average utterance is long enough to feel monologue-like.",
            "unit": "ms",
            "criteria": ">",
            "threshold": 20000.0,  # ~20s average response duration per call
            "category": "per_scenario",
            "sources": [
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC10077995/",
                "https://journalofcognition.org/articles/10.5334/joc.268",
            ],
        },
        "prefer_upper_outliers": True,
    },
    "avg_agent_latency_ms": {
        "label": "AGENT LATENCY",
        "description": "Avg reaction latency (ms) in a single call.",
        "issue_group": {
            "id": "laggy_agent_reaction_in_call",
            "description": "Flag calls where reaction latency is slow enough to feel unnatural / cause barge-ins.",
            "unit": "ms",
            "criteria": ">",
            "threshold": 1200.0,  # many voice-ai guides treat ~1s+ as damaging
            "category": "per_scenario",
            "sources": [
                "https://telnyx.com/resources/how-telnyx-fixed-voice-ai-latency-with-co-located-infrastructure",
                "https://elevenlabs.io/blog/how-do-you-optimize-latency-for-conversational-ai",
                "https://www.retellai.com/resources/ai-voice-agent-latency-face-off-2025",
            ],
        },
        "prefer_upper_outliers": True,
    },
    "user_interruption_count": {
        "label": "CUSTOMER INTERRUPTIONS",
        "description": "Count of customer barge-ins per call.",
        "issue_group": {
            "id": "many_customer_interruptions_in_call",
            "description": "Frequent customer barge-ins often correlate with long turns or latency; thresholds should be calibrated to your traffic.",
            "unit": "count",
            "criteria": ">=",
            "threshold": 4.0,
            "category": "per_scenario",
            "sources": [
                "https://journalofcognition.org/articles/10.5334/joc.268",
                "https://telnyx.com/resources/how-telnyx-fixed-voice-ai-latency-with-co-located-infrastructure",
            ],
        },
        "prefer_upper_outliers": True,
    },
    "agent_interruption_count": {
        "label": "AGENT INTERRUPTIONS",
        "description": "Count of times the agent talks over the customer per call.",
        "issue_group": {
            "id": "agent_talking_over_customer",
            "description": "Agent overlap should be rare; repeated talk-over events strongly degrade perceived quality.",
            "unit": "count",
            "criteria": ">=",
            "threshold": 3.0,
            "category": "per_scenario",
            "sources": [
                "https://journalofcognition.org/articles/10.5334/joc.268",
            ],
        },
        "prefer_upper_outliers": True,
    },
    # =========================
    # System Latency Issue Groups (Industry / Voice-AI Standards)
    # =========================
    "turn": {
        "label": "TURN LATENCY",
        "description": "End-to-end turn latency (ms): customer finishes -> agent audio reply finished.",
        "issue_group": {
            "id": "high_turn_latency",
            "description": "Flag turns that exceed common 'sub-second' conversational latency targets for voice agents.",
            "unit": "ms",
            "criteria": ">",
            "threshold": 1500.0,
            "category": "system_latency",
            "sources": [
                "https://elevenlabs.io/blog/how-do-you-optimize-latency-for-conversational-ai",
                "https://telnyx.com/resources/how-telnyx-fixed-voice-ai-latency-with-co-located-infrastructure",
                "https://www.cresta.com/blog/engineering-for-real-time-voice-agent-latency",
            ],
        },
        "prefer_upper_outliers": True,
    },
    "model": {
        "label": "MODEL LATENCY",
        "description": "LLM/model processing latency (ms).",
        "issue_group": {
            "id": "high_model_latency",
            "description": "Flag model latency that makes sub-second interaction hard to achieve.",
            "unit": "ms",
            "criteria": ">",
            "threshold": 700.0,
            "category": "system_latency",
            "sources": [
                "https://www.cresta.com/blog/engineering-for-real-time-voice-agent-latency",
                "https://elevenlabs.io/blog/how-do-you-optimize-latency-for-conversational-ai",
            ],
        },
        "prefer_upper_outliers": True,
    },
    "voice": {
        "label": "VOICE LATENCY",
        "description": "TTS/voice synthesis latency (ms).",
        "issue_group": {
            "id": "high_tts_latency",
            "description": "Flag TTS latency that noticeably slows voice turn-taking.",
            "unit": "ms",
            "criteria": ">",
            "threshold": 700.0,
            "category": "system_latency",
            "sources": [
                "https://developers.deepgram.com/docs/text-to-speech-latency",
                "https://www.speechmatics.com/company/articles-and-news/why-we-built-our-low-latency-text-to-speech",
            ],
        },
        "prefer_upper_outliers": True,
    },
    "endpointing": {
        "label": "ENDPOINTING LATENCY",
        "description": "Endpointing/silence-detection latency (ms).",
        "issue_group": {
            "id": "high_endpointing_latency",
            "description": "Endpointing delay (end-of-speech -> system decides user stopped) is a major contributor to 'laggy' feel.",
            "unit": "ms",
            "criteria": ">",
            "threshold": 450.0,
            "category": "system_latency",
            "sources": [
                "https://www.gladia.io/blog/measuring-latency-in-stt",
                "https://telnyx.com/resources/how-telnyx-fixed-voice-ai-latency-with-co-located-infrastructure",
            ],
        },
        "prefer_upper_outliers": True,
    },
    "transcriber": {
        "label": "TRANSCRIBER LATENCY",
        "description": "STT/transcription latency (ms).",
        "issue_group": {
            "id": "high_stt_latency",
            "description": "Flag STT latency that delays downstream LLM response generation.",
            "unit": "ms",
            "criteria": ">",
            "threshold": 500.0,
            "category": "system_latency",
            "sources": [
                "https://deepgram.com/learn/streaming-speech-recognition-api",
                "https://www.assemblyai.com/products/streaming-speech-to-text",
            ],
        },
        "prefer_upper_outliers": True,
    },
}

# Default modality is voice unless explicitly marked as chat.
for _cfg in METRICS_CONFIG.values():
    if isinstance(_cfg, dict):
        _cfg.setdefault("modalities", ["voice"])
