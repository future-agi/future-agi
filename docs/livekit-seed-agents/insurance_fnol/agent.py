"""
Insurance — FNOL & Claims Intake  (voice, inbound)

Runnable LiveKit Agents app generated from ./config.json. Unlicensed-intake role: takes First
Notice of Loss, answers claim-status and general policy questions, and routes anything requiring a
coverage determination, quote, or advice to a licensed human. Tool backends mocked inline.

Run:  python agent.py dev

⚠️ Unlicensed-intake boundary is a compliance control, not legal advice — review with compliance.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import yaml
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.agents.llm import ToolError
from livekit.plugins import cartesia, deepgram, openai, silero

logger = logging.getLogger("insurance_fnol")


@dataclass
class InsuranceData:
    policyholder_name: str = "the policyholder"
    policy_number: str = ""
    verified: bool = False
    intent: Optional[str] = None
    loss_fields: dict = field(default_factory=dict)
    claim_id: Optional[str] = None
    outcome: Optional[str] = None
    agents: dict = field(default_factory=dict)

    def summarize(self) -> str:
        return yaml.safe_dump(
            {"policyholder": self.policyholder_name, "verified": self.verified,
             "intent": self.intent, "claim_id": self.claim_id, "outcome": self.outcome},
            sort_keys=False,
        )


RunCtx = RunContext[InsuranceData]


class BaseInsurance(Agent):
    async def _end(self, ctx: RunCtx, disposition: str, line: str) -> None:
        ctx.userdata.outcome = disposition
        await self.session.say(line)
        await self.session.aclose()

    def _to(self, ctx: RunCtx, phase: str, line: str = "") -> tuple[Agent, str]:
        return ctx.userdata.agents[phase], line

    @function_tool()
    async def escalate_to_licensed_agent(self, ctx: RunCtx, reason: str) -> None:
        """Warm-transfer to a licensed insurance rep. Use for any coverage determination, quote, advice, dispute, or human request."""
        ctx.userdata.outcome = "ESCALATED_LICENSED"
        logger.info("licensed handoff: %s\n%s", reason, ctx.userdata.summarize())
        await self.session.say("Let me connect you with a licensed representative who can help with that.")
        # await self.session.transfer_sip(LICENSED_AGENT_SIP)

    @function_tool()
    async def advise_emergency(self, ctx: RunCtx) -> None:
        """Active emergency / injury / hazard: tell the caller to contact 911 now, then end safely."""
        await self._end(ctx, "EMERGENCY_REDIRECT",
                        "If anyone is in danger, please hang up and call 911 right away. We can take the report once everyone is safe.")

    @function_tool()
    async def schedule_adjuster_callback(self, ctx: RunCtx, when: str) -> None:
        """Schedule a callback from a licensed adjuster, then end."""
        await self._end(ctx, "CALLBACK", f"Done — an adjuster will call you {when}. Thank you.")


class IdentifyAgent(BaseInsurance):
    def __init__(self) -> None:
        super().__init__(instructions=(
            "You are an inbound insurance intake agent. If the caller describes an active emergency or "
            "injuries, call advise_emergency first. Otherwise identify them by policy number and verify one "
            "factor with verify_policyholder, then route: reporting a new loss -> to fnol; existing claim -> "
            "to claim_status; general policy question -> to policy_qa. Never quote a premium, confirm or deny "
            "coverage, or give advice — route those to escalate_to_licensed_agent."))

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Greet warmly, say you can report a claim, check a claim, or answer a policy question, and ask for the policy number.")

    @function_tool()
    async def verify_policyholder(self, ctx: RunCtx, policy_number: str, answer: str, factor: str) -> None:
        """Verify the caller against the policy by matching ONE factor (dob | zip | last4_ssn)."""
        if factor not in {"dob", "zip", "last4_ssn"}:
            raise ToolError("Ask for date of birth, ZIP, or last four of SSN.")
        ctx.userdata.policy_number = policy_number
        ctx.userdata.verified = True
        ctx.userdata.policyholder_name = "Sam Rivera"
        await self.session.generate_reply(instructions="Thank them, confirm verified, and ask how you can help today.")

    @function_tool()
    async def to_fnol(self, ctx: RunCtx) -> tuple[Agent, str]:
        """Caller is reporting a new loss/incident."""
        if not ctx.userdata.verified:
            raise ToolError("Verify the policyholder before taking a report.")
        return self._to(ctx, "fnol")

    @function_tool()
    async def to_claim_status(self, ctx: RunCtx) -> tuple[Agent, str]:
        """Caller asks about an existing claim."""
        if not ctx.userdata.verified:
            raise ToolError("Verify the policyholder first.")
        return self._to(ctx, "claim_status")

    @function_tool()
    async def to_policy_qa(self, ctx: RunCtx) -> tuple[Agent, str]:
        """Caller asks a general policy question."""
        return self._to(ctx, "policy_qa")


class FnolAgent(BaseInsurance):
    def __init__(self) -> None:
        super().__init__(instructions=(
            "Take a First Notice of Loss. Collect one at a time: date & time of loss, location, loss type "
            "(auto|property|other), a plain description, whether anyone was injured, other parties, and whether "
            "police/fire were called (report number if so). When required fields are captured, call create_fnol, "
            "read back the claim number and next steps, and call send_document_upload_link. Do NOT assess fault "
            "or coverage. If injuries, a fatality, liability, or a likely total loss are involved, hand to triage."))

    @function_tool()
    async def create_fnol(self, ctx: RunCtx, loss_datetime: str, location: str, loss_type: str,
                          description: str, injuries: bool, other_parties: str = "", police_report: str = "") -> dict:
        """Create a First Notice of Loss and return a claim number."""
        if not ctx.userdata.verified:
            raise ToolError("Cannot create a claim before verification.")
        ctx.userdata.loss_fields = {"loss_datetime": loss_datetime, "location": location,
                                    "loss_type": loss_type, "injuries": injuries}
        ctx.userdata.claim_id = "CLM-77213"
        if injuries:
            # High-severity indicator → move to triage after acknowledging.
            await self.session.say("Thank you. Because someone was injured, I'll connect you with an adjuster.")
            nxt = ctx.userdata.agents["triage"]
            await self.session.update_agent(nxt)
        return {"claim_id": "CLM-77213", "next_steps": "An adjuster will contact you within 1 business day."}

    @function_tool()
    async def send_document_upload_link(self, ctx: RunCtx, channel: str = "sms") -> dict:
        """Send a secure link for photos/documents."""
        return {"sent": True, "link_id": "UP-5521"}


class TriageAgent(BaseInsurance):
    def __init__(self) -> None:
        super().__init__(instructions=(
            "This claim has high-severity indicators. Briefly confirm the key facts, reassure the claimant, and "
            "hand off to a licensed adjuster via escalate_to_licensed_agent. Make no coverage or liability statements."))

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Reassure the claimant and say a licensed adjuster will take it from here, then call escalate_to_licensed_agent.")


class ClaimStatusAgent(BaseInsurance):
    def __init__(self) -> None:
        super().__init__(instructions=(
            "Look up the claim with get_claim_status and read the status, assigned adjuster, and next step plainly. "
            "Offer to send details via send_claim_confirmation. If the caller disputes the status, hand to "
            "escalate_to_licensed_agent."))

    @function_tool()
    async def get_claim_status(self, ctx: RunCtx, claim_id: str) -> dict:
        """Look up an existing claim's status, adjuster, and next step."""
        if not ctx.userdata.verified:
            raise ToolError("Verify the policyholder before disclosing claim details.")
        return {"status": "Under review", "adjuster": "T. Okafor", "next_steps": "Estimate scheduled for Friday."}

    @function_tool()
    async def send_claim_confirmation(self, ctx: RunCtx, channel: str = "sms") -> dict:
        """Send the claim details/status summary by SMS or email."""
        return {"sent": True}


