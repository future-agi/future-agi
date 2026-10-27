# Synthetic Data Generation Architecture

## Overview

This document provides a comprehensive overview of the synthetic data generation system architecture, designed to create high-quality, human-like synthetic datasets. The system has been enhanced with robust validation, automated repair mechanisms, and sophisticated prompting strategies to eliminate null/rubbish values and generate authentic human communication patterns.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    SYNTHETIC DATA GENERATION PIPELINE           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐         │
│  │   INPUT     │    │  PLANNING   │    │ GENERATION  │         │
│  │ VALIDATION  │───▶│   PHASE     │───▶│   PHASE     │         │
│  │             │    │             │    │             │         │
│  └─────────────┘    └─────────────┘    └─────────────┘         │
│         │                   │                   │              │
│         ▼                   ▼                   ▼              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐         │
│  │  PAYLOAD    │    │ MULTI-PLAN  │    │ BATCH-WISE  │         │
│  │ PROCESSING  │    │ STRATEGY    │    │ GENERATION  │         │
│  │             │    │             │    │             │         │
│  └─────────────┘    └─────────────┘    └─────────────┘         │
│                             │                   │              │
│                             ▼                   ▼              │
│                    ┌─────────────┐    ┌─────────────┐         │
│                    │ KNOWLEDGE   │    │ QUALITY     │         │
│                    │ BASE        │    │ VALIDATION  │         │
│                    │ INTEGRATION │    │ & REPAIR    │         │
│                    │             │    │             │         │
│                    └─────────────┘    └─────────────┘         │
│                             │                   │              │
│                             ▼                   ▼              │
│                    ┌─────────────┐    ┌─────────────┐         │
│                    │ DIVERSITY   │    │ FINAL       │         │
│                    │ EVALUATION  │    │ ASSEMBLY    │         │
│                    │             │    │             │         │
│                    └─────────────┘    └─────────────┘         │
│                             │                   │              │
│                             └───────┬───────────┘              │
│                                     ▼                          │
│                            ┌─────────────┐                     │
│                            │   OUTPUT    │                     │
│                            │ DATAFRAME   │                     │
│                            │             │                     │
│                            └─────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. SyntheticDataAgent (Main Orchestrator)

**Location**: `agentic_eval/agenthub/synthetic_data_agent/synthetic_data_agent.py`

The central component that coordinates the entire synthetic data generation process.

#### Key Responsibilities:
- **Pipeline Orchestration**: Manages the complete generation workflow
- **Multi-Plan Execution**: Coordinates parallel generation strategies
- **Quality Control**: Integrates validation and repair mechanisms
- **Knowledge Base Integration**: Handles RAG-based generation
- **Distribution Correction**: Ensures proper categorical distributions

#### Core Methods:
```python
class SyntheticDataAgent:
    def generate_and_validate(payload, dataset_id=None, headers=None, base_url=None)
    def _generate_plan_data(plan_info, retry_count, payload, ...)
    def _validate_and_repair_batch(generated_batch, params, max_repair_attempts=2)
    def _detect_quality_issues(generated_batch)
    def diversity_evaluation(all_gen_data)
```

### 2. Enhanced Prompt System

**Location**: `agentic_eval/agenthub/synthetic_data_agent/prompts.py`

A sophisticated prompting framework designed for human-like data generation.

#### Key Prompts:

##### **GENERATION_PROMPT**
- **Purpose**: Main data generation with human-like characteristics
- **Features**: 
  - Anti-pattern prevention (no placeholders)
  - Natural imperfection guidelines
  - Cultural authenticity requirements
  - Communication style variation

##### **DATA_QUALITY_VALIDATION_PROMPT**
- **Purpose**: Comprehensive quality assessment
- **Validates**: Completeness, authenticity, coherence, diversity

##### **DATA_REPAIR_PROMPT**
- **Purpose**: Automated issue resolution
- **Repairs**: Null values, placeholders, artificial patterns

##### **INS_PROMPT**
- **Purpose**: Scenario-based instruction generation
- **Creates**: Detailed personas and realistic contexts

### 3. Knowledge Base Integration

**Location**: `agentic_eval/agenthub/synthetic_data_agent/kb_seed_instruction_agent.py`

Enables grounding synthetic data generation in external knowledge sources.

#### Components:
- **KBSeedInstructionAgent**: Manages knowledge base interactions
- **RAG Integration**: Retrieval-augmented generation for contextual data
- **Task Classification**: Categorizes knowledge chunks by task type
- **Few-Shot Retrieval**: Finds relevant examples for generation

#### Key Features:
```python
class KBSeedInstructionAgent:
    def fetch_random_seeds(percentage)
    def generate_plans_from_kb(payload, num_plans)
    def fetch_few_shots_per_datapoint(ins, plan_details, n=4)
    def _classify_seeds(seed_data_points)
```

### 4. Quality Assurance System

#### Multi-Layer Validation:

