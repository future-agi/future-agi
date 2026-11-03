"""
Healthcare — Scheduling, Intake & Triage  (voice, inbound)

Runnable LiveKit Agents app generated from ./config.json. Clinic front-desk agent: book/reschedule/
cancel, capture intake + insurance, and rules-based symptom triage that routes red-flags to 911/nurse.
Tool backends mocked inline. Never diagnoses or gives medical advice; PHI gated on verification.

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

logger = logging.getLogger("healthcare_scheduling")

# Fixed clinical rules table (owned by the clinic's clinical team, not the model).
RED_FLAGS = ("chest pain", "difficulty breathing", "shortness of breath", "stroke",
             "face drooping", "slurred speech", "severe bleeding", "suicidal", "not breathing")


@dataclass
class HealthData:
    patient_name: str = "the patient"
    patient_id: Optional[str] = None
    verified: bool = False
    is_new_patient: bool = False
    intent: Optional[str] = None
    appointment_id: Optional[str] = None
    outcome: Optional[str] = None
    agents: dict = field(default_factory=dict)

    def summarize(self) -> str:
        return yaml.safe_dump(
            {"patient": self.patient_name, "verified": self.verified,
             "appointment_id": self.appointment_id, "outcome": self.outcome},
            sort_keys=False,
        )


RunCtx = RunContext[HealthData]


class BaseHealth(Agent):
    async def _end(self, ctx: RunCtx, disposition: str, line: str) -> None:
        ctx.userdata.outcome = disposition
        await self.session.say(line)
        await self.session.aclose()

    def _to(self, ctx: RunCtx, phase: str, line: str = "") -> tuple[Agent, str]:
        return ctx.userdata.agents[phase], line

    async def _do_emergency(self, ctx: RunCtx) -> None:
        await self._end(ctx, "EMERGENCY_911",
                        "This may be an emergency. Please hang up and call 911 or go to your nearest emergency room right now.")

    @function_tool()
    async def advise_emergency(self, ctx: RunCtx) -> None:
        """Red-flag symptom or emergency: tell the caller to call 911 / go to the ER now, then end safely."""
        await self._do_emergency(ctx)

    @function_tool()
    async def transfer_to_nurse(self, ctx: RunCtx, reason: str) -> None:
        """Warm-transfer to the nurse line with a summary of the reported symptoms."""
        ctx.userdata.outcome = "NURSE_ESCALATED"
        logger.info("nurse handoff: %s\n%s", reason, ctx.userdata.summarize())
        await self.session.say("Let me get a nurse on the line to help you with that.")
        # await self.session.transfer_sip(NURSE_LINE_SIP)


class FrontDeskAgent(BaseHealth):
    def __init__(self) -> None:
        super().__init__(instructions=(
            "You are a clinic front-desk agent. If the caller describes an emergency or red-flag symptom "
            "(chest pain, trouble breathing, stroke signs, severe bleeding, suicidal thoughts), call "
            "advise_emergency immediately. Otherwise verify the patient with verify_patient (name + date of "
            "birth) and route: booking/reschedule/cancel -> to scheduling; new-patient details or insurance -> "
            "to intake; describing symptoms to decide urgency -> to triage. Warm and unhurried. Never diagnose "
            "or give medical advice."))

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Greet, say you can help book/reschedule/cancel or route them, and ask for full name and date of birth.")

    @function_tool()
    async def verify_patient(self, ctx: RunCtx, name: str, dob: str) -> None:
        """Verify the patient by name + date of birth. Required before reading or changing any record (PHI)."""
        ctx.userdata.verified = True
        ctx.userdata.patient_id = "PT-3391"
        ctx.userdata.patient_name = name
        await self.session.generate_reply(instructions="Confirm you found them and ask how you can help today.")

    @function_tool()
    async def to_scheduling(self, ctx: RunCtx) -> tuple[Agent, str]:
        """Caller wants to book, reschedule, or cancel."""
        if not ctx.userdata.verified:
            raise ToolError("Verify the patient first.")
        return self._to(ctx, "scheduling")

    @function_tool()
    async def to_intake(self, ctx: RunCtx) -> tuple[Agent, str]:
        """New patient, or needs to give/update insurance & details."""
        if not ctx.userdata.verified:
            raise ToolError("Verify the patient first.")
        return self._to(ctx, "intake")

    @function_tool()
    async def to_triage(self, ctx: RunCtx) -> tuple[Agent, str]:
        """Caller is describing symptoms to decide whether/when to be seen."""
        return self._to(ctx, "triage")


class SchedulingAgent(BaseHealth):
    def __init__(self) -> None:
        super().__init__(instructions=(
            "Handle booking, rescheduling, cancellation. Ask reason for visit and preferred provider/timeframe, "
            "call check_availability, offer 2-3 concrete slots, confirm before book_appointment (or reschedule/ "
            "cancel). Read back date, time, provider, location. New patient -> hand to intake. Urgent reason -> triage."))

    @function_tool()
    async def check_availability(self, ctx: RunCtx, provider_type: str, date_range: str = "next week") -> list[dict]:
        """Find open appointment slots for a provider type within a date range."""
        return [{"slot_id": "S-101", "provider": "Dr. Nguyen", "when": "Tue 10:00", "location": "Main Clinic"},
                {"slot_id": "S-102", "provider": "Dr. Nguyen", "when": "Wed 14:30", "location": "Main Clinic"}]

    @function_tool()
    async def book_appointment(self, ctx: RunCtx, slot_id: str, reason: str) -> dict:
        """Book a specific slot for the verified patient with a reason for visit."""
        if not ctx.userdata.verified:
            raise ToolError("Verify the patient before booking.")
        ctx.userdata.appointment_id = "AP-8842"
        await self._end(ctx, "BOOKED", "You're booked. You'll get a confirmation by text. Anything else?")
        return {"appointment_id": "AP-8842"}

    @function_tool()
    async def reschedule_appointment(self, ctx: RunCtx, appointment_id: str, slot_id: str) -> dict:
        """Move an existing appointment to a new slot."""
        await self._end(ctx, "RESCHEDULED", "All set — I've moved your appointment. You'll get a new confirmation.")
        return {"ok": True}

    @function_tool()
    async def cancel_appointment(self, ctx: RunCtx, appointment_id: str, reason: str = "") -> dict:
        """Cancel an existing appointment."""
        await self._end(ctx, "CANCELLED", "Done — your appointment is cancelled. Take care.")
        return {"ok": True}


class IntakeAgent(BaseHealth):
    def __init__(self) -> None:
        super().__init__(instructions=(
            "Collect intake for a new/updating patient: contact info, reason for visit, and insurance (payer, "
            "member ID, group) via capture_insurance. Offer send_intake_form_link for full forms. Efficient and "
            "reassuring about privacy. Leave clinical questions to the provider. When done, hand to scheduling if "
            "an appointment is still needed."))

    @function_tool()
    async def capture_insurance(self, ctx: RunCtx, payer: str, member_id: str, group: str = "") -> dict:
        """Record the patient's insurance (payer, member ID, group)."""
        if not ctx.userdata.verified:
            raise ToolError("Verify the patient first.")
        return {"ok": True}

    @function_tool()
    async def send_intake_form_link(self, ctx: RunCtx, channel: str = "sms") -> dict:
        """Send the digital intake/consent forms by SMS or email."""
        return {"sent": True}

    @function_tool()
    async def to_scheduling(self, ctx: RunCtx) -> tuple[Agent, str]:
        """Intake complete and an appointment still needs booking."""
        return self._to(ctx, "scheduling")


