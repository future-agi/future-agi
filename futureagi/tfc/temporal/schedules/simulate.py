"""
Simulate Temporal schedules.

Schedules for test execution monitoring and call creation.
Scenario workflows remain in Temporal (tfc/temporal/simulate/).
"""

from tfc.temporal.schedules.config import ScheduleConfig

# Simulate schedules for test execution
SIMULATE_SCHEDULES: list[ScheduleConfig] = [
    # ScheduleConfig(
    #     schedule_id="create-call-executions",
    #     activity_name="create_call_executions",
    #     interval_seconds=60,  # Every minute
    #     queue="tasks_s",
    #     description="Create call executions for active test executions and handle stuck calls",
    # ),
    # ScheduleConfig(
    #     schedule_id="monitor-test-executions",
    #     activity_name="monitor_test_executions",
    #     interval_seconds=60,  # Every minute
    #     queue="tasks_s",
    #     description="Monitor all active test executions and update their status",
    # ),
    ScheduleConfig(
        schedule_id="monitor-chat-test-executions",
        activity_name="monitor_chat_test_executions",
        interval_seconds=60,  # Every minute
        queue="tasks_s",
        description="Monitor all active chat test executions and update their status",
    ),
    ScheduleConfig(
        schedule_id="monitor-chat-timeout-call-executions",
        activity_name="monitor_chat_timeout_call_executions",
        interval_seconds=2700,  # Every 45 minutes
        queue="tasks_s",
        description="Monitor all active chat call executions that have been in ONGOING status for >30 minutes and update their status",
    ),
    ScheduleConfig(
        schedule_id="sweep-stuck-scoring",
        activity_name="sweep_stuck_scoring",
        interval_seconds=60,  # Every minute
        queue="tasks_s",
        description="Time out stuck eval/CSAT scoring in evaluating/cancelling simulate runs",
    ),
    ScheduleConfig(
        schedule_id="process-prompt-based-chat-simulations",
        activity_name="process_prompt_based_chat_simulations",
        interval_seconds=30,  # Every 30 seconds
        queue="tasks_s",
        description="Process REGISTERED CallExecutions for prompt-based chat simulations",
    ),
    ScheduleConfig(
        schedule_id="recover-hosted-harness-conversations",
        activity_name="recover_hosted_harness_conversations",
        interval_seconds=15,
        queue="default",
        description="Restart hosted environment chat runtimes that died with messages waiting",
    ),
    ScheduleConfig(
        schedule_id="seal-unsealed-hosted-harness-usage",
        activity_name="seal_unsealed_hosted_harness_usage",
        interval_seconds=600,
        queue="default",
        description="Re-enqueue usage sealing for hosted sandbox cleanups whose seal never ran",
    ),
]