##### **Quick Quality Detection**
```python
def _detect_quality_issues(generated_batch):
    # Fast detection of:
    # - Null/empty values
    # - Placeholder patterns
    # - Content length issues
    # - Structure validation
```

##### **LLM-Based Validation**
```python
def _validate_data_quality(generated_data, params):
    # Comprehensive assessment:
    # - Human authenticity scoring
    # - Contextual coherence analysis
    # - Cultural appropriateness check
    # - Diversity measurement
```

##### **Automated Repair**
```python
def _repair_data_issues(original_data, validation_issues, params):
    # Intelligent repair:
    # - Contextual value replacement
    # - Human characteristic enhancement
    # - Coherence improvement
```

## Data Flow Architecture

### Phase 1: Input Processing & Validation

```python
# Input payload structure
payload = {
    "requirements": {
        "Dataset Name": "...",
        "Dataset Description": "...",
        "Objective": "...",
        "patterns": "..."
    },
    "constraints": [...],
    "schema": {...},
    "batch_size": int,
    "knowledge_base": {...}  # Optional
}
```

### Phase 2: Planning & Strategy Generation

#### Three Generation Modes:

1. **Schema-Only Mode**
   ```python
   # Uses PLANNING_PROMPT to create diverse strategies
   plans = generate_multiple_strategies(schema, constraints, num_plans)
   ```

2. **Reference Data Mode**
   ```python
   # Analyzes existing data patterns
   seed_agent = SeedInstructionAgent()
   plans = seed_agent.generate_plans_from_examples(payload, df, num_plans)
   ```

3. **Knowledge Base Mode**
   ```python
   # Grounds generation in external knowledge
   kb_agent = KBSeedInstructionAgent(table_name, doc_ids)
   plans = kb_agent.generate_plans_from_kb(payload, num_plans)
   ```

### Phase 3: Multi-Plan Parallel Execution

```python
# Parallel plan execution with ThreadPoolExecutor
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
    future_to_plan = {
        executor.submit(self._generate_plan_data, plan_info, ...): plan_id
        for plan_info in plan_infos
    }
    
    # Collect results with validation and repair
    for future in concurrent.futures.as_completed(future_to_plan):
        plan_data = future.result()
        validated_data = self._validate_and_repair_batch(plan_data, params)
```

### Phase 4: Quality Control Pipeline

```
Generated Batch
       │
       ▼
┌─────────────┐
│ Quick Check │ ──── Issues Found ────┐
└─────────────┘                       │
       │                              │
    No Issues                         │
       │                              ▼
       ▼                    ┌─────────────┐
┌─────────────┐             │ LLM         │
│ Return Data │             │ Validation  │
└─────────────┘             └─────────────┘
                                   │
                                   ▼
                            ┌─────────────┐
                            │ Repair      │
                            │ Attempts    │
                            └─────────────┘
                                   │
                                   ▼
                            ┌─────────────┐
                            │ Re-validate │
                            └─────────────┘
```

### Phase 5: Diversity & Distribution Control

#### Semantic Diversity Evaluation
```python
def diversity_evaluation(all_gen_data):
    # Measures semantic similarity between plans
    # Identifies and discards overly similar content
    # Uses embedding-based similarity metrics
```

#### Distribution Correction
```python
def iterative_correction_holistic(params, all_generated_data, category_column):
    # Adjusts categorical distributions
    # Generates additional samples for underrepresented categories
    # Removes excess samples from overrepresented categories
```

## Advanced Features

### 1. Human-Like Characteristics

#### Natural Imperfections
- **Strategic Typos**: 5-10% of text contains realistic errors
- **Grammar Variations**: Natural grammatical inconsistencies
- **Informal Language**: Abbreviations, slang, colloquialisms
- **Emotional Expressions**: Excitement, frustration, enthusiasm

#### Communication Styles
- **Formal**: Professional, structured communication
- **Casual**: Relaxed, conversational tone
- **Technical**: Domain-specific jargon and explanations
- **Emotional**: Personal experiences and feelings

### 2. Cultural Authenticity

#### Diverse Representation
- **Names**: Culturally diverse, realistic names
- **References**: Appropriate cultural context
- **Regional Variations**: Location-specific patterns
- **Demographic Diversity**: Age, profession, background variety

### 3. Anti-Pattern Prevention

#### Placeholder Detection
```python
placeholder_patterns = [
    "example@", "user123", "lorem ipsum", "placeholder",
    "test@test", "sample", "dummy", "n/a", "tbd", "todo",
    "xxx", "yyy", "zzz", "abc123", "temp"
]
```

#### Quality Failure Patterns
- Null, empty, or undefined values
- Overly generic or template-like content
- Unrealistic perfection in language
- Inconsistent field relationships
- Artificial communication patterns

## Configuration & Customization

### Quality Thresholds
```python
def _default_quality_thresholds():
    return {
        "completeness": 0.99,
        "consistency": 0.95,
        "distribution_p_value": 0.05,
        "correlation_threshold": 0.8,
        "anomaly_rate": 0.01,
    }
```

