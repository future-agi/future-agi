import { describe, expect, it, vi } from "vitest";

import { API_KEY_ENV } from "src/sections/gateway/overview/quickstart";
import { PLACEHOLDER_BASE_URL } from "src/sections/gateway/utils/snippetBaseUrl";
import {
  QUICKSTART_DATASET,
  QUICKSTART_EVAL,
  QUICKSTART_GATEWAY,
  QUICKSTART_PROMPT,
  quickstartTracing,
} from "../quickstarts";

// A first-run screen is seen with an empty workspace and only an API key. Each
// snippet must create what it reads and run top to bottom on the released
// SDK, so its text is pinned exactly: change it only after running it again.
// Each is labelled Python with one Copy button, so the whole snippet is Python:
// the install step is a comment and the keys are set through os.environ.

// This build's API host, which the futureagi SDK snippets print as
// FI_BASE_URL (src/sections/common/commands/apiBase.js). vi.mock is hoisted
// above the imports, so the literal is repeated in the factory.
vi.mock("src/config-global", async (importOriginal) => ({
  ...(await importOriginal()),
  HOST_API: "https://fa.example.test",
}));

const code = (quickstart) => quickstart.snippets[0].code;

const KEYS = `# Replace with your API key and secret key
os.environ["FI_API_KEY"] = "your-api-key"
os.environ["FI_SECRET_KEY"] = "your-secret-key"`;

const DATASET = `# pip install futureagi
# Save this as quickstart.py, then run: python quickstart.py
import os

# Replace with your API key and secret key
os.environ["FI_API_KEY"] = "your-api-key"
os.environ["FI_SECRET_KEY"] = "your-secret-key"
os.environ["FI_BASE_URL"] = "https://fa.example.test"  # This instance's API (tracing uses its collector address instead)

# A pydantic "shadows an attribute" warning on import is harmless
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
# Prints "my-first-dataset has 2 rows". Reload this page to see it.`;

const PROMPT = `# pip install futureagi
# Save this as quickstart.py, then run: python quickstart.py
import os

# Replace with your API key and secret key
os.environ["FI_API_KEY"] = "your-api-key"
os.environ["FI_SECRET_KEY"] = "your-secret-key"
os.environ["FI_BASE_URL"] = "https://fa.example.test"  # This instance's API (tracing uses its collector address instead)

# A pydantic "shadows an attribute" warning on import is harmless
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
# Prints both messages with the values filled in. Find the prompt under All Prompts.`;

const tracing = (project) => `# pip install fi-instrumentation-otel
# Save this as quickstart.py, then run: python quickstart.py
import os

# Replace with your API key and secret key
os.environ["FI_API_KEY"] = "your-api-key"
os.environ["FI_SECRET_KEY"] = "your-secret-key"
# Self-hosted? Also set os.environ["FI_BASE_URL"] to your collector, for example "http://localhost:4318", or traces and both keys go to Future AGI cloud

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

# Reload this page to see the trace under ${project}.`;

const EVAL = `# Needs Python 3.11 or newer
# pip install "futureagi[evals]>=0.8.0"
# Save this as quickstart.py, then run: python quickstart.py
from fi.evals import evaluate

# Runs on your machine, so no API key is needed
result = evaluate("contains", output="Hello world", keyword="Hello")
print(result.score, result.passed, result.reason)
# Prints: 1.0 True Keyword 'Hello' found`;

const GATEWAY = `# pip install openai
# Save this as quickstart.py, then run: python quickstart.py
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
print(response.choices[0].message.content)`;

// Calls that read a dataset or prompt by name, which an empty workspace has none of.
const FETCH_BY_NAME =
  /get_dataset_config|download_dataset|add_dataset_(rows|columns)|delete_dataset|get_template_by_name|list_names|get-by-name/;

