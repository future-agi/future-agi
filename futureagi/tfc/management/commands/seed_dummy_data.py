"""
Management command to seed dummy data for testing.
Creates data across tracing, datasets, simulation, and prototyping.

Usage:
    python manage.py seed_dummy_data --email=nikhilpareekiitr@gmail.com
    python manage.py seed_dummy_data --email=nikhilpareekiitr@gmail.com --flush

CH25-TODO: KEEP-PG. Dev seed script — all ObservationSpan.objects sites
in this file stay PG by design:
  • ``.create()`` writes are dual-write source-of-truth (D-027). CH
    receives the seeded rows via PeerDB CDC.
  • ``_flush()`` performs PG reads/deletes against the legacy
    ObservationSpan rows; the CH side gets cleaned up by the same
    CDC pipeline (or a separate dev reset) — the seed script is
    explicitly PG-scoped.
``CHSpanReader`` is not applicable here.
"""

import random
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-3.5-turbo",
    "claude-3-opus",
    "claude-3-sonnet",
    "claude-3-haiku",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    "llama-3-70b",
    "mistral-large",
]

SAMPLE_INPUTS = [
    "What is the capital of France?",
    "Explain quantum computing in simple terms.",
    "Write a Python function to sort a list.",
    "Summarize the following document for me.",
    "How does photosynthesis work?",
    "Translate this text to Spanish: Hello, how are you?",
    "Generate a marketing email for our new product launch.",
    "What are the key differences between REST and GraphQL?",
    "Help me debug this JavaScript error: TypeError undefined.",
    "Create a meal plan for someone with diabetes.",
    "What is the weather forecast for tomorrow?",
    "Explain the theory of relativity.",
    "Write a haiku about programming.",
    "How do I deploy a Django app to production?",
    "What are best practices for API security?",
]

SAMPLE_OUTPUTS = [
    "The capital of France is Paris, which is also the largest city in the country.",
    "Quantum computing uses quantum bits (qubits) that can be in multiple states simultaneously, unlike classical bits.",
    "def sort_list(lst):\n    return sorted(lst)",
    "The document discusses the impact of AI on modern healthcare, highlighting key advancements in diagnostics.",
    "Photosynthesis is the process by which plants convert sunlight, water, and CO2 into glucose and oxygen.",
    "Hola, ¿cómo estás?",
    "Subject: Introducing Our Revolutionary New Product!\n\nDear valued customer...",
    "REST uses fixed endpoints with HTTP methods, while GraphQL uses a single endpoint with flexible queries.",
    "The TypeError occurs because you're trying to access a property of an undefined variable. Check line 42.",
    "Day 1: Breakfast - Oatmeal with berries. Lunch - Grilled chicken salad. Dinner - Salmon with vegetables.",
    "Tomorrow's forecast shows partly cloudy skies with temperatures around 72°F (22°C).",
    "Einstein's theory of relativity describes how space and time are interconnected and affected by gravity.",
    "Bugs crawl through code\nSilent errors multiply\nCoffee saves the day",
    "Use gunicorn as WSGI server, nginx as reverse proxy, and deploy on a cloud VM or container platform.",
    "Key practices: use HTTPS, implement rate limiting, validate inputs, use OAuth2, and rotate API keys regularly.",
]

TAGS_POOL = [
    "production",
    "staging",
    "debug",
    "customer-support",
    "internal",
    "high-priority",
    "v2",
    "experiment",
    "chatbot",
    "rag",
    "search",
    "summarization",
    "translation",
    "code-gen",
    "analysis",
]

DATASET_DOMAINS = [
    (
        "Customer Support QA",
        "Question-answer pairs for customer support chatbot training",
    ),
    ("Product Reviews", "Sentiment-labeled product review dataset"),
    ("Code Generation", "Programming task prompts with expected code outputs"),
    ("Medical QA", "Medical question answering dataset for healthcare AI"),
    ("Legal Documents", "Legal clause classification dataset"),
    ("Translation Pairs", "English-Spanish translation training data"),
    ("Summarization", "Article summarization dataset with source and summary"),
    ("Chat Conversations", "Multi-turn conversation dataset for chatbot fine-tuning"),
    ("SQL Generation", "Natural language to SQL query pairs"),
    ("Image Captioning", "Image descriptions for multimodal model training"),
]

AGENT_NAMES = [
    "Insurance Claims Bot",
    "Travel Booking Assistant",
    "Tech Support Agent",
    "Restaurant Reservation Bot",
    "Banking Customer Service",
    "Healthcare Appointment Scheduler",
    "E-commerce Returns Handler",
    "HR Benefits Advisor",
    "Real Estate Virtual Agent",
    "Fitness Coaching Bot",
]

AGENT_DESCRIPTIONS = [
    "Handles insurance claims processing, policy inquiries, and claims status updates.",
    "Assists customers with flight/hotel bookings, itinerary changes, and travel recommendations.",
    "Provides technical support for software products, troubleshooting, and escalation.",
    "Manages restaurant table reservations, menu inquiries, and special requests.",
    "Handles banking queries including account balance, transfers, and card services.",
    "Schedules medical appointments, handles rescheduling, and provides clinic information.",
    "Processes product returns, refund requests, and exchange coordination.",
    "Answers questions about employee benefits, enrollment, and policy details.",
    "Assists with property listings, virtual tours scheduling, and mortgage inquiries.",
    "Provides workout plans, nutrition advice, and progress tracking support.",
]

PERSONA_NAMES = [
    "Alex Johnson",
    "Priya Sharma",
    "Marcus Chen",
    "Sarah Williams",
    "James Rodriguez",
    "Emily Park",
    "David Kim",
    "Lisa Thompson",
    "Robert Singh",
    "Maria Garcia",
]

SCENARIO_NAMES = [
    "Happy Path - Simple Inquiry",
    "Angry Customer - Billing Issue",
    "Complex Multi-step Request",
    "Edge Case - Missing Information",
    "Escalation Required",
    "Multilingual Interaction",
    "Technical Troubleshooting",
    "Cancellation Flow",
    "Upsell Opportunity",
    "After-hours Inquiry",
]


def rand_ts(days_back=30):
    """Random timestamp within last N days."""
    return timezone.now() - timedelta(
        days=random.randint(0, days_back),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )


def rand_latency():
    return random.randint(50, 5000)


def rand_tokens():
    prompt = random.randint(50, 2000)
    completion = random.randint(20, 1500)
    return prompt, completion, prompt + completion


