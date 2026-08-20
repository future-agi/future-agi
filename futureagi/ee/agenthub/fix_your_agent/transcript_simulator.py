import uuid
import re
from typing import Dict, Any, List, Tuple, Optional

from agentic_eval.core.data_generation.chat_simulator import ChatSimulator
from agentic_eval.core.llm.llm import LLM

import structlog

logger = structlog.get_logger(__name__)

# Termination keyword that customer should use when conversation is complete
END_CONVERSATION_MARKER = "[CONVERSATION_COMPLETE]"

# Common farewell patterns that indicate conversation is ending
FAREWELL_PATTERNS = [
    r"\b(goodbye|bye|bye-bye|take care|have a (good|nice|great) (day|one)|thank you.*(bye|goodbye)|thanks.*(bye|goodbye))\b",
    r"\bthat('s| is) all\b",
    r"\bnothing else\b",
    r"\bno (more|other) questions\b",
    r"\bthat solves (my|the) (problem|issue)\b",
    r"\bappreciate your help\b",
]


SINGLE_LLM_TRANSCRIPT_PROMPT = """You are simulating a phone call between a Customer and an Agent.

## Agent System Prompt (how the agent should behave):
{agent_system_prompt}

## Customer System Prompt (how the customer should behave):
{customer_system_prompt}

{issues_section}

## Instructions:
Generate a REALISTIC phone conversation transcript between the Customer and the Agent.

CRITICAL - Make the transcript realistic with these elements:
- Include natural human behavior: hesitations, interruptions, clarifications
- The Customer may be confused, impatient, or go off-topic sometimes
- Include realistic challenges: misunderstandings, unclear requests, customer changing their mind
- The Agent may NOT handle everything perfectly - they might miss something or make small errors
- Some customer questions might be difficult or outside the Agent's scope
- Include realistic pauses/filler words (um, uh, let me think...)
- The conversation may have awkward moments or require rephrasing

Conversation Flow:
{conversation_flow}
- Allow 6-12 exchanges for a complete conversation
- Include at least one challenge or difficulty the Agent must handle
- End the conversation naturally with goodbyes

Format each line as "Agent: ..." or "Customer: ..."

## Transcript:
"""

# Prompt with known issues to test
SINGLE_LLM_TRANSCRIPT_WITH_ISSUES_PROMPT = """You are simulating a phone call between a Customer and an Agent.

## Agent System Prompt (how the agent should behave):
{agent_system_prompt}

## Customer System Prompt (how the customer should behave):
{customer_system_prompt}

## KNOWN ISSUES TO TEST:
The agent prompt has these known weaknesses. Create a scenario where the customer's behavior might TRIGGER these issues, allowing us to see if the agent handles them well or poorly.

{issues_text}

## Instructions:
Generate a REALISTIC phone conversation that TESTS whether these issues occur.

CRITICAL:
- Design the customer's behavior to potentially trigger the known issues above
- The conversation should feel natural, not artificially constructed
- See if the Agent fails or succeeds when faced with challenging situations
- Include realistic human elements: confusion, impatience, interruptions
- Don't make it too easy for the agent - create realistic challenges

Conversation Flow:
{conversation_flow}

Format each line as "Agent: ..." or "Customer: ..."

## Transcript:
"""


