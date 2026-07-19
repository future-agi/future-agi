"""
Management command to monitor resource usage and health.

Usage:
    python manage.py monitor_resources
    python manage.py monitor_resources --watch
    python manage.py monitor_resources --org-id=123 --detailed
"""

import asyncio
import json

from django.core.management.base import BaseCommand
from django.utils import timezone

from common.resource_manager import get_resource_manager


class Command(BaseCommand):
    help = 'Monitor resource manager health and usage'

    def add_arguments(self, parser):
        parser.add_argument(
            '--watch',
            action='store_true',
            help='Continuously watch resource usage (refresh every 5s)'
        )
        parser.add_argument(
            '--org-id',
            type=str,
            help='Show detailed usage for specific organization'
        )
        parser.add_argument(
            '--detailed',
            action='store_true',
            help='Show detailed breakdown including circuit breaker states'
        )
        parser.add_argument(
            '--json',
            action='store_true',
            help='Output in JSON format'
        )

    def handle(self, *args, **options):
        if options['watch']:
            asyncio.run(self._watch_mode(options))
        else:
            asyncio.run(self._single_report(options))

    async def _single_report(self, options):
        """Generate a single resource usage report"""
        resource_manager = get_resource_manager()
        metrics = resource_manager.get_metrics()

        if options['json']:
            self.stdout.write(json.dumps(metrics, indent=2))
            return

        self._print_summary(metrics)

        if options['detailed']:
            self._print_detailed(metrics)

        if options['org_id']:
            self._print_org_details(metrics, options['org_id'])

    async def _watch_mode(self, options):
        """Continuously monitor resources"""
        self.stdout.write(self.style.SUCCESS("Starting resource monitoring (Ctrl+C to stop)..."))

        try:
            while True:
                # Clear screen
                self.stdout.write('\033[2J\033[H')
                self.stdout.write(f"Resource Monitor - {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}")
                self.stdout.write("=" * 80)

                await self._single_report(options)

                self.stdout.write("-" * 80)
                self.stdout.write("Press Ctrl+C to stop monitoring")

                await asyncio.sleep(5)

        except KeyboardInterrupt:
            self.stdout.write(self.style.SUCCESS("\nMonitoring stopped."))

    def _print_summary(self, metrics):
        """Print resource usage summary"""
        uptime_hours = metrics['uptime_seconds'] / 3600

        self.stdout.write(self.style.HTTP_INFO("\n📊 Resource Manager Summary"))
        self.stdout.write(f"Uptime: {uptime_hours:.1f}h")
        self.stdout.write(f"Total Requests: {metrics['total_requests']:,}")
        self.stdout.write(f"Rejected Requests: {metrics['rejected_requests']:,}")
        self.stdout.write(f"Success Rate: {metrics['success_rate']:.2%}")
        self.stdout.write(f"Active Organizations: {metrics['active_organizations']}")

    def _print_detailed(self, metrics):
        """Print detailed circuit breaker and quota information"""
        self.stdout.write(self.style.HTTP_INFO("\n🔌 Circuit Breakers"))

        if not metrics['circuit_breakers']:
            self.stdout.write("No circuit breakers active")
        else:
            for circuit_key, breaker_info in metrics['circuit_breakers'].items():
                state = breaker_info['state']
                failure_count = breaker_info['failure_count']

                if state == 'open':
                    style = self.style.ERROR
                elif state == 'half_open':
                    style = self.style.WARNING
                else:
                    style = self.style.SUCCESS

                self.stdout.write(
                    f"  {circuit_key}: {style(state.upper())} "
                    f"(failures: {failure_count})"
                )

    def _print_org_details(self, metrics, org_id):
        """Print detailed usage for specific organization"""
        self.stdout.write(self.style.HTTP_INFO(f"\n🏢 Organization: {org_id}"))

        usage_by_org = metrics.get('usage_by_org', {})
        if org_id not in usage_by_org:
            self.stdout.write(f"No usage data found for organization {org_id}")
            return

        usage = usage_by_org[org_id]

        # Get resource manager to fetch quotas
        resource_manager = get_resource_manager()
        quota = resource_manager.get_quota(org_id)

        resources = [
            ("DB Connections", usage['db_connections'], quota.max_db_connections),
            ("Optimization Workers", usage['optimization_workers'], quota.max_optimization_workers),
            ("Evaluation Workers", usage['evaluation_workers'], quota.max_evaluation_workers),
            ("LLM Requests/min", usage['llm_requests_last_minute'], quota.max_llm_requests_per_minute),
            ("Async Tasks", usage['async_tasks'], quota.max_async_tasks),
        ]

        for resource_name, current, limit in resources:
            percentage = (current / limit) * 100 if limit > 0 else 0

            if percentage >= 90:
                style = self.style.ERROR
            elif percentage >= 70:
                style = self.style.WARNING
            else:
                style = self.style.SUCCESS

            bar = self._create_usage_bar(current, limit)
            self.stdout.write(
                f"  {resource_name:<20} {style(f'{current:>3}/{limit:<3}')} "
                f"({percentage:>5.1f}%) {bar}"
            )

    def _create_usage_bar(self, current, limit, width=20):
        """Create a visual usage bar"""
        if limit == 0:
            return "█" * width

        filled = int((current / limit) * width)
        bar = "█" * filled + "░" * (width - filled)
        return f"[{bar}]"
