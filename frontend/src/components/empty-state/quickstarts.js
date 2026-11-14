/*
  Code quickstarts for first-run screens, taken from the docs
  (docs.futureagi.com) and the product's own tracing setup — keep them in step
  with the SDK. Each is { title, snippets: [{ label, code }], waiting? }.
*/
const KEYS = 'export FI_API_KEY="your-api-key"\nexport FI_SECRET_KEY="your-secret-key"';

export const QUICKSTART_DATASET = {
  title: "Or create it from code",
  snippets: [
    {
      label: "Python",
      code: `pip install futureagi
${KEYS}

from fi.datasets import Dataset
from fi.datasets.types import Cell, Column, DataTypeChoices, Row, SourceChoices

dataset = Dataset.get_dataset_config("support-agent-eval")
dataset = dataset.add_columns(columns=[
    Column(name="user_query", data_type=DataTypeChoices.TEXT, source=SourceChoices.OTHERS),
])
dataset = dataset.add_rows(rows=[
    Row(order=1, cells=[Cell(column_name="user_query", value="Where is my order?")]),
])`,
    },
  ],
};

export const QUICKSTART_EVAL = {
  title: "Or run one from code",
  snippets: [
    {
      label: "Python",
      code: `pip install futureagi

from fi.evals import evaluate

result = evaluate("contains", output="Hello world", keyword="Hello")
print(result.score, result.passed, result.reason)`,
    },
  ],
};

export const QUICKSTART_PROMPT = {
  title: "Then use it from code",
  snippets: [
    {
      label: "Python",
      code: `pip install futureagi
${KEYS}

from fi.prompt import Prompt

prompt = Prompt.get_template_by_name("support-agent", label="Staging")
messages = prompt.compile(company_name="Acme", customer_question="Where is my order?")`,
    },
  ],
};

export const quickstartTracing = (project = "my-project") => ({
  title: "Send your first trace",
  snippets: [
    {
      label: "Python",
      code: `pip install fi-instrumentation-otel
${KEYS}

from fi_instrumentation import register
from fi_instrumentation.fi_types import ProjectType

trace_provider = register(
    project_type=ProjectType.OBSERVE,
    project_name="${project}",
)`,
    },
  ],
  waiting: "Waiting for the first trace…",
});

export const QUICKSTART_GATEWAY = {
  title: "Send your first request",
  snippets: [
    {
      label: "Python",
      code: `import os
from openai import OpenAI

client = OpenAI(
    base_url="https://gateway.futureagi.com/v1",
    api_key=os.environ["AGENTCC_API_KEY"],
)
response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "What is the capital of France?"}],
)`,
    },
  ],
  waiting: "Waiting for the first request…",
};
