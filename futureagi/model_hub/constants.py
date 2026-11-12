import json

from model_hub.models.choices import AnnotationTypeChoices
from tfc.settings.settings import BASE_URL

ANNOTATION_LABEL_VALUE_KEYS = {
    AnnotationTypeChoices.TEXT.value: "text",
    AnnotationTypeChoices.NUMERIC.value: "value",
    AnnotationTypeChoices.STAR.value: "rating",
    AnnotationTypeChoices.THUMBS_UP_DOWN.value: "value",
    AnnotationTypeChoices.CATEGORICAL.value: "selected",
}

# ---------------------------------------------------------------------------
# Dataset validation constants
# ---------------------------------------------------------------------------
MAX_DATASET_NAME_LENGTH = 2000
MAX_MANUAL_ROWS = 100
MAX_MANUAL_COLUMNS = 100
MAX_EMPTY_DATASET_ROWS = 10
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_FILE_SIZE_MB = 25
MAX_BATCH_DELETE_SIZE = 50
MAX_PAGE_SIZE = 100
ALLOWED_FILE_EXTENSIONS = {".csv", ".xls", ".xlsx", ".json", ".jsonl"}
ALLOWED_DATASET_SOURCES = ["demo", "build", "observe"]
SDK_API_KEY_PLACEHOLDER = "YOUR_API_KEY"
SDK_SECRET_KEY_PLACEHOLDER = "YOUR_SECRET_KEY"

PYTHON_CODE = """# pip install futureagi

import os
from fi.datasets import Dataset
from fi.datasets.types import (
    Cell,
    Column,
    DatasetConfig,
    DataTypeChoices,
    ModelTypes,
    Row,
    SourceChoices,
)

# Set environment variables
os.environ["FI_API_KEY"] = "{}"
os.environ["FI_SECRET_KEY"] = "{}"
os.environ["FI_BASE_URL"] = "{}"

# Get existing dataset
config = DatasetConfig(name={}, model_type= ModelTypes.GENERATIVE_LLM)
dataset = Dataset(dataset_config=config)
dataset = Dataset.get_dataset_config({})

# Define columns
columns = [
    Column(
        name="user_query",
        data_type=DataTypeChoices.TEXT,
        source=SourceChoices.OTHERS
    ),
    Column(
        name="response_quality",
        data_type=DataTypeChoices.INTEGER,
        source=SourceChoices.OTHERS
    ),
    Column(
        name="is_helpful",
        data_type=DataTypeChoices.BOOLEAN,
        source=SourceChoices.OTHERS
    )
]

# Define rows
rows = [
    Row(
        order=1,
        cells=[
            Cell(column_name="user_query", value="What is machine learning?"),
            Cell(column_name="response_quality", value=8),
            Cell(column_name="is_helpful", value=True)
        ]
    ),
    Row(
        order=2,
        cells=[
            Cell(column_name="user_query", value="Explain quantum computing"),
            Cell(column_name="response_quality", value=9),
            Cell(column_name="is_helpful", value=True)
        ]
    )
]


"""

PYTHON_ADD_COLS = (
    PYTHON_CODE
    + """
try:
    # Add columns and rows to dataset
    dataset = dataset.add_columns(columns=columns)
    dataset = dataset.add_rows(rows=rows)
    print("✓ Data added successfully")

except Exception as e:
    print(f"Failed to add data: {{e}}")

"""
)

PYTHON_ADD_ROWS = (
    PYTHON_CODE
    + """
try:
    # Add columns and rows to dataset
    dataset = dataset.add_rows(rows=rows)
    print("✓ Data added successfully")

except Exception as e:
    print(f"Failed to add data: {{e}}")
"""
)


def _string_literal(value):
    """A double-quoted literal that Python and JavaScript both read as ``value``.

    Quotes, backslashes and newlines in dataset and column names are escaped, so
    any name gives an example that parses.
    """
    return json.dumps(value, ensure_ascii=False)


def get_python_add_columns_code(dataset_name, api_key, secret_key):
    """The Python example that adds sample columns and rows to a dataset."""
    name = _string_literal(dataset_name)
    return PYTHON_ADD_COLS.format(api_key, secret_key, BASE_URL, name, name)


