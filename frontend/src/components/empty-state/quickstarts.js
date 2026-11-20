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

// Off Future AGI Cloud the SDK must be pointed at this install, or the spans
// go to Cloud: FI_BASE_URL is its API and FI_TRACES_URL its trace collector,
// as useSdkUrls gives them (null on Cloud and while they load, which leaves
// the keys only).
const tracingEnvLines = ({ apiUrl, tracesUrl } = {}) =>
  [
    KEYS,
    apiUrl && `export FI_BASE_URL="${apiUrl}"`,
    tracesUrl && `export FI_TRACES_URL="${tracesUrl}"`,
  ]
    .filter(Boolean)
    .join("\n");

export const quickstartTracing = (project = "my-project", sdkUrls = {}) => ({
  title: "Send your first trace",
  snippets: [
    {
      label: "Python",
      code: `pip install -U "fi-instrumentation-otel>=1.5.0"
${tracingEnvLines(sdkUrls)}

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
