"""
Banking — Card, Fraud & Account Support  (voice, inbound)

Runnable LiveKit Agents app generated from ./config.json. Retail-bank / fintech support with step-up
auth before sensitive actions (card lock/replace, disputes, full transaction history). Tool backends
mocked inline. Never gives financial advice; never reads full card/account numbers; never moves money.

Run:  python agent.py dev
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

logger = logging.getLogger("banking_support")


@dataclass
class BankData:
    customer_name: str = "the customer"
    customer_id: Optional[str] = None
    auth_level: Optional[str] = None
    step_up_verified: bool = False
    intent: Optional[str] = None
    outcome: Optional[str] = None
    agents: dict = field(default_factory=dict)

    def summarize(self) -> str:
        return yaml.safe_dump(
            {"customer": self.customer_name, "auth_level": self.auth_level,
             "step_up": self.step_up_verified, "outcome": self.outcome},
            sort_keys=False,
        )


RunCtx = RunContext[BankData]


def _require_step_up(ctx: RunCtx) -> None:
    """Deterministic gate: sensitive actions need step-up auth — enforced in code, not by the model."""
    if not ctx.userdata.step_up_verified:
        raise ToolError("Step-up authentication is required first. Send and verify a one-time passcode.")


class BaseBank(Agent):
    async def _end(self, ctx: RunCtx, disposition: str, line: str) -> None:
        ctx.userdata.outcome = disposition
        await self.session.say(line)
        await self.session.aclose()

    def _to(self, ctx: RunCtx, phase: str, line: str = "") -> tuple[Agent, str]:
        return ctx.userdata.agents[phase], line

    @function_tool()
    async def step_up_auth(self, ctx: RunCtx) -> dict:
        """Send a one-time passcode to the customer's verified device. Required before sensitive actions."""
        return {"sent": True, "channel": "sms"}

    @function_tool()
    async def verify_otp(self, ctx: RunCtx, code: str) -> dict:
        """Verify the one-time passcode. Sets step-up auth on success."""
        if not code or len(code) < 4:
            raise ToolError("That code doesn't look right — please read the 6-digit code we texted.")
        ctx.userdata.step_up_verified = True
        return {"verified": True}

    @function_tool()
    async def escalate_to_human(self, ctx: RunCtx, reason: str) -> None:
        """Warm-transfer to a human banker with a summary of the interaction."""
        ctx.userdata.outcome = "ESCALATED"
        logger.info("banker handoff: %s\n%s", reason, ctx.userdata.summarize())
        await self.session.say("Let me connect you with a specialist who can help.")
        # await self.session.transfer_sip(BANKER_SIP)


class AuthenticateAgent(BaseBank):
    def __init__(self) -> None:
        super().__init__(instructions=(
            "You are a bank support agent. If the caller reports a lost/stolen card or active fraud, verify then "
            "move quickly to card_services or fraud. Verify the caller with verify_identity (baseline). Then route: "
            "balance/transactions -> to account_info; lock/replace a card -> to card_services; dispute or fraud -> to "
            "fraud; general how-to -> to faq. Sensitive actions need step-up auth, handled in the destination phase. "
            "Never give financial advice; never read a full card or account number aloud."))

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Greet, explain you'll verify identity for security, and ask for the phone number or ID on the account plus a knowledge factor.")

    @function_tool()
    async def verify_identity(self, ctx: RunCtx, identifier: str, answer: str) -> None:
        """Baseline identity verification. Required before any account interaction."""
        if not identifier or not answer:
            raise ToolError("I need the account phone/ID and one verification answer.")
        ctx.userdata.customer_id = "CUS-4410"
        ctx.userdata.customer_name = "Alex Kim"
        ctx.userdata.auth_level = "baseline"
        await self.session.generate_reply(instructions="Confirm verified and ask how you can help.")

    @function_tool()
    async def to_account_info(self, ctx: RunCtx) -> tuple[Agent, str]:
        """Caller wants balance or recent transactions."""
        self._require_verified(ctx)
        return self._to(ctx, "account_info")

    @function_tool()
    async def to_card_services(self, ctx: RunCtx) -> tuple[Agent, str]:
        """Caller wants to lock, freeze, or replace a card."""
        self._require_verified(ctx)
        return self._to(ctx, "card_services")

    @function_tool()
    async def to_fraud(self, ctx: RunCtx) -> tuple[Agent, str]:
        """Caller wants to dispute a charge or report fraud/lost card."""
        self._require_verified(ctx)
        return self._to(ctx, "fraud")

    @function_tool()
    async def to_faq(self, ctx: RunCtx) -> tuple[Agent, str]:
        """Caller asks a general how-to question."""
        return self._to(ctx, "faq")

    def _require_verified(self, ctx: RunCtx) -> None:
        if not ctx.userdata.auth_level:
            raise ToolError("Verify the caller's identity first.")