def get_curl_ts_code(dataset_id, api_key, secret_key, dataset_name):
    CURL_ADD_COLUMN_REQUEST = f"""curl --request POST \
    --url {BASE_URL}/model-hub/develops/{dataset_id}/add_columns/ \
    --header 'X-Api-Key: {api_key}' \
    --header 'X-Secret-Key: {secret_key}' \
    --header 'content-type: application/json' \
    --data '{{
    "new_columns_data": [
        {{
          "name": "user_query",
          "data_type": "text"
        }},
        {{
          "name": "response_quality",
          "data_type": "integer"
        }},
        {{
          "name": "is_helpful",
          "data_type": "boolean"
        }}
      ]
  }}'"""

    CURL_ADD_ROWS_REQUEST = f"""curl --request POST \
    --url {BASE_URL}/model-hub/develops/{dataset_id}/add_rows/ \
    --header 'content-type: application/json' \
    --header 'X-Api-Key: {api_key}' \
    --header 'X-Secret-Key: {secret_key}' \
    --data '{{
    "rows":[
      {{
        "order": 1,
        "cells":[
          {{
            "column_name": "user_query",
            "value": "What is machine learning?"
          }},
          {{
            "column_name": "response_quality",
            "value": 8
          }},
          {{
            "column_name": "is_helpful",
            "value": true
          }}
        ]
      }},
      {{
        "order": 2,
        "cells":[
          {{
            "column_name": "user_query",
            "value": "Explain quantum computing"
          }},
          {{
            "column_name": "response_quality",
            "value": 9
          }},
          {{
            "column_name": "is_helpful",
            "value": true
          }}
        ]
      }}
    ]
  }}'"""

    TYPESCRIPT_ADD_COLUMNS = f"""import {{ Dataset, DataTypeChoices, createRow, createCell }} from "@future-agi/sdk";

process.env["FI_API_KEY"] = "{api_key}";
process.env["FI_SECRET_KEY"] = "{secret_key}";
process.env["FI_BASE_URL"] = "{BASE_URL}";

async function main() {{
  try {{
    const dsName = {_string_literal(dataset_name)};

    // 1) Open the dataset (fetch if it exists, create if not)
    const dataset = await Dataset.open(dsName);

    // 2) Define columns
    const columns = [
      {{ name: "user_query", data_type: DataTypeChoices.TEXT }},
      {{ name: "response_quality", data_type: DataTypeChoices.INTEGER }},
      {{ name: "is_helpful", data_type: DataTypeChoices.BOOLEAN }},
    ];

    // 3) Define rows
    const rows = [
      createRow({{
        cells: [
          createCell({{ column_name: "user_query", value: "What is machine learning?" }}),
          createCell({{ column_name: "response_quality", value: 8 }}),
          createCell({{ column_name: "is_helpful", value: true }}),
        ],
      }}),
      createRow({{
        cells: [
          createCell({{ column_name: "user_query", value: "Explain quantum computing" }}),
          createCell({{ column_name: "response_quality", value: 9 }}),
          createCell({{ column_name: "is_helpful", value: true }}),
        ],
      }}),
    ];

    // 4) Add columns and rows
    await dataset.addColumns(columns);
    await dataset.addRows(rows);
    console.log("\u2713 Data added successfully");
  }} catch (err) {{
    console.error("Failed to add data:", err);
  }}
}}

main();
  """

    TYPESCRIPT_ADD_ROWS = f"""
 import {{ Dataset, DataTypeChoices, createRow, createCell }} from "@future-agi/sdk";

process.env["FI_API_KEY"] = "{api_key}";
process.env["FI_SECRET_KEY"] = "{secret_key}";
process.env["FI_BASE_URL"] = "{BASE_URL}";

async function main() {{
  try {{
    const dsName = {_string_literal(dataset_name)};

    // 1) Open the dataset (fetch if it exists, create if not)
    const dataset = await Dataset.open(dsName);

    // 3) Define rows
    const rows = [
      createRow({{
        cells: [
          createCell({{ column_name: "user_query", value: "What is machine learning?" }}),
          createCell({{ column_name: "response_quality", value: 8 }}),
          createCell({{ column_name: "is_helpful", value: true }}),
        ],
      }}),
      createRow({{
        cells: [
          createCell({{ column_name: "user_query", value: "Explain quantum computing" }}),
          createCell({{ column_name: "response_quality", value: 9 }}),
          createCell({{ column_name: "is_helpful", value: true }}),
        ],
      }}),
    ];
    await dataset.addRows(rows);
    console.log("\u2713 Data added successfully");
  }} catch (err) {{
    console.error("Failed to add data:", err);
  }}
}}

main();
"""
    return (
        CURL_ADD_COLUMN_REQUEST,
        CURL_ADD_ROWS_REQUEST,
        TYPESCRIPT_ADD_COLUMNS,
        TYPESCRIPT_ADD_ROWS,
    )

    # Generate Python code