class TriageAgent(BaseHealth):
    def __init__(self) -> None:
        super().__init__(instructions=(
            "Determine urgency to route correctly — NOT to diagnose. Ask a few structured questions about the main "
            "symptom, severity, and duration, then call triage_check. Follow its band exactly: emergency -> "
            "advise_emergency; urgent -> transfer_to_nurse; routine -> reassure and hand to scheduling. Never suggest "
            "a diagnosis, treatment, or medication."))

    @function_tool()
    async def triage_check(self, ctx: RunCtx, main_symptom: str, severity: str, duration: str = "") -> dict:
        """Apply the fixed clinical rules table to the symptoms and return a band (emergency|urgent|routine)."""
        text = f"{main_symptom} {severity}".lower()
        if any(flag in text for flag in RED_FLAGS) or severity.lower() == "severe":
            # Deterministic red-flag → force emergency routing, not model discretion.
            await self._do_emergency(ctx)
            return {"band": "emergency"}
        return {"band": "routine", "advice": "Book a standard visit within a week."}

    @function_tool()
    async def to_scheduling(self, ctx: RunCtx) -> tuple[Agent, str]:
        """Triage result is routine — book an appropriate visit."""
        return self._to(ctx, "scheduling")


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    userdata = HealthData()
    userdata.agents = {
        "front_desk": FrontDeskAgent(),
        "scheduling": SchedulingAgent(),
        "intake": IntakeAgent(),
        "triage": TriageAgent(),
    }
    session = AgentSession[HealthData](
        userdata=userdata,
        stt=deepgram.STT(model="nova-2"),
        llm=openai.LLM(model="gpt-4o", temperature=0.3),
        tts=cartesia.TTS(),
        vad=silero.VAD.load(),
    )
    await session.start(agent=userdata.agents["front_desk"], room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
