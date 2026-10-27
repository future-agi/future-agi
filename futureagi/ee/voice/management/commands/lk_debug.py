"""Management command to inspect LiveKit infrastructure state.

Usage:
    python manage.py lk_debug               # Full summary
    python manage.py lk_debug --rooms       # Active rooms only
    python manage.py lk_debug --trunks      # SIP trunks only
    python manage.py lk_debug --egress      # Egress sessions only
    python manage.py lk_debug --dispatch    # Dispatch rules only
"""

import asyncio

import structlog
from django.core.management.base import BaseCommand
from livekit.api import LiveKitAPI

from ee.voice.services.livekit.config import LiveKitConfig

logger = structlog.get_logger(__name__)


class Command(BaseCommand):
    help = "Inspect LiveKit rooms, SIP trunks, dispatch rules, and egress sessions"

    def add_arguments(self, parser):
        parser.add_argument("--rooms", action="store_true", help="Show active rooms")
        parser.add_argument("--trunks", action="store_true", help="Show SIP trunks")
        parser.add_argument(
            "--egress", action="store_true", help="Show egress sessions"
        )
        parser.add_argument(
            "--dispatch", action="store_true", help="Show SIP dispatch rules"
        )

    def handle(self, *args, **options):
        show_all = not any(
            options[k] for k in ("rooms", "trunks", "egress", "dispatch")
        )
        asyncio.run(self._run(options, show_all))

    async def _run(self, options, show_all):
        config = LiveKitConfig.from_env()
        self.stdout.write(f"\n=== LiveKit Debug ({config.http_url}) ===\n")

        async with LiveKitAPI(
            url=config.http_url,
            api_key=config.api_key,
            api_secret=config.api_secret,
        ) as lkapi:
            if show_all or options["rooms"]:
                await self._show_rooms(lkapi)
            if show_all or options["trunks"]:
                await self._show_trunks(lkapi, config)
            if show_all or options["dispatch"]:
                await self._show_dispatch_rules(lkapi)
            if show_all or options["egress"]:
                await self._show_egress(lkapi)

    async def _show_rooms(self, lkapi):
        self.stdout.write("\n--- Active Rooms ---")
        try:
            resp = await lkapi.room.list_rooms()
            if not resp.rooms:
                self.stdout.write("  (none)")
                return
            for room in resp.rooms:
                self.stdout.write(
                    f"  {room.name}  sid={room.sid}  "
                    f"participants={room.num_participants}  "
                    f"created={room.creation_time}"
                )
                # List participants
                parts = await lkapi.room.list_participants(room.name)
                for p in parts.participants:
                    self.stdout.write(
                        f"    -> {p.identity}  sid={p.sid}  "
                        f"kind={p.kind}  state={p.state}  "
                        f"tracks={len(p.tracks)}"
                    )
        except Exception as e:
            self.stdout.write(f"  ERROR: {e}")

    async def _show_trunks(self, lkapi, config):
        self.stdout.write("\n--- SIP Outbound Trunks ---")
        self.stdout.write(
            f"  Config inbound_trunk_id: {config.sip_inbound_trunk_id or '(not set)'}"
        )
        self.stdout.write(
            f"  Config outbound_trunk_id: {config.sip_outbound_trunk_id or '(not set)'}"
        )
        try:
            from livekit.protocol.sip import ListSIPOutboundTrunkRequest

            resp = await lkapi.sip.list_sip_outbound_trunk(
                ListSIPOutboundTrunkRequest()
            )
            if not resp.items:
                self.stdout.write("  (none)")
            for trunk in resp.items:
                self.stdout.write(
                    f"  {trunk.name}  id={trunk.sip_trunk_id}  "
                    f"numbers={list(trunk.numbers)}  "
                    f"address={trunk.address}"
                )
        except Exception as e:
            self.stdout.write(f"  ERROR: {e}")

        self.stdout.write("\n--- SIP Inbound Trunks ---")
        try:
            from livekit.protocol.sip import ListSIPInboundTrunkRequest

            resp = await lkapi.sip.list_sip_inbound_trunk(ListSIPInboundTrunkRequest())
            if not resp.items:
                self.stdout.write("  (none)")
            for trunk in resp.items:
                self.stdout.write(
                    f"  {trunk.name}  id={trunk.sip_trunk_id}  "
                    f"numbers={list(trunk.numbers)}"
                )
        except Exception as e:
            self.stdout.write(f"  ERROR: {e}")

    async def _show_dispatch_rules(self, lkapi):
        self.stdout.write("\n--- SIP Dispatch Rules ---")
        try:
            from livekit.protocol.sip import ListSIPDispatchRuleRequest

            resp = await lkapi.sip.list_sip_dispatch_rule(ListSIPDispatchRuleRequest())
            if not resp.items:
                self.stdout.write("  (none)")
            for rule in resp.items:
                self.stdout.write(
                    f"  {rule.name}  id={rule.sip_dispatch_rule_id}  "
                    f"trunk_ids={list(rule.trunk_ids)}  "
                    f"rule={rule.rule}"
                )
        except Exception as e:
            self.stdout.write(f"  ERROR: {e}")

    async def _show_egress(self, lkapi):
        self.stdout.write("\n--- Egress Sessions ---")
        try:
            from livekit.protocol.egress import ListEgressRequest

            resp = await lkapi.egress.list_egress(ListEgressRequest())
            if not resp.items:
                self.stdout.write("  (none)")
                return
            for eg in resp.items:
                status_name = eg.status
                file_info = ""
                if eg.file_results:
                    file_info = f"  file={eg.file_results[0].filename}"
                self.stdout.write(
                    f"  egress_id={eg.egress_id}  room={eg.room_name}  "
                    f"status={status_name}{file_info}"
                )
        except Exception as e:
            self.stdout.write(f"  ERROR: {e}")

        self.stdout.write("")