# The value the add-rows examples show for each column type. Types without one
# (image, audio, document, persona, ...) get an empty value and a note to add
# it.
ADD_ROW_SAMPLE_VALUES = {
    "text": "sample text",
    "integer": 1,
    "float": 0.5,
    "boolean": True,
    "datetime": "2026-01-01T00:00:00Z",
    "json": {"key": "value"},
    "array": ["first", "second"],
}

NO_COLUMNS_NOTE = (
    "This dataset has no columns yet, so this example adds three sample columns "
    "and two rows."
)


def _python_value(value):
    if isinstance(value, bool):
        return "True" if value else "False"
    return json.dumps(value, ensure_ascii=False)


def get_add_rows_code(dataset_id, dataset_name, columns, api_key, secret_key):
    """The add-rows examples (Python, TypeScript, cURL) for an existing dataset.

    ``columns`` are the dataset's data columns as ``(name, data_type)`` pairs, in
    the order the dataset shows them. Each example adds one row with a cell for
    every column. A dataset without data columns gets the add-columns examples,
    which create sample columns first.
    """
    if not columns:
        curl_columns, curl_rows, typescript_columns, _ = get_curl_ts_code(
            dataset_id=dataset_id,
            api_key=api_key,
            secret_key=secret_key,
            dataset_name=dataset_name,
        )
        python = get_python_add_columns_code(dataset_name, api_key, secret_key)
        return (
            f"# {NO_COLUMNS_NOTE}\n{python}",
            f"// {NO_COLUMNS_NOTE}\n{typescript_columns}",
            f"# {NO_COLUMNS_NOTE}\n{curl_columns}\n\n{curl_rows}",
        )

    python_cells, typescript_cells, payload_cells = [], [], []
    empty_types = set()
    for name, data_type in columns:
        if data_type in ADD_ROW_SAMPLE_VALUES:
            value, note = ADD_ROW_SAMPLE_VALUES[data_type], ""
        else:
            value, note = "", f"{data_type} column: add its value"
            empty_types.add(str(data_type))
        python_cells.append(
            f"            Cell(column_name={_string_literal(name)}, "
            f"value={_python_value(value)}),"
            + (f"  # {note}" if note else "")
        )
        typescript_cells.append(
            f"          createCell({{ column_name: {_string_literal(name)}, "
            f"value: {json.dumps(value, ensure_ascii=False)} }}),"
            + (f"  // {note}" if note else "")
        )
        payload_cells.append({"column_name": name, "value": value})
    python_rows = "\n".join(python_cells)
    typescript_rows = "\n".join(typescript_cells)
    name = _string_literal(dataset_name)

    python = f"""# pip install futureagi

import os
from fi.datasets import Dataset
from fi.datasets.types import Cell, Row

# Set environment variables
os.environ["FI_API_KEY"] = "{api_key}"
os.environ["FI_SECRET_KEY"] = "{secret_key}"
os.environ["FI_BASE_URL"] = "{BASE_URL}"

# Get the existing dataset
dataset = Dataset.get_dataset_config({name})

# One row with a value for each of the dataset's columns
rows = [
    Row(
        cells=[
{python_rows}
        ]
    )
]

try:
    dataset = dataset.add_rows(rows=rows)
    print("✓ Data added successfully")
except Exception as e:
    print(f"Failed to add data: {{e}}")
"""

    typescript = f"""import {{ Dataset, createRow, createCell }} from "@future-agi/sdk";

process.env["FI_API_KEY"] = "{api_key}";
process.env["FI_SECRET_KEY"] = "{secret_key}";
process.env["FI_BASE_URL"] = "{BASE_URL}";

async function main() {{
  try {{
    // Open the existing dataset; fail rather than create one if it is not found
    const dataset = await Dataset.open({name}, {{ createIfMissing: false }});

    // One row with a value for each of the dataset's columns
    const rows = [
      createRow({{
        cells: [
{typescript_rows}
        ],
      }}),
    ];

    await dataset.addRows(rows);
    console.log("✓ Data added successfully");
  }} catch (err) {{
    console.error("Failed to add data:", err);
  }}
}}

main();
"""

    # The body is single-quoted for the shell, so a quote in a name is closed,
    # escaped and reopened.
    payload = json.dumps({"rows": [{"cells": payload_cells}]}, indent=2)
    payload = payload.replace("'", "'\\''")
    curl_lines = []
    if empty_types:
        curl_lines.append(
            f"# Cells of {', '.join(sorted(empty_types))} columns are left empty: "
            "add their values."
        )
    curl_lines.append(
        "curl --request POST \\\n"
        f"    --url {BASE_URL}/model-hub/develops/{dataset_id}/add_rows/ \\\n"
        "    --header 'content-type: application/json' \\\n"
        f"    --header 'X-Api-Key: {api_key}' \\\n"
        f"    --header 'X-Secret-Key: {secret_key}' \\\n"
        f"    --data '{payload}'"
    )
    return python, typescript, "\n".join(curl_lines)


