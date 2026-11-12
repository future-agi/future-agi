/*
  Code quickstarts for first-run screens. Each is { title, snippets: [{ label,
  code }], waiting? }.

  A first-run screen is seen by someone with an empty workspace and an API key,
  so every snippet creates what it reads and never fetches something by name
  first. Each one runs top to bottom on the released packages it installs:
  futureagi 0.7.0 and fi-instrumentation-otel 1.3.0, except QUICKSTART_EVAL,
  which needs futureagi 0.8.0 (see below). Keep them in step with the SDK and
  with __tests__/quickstarts.test.js, which pins the exact text.

  A snippet has one Copy button, so what it copies must run as pasted in its
  label's language. In a Python snippet the install command is a comment and
  the keys are set through os.environ before the SDK is imported, never as
  shell lines.
*/
import { FI_BASE_URL } from "src/sections/common/commands/apiBase";

const KEYS = `# Replace with your API key and secret key
os.environ["FI_API_KEY"] = "your-api-key"
os.environ["FI_SECRET_KEY"] = "your-secret-key"`;

// The futureagi SDK snippets (datasets, prompts) also point the SDK at this
// instance's API, the same base every other printed snippet uses, so the code
// works on a self-hosted instance as copied. It is set with the keys, before
// the SDK is imported. Tracing keeps KEYS: its FI_BASE_URL is the trace
// collector, not this API. The README sets FI_BASE_URL to the collector, so
// the line says which one it is.
const SDK_KEYS = FI_BASE_URL
  ? `${KEYS}\nos.environ["FI_BASE_URL"] = "${FI_BASE_URL}"  # This instance's API (tracing uses its collector address instead)`
  : KEYS;

const RUN = "# Save this as quickstart.py, then run: python quickstart.py";

// futureagi 0.7.0 prints this pydantic UserWarning whenever fi is imported.
const IMPORT_NOTE =
  '# A pydantic "shadows an attribute" warning on import is harmless';

// Rows go through the generated client: its cells are { column_name, value },
// the shape the add_rows API accepts. Dataset.add_rows in futureagi 0.7.0
// sends extra cell fields, which the API rejects.
export const QUICKSTART_DATASET = {
  title: "Or create it from code",
  snippets: [
    {
      label: "Python",
      code: `# pip install futureagi
${RUN}
import os

${SDK_KEYS}

${IMPORT_NOTE}
from fi import Client

client = Client()

# Dataset names are unique: use a new name to run this again
dataset = client.datasets.create(new_dataset_name="my-first-dataset")
client.datasets.columns.add(dataset.dataset_id, new_columns_data=[
    {"name": "question", "data_type": "text"},
    {"name": "expected_answer", "data_type": "text"},
])
# Adding rows is in beta, so Python prints a BetaWarning here
client.datasets.rows.add(dataset.dataset_id, rows=[
    {"cells": [
        {"column_name": "question", "value": "Where is my order?"},
        {"column_name": "expected_answer", "value": "Here is your tracking link."},
    ]},
    {"cells": [
        {"column_name": "question", "value": "Can I change my address?"},
        {"column_name": "expected_answer", "value": "Yes, until the order ships."},
    ]},
])

table = client.datasets.get(dataset.dataset_id)
print(dataset.dataset_name, "has", len(table.table), "rows")
# Prints "my-first-dataset has 2 rows". Reload this page to see it.`,
    },
  ],
};

// fi.evals ships in futureagi 0.8.0 under the [evals] extra (Python 3.11+);
// futureagi 0.7.0 has no fi.evals. Not shown on a screen yet.
export const QUICKSTART_EVAL = {
  title: "Or run one from code",
  snippets: [
    {
      label: "Python",
      code: `# Needs Python 3.11 or newer
# pip install "futureagi[evals]>=0.8.0"
${RUN}
from fi.evals import evaluate

# Runs on your machine, so no API key is needed
result = evaluate("contains", output="Hello world", keyword="Hello")
print(result.score, result.passed, result.reason)
# Prints: 1.0 True Keyword 'Hello' found`,
    },
  ],
};

// Shown on the empty prompts screen, so it creates the prompt it then uses.
export const QUICKSTART_PROMPT = {
  title: "Or create one from code",
  snippets: [
    {
      label: "Python",
      code: `# pip install futureagi
${RUN}
import os

${SDK_KEYS}

${IMPORT_NOTE}
from fi.prompt import Prompt, PromptTemplate, SystemMessage, UserMessage

# A new name prints "Template not found in the backend..." here; create() then makes it
prompt = Prompt(template=PromptTemplate(
    name="my-first-prompt",
    messages=[
        SystemMessage(content="You are a support agent for {{company_name}}."),
        UserMessage(content="{{customer_question}}"),
    ],
))
prompt.create()

messages = prompt.compile(company_name="Acme", customer_question="Where is my order?")
print(messages)
# Prints both messages with the values filled in. Find the prompt under All Prompts.`,
    },
  ],
};

// register() alone sends nothing: the span block is what ends the wait, and
// shutdown() sends it before the script exits.
//
// Without a collector address the tracer sends the span and both keys to
// Future AGI cloud (README.md, "Self-hosted tracing"). This build's config has
// no collector address to print, so a self-host comment beside the keys names
// the variable and the README's example address; the code still runs as pasted
// on Future AGI cloud.
const TRACING_SELF_HOST =
  '# Self-hosted? Also set os.environ["FI_BASE_URL"] to your collector, for example "http://localhost:4318", or traces and both keys go to Future AGI cloud';

export const quickstartTracing = (project = "my-project") => ({
  title: "Send your first trace",
  snippets: [
    {
      label: "Python",
      code: `# pip install fi-instrumentation-otel
${RUN}
import os

${KEYS}
${TRACING_SELF_HOST}

from fi_instrumentation import FITracer, register
from fi_instrumentation.fi_types import ProjectType

tracer_provider = register(
    project_type=ProjectType.OBSERVE,
    project_name="${project}",
)
tracer = FITracer(tracer_provider.get_tracer(__name__))

try:
    with tracer.start_as_current_span("my-first-span") as span:
        # Replace this with your app's work
        span.set_attribute("greeting", "hello")
finally:
    tracer_provider.shutdown()  # Sends the span before the script exits

# Reload this page to see the trace under ${project}.`,
    },
  ],
  waiting: "Waiting for the first trace…",
});

// Matches the gateway's own quickstart: the AGENTCC_API_KEY env var and, with
// no deployment URL to hand, its YOUR_GATEWAY_URL placeholder. Not shown on a
// screen yet.
export const QUICKSTART_GATEWAY = {
  title: "Send your first request",
  snippets: [
    {
      label: "Python",
      code: `# pip install openai
${RUN}
import os
from openai import OpenAI

# Replace with your gateway API key, and YOUR_GATEWAY_URL with your gateway's address
os.environ["AGENTCC_API_KEY"] = "YOUR_GATEWAY_API_KEY"

client = OpenAI(
    base_url="https://YOUR_GATEWAY_URL/v1",
    api_key=os.environ["AGENTCC_API_KEY"],
)
response = client.chat.completions.create(
    model="gpt-4o-mini",  # a model your gateway routes
    messages=[{"role": "user", "content": "What is the capital of France?"}],
)
print(response.choices[0].message.content)`,
    },
  ],
  waiting: "Waiting for the first request…",
};