describe("first-run quickstarts", () => {
  it("keep the { title, snippets: [{ label, code }], waiting? } shape", () => {
    const all = [
      QUICKSTART_DATASET,
      QUICKSTART_PROMPT,
      quickstartTracing(),
      QUICKSTART_EVAL,
      QUICKSTART_GATEWAY,
    ];
    for (const quickstart of all) {
      expect(Object.keys(quickstart).sort()).toEqual(
        quickstart.waiting === undefined
          ? ["snippets", "title"]
          : ["snippets", "title", "waiting"],
      );
      expect(typeof quickstart.title).toBe("string");
      expect(quickstart.snippets).toHaveLength(1);
      expect(Object.keys(quickstart.snippets[0]).sort()).toEqual([
        "code",
        "label",
      ]);
      expect(quickstart.snippets[0].label).toBe("Python");
    }
  });

  it("put the keys block, with its change-me comment, in every Future AGI snippet", () => {
    for (const quickstart of [
      QUICKSTART_DATASET,
      QUICKSTART_PROMPT,
      quickstartTracing(),
    ]) {
      expect(code(quickstart)).toContain(`\n${KEYS}\n`);
    }
  });

  describe("dataset", () => {
    it("is pinned exactly", () => {
      expect(QUICKSTART_DATASET.title).toBe("Or create it from code");
      expect(code(QUICKSTART_DATASET)).toBe(DATASET);
      expect(QUICKSTART_DATASET.waiting).toBeUndefined();
    });

    it("creates the dataset, its columns and rows before reading it back", () => {
      const text = code(QUICKSTART_DATASET);
      expect(text).not.toMatch(FETCH_BY_NAME);
      const create = text.indexOf("client.datasets.create(");
      const columns = text.indexOf("client.datasets.columns.add(");
      const rows = text.indexOf("client.datasets.rows.add(");
      const read = text.indexOf("client.datasets.get(");
      expect(create).toBeGreaterThan(-1);
      expect(create).toBeLessThan(columns);
      expect(columns).toBeLessThan(rows);
      expect(rows).toBeLessThan(read);
    });

    it("sends cells as { column_name, value } only, the add_rows contract", () => {
      // Dataset.add_rows (Row/Cell objects) sends extra cell fields that the
      // add_rows API rejects, so the snippet must not use it.
      const text = code(QUICKSTART_DATASET);
      expect(text).not.toMatch(/\bRow\(|\bCell\(|\.add_rows\(/);
      const cells = text.match(/\{"column_name": "[^"]+", "value": "[^"]+"\}/g);
      expect(cells).toHaveLength(4);
    });
  });

  describe("prompt", () => {
    it("is pinned exactly", () => {
      expect(QUICKSTART_PROMPT.title).toBe("Or create one from code");
      expect(code(QUICKSTART_PROMPT)).toBe(PROMPT);
      expect(QUICKSTART_PROMPT.waiting).toBeUndefined();
    });

    it("creates the prompt before it compiles it and never fetches one by name", () => {
      const text = code(QUICKSTART_PROMPT);
      expect(text).not.toMatch(FETCH_BY_NAME);
      expect(text).not.toMatch(/label=/);
      const build = text.indexOf("Prompt(template=PromptTemplate(");
      const create = text.indexOf("prompt.create()");
      const compile = text.indexOf("prompt.compile(");
      expect(build).toBeGreaterThan(-1);
      expect(build).toBeLessThan(create);
      expect(create).toBeLessThan(compile);
    });
  });

  describe("tracing", () => {
    it("is pinned exactly, for the default and a named project", () => {
      expect(quickstartTracing().title).toBe("Send your first trace");
      expect(code(quickstartTracing())).toBe(tracing("my-project"));
      expect(code(quickstartTracing("checkout-bot"))).toBe(
        tracing("checkout-bot"),
      );
      expect(quickstartTracing().waiting).toBe("Waiting for the first trace…");
    });

    it("emits one span and flushes it before the script exits", () => {
      const text = code(quickstartTracing());
      const register = text.indexOf("tracer_provider = register(");
      const tracer = text.indexOf("tracer_provider.get_tracer(__name__)");
      const span = text.indexOf("with tracer.start_as_current_span(");
      const flush = text.indexOf("finally:\n    tracer_provider.shutdown()");
      expect(register).toBeGreaterThan(-1);
      expect(register).toBeLessThan(tracer);
      expect(tracer).toBeLessThan(span);
      expect(span).toBeLessThan(flush);
      expect(text.match(/start_as_current_span\(/g)).toHaveLength(1);
    });
  });

  describe("eval (not on a screen)", () => {
    it("is pinned exactly and asks for the SDK version that has fi.evals", () => {
      expect(QUICKSTART_EVAL.title).toBe("Or run one from code");
      expect(code(QUICKSTART_EVAL)).toBe(EVAL);
      // futureagi 0.7.0 has no fi.evals; it ships in 0.8.0 under [evals].
      expect(code(QUICKSTART_EVAL)).toContain(
        'pip install "futureagi[evals]>=0.8.0"',
      );
    });
  });

  describe("gateway (not on a screen)", () => {
    it("is pinned exactly", () => {
      expect(QUICKSTART_GATEWAY.title).toBe("Send your first request");
      expect(code(QUICKSTART_GATEWAY)).toBe(GATEWAY);
      expect(QUICKSTART_GATEWAY.waiting).toBe("Waiting for the first request…");
    });

    it("uses the gateway quickstart's env var and base URL placeholder", () => {
      expect(API_KEY_ENV).toBe("AGENTCC_API_KEY");
      const text = code(QUICKSTART_GATEWAY);
      expect(text).toContain(`os.environ["${API_KEY_ENV}"] = "`);
      expect(text).toContain(`base_url="${PLACEHOLDER_BASE_URL}"`);
      expect(text).not.toContain("gateway.futureagi.com");
    });
  });
});