PROMPT_PYTHON_CODE = """
import requests

url = "{api_url}"
headers = {{
    "X-Api-Key": "YOUR_API_KEY",
    "X-Secret-Key": "YOUR_SECRET_KEY",
    "Content-Type": "application/json"
}}

data = {{
    "name": {name},
    "version": {version},
    "prompt_config": {prompt_config},
    "variable_names": {variable_names},
    "evaluation_configs": {evaluation_configs},
    "is_run": {is_run}
}}

response = requests.post(url, headers=headers, json=data)
print(response.json())
"""

# Generate TypeScript code
PROMPT_TYPESCRIPT_CODE = """
const url = "{api_url}";
const headers = {{
    "X-Api-Key": "YOUR_API_KEY",
    "X-Secret-Key": "YOUR_SECRET_KEY",
    "Content-Type": "application/json"
}};

const data = {{
    "name": {name},
    "version": {version},
    "prompt_config": {prompt_config},
    "variable_names": {variable_names},
    "evaluation_configs": {evaluation_configs},
    "is_run": {is_run}
}};

fetch(url, {{
    method: 'POST',
    headers: headers,
    body: JSON.stringify(data)
}})
.then(response => response.json())
.then(data => console.log(data))
.catch(error => console.error('Error:', error));
"""

# Generate cURL code
PROMPT_CURL_CODE = """
curl --location '{api_url}' \\
--header 'X-Api-Key: <YOUR_API_KEY>' \\
--header 'X-Secret-Key: <YOUR_SECRET_KEY>' \\
--header 'Content-Type: application/json' \\
--data '{{
    "name": {name},
    "version": {version},
    "prompt_config": {prompt_config},
    "variable_names": {variable_names},
    "evaluation_configs": {evaluation_configs},
    "is_run": {is_run}
}}'
"""

# Add new constants for additional languages
PROMPT_LANGCHAIN_CODE = """
from langchain.chat_models import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage

# Initialize the chat model
chat = ChatOpenAI(
    model="{model}",
    temperature={temperature}
)

# Prepare the data
data = {{
    "name": {name},
    "version": {version},
    "prompt_config": {prompt_config},
    "variable_names": {variable_names},
    "evaluation_configs": {evaluation_configs},
    "is_run": {is_run}
}}

# Create messages from prompt config
messages = []
for msg in data["prompt_config"][0]["messages"]:
    if msg["role"] == "system":
        messages.append(SystemMessage(content=msg["content"][0]["text"]))
    else:
        messages.append(HumanMessage(content=msg["content"][0]["text"]))

# Get response
response = chat(messages)
print(response.content)
"""

