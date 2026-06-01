"""Temporal workflow for batch-recalculating every EvalLogger under an EvalTask.

Triggered by submit_feedback_action_type when the user picks the
retune_recalculate radio. The view picks the sibling target rows and hands
them off; this package does the bulk soft-delete + the fan-out reruns.
"""
