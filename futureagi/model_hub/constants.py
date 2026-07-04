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
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_FILE_SIZE_MB = 10
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
config = DatasetConfig(name="{}", model_type= ModelTypes.GENERATIVE_LLM)
dataset = Dataset(dataset_config=config)
dataset = Dataset.get_dataset_config("{}")

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
    const dsName = "{dataset_name}";

    // 1) Open the dataset (fetch if it exists, create if not)
    const dataset = await Dataset.open(dsName);

    // 2) Define columns
    const columns = [
      {{ name: "user_query", dataType: DataTypeChoices.TEXT }},
      {{ name: "response_quality", dataType: DataTypeChoices.INTEGER }},
      {{ name: "is_helpful", dataType: DataTypeChoices.BOOLEAN }},
    ];

    // 3) Define rows
    const rows = [
      createRow({{
        cells: [
          createCell({{ columnName: "user_query", value: "What is machine learning?" }}),
          createCell({{ columnName: "response_quality", value: 8 }}),
          createCell({{ columnName: "is_helpful", value: true }}),
        ],
      }}),
      createRow({{
        cells: [
          createCell({{ columnName: "user_query", value: "Explain quantum computing" }}),
          createCell({{ columnName: "response_quality", value: 9 }}),
          createCell({{ columnName: "is_helpful", value: true }}),
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
    const dsName = "{dataset_name}";

    // 1) Open the dataset (fetch if it exists, create if not)
    const dataset = await Dataset.open(dsName);

    // 3) Define rows
    const rows = [
      createRow({{
        cells: [
          createCell({{ columnName: "user_query", value: "What is machine learning?" }}),
          createCell({{ columnName: "response_quality", value: 8 }}),
          createCell({{ columnName: "is_helpful", value: true }}),
        ],
      }}),
      createRow({{
        cells: [
          createCell({{ columnName: "user_query", value: "Explain quantum computing" }}),
          createCell({{ columnName: "response_quality", value: 9 }}),
          createCell({{ columnName: "is_helpful", value: true }}),
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


PROMPT_PYTHON_CODE = """
import requests

url = "{api_url}"
headers = {{
    "X-Api-Key": "YOUR_API_KEY",
    "X-Secret-Key": "YOUR_SECRET_KEY",
    "Content-Type": "application/json"
}}

data = {{
    "name": "{name}",
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
    "name": "{name}",
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
--header 'X-Api-Key: YOUR_API_KEY' \\
--header 'X-Secret-Key: YOUR_SECRET_KEY' \\
--header 'Content-Type: application/json' \\
--data '{{
    "name": "{name}",
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
    "name": "{name}",
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
        name: "{name}",
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

func main() {{{{
    url := "{api_url}"

    data := map[string]interface{{}}{{{{
        "name": "{name}",
        "prompt_config": {prompt_config},
        "variable_names": {variable_names},
        "evaluation_configs": {evaluation_configs},
        "is_run": {is_run},
    }}}}

    jsonData, err := json.Marshal(data)
    if err != nil {{{{
        fmt.Printf("Error marshaling JSON: %v\\n", err)
        return
    }}}}

    req, err := http.NewRequest("POST", url, bytes.NewBuffer(jsonData))
    if err != nil {{{{
        fmt.Printf("Error creating request: %v\\n", err)
        return
    }}}}

    req.Header.Set("X-Api-Key", "YOUR_API_KEY")
    req.Header.Set("X-Secret-Key", "YOUR_SECRET_KEY")
    req.Header.Set("Content-Type", "application/json")

    client := &http.Client{{}}
    resp, err := client.Do(req)
    if err != nil {{{{
        fmt.Printf("Error making request: %v\\n", err)
        return
    }}}}
    defer resp.Body.Close()

    body, err := ioutil.ReadAll(resp.Body)
    if err != nil {{{{
        fmt.Printf("Error reading response: %v\\n", err)
        return
    }}}}

    fmt.Println(string(body))
}}}}
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
kb_client = client.create_kb(name="{}",file_paths=['PATH_TO_FILE_1', 'PATH_TO_FILE_2',])
"""

UPDATE_KB_SDK_CODE = """
# pip install futureagi
# export FI_API_KEY="{}"
# export FI_SECRET_KEY="{}"
from fi.kb.client import KnowledgeBase
from fi.kb.types import KnowledgeBaseConfig
# To update a knowledge base
kbase = KnowledgeBaseConfig(
    name="{}",
    files=["PATH_TO_FILE_1", "PATH_TO_FILE_2"]
)
client = KnowledgeBase(
    kbase = kbase,
    fi_api_key="{}",
    fi_secret_key="{}",
)
updated_kb = client.update_kb(name = "{}", file_paths=['PATH_TO_FILE_3', 'PATH_TO_FILE_4',])
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


EVAL_PLAYGROUND_CURL_CODE = """curl '{}/model-hub/eval-playground/' \n
  -H 'X-Api-Key: {}' \n
  -H 'X-Secret-Key: {}' \n
  -H 'Accept: application/json, text/plain, */*' \n
  -H 'Content-Type: application/json' \n
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

  const result = await response.json();
  console.log(result.eval_results[0].output);
  console.log(result.eval_results[0].reason);
}}

runEval();"""