### Generation Parameters
```python
# Configurable parameters
num_plans = min(30, math.ceil(batch_size / 10))
datapoints_per_plan = math.ceil(batch_size / num_plans)
max_repair_attempts = 2
diversity_threshold = 0.35
```

## Performance Optimizations

### 1. Parallel Processing
- **Multi-threaded Plan Execution**: Up to 5 concurrent plans
- **Batch Processing**: Efficient small-batch generation
- **Embedding Caching**: Reuses computed embeddings

### 2. Smart Validation
- **Quick Checks**: Fast pattern detection before expensive LLM validation
- **Conditional Validation**: Skips detailed validation for small, clean batches
- **Progressive Repair**: Iterative improvement with early stopping

### 3. Memory Management
- **Streaming Processing**: Handles large datasets efficiently
- **Garbage Collection**: Proper cleanup of intermediate results
- **Resource Pooling**: Reuses LLM connections and embeddings

## Error Handling & Resilience

### 1. Graceful Degradation
```python
# Fallback mechanisms
try:
    validated_batch = self._validate_and_repair_batch(generated_batch, params)
except Exception:
    # Return original batch if validation fails
    return generated_batch
```

### 2. Retry Logic
```python
# Multi-level retry with backoff
max_retries = 3
retry_count = 0
while plans_to_discard and retry_count < max_retries:
    # Retry with adjusted parameters
    retry_count += 1
```

### 3. JSON Repair
```python
# Automatic JSON fixing for malformed LLM responses
def _fix_json_response_plans(invalid_response):
    # Uses repair agent to fix JSON structure
    # Handles common formatting issues
    # Provides fallback responses
```

## Integration Points

### 1. Database Integration
```python
# Dataset management
def _add_rows_to_dataset(rows, dataset_id, headers, base_url)
def _update_rows_in_dataset(dataset_id, rows, headers, base_url)
def _delete_rows_from_dataset(dataset_id, row_ids, headers, base_url)
```

### 2. Knowledge Base Integration
```python
# Vector database operations
class ClickHouseVectorDB:
    def get_random_examples(doc_ids, table_name, limit)
    def get_num_vectors(doc_ids, table_name)
```

### 3. API Integration
```python
# External service calls
class LLM:
    def _get_completion_content(messages)
    
class EmbeddingManager:
    def get_syn_embedding()
```

## Monitoring & Observability

### 1. Quality Metrics
- **Completeness Score**: Percentage of non-null values
- **Authenticity Score**: Human-like characteristics assessment
- **Coherence Score**: Logical consistency between fields
- **Diversity Score**: Variation across generated records

### 2. Performance Metrics
- **Generation Time**: Per-batch and total generation time
- **Validation Success Rate**: Percentage of batches passing validation
- **Repair Success Rate**: Percentage of successful repairs
- **Coverage Metrics**: Knowledge base chunk utilization

### 3. Logging & Debugging
```python
# Comprehensive logging
print(f"✓ Data quality validation passed")
print(f"⚠ Data quality issues detected. Overall score: {score}")
print(f"🔧 Attempting repair {attempt + 1}/{max_repair_attempts}")
```

## Usage Examples

### Basic Usage
```python
from agentic_eval.agenthub.synthetic_data_agent.synthetic_data_agent import SyntheticDataAgent

agent = SyntheticDataAgent()
result_df = agent.generate_and_validate(payload)
```

### With Knowledge Base
```python
payload = {
    "requirements": {...},
    "constraints": [...],
    "schema": {...},
    "batch_size": 100,
    "knowledge_base": {
        "table_name": "kb_table",
        "doc_ids": ["doc1", "doc2"]
    }
}
```

### With Custom Quality Thresholds
```python
custom_thresholds = {
    "completeness": 0.95,
    "consistency": 0.90,
    "distribution_p_value": 0.01
}

agent = SyntheticDataAgent(quality_thresholds=custom_thresholds)
```

## Future Enhancements

### 1. Advanced AI Integration
- **Multi-Modal Generation**: Text, image, and audio synthesis
- **Temporal Consistency**: Time-aware data generation
- **Causal Relationships**: Complex dependency modeling

### 2. Performance Improvements
- **GPU Acceleration**: Faster embedding computation
- **Distributed Processing**: Multi-node generation
- **Caching Strategies**: Intelligent result caching

### 3. Quality Enhancements
- **Domain-Specific Patterns**: Industry-specific generation rules
- **Real-Time Validation**: Streaming quality assessment
- **Adaptive Learning**: Self-improving generation strategies

## Conclusion

This synthetic data generation architecture represents a sophisticated, production-ready system for creating high-quality, human-like synthetic datasets. The multi-layered approach to quality assurance, combined with advanced prompting strategies and robust error handling, ensures that generated data is both structurally correct and authentically human-like.

The system's modular design allows for easy customization and extension, while its comprehensive monitoring and validation capabilities provide confidence in the quality and reliability of generated datasets. 