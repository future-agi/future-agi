"""
Collections — Payment Reminder  (voice, outbound)

A runnable LiveKit Agents app generated from ./config.json. Four phase-agents on one
AgentSession with handoffs that return the next Agent (history is preserved), plus global
pattern-interrupt tools on a shared BaseAgent. Tool backends are mocked inline so this runs
standalone; point them at real endpoints ({{FAI_TOOL_BASE}}) for production.

Run:  python agent.py dev      (LiveKit worker; needs LIVEKIT_URL / API key/secret + provider keys)

⚠️ Compliance note: the FDCPA/TCPA logic here is an engineering scaffold, NOT legal advice.
Review with counsel and localize before any live deployment.
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

logger = logging.getLogger("collections")

MINI_MIRANDA = (
    "This is an attempt to collect a debt, and any information obtained will be used for "
    "that purpose. This communication is from a debt collector."
)


# --------------------------------------------------------------------------------------
# Session state (config.json → session_state)  →  AgentSession[CollectionsData] userdata
# --------------------------------------------------------------------------------------
@dataclass
class CollectionsData:
    debtor_name: str = "the account holder"
    account_id: str = ""
    right_party_verified: bool = False
    verification_method: Optional[str] = None
    disclosure_given: bool = False
    dispute_flag: bool = False
    hardship_flag: bool = False
    cease_comm_flag: bool = False
    attorney_flag: bool = False
    promise_to_pay: Optional[dict] = None
    plan_selected: Optional[dict] = None
    callback_at: Optional[str] = None
    outcome: Optional[str] = None
    agents: dict = field(default_factory=dict)  # phase registry for handoffs

    def summarize(self) -> str:
        """YAML digest injected into a human agent on warm transfer."""
        return yaml.safe_dump(
            {
                "debtor": self.debtor_name,
                "verified": self.right_party_verified,
                "outcome": self.outcome,
                "flags": {
                    "dispute": self.dispute_flag,
                    "hardship": self.hardship_flag,
                    "cease": self.cease_comm_flag,
                    "attorney": self.attorney_flag,
                },
                "promise_to_pay": self.promise_to_pay,
            },
            sort_keys=False,
        )


RunCtx = RunContext[CollectionsData]


# --------------------------------------------------------------------------------------
# Mock tool backends (swap for real {{FAI_TOOL_BASE}} calls in prod)
# --------------------------------------------------------------------------------------
def _match_identity(account_id: str, factor: str, answer: str) -> bool:
    # Deterministic stub. NEVER trust caller-asserted identity — match a real factor only.
    return bool(answer and factor in {"dob", "zip", "last4"})


def _account_summary(account_id: str) -> dict:
    return {
        "creditor": "Northwind Card Services",
        "balance": 842.17,
        "days_past_due": 47,
        "min_payment": 35.0,
    }


def _plan_options(balance: float) -> list[dict]:
    return [
        {"plan_id": "P3", "months": 3, "monthly": round(balance / 3, 2), "min_down": 0},
        {"plan_id": "P6", "months": 6, "monthly": round(balance / 6, 2), "min_down": 50},
        {"plan_id": "P12", "months": 12, "monthly": round(balance / 12, 2), "min_down": 100},
    ]


# --------------------------------------------------------------------------------------
# Shared base agent: global pattern-interrupt tools (available in every phase)
# --------------------------------------------------------------------------------------
class BaseCollections(Agent):
    async def _end(self, ctx: RunCtx, disposition: str, line: str) -> None:
        ctx.userdata.outcome = disposition
        await self.session.say(line)
        await self.session.aclose()

    def _to(self, ctx: RunCtx, phase: str, line: str) -> tuple[Agent, str]:
        return ctx.userdata.agents[phase], line

    @function_tool()
    async def request_cease_communication(self, ctx: RunCtx) -> None:
        """Caller asks to stop being contacted ('stop calling me', 'do not call'). Honor immediately."""
        ctx.userdata.cease_comm_flag = True
        await self._end(ctx, "CEASE_COMM", "Understood. I've recorded that and we won't contact you about this again. Take care.")

    @function_tool()
    async def report_attorney_representation(self, ctx: RunCtx, attorney_info: str = "") -> None:
        """Caller says they have or want a lawyer / are represented by an attorney."""
        ctx.userdata.attorney_flag = True
        await self._end(ctx, "ATTORNEY", "Thank you — since you're represented, we'll direct any further contact to your attorney. Goodbye.")

    @function_tool()
    async def escalate_to_human(self, ctx: RunCtx, reason: str) -> None:
        """Caller demands a human/supervisor, or is abusive/threatening. Warm-transfer with a summary."""
        ctx.userdata.outcome = "ESCALATED"
        logger.info("escalating: %s\n%s", reason, ctx.userdata.summarize())
        await self.session.say("Of course — let me get a specialist on the line for you.")
        # await self.session.transfer_sip(...)  # wire SUPERVISOR_SIP in prod


# --------------------------------------------------------------------------------------
# Phase 1: Right-Party Verification — NO debt disclosure allowed here
# --------------------------------------------------------------------------------------
class RightPartyAgent(BaseCollections):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are an outbound account-services agent. Your ONLY job now is to confirm you are "
                "speaking with the right party. Do NOT mention a debt, balance, creditor, or the reason "
                "for the call in any way that reveals a debt — not to this person, not to anyone else who "
                "answers. Ask them to confirm ONE identity factor (date of birth, ZIP, or last 4 of SSN) "
                "and call verify_identity. If they are not the right party or unavailable, call "
                "leave_compliant_message or offer a callback and end. Never argue. Keep it brief."
            )
        )

    async def on_enter(self) -> None:
        name = self.session.userdata.debtor_name
        await self.session.generate_reply(
            instructions=f"Greet and ask if you're speaking with {name}, then request one verification factor."
        )

    @function_tool()
    async def verify_identity(self, ctx: RunCtx, answer: str, factor: str) -> tuple[Agent, str]:
        """Verify the right party by matching ONE factor (dob | zip | last4). Must pass before any disclosure."""
        if factor not in {"dob", "zip", "last4"}:
            raise ToolError("Ask for date of birth, ZIP code, or the last four of their SSN.")
        if not _match_identity(ctx.userdata.account_id, factor, answer):
            raise ToolError("That doesn't match our records — try another factor, or offer a callback.")
        ctx.userdata.right_party_verified = True
        ctx.userdata.verification_method = factor
        return self._to(ctx, "disclosure", "Thank you, you're verified.")

    @function_tool()
    async def leave_compliant_message(self, ctx: RunCtx) -> None:
        """Wrong party / unavailable: leave a message that does NOT disclose the debt, then end."""
        await self._end(ctx, "WRONG_PARTY", "No problem — please have them call our account services line at their convenience. Thank you.")

    @function_tool()
    async def schedule_callback(self, ctx: RunCtx, when: str) -> None:
        """Schedule a callback within allowed contact hours, then end."""
        ctx.userdata.callback_at = when
        await self._end(ctx, "REFUSED_VERIFY", "That's fine — we'll follow up then. Thank you.")


# --------------------------------------------------------------------------------------
# Phase 2: Disclosure (mini-Miranda) — gated on verification
# --------------------------------------------------------------------------------------
class DisclosureAgent(BaseCollections):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "The right party is verified. State the creditor and current balance plainly, without "
                "pressure or judgment, then ask an open question about how they'd like to resolve it and "
                "hand off to negotiation. No threats, no legal/tax advice."
            )
        )

    async def on_enter(self) -> None:
        # Deterministic gate + scripted disclosure — never model-generated, never skipped.
        assert self.session.userdata.right_party_verified, "disclosure before verification"
        await self.session.say(MINI_MIRANDA)
        self.session.userdata.disclosure_given = True
        await self.session.generate_reply(
            instructions="State creditor and balance from get_account_summary, then ask how they'd like to resolve it."
        )

    @function_tool()
    async def get_account_summary(self, ctx: RunCtx) -> dict:
        """Return creditor and current balance. Only after verification."""
        if not ctx.userdata.right_party_verified:
            raise ToolError("Cannot disclose account details before the party is verified.")
        return _account_summary(ctx.userdata.account_id)

    @function_tool()
    async def to_negotiation(self, ctx: RunCtx) -> tuple[Agent, str]:
        """Move to resolving the balance once creditor and balance have been stated."""
        return self._to(ctx, "negotiation", "")


# --------------------------------------------------------------------------------------
# Phase 3: Negotiation
# --------------------------------------------------------------------------------------
class NegotiationAgent(BaseCollections):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "Help the verified debtor resolve the balance. Branch on their situation: pay in full → "
                "to_payment; needs a plan → get_plan_options, capture the choice, to_payment; hardship → "
                "empathize, offer the smallest plan or a deferral, schedule_callback and end; disputes → "
                "log_dispute and end; already paid → note it, stop, end. Never pressure or threaten."
            )
        )

    @function_tool()
    async def get_plan_options(self, ctx: RunCtx, balance: float) -> list[dict]:
        """Return allowable payment-plan options for the balance."""
        if balance <= 0:
            raise ToolError("Balance must be positive to build a plan.")
        return _plan_options(balance)

    @function_tool()
    async def log_dispute(self, ctx: RunCtx, reason: str) -> None:
        """Debtor disputes the debt: pause collection, trigger validation notice, end."""
        ctx.userdata.dispute_flag = True
        await self._end(ctx, "DISPUTE", "Thank you — I've logged that dispute. We'll pause collection and send you a written validation notice. Goodbye.")

    @function_tool()
    async def schedule_callback(self, ctx: RunCtx, when: str) -> None:
        """Hardship path: capture hardship, book a follow-up, end warmly."""
        ctx.userdata.hardship_flag = True
        ctx.userdata.callback_at = when
        await self._end(ctx, "HARDSHIP_CALLBACK", "I understand — let's reconnect then, and we'll find something that works. Take care.")

    @function_tool()
    async def to_payment(self, ctx: RunCtx, plan_id: str = "") -> tuple[Agent, str]:
        """Debtor agrees to pay in full or selects a plan."""
        if plan_id:
            ctx.userdata.plan_selected = {"plan_id": plan_id}
        return self._to(ctx, "payment", "Great — let's get that set up.")


# --------------------------------------------------------------------------------------
# Phase 4: Payment — never captures a PAN by voice
# --------------------------------------------------------------------------------------
class PaymentAgent(BaseCollections):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "Set up the agreed payment. NEVER take a card number, bank number, or CVV by voice. Use "
                "send_payment_link (secure hosted page) or transfer_to_payment_ivr. Confirm amount and date, "
                "record_promise_to_pay, and end warmly."
            )
        )

    @function_tool()
    async def send_payment_link(self, ctx: RunCtx, channel: str = "sms") -> dict:
        """Text a secure hosted payment page. Never captures card/bank details in-conversation."""
        if not ctx.userdata.right_party_verified:
            raise ToolError("Cannot send a payment link before verification.")
        return {"sent": True, "link_id": "PL-90233"}

    @function_tool()
    async def transfer_to_payment_ivr(self, ctx: RunCtx) -> None:
        """Warm-transfer to the secure automated payment line."""
        await self.session.say("I'll transfer you to our secure automated payment line now.")
        # await self.session.transfer_sip(PAYMENT_IVR_SIP)

    @function_tool()
    async def record_promise_to_pay(self, ctx: RunCtx, amount: float, date: str, method: str) -> dict:
        """Record a promise-to-pay and end with the confirmation."""
        ctx.userdata.promise_to_pay = {"amount": amount, "date": date, "method": method}
        await self._end(ctx, "PTP_SET", f"You're all set — {amount:.2f} on {date}. You'll get a confirmation. Thank you.")
        return {"confirmation_id": "PTP-48213"}


# --------------------------------------------------------------------------------------
# Entrypoint — standard LiveKit worker
# --------------------------------------------------------------------------------------
async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    userdata = CollectionsData(debtor_name="Jordan Lee", account_id="ACCT-1001")
    userdata.agents = {
        "right_party": RightPartyAgent(),
        "disclosure": DisclosureAgent(),
        "negotiation": NegotiationAgent(),
        "payment": PaymentAgent(),
    }

    session = AgentSession[CollectionsData](
        userdata=userdata,
        stt=deepgram.STT(model="nova-2"),
        llm=openai.LLM(model="gpt-4o", temperature=0.2),
        tts=cartesia.TTS(),
        vad=silero.VAD.load(),
    )

    await session.start(agent=userdata.agents["right_party"], room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
