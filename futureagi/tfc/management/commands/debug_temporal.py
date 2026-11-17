"""
Debug Temporal configuration and activity registration.

Usage:
    python manage.py debug_temporal
    python manage.py debug_temporal --test-activity run_all_prompts_task
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Debug Temporal configuration and activity registration"

    def add_arguments(self, parser):
        parser.add_argument(
            "--test-activity",
            type=str,
            help="Test starting a specific activity by name",
        )
        parser.add_argument(
            "--queue",
            type=str,
            default="tasks_l",
            help="Queue to use for testing (default: tasks_l)",
        )

    def handle(self, *args, **options):
        from django.core.management.base import CommandError

        from tfc.capabilities.background_analysis import (
            BACKGROUND_ANALYSIS_UNAVAILABLE_MESSAGE,
            background_analysis_enabled,
        )

        # Reject fresh analysis admission before connecting or starting a workflow.
        # Keep this outside the diagnostic exception handlers so CLI exits non-zero.
        if (
            options.get("test_activity")
            in {
                "check_and_process_trace_errors",
                "run_deep_analysis_on_demand",
                "analyze_traces_on_demand",
            }
            and not background_analysis_enabled()
        ):
            raise CommandError(BACKGROUND_ANALYSIS_UNAVAILABLE_MESSAGE)

        self.stdout.write("=" * 60)
        self.stdout.write("Temporal Debug Information")
        self.stdout.write("=" * 60)

        # 1. Check Temporal configuration
        self.stdout.write("\n1. TEMPORAL CONFIGURATION")
        self.stdout.write("-" * 40)
        from tfc.temporal import TEMPORAL_HOST, TEMPORAL_NAMESPACE

        self.stdout.write(f"  Host: {TEMPORAL_HOST}")
        self.stdout.write(f"  Namespace: {TEMPORAL_NAMESPACE}")

        # 2. Test Temporal client connection
        self.stdout.write("\n2. TEMPORAL CLIENT CONNECTION")
        self.stdout.write("-" * 40)
        try:
            import asyncio

            from tfc.temporal import get_client

            async def test_connection():
                client = await get_client()
                return client.namespace

            namespace = asyncio.run(test_connection())
            self.stdout.write(
                self.style.SUCCESS(f"  Connected to namespace: {namespace}")
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Failed to connect: {e}"))

        # 3. Check activity registry
        self.stdout.write("\n3. DROP-IN ACTIVITY REGISTRY")
        self.stdout.write("-" * 40)
        try:
            from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY

            self.stdout.write(
                f"  Total activities in decorator registry: {len(_ACTIVITY_REGISTRY)}"
            )
            if _ACTIVITY_REGISTRY:
                self.stdout.write("  First 10 activities:")
                for name in list(_ACTIVITY_REGISTRY.keys())[:10]:
                    info = _ACTIVITY_REGISTRY[name]
                    self.stdout.write(
                        f"    - {name} (queue={info.get('queue', 'default')})"
                    )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Error: {e}"))

        # 4. Import modules to populate registry
        self.stdout.write("\n4. IMPORTING ACTIVITY MODULES")
        self.stdout.write("-" * 40)
        try:
            from tfc.temporal.common.registry import _import_temporal_activity_modules

            _import_temporal_activity_modules()

            from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY

            self.stdout.write(
                f"  After importing, total activities: {len(_ACTIVITY_REGISTRY)}"
            )

            # Check for specific activities
            key_activities = [
                "run_all_prompts_task",
                "execute_run_prompt",
                "process_prompts_single",
            ]
            for activity in key_activities:
                if activity in _ACTIVITY_REGISTRY:
                    info = _ACTIVITY_REGISTRY[activity]
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  [OK] {activity} (queue={info.get('queue')})"
                        )
                    )
                else:
                    self.stdout.write(self.style.WARNING(f"  [MISSING] {activity}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Error: {e}"))

        # 5. Check temporal activity wrappers
        self.stdout.write("\n5. TEMPORAL ACTIVITY WRAPPERS")
        self.stdout.write("-" * 40)
        try:
            from tfc.temporal.drop_in.decorator import (
                _ACTIVITY_WRAPPERS,
                get_temporal_activities,
            )

            activities = get_temporal_activities()
            self.stdout.write(f"  Total activity wrappers created: {len(activities)}")
            self.stdout.write(
                f"  Activity wrappers: {list(_ACTIVITY_WRAPPERS.keys())[:10]}..."
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Error: {e}"))

        # 6. Check registry
        self.stdout.write("\n6. WORKFLOW & ACTIVITY REGISTRY")
        self.stdout.write("-" * 40)
        try:
            from tfc.temporal.common.registry import get_registry_info

            info = get_registry_info()

            self.stdout.write("  Workflows by queue:")
            for queue, workflows in info.get("workflows", {}).items():
                self.stdout.write(f"    {queue}: {workflows}")

            self.stdout.write("  Activities by queue (first 5 per queue):")
            for queue, activities in info.get("activities", {}).items():
                self.stdout.write(
                    f"    {queue}: {len(activities)} total - {activities[:5]}..."
                )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Error: {e}"))

        # 7. Test activity if requested
        if options.get("test_activity"):
            activity_name = options["test_activity"]
            queue = options["queue"]

            self.stdout.write(f"\n7. TESTING ACTIVITY: {activity_name}")
            self.stdout.write("-" * 40)

            try:
                from tfc.temporal.drop_in.runner import start_activity

                self.stdout.write(f"  Starting activity on queue '{queue}'...")
                # Use empty args for testing - the activity will fail but we can verify the workflow starts
                workflow_id = start_activity(
                    activity_name,
                    args=(),
                    kwargs={},
                    queue=queue,
                )
                self.stdout.write(
                    self.style.SUCCESS(f"  Workflow started: {workflow_id}")
                )

                # Check workflow status
                import asyncio

                from tfc.temporal import get_workflow_status_async

                async def check_status():
                    import time

                    time.sleep(2)  # Wait a bit for workflow to start
                    return await get_workflow_status_async(workflow_id)

                status = asyncio.run(check_status())
                if status:
                    self.stdout.write(f"  Workflow status: {status}")
                else:
                    self.stdout.write("  Could not get workflow status")

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Error: {e}"))
                import traceback

                self.stdout.write(traceback.format_exc())

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("Debug complete")
        self.stdout.write("=" * 60)