class AccountInfoAgent(BaseBank):
    def __init__(self) -> None:
        super().__init__(instructions=(
            "Provide balance and recent transactions. get_balance is allowed at baseline auth; get_transactions "
            "requires step-up — if not stepped up, call step_up_auth then verify_otp first. Refer to cards/accounts "
            "by last 4 only. Dispute intent -> hand to fraud. No spending/investment advice."))

    @function_tool()
    async def get_balance(self, ctx: RunCtx) -> dict:
        """Return current account balance(s). Allowed at baseline auth."""
        if not ctx.userdata.auth_level:
            raise ToolError("Verify identity first.")
        return {"checking": 1284.55, "savings": 5230.10}

    @function_tool()
    async def get_transactions(self, ctx: RunCtx, count: int = 5) -> list[dict]:
        """Return recent transactions. Requires step-up auth."""
        _require_step_up(ctx)
        return [{"id": "TX-771", "date": "Aug 28", "merchant": "Blue Bottle", "amount": 6.75},
                {"id": "TX-772", "date": "Aug 29", "merchant": "Amazon", "amount": 42.10}]

    @function_tool()
    async def to_fraud(self, ctx: RunCtx) -> tuple[Agent, str]:
        """Caller wants to dispute a transaction they see."""
        return self._to(ctx, "fraud")


class CardServicesAgent(BaseBank):
    def __init__(self) -> None:
        super().__init__(instructions=(
            "Handle card lock/freeze and replacement. Both require step-up auth: if step_up is not verified, call "
            "step_up_auth then verify_otp before acting. Then lock_card or order_replacement_card, confirm the last 4 "
            "and timeframe. Lost/stolen -> lock first, then offer a replacement."))

    @function_tool()
    async def lock_card(self, ctx: RunCtx, card_last4: str) -> dict:
        """Lock/freeze a card by its last 4 digits. Requires step-up auth."""
        _require_step_up(ctx)
        await self._end(ctx, "CARD_LOCKED", f"Your card ending {card_last4} is locked. Would you like a replacement mailed?")
        return {"locked": True}

    @function_tool()
    async def order_replacement_card(self, ctx: RunCtx, card_last4: str, reason: str) -> dict:
        """Order a replacement card. Requires step-up auth."""
        _require_step_up(ctx)
        await self._end(ctx, "CARD_REPLACED", f"Done — a new card is on the way, arriving in about 5 business days.")
        return {"ordered": True, "arrives_in_days": 5}


class FraudAgent(BaseBank):
    def __init__(self) -> None:
        super().__init__(instructions=(
            "Handle disputes and fraud. Require step-up (step_up_auth + verify_otp) before filing anything. For a "
            "specific unrecognized charge, gather amount/date/merchant and call file_dispute. For clear fraud or a "
            "compromised card, call report_fraud (locks the card and escalates), then reassure. Never promise a "
            "specific refund amount or timeline beyond what the tool returns."))

    @function_tool()
    async def file_dispute(self, ctx: RunCtx, transaction_id: str, reason: str) -> dict:
        """File a dispute for a specific transaction. Requires step-up auth."""
        _require_step_up(ctx)
        await self._end(ctx, "DISPUTE_FILED", "Your dispute is filed. Provisional credit is under review; you'll get an update by email.")
        return {"dispute_id": "DSP-5521"}

    @function_tool()
    async def report_fraud(self, ctx: RunCtx, detail: str, card_last4: str = "") -> dict:
        """Report fraud or a lost/stolen card: locks the affected card and escalates to the fraud team."""
        # Always locks + escalates — cannot be downgraded by the conversation.
        ctx.userdata.outcome = "FRAUD_ESCALATED"
        await self.session.say("I've locked the card and opened a fraud case. I'm connecting you to our fraud team now.")
        logger.info("fraud escalation: %s\n%s", detail, ctx.userdata.summarize())
        # await self.session.transfer_sip(FRAUD_TEAM_SIP)
        return {"case_id": "FRD-3390", "card_locked": True}


class FaqAgent(BaseBank):
    def __init__(self) -> None:
        super().__init__(instructions=(
            "Answer general how-to/policy questions from the KB with search_kb, cite the source. No financial, tax, "
            "or investment advice. Account-specific action -> route to the right phase; needs a human -> escalate_to_human."))

    @function_tool()
    async def search_kb(self, ctx: RunCtx, query: str) -> dict:
        """Answer a general how-to/policy question from the KB with citations. Never financial advice."""
        return {"answer": "You can set travel notices in the app under Card Settings > Travel.",
                "citations": ["help/travel-notice"]}


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    userdata = BankData()
    userdata.agents = {
        "authenticate": AuthenticateAgent(),
        "account_info": AccountInfoAgent(),
        "card_services": CardServicesAgent(),
        "fraud": FraudAgent(),
        "faq": FaqAgent(),
    }
    session = AgentSession[BankData](
        userdata=userdata,
        stt=deepgram.STT(model="nova-2"),
        llm=openai.LLM(model="gpt-4o", temperature=0.2),
        tts=cartesia.TTS(),
        vad=silero.VAD.load(),
    )
    await session.start(agent=userdata.agents["authenticate"], room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