class PolicyQaAgent(BaseInsurance):
    def __init__(self) -> None:
        super().__init__(instructions=(
            "Answer general, factual questions about how the policy/claims process works using search_policy_kb, "
            "and cite the source. HARD BOUNDARY: never say whether a specific situation is covered, never quote a "
            "premium, never advise. The moment a question needs a coverage determination or advice, call "
            "escalate_to_licensed_agent."))

    @function_tool()
    async def search_policy_kb(self, ctx: RunCtx, query: str) -> dict:
        """Answer a general policy/process question from the KB with citations. Never a coverage determination."""
        return {"answer": "Comprehensive coverage generally applies to non-collision damage such as theft, fire, or hail.",
                "citations": ["policy_guide.pdf#p12"]}


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    userdata = InsuranceData()
    userdata.agents = {
        "identify": IdentifyAgent(),
        "fnol": FnolAgent(),
        "triage": TriageAgent(),
        "claim_status": ClaimStatusAgent(),
        "policy_qa": PolicyQaAgent(),
    }
    session = AgentSession[InsuranceData](
        userdata=userdata,
        stt=deepgram.STT(model="nova-2"),
        llm=openai.LLM(model="gpt-4o", temperature=0.3),
        tts=cartesia.TTS(),
        vad=silero.VAD.load(),
    )
    await session.start(agent=userdata.agents["identify"], room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
