import json
import math
import os
import random
import uuid
from collections import defaultdict

import json_repair
import numpy as np
import pandas as pd
import requests
from scipy.stats import chisquare
from sklearn.metrics.pairwise import cosine_similarity
from transformers import AutoTokenizer

from ee.agenthub.synthetic_data_agent.kb_seed_instruction_agent import (
    KBSeedInstructionAgent,
)
from ee.agenthub.synthetic_data_agent.prompts import (
    COLUMN_GENERATION_PROMPT,
    GENERATION_PROMPT,
    INS_PROMPT,
    PLANNING_PROMPT,
    PREPARE_PAYLOAD_PROMPT,
    VALIDATION_PROMPT,
)
from ee.agenthub.synthetic_data_agent.seed_instruction_agent import (
    SeedInstructionAgent,
)
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
import structlog

logger = structlog.get_logger(__name__)


class SyntheticDataAgent:
    def __init__(
        self,
        model_name=ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name,
        temperature=ModelConfigs.VERTEX_GEMINI_2_5_PRO.temperature,
        max_tokens=ModelConfigs.VERTEX_GEMINI_2_5_PRO.max_tokens,
        provider=ModelConfigs.VERTEX_GEMINI_2_5_PRO.provider,
        quality_thresholds=None,
    ):
        self.llm = LLM(
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            provider=provider,
        )
        self.quality_thresholds = (
            quality_thresholds or self._default_quality_thresholds()
        )
        self.tokenizer = AutoTokenizer.from_pretrained(
            "bert-base-uncased", strip_accents=True, lowercase=True
        )

    def _default_quality_thresholds(self):
        """Define default quality thresholds for data validation"""
        return {
            "completeness": 0.99,
            "consistency": 0.95,
            "distribution_p_value": 0.05,
            "correlation_threshold": 0.8,
            "anomaly_rate": 0.01,
        }

    def normalize_text(self, text):
        tokens = self.tokenizer.tokenize(text)
        return " ".join(tokens)

    def _get_prompt(self, payload):
        schema = payload.get("schema", {})
        requirements = payload.get("requirements", "")
        constraints = payload.get("constraints", [])
        batch_size = payload.get("batch_size", 100)
        # distribution_params = payload.get("distribution_params", {})

        # Format the content for the LLM
        content = []

        # Add any reference data if provided
        if "reference_data" in payload:
            content.append({"type": "text", "text": "<reference_data>"})
            content.append(
                {
                    "type": "text",
                    "text": json.dumps(payload["reference_data"], indent=2),
                }
            )
            content.append({"type": "text", "text": "</reference_data>"})

        return content, {
            "requirements": requirements,
            "schema": schema,
            "constraints": constraints,
            "batch_size": batch_size,
        }

    def _fix_json_response(self, invalid_response):
        """Attempts to fix invalid JSON response using a repair agent"""
        repair_prompt = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""You are a JSON repair agent. Fix the following invalid JSON response to match the required validation format:

Original Response:
{invalid_response}

Required JSON Format:
{{
    "validation_result": {{
        "status": "ACCEPTED|REJECTED",
        "quality_score": <float>,
        "statistical_validity": {{...}},
        "quality_metrics": {{...}},
        "failing_criteria": [...],
        "improvement_recommendations": [...],
        "explanation": "<explanation text>"
    }}
}}

Return ONLY the fixed JSON, nothing else.""",
                }
            ],
        }

        try:
            fixed_response = self.llm._get_completion_content(messages=[repair_prompt])
            return json.loads(fixed_response)
        except json.JSONDecodeError:
            return {
                "validation_result": {
                    "status": "REJECTED",
                    "quality_score": 0.0,
                    "statistical_validity": {},
                    "quality_metrics": {},
                    "failing_criteria": [
                        {"criterion": "json_parsing", "impact": "HIGH"}
                    ],
                    "improvement_recommendations": [
                        {
                            "issue": "Failed to parse validation response",
                            "suggested_action": "Review generation parameters",
                            "priority": "HIGH",
                        }
                    ],
                    "explanation": "Failed to parse validation response. Rejecting batch as precaution.",
                }
            }

    def _fix_json_response_plans(self, invalid_response):
        """Attempts to fix invalid JSON response using a repair agent"""

        repair_prompt = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""You are a JSON repair agent. Fix the following invalid JSON response to match the required format:

Original Response:
{invalid_response}

Return the strategies in this JSON format only:
{{
    "1": {{
        "name": "Strategy name",
        "column_focus": {{
            "column_name": {{
                "type": "categorical|numerical|datetime|text|boolean",
                "value_range": "Specific values or ranges this strategy will use",
                "distribution": "How values will be distributed within the range"
            }}
        }},
        "generation_rules": [
            "Rules for generating values for each focused column"
        ],
        "validation_criteria": [
            "Rules for validating the generated values"
        ]
    }},
    ... // Repeat for strategies ...
}}
Rules: Return valid JSON matching the required format. Remove any extra text in the begining or end.

Return ONLY the fixed JSON, nothing else.""",
                }
            ],
        }

        try:
            # Get repaired response
            fixed_response = self.llm._get_completion_content(messages=[repair_prompt])
            return json.loads(fixed_response)
        except json.JSONDecodeError:
            # If repair fails, return a safe fallback response
            raise ValueError("Repair Failed")

    def _fix_json_response_ins(self, invalid_response):
        """Attempts to fix invalid JSON response using a repair agent"""

        repair_prompt = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""You are a JSON repair agent. Fix the following invalid JSON response to match the required format:

Original Response:
{invalid_response}