class Command(BaseCommand):
    help = "Seed dummy data for tracing, datasets, simulation, and prototyping"

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            default="nikhilpareekiitr@gmail.com",
            help="Email of the user to associate data with",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete all existing seed data before creating new data",
        )

    def handle(self, *args, **options):
        from accounts.models.user import User

        email = options["email"]
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"User with email {email} not found"))
            return

        org = user.organization
        if not org:
            self.stderr.write(self.style.ERROR("User has no organization"))
            return

        self.stdout.write(f"Seeding data for {user.name} ({email}) in org {org.name}")

        if options["flush"]:
            self._flush(org)

        self._seed_tracing(user, org)
        self._seed_datasets(user, org)
        self._seed_simulation(user, org)
        self._seed_voice_observability(user, org)

        self.stdout.write(
            self.style.SUCCESS("\nAll done! Dummy data seeded successfully.")
        )

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------
    def _flush(self, org):
        from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
        from simulate.models.agent_definition import AgentDefinition
        from simulate.models.persona import Persona
        from simulate.models.run_test import RunTest
        from simulate.models.scenarios import Scenarios
        from simulate.models.simulator_agent import SimulatorAgent
        from simulate.models.test_execution import CallExecution, TestExecution
        from tracer.models.observation_span import ObservationSpan
        from tracer.models.project import Project
        from tracer.models.trace import Trace

        self.stdout.write("Flushing existing seed data...")

        # Tracing - delete projects with [Seed] prefix
        seed_projects = Project.all_objects.filter(
            organization=org, name__startswith="[Seed]"
        )
        from tracer.models.observability_provider import ObservabilityProvider

        ObservabilityProvider.all_objects.filter(project__in=seed_projects).delete()
        ObservationSpan.all_objects.filter(project__in=seed_projects).delete()
        Trace.all_objects.filter(project__in=seed_projects).delete()
        seed_projects.delete()

        # Datasets
        seed_datasets = Dataset.all_objects.filter(
            organization=org, name__startswith="[Seed]"
        )
        Cell.all_objects.filter(dataset__in=seed_datasets).delete()
        Row.all_objects.filter(dataset__in=seed_datasets).delete()
        Column.all_objects.filter(dataset__in=seed_datasets).delete()
        seed_datasets.delete()

        # Simulation
        seed_agents = AgentDefinition.all_objects.filter(
            organization=org, agent_name__startswith="[Seed]"
        )
        CallExecution.all_objects.filter(
            test_execution__run_test__agent_definition__in=seed_agents
        ).delete()
        TestExecution.all_objects.filter(
            run_test__agent_definition__in=seed_agents
        ).delete()
        RunTest.all_objects.filter(agent_definition__in=seed_agents).delete()
        Scenarios.all_objects.filter(agent_definition__in=seed_agents).delete()
        seed_agents.delete()

        SimulatorAgent.all_objects.filter(
            organization=org, name__startswith="[Seed]"
        ).delete()
        Persona.all_objects.filter(organization=org, name__startswith="[Seed]").delete()

        self.stdout.write(self.style.WARNING("  Flushed."))

    # ------------------------------------------------------------------
    # TRACING
    # ------------------------------------------------------------------
    def _seed_tracing(self, user, org):
        from tracer.models.observation_span import ObservationSpan
        from tracer.models.project import Project
        from tracer.models.project_version import ProjectVersion
        from tracer.models.trace import Trace
        from tracer.models.trace_session import TraceSession

        self.stdout.write("\n--- Seeding Tracing ---")

        project_configs = [
            ("[Seed] Customer Support Bot", "observe", "prototype"),
            ("[Seed] RAG Pipeline", "observe", "prototype"),
            ("[Seed] Code Assistant", "observe", "prototype"),
            ("[Seed] Translation Service", "experiment", "prototype"),
            ("[Seed] Content Summarizer", "experiment", "prototype"),
            ("[Seed] Data Extraction Agent", "observe", "prototype"),
            ("[Seed] Search & Retrieval", "observe", "prototype"),
            ("[Seed] Multi-Agent Workflow", "observe", "prototype"),
        ]

        for proj_name, trace_type, source in project_configs:
            project = Project.objects.create(
                name=proj_name,
                organization=org,
                model_type="GenerativeLLM",
                trace_type=trace_type,
                source=source,
                user=user,
                metadata={"seeded": True},
            )
            self.stdout.write(f"  Project: {proj_name}")

            # Create 2-3 versions per project
            versions = []
            for v in range(1, random.randint(3, 5)):
                ts = rand_ts(60)
                pv = ProjectVersion.objects.create(
                    project=project,
                    name=f"v{v}",
                    version=f"v{v}",
                    start_time=ts,
                    end_time=ts + timedelta(hours=random.randint(1, 48)),
                    metadata={"seeded": True},
                )
                versions.append(pv)

            # Create sessions
            sessions = []
            for s in range(random.randint(3, 8)):
                session = TraceSession.objects.create(
                    project=project,
                    name=f"Session {s + 1}",
                    bookmarked=random.random() > 0.7,
                )
                sessions.append(session)

            # Create 15-40 traces per project
            num_traces = random.randint(15, 40)
            for t in range(num_traces):
                inp_idx = random.randint(0, len(SAMPLE_INPUTS) - 1)
                trace_ts = rand_ts(30)
                trace = Trace.objects.create(
                    project=project,
                    project_version=random.choice(versions),
                    name=f"trace-{t + 1}",
                    input={
                        "messages": [
                            {"role": "user", "content": SAMPLE_INPUTS[inp_idx]}
                        ]
                    },
                    output={"content": SAMPLE_OUTPUTS[inp_idx]},
                    session=(
                        random.choice(sessions)
                        if sessions and random.random() > 0.3
                        else None
                    ),
                    tags=random.sample(TAGS_POOL, k=random.randint(0, 3)),
                    metadata={
                        "seeded": True,
                        "environment": random.choice(["prod", "staging", "dev"]),
                    },
                )
                # Force created_at
                Trace.all_objects.filter(id=trace.id).update(created_at=trace_ts)

                # Create 2-6 spans per trace
                num_spans = random.randint(2, 6)
                span_types = ["llm", "chain", "tool", "retriever", "agent", "guardrail"]
                parent_span_id = None
                for sp in range(num_spans):
                    span_type = (
                        span_types[sp]
                        if sp < len(span_types)
                        else random.choice(span_types)
                    )
                    model = random.choice(MODELS) if span_type == "llm" else None
                    p_tok, c_tok, t_tok = (
                        rand_tokens() if span_type == "llm" else (None, None, None)
                    )
                    latency = rand_latency()
                    span_start = trace_ts + timedelta(milliseconds=sp * latency)
                    span_end = span_start + timedelta(milliseconds=latency)
                    cost = (
                        round(random.uniform(0.0001, 0.05), 4)
                        if span_type == "llm"
                        else None
                    )

                    span_id = str(uuid.uuid4())[:16]
                    span = ObservationSpan.objects.create(
                        id=span_id,
                        project=project,
                        project_version=trace.project_version,
                        trace=trace,
                        parent_span_id=parent_span_id,
                        name=f"{span_type}-{sp + 1}",
                        observation_type=span_type,
                        start_time=span_start,
                        end_time=span_end,
                        input=(
                            {"content": SAMPLE_INPUTS[inp_idx]}
                            if span_type == "llm"
                            else {"query": f"step-{sp}"}
                        ),
                        output=(
                            {"content": SAMPLE_OUTPUTS[inp_idx]}
                            if span_type == "llm"
                            else {"result": f"done-{sp}"}
                        ),
                        model=model,
                        latency_ms=latency,
                        prompt_tokens=p_tok,
                        completion_tokens=c_tok,
                        total_tokens=t_tok,
                        response_time=latency / 1000.0,
                        cost=cost,
                        status=random.choice(["OK", "OK", "OK", "ERROR"]),
                        tags=random.sample(TAGS_POOL, k=random.randint(0, 2)),
                        metadata={"seeded": True},
                        provider=(
                            random.choice(["openai", "anthropic", "google", "meta"])
                            if span_type == "llm"
                            else None
                        ),
                    )
                    parent_span_id = span_id

            self.stdout.write(f"    {num_traces} traces with spans")

        self.stdout.write(self.style.SUCCESS("  Tracing seeded!"))

    # ------------------------------------------------------------------
    # DATASETS
    # ------------------------------------------------------------------
    def _seed_datasets(self, user, org):
        from model_hub.models.choices import (
            DatasetSourceChoices,
            DataTypeChoices,
            SourceChoices,
            StatusType,
        )
        from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

        self.stdout.write("\n--- Seeding Datasets ---")

        for domain_name, domain_desc in DATASET_DOMAINS:
            ds_name = f"[Seed] {domain_name}"
            # Define columns based on domain
            col_defs = self._get_column_defs(domain_name)

            dataset = Dataset.objects.create(
                name=ds_name,
                organization=org,
                source=DatasetSourceChoices.BUILD.value,
                model_type="GenerativeLLM",
                column_order=[],
                user=user,
            )
            self.stdout.write(f"  Dataset: {ds_name}")

            # Create columns
            columns = []
            for cd in col_defs:
                col = Column.objects.create(
                    name=cd["name"],
                    data_type=cd["data_type"],
                    dataset=dataset,
                    source=SourceChoices.OTHERS.value,
                    status=StatusType.COMPLETED.value,
                )
                columns.append(col)

            # Update column_order with actual column UUIDs
            dataset.column_order = [str(c.id) for c in columns]
            dataset.save(update_fields=["column_order"])

            # Create 20-50 rows per dataset
            num_rows = random.randint(20, 50)
            rows_to_create = []
            for r in range(num_rows):
                rows_to_create.append(Row(dataset=dataset, order=r + 1))
            Row.objects.bulk_create(rows_to_create)
            created_rows = list(Row.objects.filter(dataset=dataset).order_by("order"))

            # Create cells
            cells_to_create = []
            for row in created_rows:
                for col_idx, col in enumerate(columns):
                    value = self._generate_cell_value(
                        col_defs[col_idx]["name"],
                        col_defs[col_idx]["data_type"],
                        domain_name,
                        row.order,
                    )
                    cells_to_create.append(
                        Cell(
                            dataset=dataset,
                            column=col,
                            row=row,
                            value=value,
                            status="pass",
                        )
                    )
            Cell.objects.bulk_create(cells_to_create)

            self.stdout.write(f"    {num_rows} rows x {len(columns)} columns")

        self.stdout.write(self.style.SUCCESS("  Datasets seeded!"))

    def _get_column_defs(self, domain):
        base_cols = {
            "Customer Support QA": [
                {"name": "question", "data_type": "text"},
                {"name": "answer", "data_type": "text"},
                {"name": "category", "data_type": "text"},
                {"name": "difficulty", "data_type": "text"},
            ],
            "Product Reviews": [
                {"name": "review_text", "data_type": "text"},
                {"name": "sentiment", "data_type": "text"},
                {"name": "rating", "data_type": "integer"},
                {"name": "product_category", "data_type": "text"},
            ],
            "Code Generation": [
                {"name": "prompt", "data_type": "text"},
                {"name": "expected_code", "data_type": "text"},
                {"name": "language", "data_type": "text"},
                {"name": "difficulty", "data_type": "text"},
            ],
            "Medical QA": [
                {"name": "question", "data_type": "text"},
                {"name": "answer", "data_type": "text"},
                {"name": "specialty", "data_type": "text"},
                {"name": "verified", "data_type": "boolean"},
            ],
            "Legal Documents": [
                {"name": "clause_text", "data_type": "text"},
                {"name": "classification", "data_type": "text"},
                {"name": "risk_level", "data_type": "text"},
            ],
            "Translation Pairs": [
                {"name": "source_text", "data_type": "text"},
                {"name": "target_text", "data_type": "text"},
                {"name": "source_lang", "data_type": "text"},
                {"name": "target_lang", "data_type": "text"},
            ],
            "Summarization": [
                {"name": "article", "data_type": "text"},
                {"name": "summary", "data_type": "text"},
                {"name": "word_count", "data_type": "integer"},
            ],
            "Chat Conversations": [
                {"name": "conversation", "data_type": "text"},
                {"name": "topic", "data_type": "text"},
                {"name": "turns", "data_type": "integer"},
                {"name": "quality_score", "data_type": "float"},
            ],
            "SQL Generation": [
                {"name": "natural_language", "data_type": "text"},
                {"name": "sql_query", "data_type": "text"},
                {"name": "database_schema", "data_type": "text"},
            ],
            "Image Captioning": [
                {"name": "image_url", "data_type": "text"},
                {"name": "caption", "data_type": "text"},
                {"name": "detail_level", "data_type": "text"},
            ],
        }
        return base_cols.get(
            domain,
            [
                {"name": "input", "data_type": "text"},
                {"name": "output", "data_type": "text"},
            ],
        )

    def _generate_cell_value(self, col_name, data_type, domain, row_idx):
        if data_type == "integer":
            if col_name == "rating":
                return str(random.randint(1, 5))
            if col_name == "word_count":
                return str(random.randint(50, 2000))
            if col_name == "turns":
                return str(random.randint(2, 20))
            return str(random.randint(1, 100))

        if data_type == "float":
            return str(round(random.uniform(0.0, 1.0), 2))

        if data_type == "boolean":
            return random.choice(["true", "false"])

        # Text values
        text_generators = {
            "question": lambda: random.choice(SAMPLE_INPUTS),
            "answer": lambda: random.choice(SAMPLE_OUTPUTS),
            "prompt": lambda: random.choice(SAMPLE_INPUTS),
            "expected_code": lambda: f"def solution_{row_idx}():\n    return {random.randint(1, 100)}",
            "review_text": lambda: random.choice(
                [
                    "Great product! Exactly what I needed. Fast shipping too.",
                    "Terrible quality. Broke after 2 days of use.",
                    "Average product. Does what it says but nothing special.",
                    "Exceeded my expectations. Would buy again!",
                    "Not worth the price. Very disappointing.",
                    "Good value for money. Solid build quality.",
                    "Amazing customer service but product could be better.",
                    "Perfect gift idea! The recipient loved it.",
                ]
            ),
            "sentiment": lambda: random.choice(["positive", "negative", "neutral"]),
            "category": lambda: random.choice(
                ["billing", "technical", "shipping", "returns", "general"]
            ),
            "difficulty": lambda: random.choice(["easy", "medium", "hard"]),
            "product_category": lambda: random.choice(
                ["electronics", "clothing", "home", "sports", "books"]
            ),
            "language": lambda: random.choice(
                ["python", "javascript", "java", "go", "rust"]
            ),
            "specialty": lambda: random.choice(
                ["cardiology", "neurology", "pediatrics", "oncology", "dermatology"]
            ),
            "clause_text": lambda: f"Section {row_idx}.{random.randint(1,9)}: The party shall comply with all applicable regulations...",
            "classification": lambda: random.choice(
                ["termination", "liability", "indemnification", "confidentiality"]
            ),
            "risk_level": lambda: random.choice(["low", "medium", "high", "critical"]),
            "source_text": lambda: random.choice(SAMPLE_INPUTS),
            "target_text": lambda: f"[Translated] {random.choice(SAMPLE_OUTPUTS)[:80]}",
            "source_lang": lambda: "English",
            "target_lang": lambda: random.choice(
                ["Spanish", "French", "German", "Japanese", "Chinese"]
            ),
            "article": lambda: f"In a recent study published in Nature, researchers discovered that {random.choice(['AI models', 'neural networks', 'language models'])} can {random.choice(['predict', 'generate', 'analyze'])} {random.choice(['medical data', 'scientific papers', 'code snippets'])} with unprecedented accuracy...",
            "summary": lambda: f"Researchers found that AI can effectively process and analyze complex data with high accuracy.",
            "conversation": lambda: f"User: {random.choice(SAMPLE_INPUTS)}\nAssistant: {random.choice(SAMPLE_OUTPUTS)}",
            "topic": lambda: random.choice(
                ["tech-support", "sales", "general-inquiry", "complaint", "feedback"]
            ),
            "natural_language": lambda: random.choice(
                [
                    "Show me all users who signed up last month",
                    "Find the top 10 products by revenue",
                    "Count orders by status",
                    "Get average order value per customer",
                ]
            ),
            "sql_query": lambda: random.choice(
                [
                    "SELECT * FROM users WHERE created_at > DATE_SUB(NOW(), INTERVAL 1 MONTH)",
                    "SELECT p.name, SUM(o.total) as revenue FROM products p JOIN orders o ON p.id = o.product_id GROUP BY p.id ORDER BY revenue DESC LIMIT 10",
                ]
            ),
            "database_schema": lambda: "users(id, name, email, created_at), orders(id, user_id, total, status, created_at), products(id, name, price, category)",
            "image_url": lambda: f"https://example.com/images/sample_{row_idx}.jpg",
            "caption": lambda: random.choice(
                [
                    "A golden retriever playing in a park",
                    "Sunset over the ocean with sailboats",
                    "A busy city street at night with neon lights",
                    "Mountain landscape covered in snow",
                ]
            ),
            "detail_level": lambda: random.choice(
                ["brief", "detailed", "comprehensive"]
            ),
            "quality_score": lambda: str(round(random.uniform(0.0, 1.0), 2)),
        }

        gen = text_generators.get(
            col_name, lambda: f"Sample {col_name} value {row_idx}"
        )
        return gen()

    # ------------------------------------------------------------------
    # SIMULATION
    # ------------------------------------------------------------------
    def _seed_simulation(self, user, org):
        from model_hub.models.choices import StatusType
        from simulate.models.agent_definition import AgentDefinition
        from simulate.models.agent_version import AgentVersion
        from simulate.models.persona import Persona
        from simulate.models.run_test import RunTest
        from simulate.models.scenarios import Scenarios
        from simulate.models.simulator_agent import SimulatorAgent
        from simulate.models.test_execution import CallExecution, TestExecution

        self.stdout.write("\n--- Seeding Simulation ---")

        # Create personas
        personas = []
        for name in PERSONA_NAMES:
            persona = Persona.objects.create(
                persona_type="workspace",
                name=f"[Seed] {name}",
                description=f"Test persona for {name}",
                organization=org,
                gender=random.choice([["male"], ["female"], ["non-binary"]]),
                age_group=random.choice([["18-25"], ["25-32"], ["32-40"], ["40-50"]]),
                occupation=[
                    random.choice(
                        ["engineer", "teacher", "doctor", "student", "retired"]
                    )
                ],
                personality=[
                    random.choice(
                        ["patient", "impatient", "friendly", "assertive", "confused"]
                    )
                ],
                communication_style=[
                    random.choice(["formal", "casual", "technical", "brief"])
                ],
                simulation_type=random.choice(["voice", "text"]),
            )
            personas.append(persona)
        self.stdout.write(f"  {len(personas)} personas created")

        # Create simulator agents
        sim_agents = []
        for i in range(3):
            sa = SimulatorAgent.objects.create(
                name=f"[Seed] Simulator Agent {i + 1}",
                prompt="You are simulating a customer calling for support. Be realistic and natural.",
                voice_provider="elevenlabs",
                voice_name=random.choice(["Rachel", "Drew", "Paul", "Bella"]),
                model=random.choice(["gpt-4o", "claude-3-sonnet"]),
                llm_temperature=round(random.uniform(0.5, 1.0), 1),
                max_call_duration_in_minutes=random.randint(5, 30),
                organization=org,
            )
            sim_agents.append(sa)
        self.stdout.write(f"  {len(sim_agents)} simulator agents created")

        # Create agent definitions + versions + scenarios + tests
        for idx in range(min(len(AGENT_NAMES), 6)):
            agent = AgentDefinition.objects.create(
                agent_name=f"[Seed] {AGENT_NAMES[idx]}",
                agent_type=random.choice(["voice", "text"]),
                description=AGENT_DESCRIPTIONS[idx],
                inbound=random.choice([True, False]),
                provider=random.choice(["vapi", "retell", "bland"]),
                language="en",
                organization=org,
                model=random.choice(MODELS[:4]),
            )
            self.stdout.write(f"  Agent: {AGENT_NAMES[idx]}")

            # Versions
            versions = []
            for v in range(1, random.randint(3, 5)):
                av = AgentVersion.objects.create(
                    agent_definition=agent,
                    version_number=v,
                    version_name=f"v{v}.0",
                    status=random.choice(["draft", "active", "active", "archived"]),
                    score=(
                        round(random.uniform(3.0, 5.0), 1)
                        if random.random() > 0.3
                        else None
                    ),
                    test_count=random.randint(0, 20),
                    pass_rate=(
                        round(random.uniform(60, 100), 2)
                        if random.random() > 0.3
                        else None
                    ),
                    description=f"Version {v} of {AGENT_NAMES[idx]}",
                    organization=org,
                    configuration_snapshot={
                        "model": random.choice(MODELS[:4]),
                        "temperature": 0.7,
                    },
                )
                versions.append(av)

            # Scenarios
            scenarios = []
            num_scenarios = random.randint(3, 6)
            for s in range(num_scenarios):
                scenario_name = SCENARIO_NAMES[s % len(SCENARIO_NAMES)]
                scenario = Scenarios.objects.create(
                    name=f"{scenario_name} - {AGENT_NAMES[idx][:20]}",
                    description=f"Test scenario: {scenario_name}",
                    source=f"Simulated scenario for testing {AGENT_NAMES[idx]}",
                    scenario_type="dataset",
                    source_type="agent_definition",
                    organization=org,
                    agent_definition=agent,
                    status=StatusType.COMPLETED.value,
                    metadata={"seeded": True},
                )
                scenarios.append(scenario)

            # Run tests
            for rt_idx in range(random.randint(2, 4)):
                run_test = RunTest.objects.create(
                    name=f"Test Run {rt_idx + 1} - {AGENT_NAMES[idx][:20]}",
                    description=f"Automated test run #{rt_idx + 1}",
                    agent_definition=agent,
                    agent_version=random.choice(versions),
                    simulator_agent=random.choice(sim_agents),
                    organization=org,
                )
                run_test.scenarios.set(
                    random.sample(
                        scenarios, k=min(len(scenarios), random.randint(2, 4))
                    )
                )

                # Test executions
                for te_idx in range(random.randint(1, 3)):
                    te_status = random.choice(
                        ["completed", "completed", "completed", "failed", "running"]
                    )
                    started = rand_ts(14)
                    te = TestExecution.objects.create(
                        run_test=run_test,
                        status=te_status,
                        started_at=started,
                        completed_at=(
                            started + timedelta(minutes=random.randint(5, 60))
                            if te_status == "completed"
                            else None
                        ),
                        total_scenarios=len(scenarios),
                        total_calls=random.randint(5, 20),
                        completed_calls=random.randint(3, 15),
                        failed_calls=random.randint(0, 3),
                        agent_definition=agent,
                        agent_version=random.choice(versions),
                        simulator_agent=random.choice(sim_agents),
                    )

                    # Call executions
                    num_calls = random.randint(3, 10)
                    for c in range(num_calls):
                        call_status = random.choice(
                            [
                                "completed",
                                "completed",
                                "completed",
                                "failed",
                                "analyzing",
                            ]
                        )
                        call_start = started + timedelta(minutes=c * 2)
                        duration = random.randint(30, 600)

                        CallExecution.objects.create(
                            test_execution=te,
                            simulation_call_type=agent.agent_type,
                            scenario=random.choice(scenarios),
                            status=call_status,
                            started_at=call_start,
                            completed_at=(
                                call_start + timedelta(seconds=duration)
                                if call_status in ("completed", "analyzing")
                                else None
                            ),
                            duration_seconds=(
                                duration
                                if call_status in ("completed", "analyzing")
                                else None
                            ),
                            cost_cents=random.randint(1, 50),
                            overall_score=(
                                round(random.uniform(1.0, 5.0), 1)
                                if call_status == "completed"
                                else None
                            ),
                            response_time_ms=random.randint(200, 3000),
                            message_count=random.randint(4, 30),
                            transcript_available=call_status == "completed",
                            call_summary=(
                                f"Customer called about {random.choice(['billing issue', 'product inquiry', 'technical problem', 'appointment scheduling'])}. Agent {'resolved' if random.random() > 0.3 else 'escalated'} the issue."
                                if call_status == "completed"
                                else None
                            ),
                            ended_reason=(
                                random.choice(
                                    ["customer_ended", "agent_ended", "timeout"]
                                )
                                if call_status == "completed"
                                else None
                            ),
                            call_metadata={"seeded": True},
                            avg_agent_latency_ms=(
                                random.randint(500, 3000)
                                if call_status == "completed"
                                else None
                            ),
                            user_wpm=(
                                random.randint(100, 180)
                                if call_status == "completed"
                                else None
                            ),
                            bot_wpm=(
                                random.randint(120, 200)
                                if call_status == "completed"
                                else None
                            ),
                        )

            self.stdout.write(f"    Versions, scenarios, tests & calls created")

        self.stdout.write(self.style.SUCCESS("  Simulation seeded!"))

    # ------------------------------------------------------------------
    # VOICE OBSERVABILITY (VAPI)
    # ------------------------------------------------------------------

    VAPI_CONVERSATIONS = [
        # (scenario, turns list of (role, message))
        (
            "Insurance Claim Inquiry",
            [
                (
                    "assistant",
                    "Thank you for calling Apex Insurance. My name is Sarah. How can I help you today?",
                ),
                (
                    "user",
                    "Hi Sarah, I need to file a claim for water damage in my basement.",
                ),
                (
                    "assistant",
                    "I'm sorry to hear about the water damage. I can definitely help you with that. Can you please provide me with your policy number?",
                ),
                ("user", "Sure, it's P-7 7 4 2 3 1."),
                (
                    "assistant",
                    "Thank you. I've pulled up your policy. I can see you have comprehensive homeowner's coverage. When did the damage occur?",
                ),
                (
                    "user",
                    "It happened last Thursday during the heavy rain. I noticed water seeping through the foundation.",
                ),
                (
                    "assistant",
                    "I understand. I'll initiate a claim for you right now. A claims adjuster will contact you within 24 to 48 hours to schedule an inspection. Your claim number is C-98451. Is there anything else I can help with?",
                ),
                ("user", "No, that's all. Thank you for your help."),
                (
                    "assistant",
                    "You're welcome. I hope we can get this resolved quickly for you. Have a good day!",
                ),
            ],
        ),
        (
            "Healthcare Appointment Scheduling",
            [
                (
                    "assistant",
                    "Good morning, this is Valley Medical Center. How may I assist you?",
                ),
                (
                    "user",
                    "Hi, I need to schedule a follow-up appointment with Dr. Chen.",
                ),
                (
                    "assistant",
                    "Of course. May I have your name and date of birth for verification?",
                ),
                ("user", "It's James Rodriguez, date of birth March 15th, 1985."),
                (
                    "assistant",
                    "Thank you, James. I can see Dr. Chen has availability next Tuesday at 2:30 PM or Thursday at 10 AM. Which works better for you?",
                ),
                ("user", "Thursday at 10 would be perfect."),
                (
                    "assistant",
                    "Great, I've booked you for Thursday at 10 AM with Dr. Chen. You'll receive a confirmation text shortly. Please arrive 10 minutes early. Is there anything else?",
                ),
                ("user", "Nope, that's it. Thanks!"),
                ("assistant", "Have a wonderful day, James. We'll see you Thursday."),
            ],
        ),
        (
            "Banking Customer Service - Transfer",
            [
                (
                    "assistant",
                    "Welcome to FirstBank. I'm Alex, your virtual banking assistant. How can I help you today?",
                ),
                (
                    "user",
                    "I need to transfer money from my savings to my checking account.",
                ),
                (
                    "assistant",
                    "I can help with that. For security, could you please verify your account with your full name and the last four digits of your social security number?",
                ),
                ("user", "Emily Park, last four 8 8 2 1."),
                (
                    "assistant",
                    "Thank you, Emily. Your identity is verified. How much would you like to transfer from savings to checking?",
                ),
                ("user", "I need to move two thousand five hundred dollars."),
                (
                    "assistant",
                    "I'll transfer $2,500 from your savings ending in 4521 to your checking ending in 7893. Your savings balance will be $12,340 after the transfer. Shall I proceed?",
                ),
                ("user", "Yes, go ahead."),
                (
                    "assistant",
                    "The transfer is complete. The funds should be available in your checking account immediately. Your confirmation number is T-55219. Is there anything else?",
                ),
                ("user", "Can you also check if my credit card payment went through?"),
                (
                    "assistant",
                    "Your credit card payment of $450 was processed on March 5th and is confirmed. Your next statement date is March 28th.",
                ),
                ("user", "Great, thank you so much."),
                (
                    "assistant",
                    "You're welcome, Emily. Thank you for banking with FirstBank. Have a great day!",
                ),
            ],
        ),
        (
            "Tech Support - Software Issue",
            [
                (
                    "assistant",
                    "Thank you for contacting TechPro Support. I'm here to help. What issue are you experiencing?",
                ),
                (
                    "user",
                    "My application keeps crashing whenever I try to export a PDF. It just freezes and then closes.",
                ),
                (
                    "assistant",
                    "I'm sorry about that frustration. Which version of the application are you running, and what operating system?",
                ),
                ("user", "Version 4.2.1 on Windows 11."),
                (
                    "assistant",
                    "Thank you. There's a known issue with PDF exports in version 4.2.1. I'd recommend updating to version 4.2.3 which has the fix. Would you like me to walk you through the update process?",
                ),
                ("user", "Yes please."),
                (
                    "assistant",
                    "Go to Settings, then About, and click Check for Updates. The update should download automatically. Once installed, restart the application and try your PDF export again.",
                ),
                (
                    "user",
                    "Okay, it's downloading now. Looks like it's installing. Let me try the export... it worked! Thank you!",
                ),
                (
                    "assistant",
                    "Excellent! I'm glad that resolved the issue. Is there anything else I can help with today?",
                ),
                ("user", "No, that's everything. You've been really helpful."),
                (
                    "assistant",
                    "Thank you for contacting TechPro Support. Have a great day!",
                ),
            ],
        ),
        (
            "Restaurant Reservation",
            [
                (
                    "assistant",
                    "Thank you for calling Bella Italia. How can I help you this evening?",
                ),
                ("user", "Hi, I'd like to make a reservation for this Saturday."),
                ("assistant", "Wonderful! How many guests will be dining with us?"),
                ("user", "There will be six of us. It's a birthday celebration."),
                (
                    "assistant",
                    "How lovely! Happy birthday! For Saturday with six guests, I have openings at 6 PM, 7:30 PM, or 8:45 PM. What time would you prefer?",
                ),
                ("user", "7:30 would be ideal."),
                (
                    "assistant",
                    "I've reserved a table for six at 7:30 PM this Saturday. Since it's a birthday, would you like us to prepare a complimentary dessert with a candle?",
                ),
                ("user", "Oh that would be amazing, yes please!"),
                (
                    "assistant",
                    "Consider it done! May I have a name for the reservation?",
                ),
                ("user", "Marcus Chen."),
                (
                    "assistant",
                    "All set, Marcus. Table for six, Saturday at 7:30, with a birthday dessert. We look forward to seeing you!",
                ),
            ],
        ),
        (
            "E-commerce Returns",
            [
                (
                    "assistant",
                    "Hello, thank you for calling ShopEasy customer service. What can I do for you today?",
                ),
                (
                    "user",
                    "I received a damaged item and I need to return it for a refund.",
                ),
                (
                    "assistant",
                    "I'm sorry to hear that. I can definitely help with the return. Could you provide your order number?",
                ),
                ("user", "It's order SE-88234."),
                (
                    "assistant",
                    "I found your order. You purchased a ceramic vase, delivered on March 3rd. Can you describe the damage?",
                ),
                (
                    "user",
                    "There's a large crack down the side. It looks like it wasn't packed well.",
                ),
                (
                    "assistant",
                    "I apologize for that. I've initiated a return and full refund. You don't need to ship it back since it's damaged — you can dispose of it or keep it. The refund of $45.99 will appear in 3 to 5 business days. Would you also like a replacement shipped?",
                ),
                ("user", "Yes, a replacement would be great."),
                (
                    "assistant",
                    "Done. A replacement will ship today with expedited delivery at no extra charge. You should receive it within 2 business days. Is there anything else?",
                ),
                ("user", "No, thank you for making this so easy."),
                (
                    "assistant",
                    "My pleasure! Thank you for shopping with ShopEasy. Have a good day!",
                ),
            ],
        ),
        (
            "Angry Customer - Billing Dispute",
            [
                (
                    "assistant",
                    "Thank you for calling Streamline Services. How can I assist you?",
                ),
                (
                    "user",
                    "Yeah, I'm looking at my bill and there's a charge for $89 that I never authorized. This is ridiculous.",
                ),
                (
                    "assistant",
                    "I completely understand your frustration, and I want to get this resolved for you right away. Let me pull up your account. Could you give me your account number or the email on file?",
                ),
                (
                    "user",
                    "It's david.kim@email.com. I've been a customer for three years and I've never had this kind of problem.",
                ),
                (
                    "assistant",
                    "Thank you, David. I can see your account and the $89 charge from March 1st. It appears this was for a premium tier upgrade. Let me check if this was applied in error.",
                ),
                ("user", "I never asked for any upgrade!"),
                (
                    "assistant",
                    "You're absolutely right, David. I can see this was a system error during our recent migration. I sincerely apologize. I'm reversing the charge right now and confirming your plan remains on the standard tier. The $89 will be credited back within 24 hours.",
                ),
                ("user", "Okay... well, thank you for fixing it quickly at least."),
                (
                    "assistant",
                    "I appreciate your patience, David. As a gesture of goodwill, I've also applied a $20 credit to your next bill. Is there anything else I can help with?",
                ),
                ("user", "No, that covers it. Thank you."),
                (
                    "assistant",
                    "Thank you for being a loyal customer, David. Have a great day.",
                ),
            ],
        ),
        (
            "Travel Booking Assistance",
            [
                (
                    "assistant",
                    "Welcome to TravelWise. I'm your booking assistant. Where are you looking to travel?",
                ),
                (
                    "user",
                    "I'm planning a trip to Tokyo for two weeks starting April 10th.",
                ),
                (
                    "assistant",
                    "Tokyo in April is beautiful — you'll catch the cherry blossoms! Are you looking for flights only, or a complete package with hotel?",
                ),
                (
                    "user",
                    "Complete package. Two adults. We'd prefer a hotel near Shinjuku.",
                ),
                (
                    "assistant",
                    "I have several great options. The Park Hyatt Shinjuku is available for those dates at $245 per night, or the Hilton Tokyo at $189 per night. For flights, I found round-trip from LAX for $1,280 per person with ANA, or $1,150 with United with one layover.",
                ),
                (
                    "user",
                    "Let's go with the Hilton and the ANA flights. Direct is worth the extra.",
                ),
                (
                    "assistant",
                    "Excellent choice. That brings your total to $7,850 for two adults: $2,560 for flights and $5,290 for 14 nights at the Hilton. Shall I book this package?",
                ),
                ("user", "Yes, please go ahead and book it."),
                (
                    "assistant",
                    "Your booking is confirmed. Confirmation number is TW-44821. I'll send all the details to your email. Would you like me to add travel insurance for $180?",
                ),
                ("user", "Sure, add the insurance too."),
                (
                    "assistant",
                    "All set! Your total is $8,030 including insurance. Have a wonderful trip to Tokyo!",
                ),
            ],
        ),
    ]

    VAPI_PHONE_NUMBERS = [
        "+1-415-555-0142",
        "+1-212-555-0198",
        "+1-310-555-0167",
        "+1-646-555-0123",
        "+1-408-555-0189",
        "+1-512-555-0134",
        "+1-773-555-0156",
        "+1-202-555-0178",
        "+1-617-555-0145",
        "+1-503-555-0112",
    ]

    VAPI_ENDED_REASONS = [
        "customer-ended-call",
        "assistant-ended-call",
        "silence-timed-out",
        "max-duration-reached",
        "customer-ended-call",
        "assistant-ended-call",
        "customer-ended-call",
        "customer-ended-call",
    ]

    VAPI_ASSISTANT_MODELS = [
        ("gpt-4o", "openai"),
        ("gpt-4o-mini", "openai"),
        ("claude-3-5-sonnet-20241022", "anthropic"),
        ("claude-3-haiku-20240307", "anthropic"),
        ("gemini-1.5-pro", "google"),
    ]

    VAPI_PROJECT_NAMES = [
        "[Seed] Insurance Voice Agent",
        "[Seed] Healthcare Receptionist",
        "[Seed] Banking Support Line",
        "[Seed] Customer Service Hub",
    ]

    def _seed_voice_observability(self, user, org):
        from tracer.models.observability_provider import ObservabilityProvider
        from tracer.models.observation_span import ObservationSpan
        from tracer.models.project import Project
        from tracer.models.project_version import ProjectVersion
        from tracer.models.trace import Trace
        from tracer.models.trace_session import TraceSession

        self.stdout.write("\n--- Seeding Voice Observability (VAPI) ---")

        for proj_name in self.VAPI_PROJECT_NAMES:
            project = Project.objects.create(
                name=proj_name,
                organization=org,
                model_type="GenerativeLLM",
                trace_type="observe",
                source="simulator",
                user=user,
                metadata={"seeded": True},
            )
            self.stdout.write(f"  Project: {proj_name}")

            # Create an observability provider for this project
            ObservabilityProvider.objects.create(
                project=project,
                provider="vapi",
                enabled=True,
                organization=org,
                workspace=None,
            )

            # Create versions
            versions = []
            for v in range(1, random.randint(3, 5)):
                ts = rand_ts(60)
                pv = ProjectVersion.objects.create(
                    project=project,
                    name=f"v{v}",
                    version=f"v{v}",
                    start_time=ts,
                    end_time=ts + timedelta(hours=random.randint(1, 48)),
                    metadata={"seeded": True},
                )
                versions.append(pv)

            # Create sessions
            sessions = []
            for s in range(random.randint(3, 6)):
                session = TraceSession.objects.create(
                    project=project,
                    name=f"Session {s + 1}",
                    bookmarked=random.random() > 0.7,
                )
                sessions.append(session)

            # Create 15-30 voice call traces
            num_calls = random.randint(15, 30)
            for c in range(num_calls):
                conversation = random.choice(self.VAPI_CONVERSATIONS)
                scenario_name, turns = conversation
                model_name, provider_name = random.choice(self.VAPI_ASSISTANT_MODELS)

                call_ts = rand_ts(30)
                duration_secs = random.randint(45, 480)
                end_ts = call_ts + timedelta(seconds=duration_secs)

                # Build eval_attributes in the same format as normalize_vapi_data
                eval_attributes = self._build_vapi_eval_attributes(
                    turns, model_name, provider_name, duration_secs, call_ts
                )

                vapi_call_id = str(uuid.uuid4())
                metadata = {
                    "provider": "vapi",
                    "provider_log_id": vapi_call_id,
                    "seeded": True,
                }

                # Build input/output from conversation
                first_user_msg = next((t[1] for t in turns if t[0] == "user"), "")
                last_assistant_msg = next(
                    (t[1] for t in reversed(turns) if t[0] == "assistant"), ""
                )

                trace = Trace.objects.create(
                    project=project,
                    project_version=random.choice(versions),
                    name=f"Vapi Call Log",
                    input={"messages": [{"role": "user", "content": first_user_msg}]},
                    output={"content": last_assistant_msg},
                    session=(
                        random.choice(sessions)
                        if sessions and random.random() > 0.3
                        else None
                    ),
                    tags=random.sample(
                        ["voice", "vapi", "production", "inbound", "outbound"],
                        k=random.randint(1, 3),
                    ),
                    metadata=metadata,
                )
                Trace.all_objects.filter(id=trace.id).update(created_at=call_ts)

                # Token counts
                prompt_tokens = random.randint(500, 4000)
                completion_tokens = random.randint(200, 2000)
                cost = round(random.uniform(0.01, 0.35), 4)

                span = ObservationSpan.objects.create(
                    id=str(uuid.uuid4())[:16],
                    project=project,
                    project_version=trace.project_version,
                    trace=trace,
                    name="Vapi Call Log",
                    observation_type="conversation",
                    start_time=call_ts,
                    end_time=end_ts,
                    input={},
                    output={},
                    metadata=metadata,
                    provider="vapi",
                    cost=cost,
                    status="ok" if random.random() > 0.1 else "error",
                    eval_attributes=eval_attributes,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                    latency_ms=random.randint(800, 3000),
                    model=model_name,
                )

            self.stdout.write(f"    {num_calls} voice call traces")

        self.stdout.write(self.style.SUCCESS("  Voice observability seeded!"))

    def _build_vapi_eval_attributes(
        self, turns, model_name, provider_name, duration_secs, call_ts
    ):
        """Build eval_attributes dict matching the format produced by normalize_vapi_data."""
        phone = random.choice(self.VAPI_PHONE_NUMBERS)
        ended_reason = random.choice(self.VAPI_ENDED_REASONS)
        vapi_call_id = str(uuid.uuid4())

        prompt_tokens = random.randint(500, 4000)
        completion_tokens = random.randint(200, 2000)

        attrs = {
            "fi_span_kind": "conversation",
            "vapi.call_id": vapi_call_id,
            # LLM info
            "llm.model_name": model_name,
            "llm.provider": provider_name,
            "llm.token_count.prompt": prompt_tokens,
            "llm.token_count.completion": completion_tokens,
            "llm.token_count.total": prompt_tokens + completion_tokens,
            # Call metadata
            "call.total_turns": len(turns),
            "call.duration": duration_secs,
            "call.participant_phone_number": phone,
            "call.status": "ended",
            "call.user_wpm": random.randint(110, 175),
            "call.bot_wpm": random.randint(130, 195),
            "call.talk_ratio": round(random.uniform(0.3, 0.7), 2),
            "ended_reason": ended_reason,
            # IDs
            "squad.id": None,
            "phone_number.id": str(uuid.uuid4()),
            "customer.id": str(uuid.uuid4()),
            # Workflow
            "workflow.id": str(uuid.uuid4()),
            "workflow.name": f"Main Workflow",
            "workflow.background_sound": random.choice(["off", "office", "off"]),
            "workflow.voicemail_detection": None,
            "workflow.voicemail_message": None,
            # Interruption metrics
            "avg_agent_latency_ms": random.randint(600, 2500),
            "user_interruption_count": random.randint(0, 4),
            "user_interruption_rate": round(random.uniform(0, 0.15), 3),
            "ai_interruption_count": random.randint(0, 2),
            "ai_interruption_rate": round(random.uniform(0, 0.08), 3),
            "avg_stop_time_after_interruption_ms": random.randint(200, 800),
            # Cost breakdown
            "cost_breakdown.llm": round(random.uniform(0.005, 0.15), 4),
            "cost_breakdown.stt": round(random.uniform(0.002, 0.05), 4),
            "cost_breakdown.tts": round(random.uniform(0.003, 0.08), 4),
            "cost_breakdown.vapi": round(random.uniform(0.01, 0.05), 4),
            "cost_breakdown.total": round(random.uniform(0.02, 0.35), 4),
            "cost_breakdown.llm_prompt_tokens": prompt_tokens,
            "cost_breakdown.llm_completion_tokens": completion_tokens,
            "cost_breakdown.tts_characters": random.randint(200, 2000),
            "cost_breakdown.transport": round(random.uniform(0.001, 0.02), 4),
        }

        # Conversation transcript (flattened OTEL format)
        elapsed = 0.0
        provider_transcript = []
        for i, (role, content) in enumerate(turns):
            msg_duration = round(random.uniform(1.5, 8.0), 2)
            attrs[f"conversation.transcript.{i}.message.role"] = role
            attrs[f"conversation.transcript.{i}.message.content"] = content
            attrs[f"conversation.transcript.{i}.start_time"] = round(elapsed, 2)
            attrs[f"conversation.transcript.{i}.duration"] = msg_duration
            provider_transcript.append({"role": role, "content": content})
            elapsed += msg_duration + round(random.uniform(0.3, 1.5), 2)

        attrs["provider_transcript"] = provider_transcript

        # Turn latencies (one per assistant turn)
        assistant_turns = [
            i for i, (role, _) in enumerate(turns) if role == "assistant"
        ]
        for idx, _ in enumerate(assistant_turns):
            model_lat = random.randint(200, 1500)
            voice_lat = random.randint(100, 600)
            transcriber_lat = random.randint(50, 400)
            endpointing_lat = random.randint(100, 800)
            turn_lat = model_lat + voice_lat + transcriber_lat

            attrs[f"performance_metrics.turn_latencies.{idx}.model_latency"] = model_lat
            attrs[f"performance_metrics.turn_latencies.{idx}.voice_latency"] = voice_lat
            attrs[f"performance_metrics.turn_latencies.{idx}.transcriber_latency"] = (
                transcriber_lat
            )
            attrs[f"performance_metrics.turn_latencies.{idx}.endpointing_latency"] = (
                endpointing_lat
            )
            attrs[f"performance_metrics.turn_latencies.{idx}.turn_latency"] = turn_lat

        # Average latencies
        if assistant_turns:
            attrs["performance_metrics.model_latency_average"] = random.randint(
                400, 1200
            )
            attrs["performance_metrics.voice_latency_average"] = random.randint(
                150, 500
            )
            attrs["performance_metrics.transcriber_latency_average"] = random.randint(
                100, 350
            )
            attrs["performance_metrics.endpointing_latency_average"] = random.randint(
                150, 600
            )

        return attrs