PROMPT_NODEJS_CODE = """
const axios = require('axios');

async function runPrompt() {{
    const url = "{api_url}";
    const data = {{
        name: {name},
        version: {version},
        prompt_config: {prompt_config},
        variable_names: {variable_names},
        evaluation_configs: {evaluation_configs},
        is_run: {is_run}
    }};

    try {{
        const response = await axios.post(url, data, {{
            headers: {{
                'X-Api-Key': 'YOUR_API_KEY',
                'X-Secret-Key': 'YOUR_SECRET_KEY',
                'Content-Type': 'application/json'
            }}
        }});
        console.log(response.data);
    }} catch (error) {{
        console.error('Error:', error);
    }}
}}

runPrompt();
"""

PROMPT_GO_CODE = """
package main

import (
    "bytes"
    "encoding/json"
    "fmt"
    "io/ioutil"
    "net/http"
)

func main() {{
    url := "{api_url}"

    data := map[string]interface{{}}{{
        "name": {name},
        "version": {version},
        "prompt_config": {prompt_config},
        "variable_names": {variable_names},
        "evaluation_configs": {evaluation_configs},
        "is_run": {is_run},
    }}

    jsonData, err := json.Marshal(data)
    if err != nil {{
        fmt.Printf("Error marshaling JSON: %v\\n", err)
        return
    }}

    req, err := http.NewRequest("POST", url, bytes.NewBuffer(jsonData))
    if err != nil {{
        fmt.Printf("Error creating request: %v\\n", err)
        return
    }}

    req.Header.Set("X-Api-Key", "YOUR_API_KEY")
    req.Header.Set("X-Secret-Key", "YOUR_SECRET_KEY")
    req.Header.Set("Content-Type", "application/json")

    client := &http.Client{{}}
    resp, err := client.Do(req)
    if err != nil {{
        fmt.Printf("Error making request: %v\\n", err)
        return
    }}
    defer resp.Body.Close()

    body, err := ioutil.ReadAll(resp.Body)
    if err != nil {{
        fmt.Printf("Error reading response: %v\\n", err)
        return
    }}

    fmt.Println(string(body))
}}
"""

CREATE_KB_SDK_CODE = """
# pip install futureagi
# export FI_API_KEY="{}"
# export FI_SECRET_KEY="{}"
from fi.kb.client import KnowledgeBase
# To create a knowledge base from scratch
client = KnowledgeBase(
    fi_api_key="{}",
    fi_secret_key="{}",
)
kb_client = client.create_kb(name={},file_paths=['PATH_TO_FILE_1', 'PATH_TO_FILE_2',])
"""

UPDATE_KB_SDK_CODE = """
# pip install futureagi
# export FI_API_KEY="{}"
# export FI_SECRET_KEY="{}"
from fi.kb.client import KnowledgeBase
# To add files to an existing knowledge base
client = KnowledgeBase(
    fi_api_key="{}",
    fi_secret_key="{}",
)
updated_kb = client.update_kb(kb_name={}, file_paths=['PATH_TO_FILE_3', 'PATH_TO_FILE_4',])
"""

MAX_KB_SIZE = 1024 * 1024 * 1024

MAX_KB_SIZE = 1024 * 1024 * 1024  # 1GB


EVAL_PLAYGROUND_PYTHON_CODE = """#pip install futureagi
#pip install ai-evaluation

from fi.evals import Evaluator

evaluator = Evaluator(fi_api_key="{}", fi_secret_key="{}")

result = evaluator.evaluate(
\teval_templates="{}",
\tinputs={},
\t{}
)

print(result.eval_results[0].output)
print(result.eval_results[0].reason)"""


EVAL_PLAYGROUND_CURL_CODE = """curl '{}/model-hub/eval-playground/' \\
  -H 'X-Api-Key: {}' \\
  -H 'X-Secret-Key: {}' \\
  -H 'Accept: application/json, text/plain, */*' \\
  -H 'Content-Type: application/json' \\
  --data-raw '{}'"""

EVAL_PLAYGROUND_JS_CODE = """async function runEval() {{
  const response = await fetch('{}/model-hub/eval-playground/', {{
    method: 'POST',
    headers: {{
      'X-Api-Key': '{}',
      'X-Secret-Key': '{}',
      'Accept': 'application/json, text/plain, */ *',
      'Content-Type': 'application/json'
    }},
    body: '{}'
  }});

  const data = await response.json();
  if (!response.ok) throw new Error(data.message);
  console.log(data.result.output);
  console.log(data.result.reason);
}}

runEval();"""