Return the strategies in this JSON format only:
{{
   "1":{{"instruction for data point 1": "Generate a data point where ..."}},
   "2":{{"instruction for data point 2": "Generate a data point where ..."}},
   "3":{{"instruction for data point 3": "Generate a data point where ..."}},
      ...
   "10":{{"instruction for data point 10": "Generate a data point where ..."}},
}}
Rules:
1. Return valid JSON matching the required format. Remove any extra text in the begining or end.
2. Response should only use the curly braces {{ and }} and not the square brackets [ and ]. If you see any square brackets, remove them and replace them with curly braces respectively.
3. Return ONLY the fixed JSON, nothing else.""",
                }
            ],
        }

        try:
            # Get repaired response
            fixed_response = self.llm._get_completion_content(messages=[repair_prompt])
            return json.loads(fixed_response)
        except json.JSONDecodeError:
            # If repair fails, return a safe fallback response
            raise ValueError("Repair Failed")

    def compute_overall_distribution_stats(
        self, all_generated_data, category_column, expected_distribution=None
    ):
        """
        Compute and evaluate the overall distribution of a specified categorical column across all plans.

        Args:
            all_generated_data (dict): The dictionary containing all generated data.
            category_column (str): The name of the categorical column to evaluate.
            expected_distribution (dict, optional): Expected distribution of categories.
                Example: {"category_1": 0.4, "category_2": 0.6}

        Returns:
            dict: Overall distribution statistics including observed distribution, expected distribution,
                  and chi-square test results.
        """
        all_values = []

        for plan_data in all_generated_data["plans"].values():
            generated_data = plan_data.get("generated_data", {})
            all_values.extend(
                [
                    item.get(category_column)
                    for item in generated_data.values()
                    if category_column in item
                ]
            )

        if not all_values:
            return {"error": f"Column '{category_column}' not found in any plan data."}

        # Compute observed distribution
        unique_values, counts = np.unique(all_values, return_counts=True)
        observed_distribution = dict(
            zip(unique_values, counts / sum(counts), strict=False)
        )
        # Use expected distribution or assume uniform if not provided
        if expected_distribution:
            expected_dist = np.array(
                [
                    expected_distribution.get(self.normalize_text(val), 0)
                    for val in unique_values
                ]
            )
            expected_dist /= (
                expected_dist.sum()
            )  # Normalize to ensure it's a valid distribution
        else:
            expected_dist = np.ones_like(counts) / len(counts)  # Uniform distribution

        sum(counts)

        # Perform chi-square test
        chi2_stat, chi2_p_value = chisquare(
            f_obs=counts, f_exp=expected_dist * sum(counts)
        )

        return {
            "observed_distribution": observed_distribution,
            "expected_distribution": dict(
                zip(unique_values, expected_dist, strict=False)
            ),
            "chi2_stat": chi2_stat,
            "chi2_p_value": chi2_p_value,
            "is_uniform": chi2_p_value
            > 0.05,  # Null hypothesis: no significant difference
        }

    def iterative_correction_holistic(
        self,
        params,
        all_generated_data,
        category_column,
        expected_distribution=None,
        max_iterations=3,
    ):
        """
        Iteratively correct the generated data to meet the expected overall distribution for a categorical column.

        Args:
            all_generated_data (dict): The dictionary containing all generated data.
            category_column (str): The name of the categorical column to evaluate.
            expected_distribution (dict, optional): Expected distribution of categories.
            max_iterations (int): Maximum number of correction iterations.

        Returns:
            dict: Final corrected data and logs of iterations.
        """
        iteration_logs = []
        for iteration in range(max_iterations):
            distribution_stats = self.compute_overall_distribution_stats(
                all_generated_data, category_column, expected_distribution
            )
            iteration_logs.append(
                {"iteration": iteration + 1, "distribution_stats": distribution_stats}
            )

            if distribution_stats.get("is_uniform", True):
                logger.info(
                    "The overall data meets the expected distribution. Stopping correction."
                )
                break
            # Combine all data across plans for adjustment
            combined_data = {}
            ctr = 0
            for plan_id, plan_data in all_generated_data["plans"].items():
                gen_data = plan_data.get("generated_data", {})
                for data_id, data_point in gen_data.items():
                    # Add the original plan ID to each data point
                    data_point["original_plan_id"] = plan_id
                    # Add the data point to combined_data
                    combined_data[ctr] = data_point
                    ctr += 1

            # Adjust data holistically
            corrected_data = self._adjust_data_distribution(
                params, combined_data, category_column, expected_distribution
            )

            # Redistribute corrected data
            new_plan_id = f"itr_adj_{iteration}"
            adjusted_plans = defaultdict(dict)

            for data_id, data_point in corrected_data.items():
                original_plan_id = data_point.get("original_plan_id")
                if original_plan_id in all_generated_data["plans"]:
                    adjusted_plans[original_plan_id][data_id] = data_point
                else:
                    adjusted_plans[new_plan_id][data_id] = data_point
                    data_point["original_plan_id"] = new_plan_id

            # Update plans
            for plan_id, data_points in adjusted_plans.items():
                if plan_id in all_generated_data["plans"]:
                    all_generated_data["plans"][plan_id]["generated_data"] = data_points
                else:
                    all_generated_data["plans"][str(plan_id)] = {
                        "generated_data": data_points,
                        "plan_details": {"name": f"New Plan {plan_id}"},
                    }

        return all_generated_data

    def _adjust_data_distribution(
        self, params, generated_data, category_column, expected_distribution
    ):
        """
        Adjust data distribution by generating specific numbers of datapoints per category and removing excess data.

        Args:
            generated_data (dict): The generated data to adjust.
            category_column (str): The categorical column to adjust.
            expected_distribution (dict): The desired distribution.

        Returns:
            dict: Adjusted generated data.
        """
        category_counts = defaultdict(list)

        # Organize data by category
        for data_id, data_point in generated_data.items():
            category = data_point.get(category_column)
            if category is not None:
                category_counts[category].append((data_id, data_point))

        total_samples = sum(len(items) for items in category_counts.values())

        adjusted_data = {}
        next_id = max(map(int, generated_data.keys())) + 1 if generated_data else 1

        for category, target_proportion in expected_distribution.items():
            target_count = int(target_proportion * total_samples)
            current_count = len(category_counts.get(category, []))

            if current_count > target_count:
                # Remove excess samples randomly
                retained_samples = random.sample(
                    category_counts[category], target_count
                )
                category_counts[category] = retained_samples
            elif current_count < target_count:
                # Keep existing samples and calculate missing count

                missing_count = target_count - current_count
                few_shot_examples = (
                    random.sample(
                        category_counts.get(category, []), min(4, current_count)
                    )
                    if current_count > 0
                    else []
                )

                # Generate new samples
                new_samples = self._generate_category_samples(
                    params,
                    category,
                    missing_count,
                    [item[1] for item in few_shot_examples],
                )

                # Add new samples to the category
                for sample in new_samples:
                    sample["original_plan_id"] = (
                        "new_class_updated_data"  # Mark new samples with no original plan
                    )
                    category_counts[category].append((str(next_id), sample))
                    next_id += 1

            # Add final samples to adjusted data
            for data_id, sample in category_counts[category]:
                adjusted_data[data_id] = sample

        return adjusted_data

    def _generate_category_samples(
        self,
        params,
        category,
        count,
        few_shot_examples,
    ):
        """
        Generate new data samples for a specific category using few-shot examples.

        Args:
            category (str): The category for which data needs to be generated.
            count (int): Number of new samples to generate.
            few_shot_examples (list): Few-shot examples to guide the generation.

        Returns:
            list: Generated samples.
        """

        remaining_data_points = count
        plan_data: list[Any] = []
        while remaining_data_points > 0:
            # Adjust batch size dynamically based on remaining data points
            batch_size = min(remaining_data_points, 5)
            completed_points = 0
            org_batch_size = batch_size
            batch_try = 1
            while completed_points != org_batch_size:
                formatted_examples = "\n".join(
                    str(example) for example in few_shot_examples
                )
                plan_details = {
                    "Standing Instruction": f"Generate datapoints for class {category}. Use the following as reference datapoints: \n {formatted_examples}"
                }
                plan_details["Batch Instruction"] = (
                    f"This is Batch {batch_try} of this plan. Your generation should be diverse across batches, so do not go for the most obvious choices in subsequent generations."
                )
                try:
                    generated_batch = self._generate_data_for_plan(
                        plan_details,
                        params,
                        dynamic_batch_size=batch_size,
                        seed=random.randint(0, 1000),  # Random seed for variation
                    )
                    if generated_batch:
                        # Add generated data to plan_data
                        plan_data.extend(
                            generated_batch[str(j)] for j in range(1, batch_size + 1)
                        )
                        completed_points += batch_size
                        batch_try += 1
                    else:
                        raise ValueError("Generated batch is None")
                except Exception as e:
                    logger.error(
                        f"Error generating data with batch size {batch_size}: {e}. Reducing batch size."
                    )
                    if batch_size <= 1:
                        logger.error(
                            "Error: Unable to generate valid data even with batch size 1. Please increase token limit."
                        )
                        break
                    batch_size -= 1
            remaining_data_points -= completed_points

        return plan_data

    def calculate_similarity(self, set1, set2):
        # Load MiniLM model'
        model = 'SentenceTransformer("all-MiniLM-L6-v2")'

        # Compute embeddings for both sets
        embeddings1 = model.encode(set1)
        embeddings2 = model.encode(set2)

        def compute_intra_set_similarity(embeddings):
            similarities = cosine_similarity(embeddings)
            num_pairs = len(embeddings) * (len(embeddings) - 1) / 2
            if num_pairs == 0:
                return 0.0

            return np.sum(np.triu(similarities, k=1)) / num_pairs

        intra_similarity_set1 = compute_intra_set_similarity(embeddings1)
        intra_similarity_set2 = compute_intra_set_similarity(embeddings2)

        inter_set_similarities = cosine_similarity(embeddings1, embeddings2)
        inter_set_avg_similarity = np.mean(inter_set_similarities)

        return {
            "intra_similarity_set1": str(intra_similarity_set1),
            "intra_similarity_set2": str(intra_similarity_set2),
            "inter_set_similarity": str(inter_set_avg_similarity),
        }

    def diversity_evaluation(self, all_gen_data):
        concatenated_sources = defaultdict(list)

        longest_column = None

        for plan_id, plan_data in all_gen_data["plans"].items():
            generated_data = plan_data.get("generated_data", {})
            first_item = next(iter(generated_data.values()), {})

            if first_item and not longest_column:  # Only determine the column once
                longest_column = max(
                    first_item,
                    key=lambda key: (
                        len(str(first_item[key])) if first_item.get(key) else 0
                    ),
                )
                break  # Exit loop after finding the column

        # Use the determined column for all plans and items
        for plan_id, plan_data in all_gen_data["plans"].items():
            sources = []
            for _item_id, item_data in plan_data.get("generated_data", {}).items():
                sources.append(item_data.get(longest_column, ""))
            concatenated_sources[plan_id].extend(sources)

        # Step 2: Compute pairwise similarities
        plan_ids = list(concatenated_sources.keys())
        num_plans = len(plan_ids)
        similarity_results = {}
        threshold = 0.35
        plan_to_discard = []
        for i in range(num_plans):
            for j in range(i + 1, num_plans):
                plan_i = plan_ids[i]
                plan_j = plan_ids[j]
                text_i = concatenated_sources[plan_i]
                text_j = concatenated_sources[plan_j]
                plan_name_i = all_gen_data["plans"][plan_i]["plan_details"]["name"]
                plan_name_j = all_gen_data["plans"][plan_j]["plan_details"]["name"]

                # Compute similarities
                similarities = self.calculate_similarity(text_i, text_j)

                # Store results
                similarity_results[f"{plan_name_i}-{plan_name_j}"] = similarities
                if float(similarities["intra_similarity_set1"]) > threshold:
                    plan_to_discard.append(f"{plan_name_i}")
                if float(similarities["intra_similarity_set2"]) > threshold:
                    plan_to_discard.append(f"{plan_name_j}")
                if float(similarities["inter_set_similarity"]) > threshold:
                    plan_to_discard.append(f"{plan_name_j}")

        plans_to_discard = list(set(plan_to_discard))
        return plans_to_discard

    def prepare_payload(self, payload):
        prompt_name = payload.get("prompt_name", "")
        prompt_instruction = payload.get("prompt_instruction", "")
        variable_names = payload.get("variable_names", [])
        batch_size = payload.get("batch_size", 1)
        converted_variables = []
        space_map = {}
        for var in variable_names:
            new_var = re.sub(r"\s+", "_", var)
            space_map[new_var] = var
            converted_variables.append(new_var)

        content = []
        content.append(
            {
                "type": "text",
                "text": PREPARE_PAYLOAD_PROMPT.format(
                    prompt_name=prompt_name,
                    prompt_instruction=prompt_instruction,
                    variable_names=converted_variables,
                ),
            }
        )
        ins_response = self.llm._get_completion_content(
            messages=[{"role": "user", "content": content}]
        )
        try:
            payload = json.loads(ins_response)
            for constraint in payload["constraints"]:
                # Get current value of the "field"
                constraint["field"]

            field_names = [constraint["field"] for constraint in payload["constraints"]]
            if field_names != converted_variables:
                raise ValueError(
                    "Cannot generate data at this time please try again later."
                )
            schema = {
                constraint["field"]: {"type": constraint["type"]}
                for constraint in payload["constraints"]
            }
            payload["schema"] = schema
            payload["batch_size"] = batch_size
            return payload, space_map

        except json.JSONDecodeError:
            raise ValueError(
                "Cannot generate data at this time please try again later."
            )

    def get_ins(self, payload, plan, datapoints_per_plan):
        content, params = self._get_prompt(payload)
        # params
        content.append(
            {
                "type": "text",
                "text": INS_PROMPT.format(
                    plan=plan, datapoints_per_plan=datapoints_per_plan, **params
                ),
            }
        )
        ins_response = self.llm._get_completion_content(
            messages=[{"role": "user", "content": content}]
        )

        try:
            ins_response = ins_response.strip()
            if not ins_response.startswith("{"):
                # Try to find the first valid JSON object
                start_idx = ins_response.find("{")
                if start_idx != -1:
                    ins_response = ins_response[start_idx:]
            if not ins_response.endswith("}"):
                # Try to find the last valid JSON object
                end_idx = ins_response.rfind("}")
                if end_idx != -1:
                    ins_response = ins_response[: end_idx + 1]
            ins = json_repair.loads(ins_response)
        except json.JSONDecodeError:
            logger.error(
                "Error: Failed to parse Instructions into JSON. Attempting to fix ..."
            )
            ins = self._fix_json_response_ins(ins_response)
        return ins

    def prepare_final_dataframe(self, holistic_results, user_input_columns):
        """
        Prepare the final DataFrame with only user-specified columns and generated values.

        Args:
            holistic_results (dict): The dictionary containing the final generated data.
            user_input_columns (list): List of column names provided by the user.

        Returns:
            pd.DataFrame: A DataFrame containing only the user-specified columns and generated values.
        """
        # Extract all generated data across plans
        all_data = []
        for plan_data in holistic_results["plans"].values():
            generated_data = plan_data.get("generated_data", {})
            for data_point in generated_data.values():
                # Extract only the user-specified columns
                filtered_data = {
                    col: data_point.get(col, None) for col in user_input_columns
                }
                all_data.append(filtered_data)

        # Convert to a DataFrame
        final_df = pd.DataFrame(all_data)

        # Ensure columns are ordered as per the user's input
        final_df = final_df[user_input_columns]

        return final_df

    def generate_column_data(self, payload):
        """
        Generate data for a specific column based on the provided payload.

        Args:
            payload (dict): The payload containing requirements, constraints, and reference data.

        Returns:
            pd.DataFrame: DataFrame with generated column data.

        Raises:
            ValueError: If required fields are missing or data generation fails.
        """
        # Validate required fields
        required_fields = ["requirements", "constraints", "reference_data"]
        for field in required_fields:
            if not payload.get(field):
                raise ValueError(f"{field} is required")

        def generate_data(row, requirements, constraint, llm):
            try:
                column_generation_prompt = COLUMN_GENERATION_PROMPT.format(
                    constraints=json.dumps(constraint, indent=2),
                    row_data=json.dumps(row.to_dict(), indent=2),
                    requirements=json.dumps(requirements, indent=2),
                )

                response = llm._get_completion_content(
                    messages=[{"role": "user", "content": column_generation_prompt}]
                )

                try:
                    generated_value = json.loads(response)
                    return generated_value
                except (json.JSONDecodeError, KeyError) as e:
                    logger.error(f"Error parsing response for : {str(e)}")
                    return None

            except Exception as e:
                logger.exception(f"Error generating data for : {str(e)}")
                return None

        try:
            # Create DataFrame from reference data
            df = pd.DataFrame(payload["reference_data"])

            generated_values = df.apply(
                lambda x: generate_data(
                    x, payload["requirements"], payload["constraints"], self.llm
                ),
                axis=1,
            )

            new_df = pd.DataFrame(generated_values.tolist())

            # Check for failed generations (None values)
            failed_rows = new_df[new_df.isna().any(axis=1)].index.tolist()
            if len(failed_rows) > 0:
                logger.warning(
                    f"Warning: Failed to generate {len(failed_rows)}. Retrying..."
                )

                # Retry failed rows individually
                for idx in failed_rows:
                    retry_value = generate_data(
                        df.loc[idx],
                        payload["requirements"],
                        payload["constraints"],
                        self.llm,
                    )
                    if retry_value is not None:
                        new_df.loc[idx, retry_value.keys()] = retry_value.values()

            return new_df

        except Exception as e:
            raise ValueError(f"Failed to generate column data: {str(e)}")

    def generate_and_validate(
        self, payload, dataset_id=None, headers=None, base_url=None
    ):
        """Generates synthetic data using multiple plans and returns a structured response with validation"""

        if "generation_type" in payload:
            payload, space_map = self.prepare_payload(payload)
            payload["generation_type"] = "prompt"

        # Input validation
        required_fields = ["requirements", "schema", "constraints"]
        for field in required_fields:
            if not payload.get(field) and "reference_data" not in payload:
                raise ValueError(f"{field} is required")

        chunk_ids = set()
        num_plans = min(30, math.ceil(payload["batch_size"] / 10))
        datapoints_per_plan = math.ceil(payload["batch_size"] / num_plans)
        number_of_iterations_per_plan = math.ceil(datapoints_per_plan / 5)

        if "reference_data" in payload:
            df = pd.DataFrame(payload["reference_data"])
            seed_agent = SeedInstructionAgent()
            payload = seed_agent.infer_payload(payload, df)
            df, category_plans = seed_agent.generate_plans_from_examples(
                payload, df, num_plans
            )

            params = payload
            plans = {}
            i = 1
            num_fewshots = 4
            for category, category_plan in category_plans.items():
                data_points = category_plan["datapoints"][:num_fewshots]
                for characteristic, plan in category_plan.items():
                    if characteristic == "datapoints":
                        continue
                    plans[i] = {
                        "Skill": category,
                        "Characteristic": characteristic,
                        "name": f"{category}_{characteristic}",
                        "Plan": plan,
                        "Datapoints": data_points,
                    }
                    if i == num_plans:
                        break
                    i += 1
        elif "knowledge_base" in payload:
            params = payload
            params["num_plans"] = num_plans
            table_name = payload["knowledge_base"]["table_name"]
            doc_id = payload["knowledge_base"]["doc_id"]
            seed_agent = KBSeedInstructionAgent(table_name=table_name, doc_id=doc_id)
            seed_data_points, category_plans, total_chunks = (
                seed_agent.generate_plans_from_kb(payload, num_plans)
            )
            plans = {}
            i = 1
            for category, category_plan in category_plans.items():
                data_points = category_plan["datapoints"][:4]
                for characteristic, plan in category_plan.items():
                    if characteristic == "datapoints":
                        continue
                    datapoints_text = []
                    for dp in data_points:
                        datapoints_text.append(dp.get("chunk_text", ""))
                    plans[i] = {
                        "Skill": category,
                        "Characteristic": characteristic,
                        "name": f"{category}_{characteristic}",
                        "Plan": plan,
                        "GROUNDING INSTRUCTION": "STRICTLY use the provided datapoints from the knowledge base. Do NOT introduce information or assumptions outside the provided data points. If any required information is not explicitly in the provided datapoints, do NOT attempt to generate it. There should be **ZERO** hallucinations. (You are allowed to do domain expansion for generating a datapoint. For example: if we know from supplied domain knowledge that the context is about a restaurant, we will generate restaurant names in name column unless clearly specified).",
                        "Datapoints": datapoints_text,
                    }
                    if i == num_plans:
                        break
                    i += 1
        else:
            # Get formatted prompt and parameters
            content, params = self._get_prompt(payload)
            content.copy()

            # Planning Phase - Get 10 different plans
            params["num_plans"] = num_plans
            content.append({"type": "text", "text": PLANNING_PROMPT.format(**params)})
            planning_response = self.llm._get_completion_content(
                messages=[{"role": "user", "content": content}]
            )
            try:
                plans = json.loads(planning_response)
            except json.JSONDecodeError:
                logger.error(
                    "Error: Failed to parse planning response into JSON. Attempting to fix ..."
                )
                plans = self._fix_json_response_plans(planning_response)

        all_generated_data = {"plans": {}}
        if dataset_id:
            row_ids = [str(uuid.uuid4()) for _ in range(payload["batch_size"])]
            rows = [{"id": row_id, "cells": []} for row_id in row_ids]

        # Generate data for each plan
        max_retries = 3
        retry_count = 0
        plans_to_discard = [
            "dummy"
        ]  # Initialize with a non-empty value to ensure the first iteration

        while plans_to_discard and retry_count < max_retries:
            mu, sigma = 0.5, 0.15  # Mean and standard deviation
            temperature = random.gauss(mu, sigma)
            temperature = max(0, min(1, temperature))
            self.llm.temperature = temperature
            retry_count += 1
            if retry_count == 2:
                logger.info("Fixing semantic diversity...")
            idx = 0
            base_datapoints_per_plan = payload["batch_size"] // num_plans
            remainder = payload["batch_size"] % num_plans
            dp_per_plan_list = [
                base_datapoints_per_plan + 1
                if i < remainder
                else base_datapoints_per_plan
                for i in range(num_plans)
            ]

            for i, (plan_id, plan_details) in enumerate(plans.items()):
                if i >= num_plans:
                    break  # Avoid using more plans than needed
                datapoints_per_plan = dp_per_plan_list[i]
                number_of_iterations_per_plan = math.ceil(datapoints_per_plan / 5)
                ins = self.get_ins(payload, plan_details, datapoints_per_plan)
                if retry_count > 1:
                    plan_details["Standing Instruction"] = (
                        "Previously generated data points from this plan was discarded because semantic diversity was very low. Generate diverse datapoints this time."
                    )

                # Generate 10 datapoints for current plan
                plan_data: list[Any] = []
                batch_try = 1
                few_shots_per_datapoint = {}
                if "knowledge_base" in payload:
                    few_shots_per_datapoint = seed_agent.fetch_few_shots_per_datapoint(
                        ins, plan_details, 4
                    )

                    for i, few_shots in few_shots_per_datapoint.items():
                        for few_shot in few_shots:
                            chunk_ids.add(few_shot["chunk_id"])
                for itr in range(number_of_iterations_per_plan):
                    max_batch_size = 5
                    if "knowledge_base" in payload:
                        max_batch_size = 1
                    fix_batch_size = 5
                    if (
                        i == number_of_iterations_per_plan - 1
                        and datapoints_per_plan % fix_batch_size != 0
                    ):
                        remaining_data_points = datapoints_per_plan % fix_batch_size
                        fix_batch_size = remaining_data_points
                    else:
                        remaining_data_points = fix_batch_size
                    while remaining_data_points > 0:
                        # Adjust batch size dynamically based on remaining data points
                        batch_size = min(remaining_data_points, max_batch_size)
                        start_idx = (
                            (fix_batch_size - remaining_data_points)
                            + 1
                            + (fix_batch_size * itr)
                        )
                        end_idx = start_idx + batch_size
                        keys_to_extract = list(range(start_idx, end_idx))
                        ins_subset = {
                            i + 1: ins[str(key)]
                            for i, key in enumerate(keys_to_extract)
                            if str(key) in ins
                        }

                        if few_shots_per_datapoint:
                            few_shot_subset = {
                                i + 1: few_shots_per_datapoint[str(key)]
                                for i, key in enumerate(keys_to_extract)
                                if str(key) in few_shots_per_datapoint
                            }

                        plan_details["Batch Instruction"] = (
                            f"This is Batch {batch_try} of this plan. Ensure diversity by avoiding any topics or ideas covered in previous batches. Explore less obvious subcategories, varying the content focus. Diversity in ideas is of prime importance. Here are the instructions for each datapoint to be generated in this batch. One instruction corresponds to one data point."
                        )
                        for i in ins_subset:
                            realism_text = (
                                "Ensure your data is hyper-realistic. There should be no gibberish content, unrealistic placeholders (e.g., partner123 instead of an actual partner name), or inconsistencies that break the illusion of real-world authenticity. "
                                + str(ins_subset[i])
                            )
                            plan_details["Batch Instruction"] += realism_text
                            if few_shots_per_datapoint:
                                # Extract only the chunk_text from each few shot example and store as a list
                                for fs_key, fs_list in few_shot_subset.items():
                                    chunk_texts = []
                                    for fs in fs_list:
                                        if "chunk_text" in fs:
                                            chunk_texts.append(fs["chunk_text"])
                                        else:
                                            # Keep the original item if chunk_text is not available
                                            chunk_texts.append(fs)

                                    # Replace the original few_shot_subset with just the list of chunk_texts
                                    few_shot_subset[fs_key] = chunk_texts
                                plan_details["Batch Instruction"] += (
                                    "\n\nDomain Knowledge for this Datapoint:\n"
                                    + "\n".join([str(fs) for fs in few_shot_subset[i]])
                                )

                        try:
                            generated_batch = self._generate_data_for_plan(
                                plan_details,
                                params,
                                dynamic_batch_size=batch_size,
                                seed=random.randint(
                                    0, 1000
                                ),  # Random seed for variation
                            )
                            if generated_batch:
                                # Add generated data to plan_data
                                plan_data.extend(
                                    generated_batch[str(j)]
                                    for j in range(1, batch_size + 1)
                                )
                                remaining_data_points -= batch_size  # Reduce the count of remaining data points
                                batch_try += 1
                            else:
                                raise ValueError("Generated batch is None")
                        except Exception as e:
                            logger.error(
                                f"Error generating data with batch size {batch_size}: {e}. Reducing batch size."
                            )
                            if batch_size <= 1:
                                logger.error(
                                    "Error: Unable to generate valid data even with batch size 1. Please increase token limit."
                                )
                                break
                            max_batch_size -= 1

                plan_data = dict(zip(range(1, 11), plan_data, strict=False))
                all_generated_data["plans"][f"itr_{retry_count}_{plan_id}"] = {
                    "plan_details": plan_details,
                    "generated_data": plan_data,
                }
                if dataset_id:
                    current_idx = idx
                    for datapoint in plan_data.values():
                        rows[idx]["cells"] = [
                            {
                                "column_name": column_name,
                                "value": datapoint[column_name],
                            }
                            for column_name in payload["schema"].keys()
                        ]
                        idx += 1
                    self._add_rows_to_dataset(
                        rows[current_idx:idx], dataset_id, headers, base_url
                    )
            if len(plans_to_discard) != 0:
                if plans_to_discard[0] == "dummy":
                    self._save_generated_data(
                        all_generated_data,
                        "_pre_semantic_diversity.json",
                        params["requirements"]["Dataset Name"],
                    )

            # Evaluate diversity and update plans_to_discard
            plans_to_discard = (
                self.diversity_evaluation(all_generated_data)
                if retry_count != max_retries
                else []
            )

            if retry_count == 1:
                temp_generated_data = all_generated_data.copy()

            new_plans = [
                plan_data["plan_details"]
                for key, plan_data in all_generated_data["plans"].items()
                if plan_data["plan_details"]["name"] in plans_to_discard
            ]

            # Remove discarded plans from all_generated_data and prepare new plans
            all_generated_data["plans"] = {
                key: value
                for key, value in all_generated_data["plans"].items()
                if value["plan_details"]["name"] not in plans_to_discard
            }

            plans = {}
            plans = {i + 1: plan for i, plan in enumerate(new_plans)}

        if not plans_to_discard:
            logger.info("Processing completed successfully.")
        elif retry_count >= max_retries:
            logger.info("Max retries reached. Exiting loop.")

        # Consolidate the final data
        final_generated_data = {"plans": {}}

        for key, plan_data in all_generated_data["plans"].items():
            # Keep only the latest iteration data
            final_generated_data["plans"][key] = plan_data
        if not final_generated_data["plans"]:
            final_generated_data = temp_generated_data.copy()
        self._save_generated_data(
            final_generated_data,
            "_pre_class_distribution.json",
            params["requirements"]["Dataset Name"],
        )

        ##check class distribution here
        holistic_results = final_generated_data.copy()
        for constraint in params["constraints"]:
            if "distribution" in constraint:
                distribution = constraint["distribution"]
                classes = constraint["values"]
                assert len(distribution) == len(classes), (
                    f"The lengths of 'distribution' and 'classes' must be equal in {constraint[field]}."
                )
                expected_distribution = {
                    self.normalize_text(cls): dist
                    for cls, dist in zip(classes, distribution, strict=False)
                }

                holistic_results = self.iterative_correction_holistic(
                    params,
                    final_generated_data,
                    category_column=constraint["field"],
                    expected_distribution=expected_distribution,
                )
                if "plans" not in holistic_results or not holistic_results["plans"]:
                    holistic_results = final_generated_data.copy()

        # ----- KB Chunk Coverage (Balancing) Branch -----
        if "knowledge_base" in payload:
            holistic_results_before_coverage = holistic_results.copy()
            self._save_generated_data(
                holistic_results_before_coverage,
                "_before_coverage.json",
                params["requirements"]["Dataset Name"],
            )

            fraction_of_distict_slots = len(chunk_ids) / (payload["batch_size"] * 4)

            corpus_coverage = len(chunk_ids) / (total_chunks)
            final_metric = 0.5 * fraction_of_distict_slots + 0.5 * corpus_coverage
            if final_metric < 0.7:
                # Use the retrieved chunk_ids from the generation phase.
                holistic_results = self._increase_coverage(
                    holistic_results_before_coverage,
                    payload,
                    retrieved_chunk_ids=chunk_ids,
                )

        self._save_generated_data(
            holistic_results,
            "final_generated_data.json",
            params["requirements"]["Dataset Name"],
        )

        column_names = params["schema"].keys()
        final_df = self.prepare_final_dataframe(holistic_results, column_names)
        if dataset_id:
            self._update_rows_in_dataset(dataset_id, rows, headers, base_url)
        if "generation_type" in payload:
            valid_mappings = {
                col: space_map[col] for col in final_df.columns if col in space_map
            }
            final_df.rename(columns=valid_mappings, inplace=True)

        return final_df

    def _delete_rows_from_dataset(
        self, dataset_id, row_ids, headers, base_url="http://localhost:80"
    ):
        """
        Delete rows from a dataset using the Model Hub API.

        Args:
            dataset_id (str): ID of the dataset to delete rows from
            row_ids (list): List of row IDs to delete
            headers (dict): Request headers
            base_url (str): Base URL for the API endpoint
        """
        try:
            response = requests.delete(
                f"{base_url}/model-hub/develops/{dataset_id}/delete_row/",
                json={"row_ids": row_ids},
                headers=headers,
            )
            if response.status_code != 200:
                logger.warning(f"Warning: Failed to delete rows: {response.text}")

        except Exception as e:
            logger.exception(f"Error deleting rows from dataset: {str(e)}")

    def _update_rows_in_dataset(
        self, dataset_id, rows, headers, base_url="http://localhost:80"
    ):
        """
        Update rows in a dataset using the Model Hub API.

        Args:
            dataset_id (str): ID of the dataset to update rows in
            rows (list): List of rows to update in the dataset.
            headers (dict): Request headers
            base_url (str): Base URL for the API endpoint
        """
        try:
            headers["Content-Type"] = "application/json"
            response = requests.put(
                f"{base_url}/model-hub/develops/{dataset_id}/update_row/",
                json={"rows": rows},
                headers=headers,
            )
            if response.status_code != 200:
                logger.warning(f"Warning: Failed to update rows: {response.text}")
        except Exception as e:
            logger.exception(f"Error updating rows in dataset: {str(e)}")

    def _add_rows_to_dataset(
        self, rows, dataset_id, headers, base_url="http://localhost:80"
    ):
        """
        Add rows to a dataset using the Model Hub API.

        Args:
            rows (list): List of rows to add to the dataset.
            dataset_id (str): ID of the dataset to add rows to.
        """
        try:
            headers["Content-Type"] = "application/json"
            for i in range(0, len(rows), 10):
                batch = rows[i : i + 10]
                response = requests.post(
                    f"{base_url}/model-hub/develops/{dataset_id}/add_rows/",
                    json={"rows": batch},
                    headers=headers,
                )

                if response.status_code != 200:
                    logger.warning(
                        f"Warning: Failed to send batch {i // 10 + 1} to API: {response.text}"
                    )

        except Exception as e:
            logger.exception(f"Error sending data to API: {str(e)}")

    def _increase_coverage(self, holistic_results, payload, retrieved_chunk_ids):
        """
        Increase KB chunk coverage by generating new samples for those KB chunks
        that were never retrieved during generation.

        Args:
            holistic_results (dict): Overall generated data including plans.
            payload (dict): The payload including a "knowledge_base" with table_name and doc_id.
            retrieved_chunk_ids (set): KB chunk IDs that have been retrieved during generation.

        Returns:
            dict: Updated holistic_results with additional samples for unused KB chunks.
        """
        import random

        # Make a copy of the original results to preserve the structure
        original_results = {
            "plans": {
                plan_id: plan_data.copy()
                for plan_id, plan_data in holistic_results["plans"].items()
            }
        }

        # Count the total number of datapoints in the original results
        original_datapoint_count = 0
        for plan_id, plan_data in original_results["plans"].items():
            original_datapoint_count += len(plan_data.get("generated_data", {}))

        table_name = payload["knowledge_base"]["table_name"]
        doc_id = payload["knowledge_base"]["doc_id"]

        # Instantiate KBSeedInstructionAgent to use its database client
        seed_agent = KBSeedInstructionAgent(table_name=table_name, doc_id=doc_id)

        # Get all KB chunks using get_random_examples with 100% fraction
        all_chunks = seed_agent.fetch_random_seeds(percentage=100)

        # Extract chunk IDs from the results
        all_chunk_ids = []
        chunk_id_to_data = {}

        for chunk in all_chunks:
            id_, eval_id, vector, metadata_key, metadata_value, _ = chunk
            # Create a metadata dictionary for this chunk
            chunk_data = {}
            chunk_id = None
            for key, value in zip(metadata_key, metadata_value, strict=False):
                chunk_data[key] = value
                if key == "chunk_id":
                    chunk_id = value

            if chunk_id:
                all_chunk_ids.append(chunk_id)
                chunk_id_to_data[chunk_id] = chunk_data

        # Compute unused chunks
        unused_chunks = set(all_chunk_ids) - set(retrieved_chunk_ids)

        if not unused_chunks:
            return holistic_results

        # Prepare a dedicated plan for unused coverage
        plan_key = "kb_unused_coverage"
        if plan_key not in holistic_results["plans"]:
            holistic_results["plans"][plan_key] = {
                "plan_details": {"name": "KB Unused Coverage Plan"},
                "generated_data": {},
            }

        # Get the next ID for new samples
        existing_ids = []
        if holistic_results["plans"][plan_key]["generated_data"]:
            try:
                existing_ids = [
                    int(k)
                    for k in holistic_results["plans"][plan_key][
                        "generated_data"
                    ].keys()
                ]
            except Exception as e:
                logger.exception(f"Error converting existing IDs: {e}")
                existing_ids = [0]
        next_id = max(existing_ids) + 1 if existing_ids else 1

        # Process unused chunks in groups with aggregation (one sample per group)
        # Only process up to 20% of unused chunks
        max_chunks_to_process = int(len(unused_chunks) * 0.2)
        group_size = 5
        unused_chunks_list = list(unused_chunks)[:max_chunks_to_process]

        # Track newly generated datapoints
        new_datapoints_count = 0

        while unused_chunks_list:
            group = unused_chunks_list[:group_size]
            unused_chunks_list = unused_chunks_list[group_size:]

            # Gather datapoints for the group
            group_datapoints = []
            for chunk_id in group:
                if chunk_id in chunk_id_to_data:
                    group_datapoints.append(chunk_id_to_data[chunk_id])

            if not group_datapoints:
                logger.info(f"No valid datapoints found for group: {group}")
                continue
            datapoints_text = []
            for dp in group_datapoints:
                datapoints_text.append(dp.get("chunk_text", ""))
            # Create individual instructions for each chunk in the group
            instructions = {}
            for i, chunk_id in enumerate(group, 1):
                if chunk_id in chunk_id_to_data:
                    chunk_text = chunk_id_to_data[chunk_id].get(
                        "chunk_text", f"Unavailable text for chunk {chunk_id}"
                    )
                    instructions[str(i)] = {
                        "instruction": f"Generate synthetic data based on this KB chunk: {chunk_text}"
                    }

            # Create a plan for this group
            plan_details = {
                "Skill": "KB Unused Coverage",
                "Characteristic": "Unused",
                "name": f"unused_plan_{group[0]}",
                "GROUNDING INSTRUCTION": "STRICTLY use the provided domain knowledge from the knowledge base. Do NOT introduce information or assumptions outside the provided data points. If any required information is not explicitly in the provided datapoints, do NOT attempt to generate it. There should be **ZERO** hallucinations. (You are allowed to do domain expansion for generating a datapoint. For example: if we know from supplied domain knowledge that the context is about a restaurant, we will generate restaurant names in name column unless clearly specified). ",
                "Datapoints": datapoints_text,
            }

            # Fetch few-shot examples for each instruction
            few_shots_per_datapoint = seed_agent.fetch_few_shots_per_datapoint(
                instructions, plan_details, 4
            )

            # Enhance each instruction with its few-shot examples
            for i, chunk_id in enumerate(group, 1):
                if (
                    str(i) in few_shots_per_datapoint
                    and few_shots_per_datapoint[str(i)]
                ):
                    few_shots = few_shots_per_datapoint[str(i)]
                    few_shots_text = "\n\nSimilar examples:\n" + "\n".join(
                        [str(fs) for fs in few_shots]
                    )
                    instructions[str(i)]["instruction"] += few_shots_text

            # Aggregate the enhanced instructions into a single plan instruction for the group
            aggregated_instruction = (
                "Generate synthetic data for the following KB chunks:\n\n"
            )
            for i in sorted(instructions.keys(), key=lambda x: int(x)):
                aggregated_instruction += (
                    f"Chunk {i}: {instructions[i]['instruction']}\n\n"
                )

            # Generate synthetic data for the aggregated group
            seed_val = random.randint(0, 1000)
            new_generated = self._generate_data_for_plan(
                aggregated_instruction, payload, dynamic_batch_size=1, seed=seed_val
            )

            if new_generated:
                # Assume the generation returns a single aggregated sample.
                sample = (
                    new_generated.get("1", new_generated)
                    if isinstance(new_generated, dict)
                    else new_generated
                )
                sample["chunk_ids"] = group
                sample["original_plan_id"] = plan_key
                holistic_results["plans"][plan_key]["generated_data"][str(next_id)] = (
                    sample
                )
                next_id += 1
                new_datapoints_count += 1
                logger.info(f"Generated aggregated sample for unused chunks: {group}")
            else:
                logger.info(f"No sample generated for group: {group}")

            # Mark these chunks as now covered
            retrieved_chunk_ids.update(group)

        # Calculate the batch size from the payload
        batch_size = payload.get("batch_size", 10)

        # Calculate how many points we need to keep from original data
        points_to_keep = batch_size - new_datapoints_count

        # Collect all original datapoints
        all_original_datapoints = []
        for plan_id, plan_data in original_results["plans"].items():
            for data_id, _data_point in plan_data.get("generated_data", {}).items():
                all_original_datapoints.append((plan_id, data_id))

        # Randomly select points to keep from original data
        if all_original_datapoints:
            to_keep = random.sample(
                all_original_datapoints,
                min(points_to_keep, len(all_original_datapoints)),
            )

            # Create new holistic_results with only kept points
            new_holistic_results = {"plans": {}}

            # First add the kept original points
            for plan_id, data_id in to_keep:
                if plan_id not in new_holistic_results["plans"]:
                    new_holistic_results["plans"][plan_id] = {
                        "plan_details": original_results["plans"][plan_id][
                            "plan_details"
                        ],
                        "generated_data": {},
                    }
                new_holistic_results["plans"][plan_id]["generated_data"][data_id] = (
                    original_results["plans"][plan_id]["generated_data"][data_id]
                )

            # Then add all new points from kb_unused_coverage
            if "kb_unused_coverage" in holistic_results["plans"]:
                new_holistic_results["plans"]["kb_unused_coverage"] = holistic_results[
                    "plans"
                ]["kb_unused_coverage"]

            holistic_results = new_holistic_results

        return holistic_results

    def _generate_data_for_plan(self, plan_details, params, dynamic_batch_size, seed=0):
        """Generate data using a specific plan"""
        # Default assignment if no modifications are needed
        plan_details_copy = plan_details

        # Remove 'Datapoints' key if using a knowledge base
        if (
            isinstance(plan_details, dict)
            and "Datapoints" in plan_details
            and "knowledge_base" in params
        ):
            # Create a copy of plan_details to avoid modifying the original
            if isinstance(plan_details, dict):
                plan_details_copy = plan_details.copy()
                plan_details_copy.pop("Datapoints", None)
            else:
                plan_details_copy = plan_details

        generation_prompt = GENERATION_PROMPT.format(
            plan=json.dumps(plan_details_copy, indent=2),
            requirements=params["requirements"],
            schema=json.dumps(params["schema"], indent=2),
            constraints=json.dumps(params["constraints"], indent=2),
            batch_size=dynamic_batch_size,
            seed=seed,
        )

        try:
            response = self.llm._get_completion_content(
                messages=[{"role": "user", "content": generation_prompt}]
            )

            # Try to fix common JSON issues before parsing
            response = response.strip()
            if not response.startswith("{"):
                # Try to find the first valid JSON object
                start_idx = response.find("{")
                if start_idx != -1:
                    response = response[start_idx:]
            if not response.endswith("}"):
                # Try to find the last valid JSON object
                end_idx = response.rfind("}")
                if end_idx != -1:
                    response = response[: end_idx + 1]

            try:
                return json.loads(response)
            except json.JSONDecodeError as e:
                logger.error(f"Initial JSON decode failed: {str(e)}")
                # Try to repair the JSON using the repair agent
                return self._fix_json_response_plans(response)

        except Exception as e:
            logger.exception(
                f"Error generating data for plan with seed {seed}: {str(e)}"
            )
            return None

    def _save_generated_data(self, data, filename, foldername):
        """Save generated data to a JSON file"""
        try:
            dir_path = "../data/"
            dir_path = os.path.join(dir_path, foldername)
            # Create the directory if it doesn't exist
            os.makedirs(dir_path, exist_ok=True)
            # Full path to the file
            file_path = os.path.join(dir_path, filename)
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2)
            return file_path
        except Exception as e:
            logger.exception(f"Error saving data to {filename}: {str(e)}")

    def _validate_all_data(self, all_generated_data, params):
        """Validate the complete generated dataset"""
        validation_prompt = VALIDATION_PROMPT.format(
            **params,
            generated_data=json.dumps(all_generated_data, indent=2),
            quality_thresholds=json.dumps(self.quality_thresholds, indent=2),
        )

        try:
            validation_response = self.llm._get_completion_content(
                messages=[{"role": "user", "content": validation_prompt}]
            )
            return json.loads(validation_response)
        except json.JSONDecodeError:
            return self._fix_json_response(validation_response)