class TranscriptSimulator:
    """
    Simulates a conversation between a Customer (simulated) and an Agent (simulated).

    Supports two modes:
    - single_llm=True (default, faster): One LLM generates the entire transcript
    - single_llm=False: Two LLMs take turns (more realistic but slower)
    """

    def __init__(
        self,
        agent_model: str = "vertex_ai/gemini-2.5-pro",
        customer_model: str = "vertex_ai/gemini-2.5-flash",
        single_llm: bool = True,  # Default to fast single-LLM mode
        inbound: bool = True,  # Default to Inbound (Customer calls Agent)
    ):
        self.agent_model = agent_model
        self.customer_model = customer_model
        self.single_llm = single_llm
        self.inbound = inbound

        # Helper to infer provider and clean model name
        def _get_provider_and_model(model_str: str):
            if "/" in model_str:
                parts = model_str.split("/", 1)
                return parts[0], parts[1]
            elif "gpt" in model_str:
                return "openai", model_str
            elif "claude" in model_str:
                return "anthropic", model_str
            elif "gemini" in model_str:
                return "vertex_ai", model_str
            elif "llama" in model_str:
                return "groq", model_str
            else:
                return "openai", model_str  # Default fallback

        agent_provider, agent_model_name = _get_provider_and_model(agent_model)
        customer_provider, customer_model_name = _get_provider_and_model(customer_model)

        logger.info(
            f"TranscriptSimulator: Agent ({agent_provider}/{agent_model_name}), "
            f"single_llm={single_llm}, inbound={inbound}"
        )

        # Initialize LLM clients
        self.agent_llm = LLM(provider=agent_provider, model_name=agent_model_name)

        if not single_llm:
            self.customer_llm = LLM(
                provider=customer_provider, model_name=customer_model_name
            )
            # Compile farewell patterns for efficiency
            self._farewell_regex = re.compile(
                "|".join(FAREWELL_PATTERNS), re.IGNORECASE
            )

    def _is_conversation_complete(
        self, message: str, recent_messages: List[str]
    ) -> Tuple[bool, str]:
        """
        Check if the conversation should end.

        Returns:
            Tuple of (should_end, reason)
        """
        # 1. Check for explicit termination marker
        if END_CONVERSATION_MARKER in message:
            return True, "explicit_marker"

        # 2. Check for endCall (legacy)
        if "endCall" in message:
            return True, "end_call"

        # 3. Check for farewell patterns
        if self._farewell_regex.search(message):
            return True, "farewell_pattern"

        # 4. Check for message repetition (if we have enough history)
        if len(recent_messages) >= 3:
            # Normalize messages for comparison (lowercase, strip whitespace)
            normalized_current = message.lower().strip()
            normalized_recent = [m.lower().strip() for m in recent_messages[-3:]]

            # Check if similar messages are repeating
            repeat_count = sum(
                1
                for m in normalized_recent
                if self._messages_similar(normalized_current, m)
            )
            if repeat_count >= 2:
                return True, "repetition_detected"

        return False, ""

    def _messages_similar(self, msg1: str, msg2: str, threshold: float = 0.8) -> bool:
        """
        Check if two messages are similar enough to be considered repetitive.
        Uses a simple word overlap ratio.
        """
        words1 = set(msg1.split())
        words2 = set(msg2.split())

        if not words1 or not words2:
            return msg1 == msg2

        intersection = words1 & words2
        union = words1 | words2

        similarity = len(intersection) / len(union) if union else 0
        return similarity >= threshold

    def run_simulation(
        self,
        agent_system_prompt: str,
        scenario: Dict[str, Any],
        customer_system_prompt: str = None,
        max_turns: int = 10,
        issues: List[Dict[str, Any]] = None,
    ) -> str:
        """
        Runs a single simulation session.

        Args:
            agent_system_prompt: The system prompt for the Agent being tested.
            scenario: A dictionary containing 'persona', 'situation', 'outcome', etc.
            customer_system_prompt: Optional explicit system prompt for the customer.
            max_turns: Maximum number of conversation turns.
            issues: Optional list of known agent issues to test against.

        Returns:
            The full transcript of the conversation as a string.
        """
        if self.single_llm:
            return self._run_single_llm_simulation(
                agent_system_prompt, scenario, customer_system_prompt, issues
            )
        else:
            return self._run_two_llm_simulation(
                agent_system_prompt, scenario, customer_system_prompt, max_turns
            )

    def _run_single_llm_simulation(
        self,
        agent_system_prompt: str,
        scenario: Dict[str, Any],
        customer_system_prompt: str = None,
        issues: List[Dict[str, Any]] = None,
    ) -> str:
        """
        Generate entire transcript with a single LLM call (fast mode).

        If issues are provided, the simulation will try to create scenarios
        that test whether these issues occur.
        """
        # Build customer system prompt if not provided
        if not customer_system_prompt:
            persona = scenario.get("persona", "A customer seeking help")
            situation = scenario.get("situation", "Calling for support")
            outcome = scenario.get("outcome", "Get their issue resolved")
            customer_system_prompt = (
                f"You are a customer calling a support agent.\n"
                f"Persona: {persona}\n"
                f"Situation: {situation}\n"
                f"Goal: {outcome}\n"
                f"Act naturally - be realistic. You may be confused, impatient, ask clarifying questions, or go slightly off-topic."
            )

        if self.inbound:
            conversation_flow = (
                "- The Customer calls the Agent. The Customer speaks first."
            )
        else:
            conversation_flow = (
                "- The Agent calls the Customer. The Agent speaks first."
            )

        # Choose prompt based on whether we have issues
        if issues:
            # Format issues for the prompt
            issues_text = self._format_issues_for_prompt(issues)
            prompt = SINGLE_LLM_TRANSCRIPT_WITH_ISSUES_PROMPT.format(
                agent_system_prompt=agent_system_prompt,
                customer_system_prompt=customer_system_prompt,
                issues_text=issues_text,
                conversation_flow=conversation_flow,
            )
            pass  # Placeholder for thought logic

        else:
            prompt = SINGLE_LLM_TRANSCRIPT_PROMPT.format(
                agent_system_prompt=agent_system_prompt,
                customer_system_prompt=customer_system_prompt,
                issues_section="",  # No issues to include
                conversation_flow=conversation_flow,
            )

        transcript = self.agent_llm._get_completion_content(
            messages=[{"role": "user", "content": prompt}]
        )

        return transcript.strip()

    def _format_issues_for_prompt(self, issues: List[Dict[str, Any]]) -> str:
        """Format issues into a text description for the simulation prompt."""
        if not issues:
            return ""

        issue_lines = []
        for i, issue in enumerate(issues, 1):
            heading = issue.get("heading", "Unknown Issue")
            priority = issue.get("priority", "medium")
            recommendation = issue.get("recommendation", "")
            breaking_points = issue.get("breaking_points", [])

            issue_text = f"{i}. [{priority.upper()}] {heading}"
            if breaking_points:
                issue_text += f"\n   Triggers: {', '.join(breaking_points[:3])}"
            if recommendation:
                issue_text += f"\n   Issue: {recommendation[:200]}"
            issue_lines.append(issue_text)

        return "\n".join(issue_lines)

    def _run_two_llm_simulation(
        self,
        agent_system_prompt: str,
        scenario: Dict[str, Any],
        customer_system_prompt: str = None,
        max_turns: int = 10,
    ) -> str:
        """
        Turn-by-turn simulation with two LLMs (slower but more realistic).
        """
        # 1. Prepare Customer System Prompt with termination instruction
        if not customer_system_prompt:
            customer_persona = scenario.get("persona", "You are a helpful customer.")
            customer_situation = scenario.get(
                "situation", "You are calling to inquire about a service."
            )
            customer_outcome = scenario.get(
                "outcome", "You want to get a clear answer."
            )

            customer_system_prompt = (
                f"You are a customer calling a support agent.\n"
                f"Persona: {customer_persona}\n"
                f"Situation: {customer_situation}\n"
                f"Goal: {customer_outcome}\n\n"
                f"IMPORTANT INSTRUCTIONS:\n"
                f"- Act naturally, be concise, and stay in character.\n"
                f"- When your goal is achieved or the conversation naturally concludes, "
                f"say goodbye and include '{END_CONVERSATION_MARKER}' at the end of your message.\n"
                f"- Do not drag the conversation unnecessarily. Once satisfied, end it politely."
            )

        # 2. Initialize Simulators
        agent_sim = ChatSimulator(
            llm_client=self.agent_llm,
            role="assistant",
            system_message=agent_system_prompt,
            model_name=self.agent_model,
        )

        customer_sim = ChatSimulator(
            llm_client=self.customer_llm,
            role="user",
            system_message=customer_system_prompt,
            model_name=self.customer_model,
        )

        # 3. Start Conversation
        transcript = []
        recent_customer_messages = []
        recent_agent_messages = []

        if self.inbound:
            # Inbound: Customer speaks first
            # We need the Customer to generate the opening line.
            # Since the Customer simulator usually responds to a message, we need to seed it or have a 'start' method.
            # Our ChatSimulator.get_response takes (last_message_content, last_message_id).
            # If it's the start, we can pass a system instruction or an empty "connection established" signal.

            # "The call connects."
            customer_response, msg_id = customer_sim.get_response(
                "The call has connected. You are the customer. Speak first.",
                str(uuid.uuid4()),
            )

            # Clean marker
            display_response = customer_response.replace(
                END_CONVERSATION_MARKER, ""
            ).strip()
            transcript.append(f"Customer: {display_response}")

            recent_customer_messages.append(customer_response)
            last_message_content = customer_response
            last_message_id = msg_id

        else:
            # Outbound: Agent speaks first
            agent_greeting = (
                "Hello, this is the support agent. How can I help you today?"
            )
            # Ideally the agent greeting should come from the LLM or be generic.
            # Or we can ask the Agent simulator to start.

            agent_response, msg_id = agent_sim.get_response(
                "The call has connected. You are the agent calling the customer. Speak first.",
                str(uuid.uuid4()),
            )

            transcript.append(f"Agent: {agent_response}")

            recent_agent_messages.append(agent_response)
            last_message_content = agent_response
            last_message_id = msg_id

        for turn in range(max_turns):
            if self.inbound:
                # Inbound Loop: Customer just spoke (or initialized).
                # Sequence: Agent -> Customer

                # Agent Turn
                agent_response, msg_id = agent_sim.get_response(
                    last_message_content, last_message_id
                )
                transcript.append(f"Agent: {agent_response}")
                recent_agent_messages.append(agent_response)
                last_message_content = agent_response
                last_message_id = msg_id

                should_end, reason = self._is_conversation_complete(
                    agent_response, recent_agent_messages
                )
                if should_end:
                    logger.debug(
                        f"Conversation ended after agent turn {turn + 1}: {reason}"
                    )
                    break

                # Customer Turn
                customer_response, msg_id = customer_sim.get_response(
                    last_message_content, last_message_id
                )

                # Clean marker
                display_response = customer_response.replace(
                    END_CONVERSATION_MARKER, ""
                ).strip()
                transcript.append(f"Customer: {display_response}")
                recent_customer_messages.append(customer_response)
                last_message_content = customer_response
                last_message_id = msg_id

                should_end, reason = self._is_conversation_complete(
                    customer_response, recent_customer_messages
                )
                if should_end:
                    logger.debug(
                        f"Conversation ended after customer turn {turn + 1}: {reason}"
                    )
                    break

            else:
                # Outbound Loop: Agent just spoke (or initialized).
                # Sequence: Customer -> Agent

                # Customer Turn
                customer_response, msg_id = customer_sim.get_response(
                    last_message_content, last_message_id
                )

                # Clean marker
                display_response = customer_response.replace(
                    END_CONVERSATION_MARKER, ""
                ).strip()
                transcript.append(f"Customer: {display_response}")
                recent_customer_messages.append(customer_response)
                last_message_content = customer_response
                last_message_id = msg_id

                should_end, reason = self._is_conversation_complete(
                    customer_response, recent_customer_messages
                )
                if should_end:
                    logger.debug(
                        f"Conversation ended after customer turn {turn + 1}: {reason}"
                    )
                    break

                # Agent Turn
                agent_response, msg_id = agent_sim.get_response(
                    last_message_content, last_message_id
                )
                transcript.append(f"Agent: {agent_response}")
                recent_agent_messages.append(agent_response)
                last_message_content = agent_response
                last_message_id = msg_id

                should_end, reason = self._is_conversation_complete(
                    agent_response, recent_agent_messages
                )
                if should_end:
                    logger.debug(
                        f"Conversation ended after agent turn {turn + 1}: {reason}"
                    )
                    break

        return "\n".join(transcript)
