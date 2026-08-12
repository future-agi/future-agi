import json
import traceback

import numpy as np
import pandas as pd
from sklearn.cluster import HDBSCAN
import traceback

from tenacity import retry, stop_after_attempt, wait_exponential
from tqdm import tqdm

from ee.agenthub.explanation_agent.prompts import (
    CLUSTER_PATTERN_PROMPT,
    ANALYSIS_PROMPT,
    SYNTHESIS_PROMPT,
    CRITIC_PROMPT,
)
from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager
from agentic_eval.core.embeddings.embeddings_v2 import get_embedding_model
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
import structlog

logger = structlog.get_logger(__name__)

import json
import re
import ast
from typing import List, Literal, TypedDict, Union, Any


class ExplanationAgent:
    def __init__(
        self,
        model_name=ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name,
        temperature=ModelConfigs.VERTEX_GEMINI_2_5_PRO.temperature,
        max_tokens=ModelConfigs.VERTEX_GEMINI_2_5_PRO.max_tokens,
        provider=ModelConfigs.VERTEX_GEMINI_2_5_PRO.provider,
        llm=None,
    ):
        self.llm = (
            llm
            if llm
            else LLM(
                model_name=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                provider=provider,
                api_key=None,
            )
        )
        embedding_manager = EmbeddingManager()

        self.embedding_model = embedding_manager.get_syn_embedding()

    @retry(
        stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=1, max=20)
    )
    def _call_llm(self, prompt, llm=None):
        llm = llm if llm else self.llm
        content = [
            {"type": "text", "text": prompt},
        ]
        messages = [{"role": "user", "content": content}]
        response = llm._get_completion_content(messages=messages, model=llm.model_name)
        return response

    def _embed(self, texts):
        model = self.embedding_model
        if texts:  # batch-encode only the unknown ones
            if get_embedding_model(input_type="check_serving"):
                # For serving client function
                # get vectors from the model in batches of 10
                try:
                    vecs = []
                    for i in range(0, len(texts), 10):
                        batch = texts[i : i + 10]
                        vecs.extend(model(batch))
                except Exception:
                    traceback.print_exc()

            else:
                # For local model
                # vecs = self.embed_texts(texts)
                raise NotImplementedError("Local embedding not implemented")
        return np.stack(list(vecs))

    # --------------------------
    # Simple HDBSCAN (bigger clusters, no tuning)
    # --------------------------

    def choose_min_cluster_size(self, n: int, pct: float = 0.08, floor: int = 3) -> int:
        """
        Choose min_cluster_size as max(floor, round(n * pct)), at least 2 and at most n.
        Examples: n=50 -> 5; n=500 -> 40; n=30 -> 5.
        Increase pct (e.g., 0.10-0.15) for fewer/larger clusters.
        """
        mcs = max(2, int(round(n * pct)))
        mcs = max(mcs, floor)
        return min(mcs, n)

    def prune_small_clusters(
        self, labels: np.ndarray, min_final_size: int = 5
    ) -> np.ndarray:
        """Relabel clusters smaller than min_final_size to noise (-1)."""
        lab = labels.copy()
        for c in np.unique(lab):
            if c == -1:
                continue
            if (lab == c).sum() < min_final_size:
                lab[lab == c] = -1
        return lab

    def simple_hdbscan(
        self,
        X: np.ndarray,
        pct: float = 0.08,
        floor: int = 3,
        min_samples: int = 1,
        min_final_size: int | None = 3,
    ):
        """
        One-shot HDBSCAN configured for larger, fewer clusters:
        - cosine distance
        - EOM selection (coarser)
        - min_cluster_size based on % of n
        - optional pruning of tiny clusters
        """
        n = len(X)
        mcs = self.choose_min_cluster_size(n, pct=pct, floor=floor)
        clusterer = HDBSCAN(
            min_cluster_size=mcs,
            min_samples=min_samples,  # ↑ for stricter density → fewer clusters
            metric="cosine",
            cluster_selection_method="eom",  # coarser than 'leaf'
            allow_single_cluster=True,
        )
        labels = clusterer.fit_predict(X)
        if min_final_size is not None and min_final_size > 1:
            labels = self.prune_small_clusters(labels, min_final_size=min_final_size)
        return labels, clusterer, {"min_cluster_size": mcs, "min_samples": min_samples}

    # --------------------------
    # Representatives & LLM
    # --------------------------

    def representative_indices(
        self, X: np.ndarray, labels: np.ndarray, per_cluster: int = 10
    ) -> dict[int, list[int]]:
        """
        For each cluster, pick representative points closest to the centroid.
        If cluster size < per_cluster, return all members.
        """
        reps: dict[int, list[int]] = {}
        for c in sorted([l for l in np.unique(labels) if l != -1]):
            idx = np.where(labels == c)[0]
            if len(idx) <= per_cluster:
                reps[c] = idx.tolist()
                continue
            Xc = X[idx]
            centroid = Xc.mean(axis=0, keepdims=True)
            d = ((Xc - centroid) ** 2).sum(axis=1)
            order = np.argsort(d)
            reps[c] = idx[order[:per_cluster]].tolist()
        return reps

    class ClusterSummary(TypedDict, total=False):
        theme: str
        status: str
        # derived from shape: "failure" = has issues, "success" = has capabilities,
        # "none" = fallback / no clear pattern
        kind: Literal["failure", "success", "none"]

        # FAILURE-shape fields
        issues: List[str]
        triggers: List[str]

        # SUCCESS-shape fields
        capabilities: List[str]
        application_contexts: List[str]

        # Shared fields
        evidence_summary: str
        guidance: str
        confidence: Literal["low", "medium", "high"]

    def _normalize_cluster(self, obj: dict) -> ClusterSummary:
        theme = str(obj.get("theme", "")).strip()
        status = str(obj.get("status", "")).strip()

        # Normalise list-like fields
        def _norm_list(val):
            if isinstance(val, str):
                val = val.strip()
                return [val] if val else []
            if val is None:
                return []
            return [str(v).strip() for v in val if str(v).strip()]

        issues = _norm_list(obj.get("issues"))
        capabilities = _norm_list(obj.get("capabilities"))
        application_contexts = _norm_list(obj.get("application_contexts"))
        triggers = _norm_list(obj.get("triggers"))

        evidence = str(obj.get("evidence_summary", "")).strip()
        guidance = str(obj.get("guidance", "")).strip()
        confidence = str(obj.get("confidence", "medium")).lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = "medium"

        # Infer `kind` from the shape (FAILURE vs SUCCESS vs fallback)
        if capabilities and not issues:
            kind: Literal["failure", "success", "none"] = "success"
        elif issues and not capabilities:
            kind = "failure"
        else:
            # Either fallback JSON (no issues/capabilities) or malformed mix
            kind = "none"

        cluster: ExplanationAgent.ClusterSummary = {
            "theme": theme,
            "status": status,
            "kind": kind,
            "triggers": triggers,
            "evidence_summary": evidence,
            "guidance": guidance,
            "confidence": confidence,
        }

        if issues:
            cluster["issues"] = issues
        if capabilities:
            cluster["capabilities"] = capabilities
        if application_contexts:
            cluster["application_contexts"] = application_contexts

        return cluster

    def _extract_json_text(self, raw: str) -> str:
        # 1) If fenced, grab inside ``` ```
        fence_match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S | re.I)
        if fence_match:
            candidate = fence_match.group(1).strip()
        else:
            candidate = raw.strip()

        # 2) Take first {...} block
        obj_match = re.search(r"\{.*\}", candidate, re.S)
        if not obj_match:
            raise ValueError("No JSON object found in LLM output")

        return obj_match.group(0)

    def _try_json(self, text: str) -> Any | None:
        try:
            return json.loads(text)
        except Exception:
            return None

    def _try_python_dict(self, text: str) -> Any | None:
        # Handle np.int64(0) → 0 etc.
        cleaned = re.sub(r"np\.int64\((\d+)\)", r"\1", text)
        try:
            return ast.literal_eval(cleaned)
        except Exception:
            return None

    def parse_cluster_response(self, resp: Union[str, dict]) -> ClusterSummary:
        """
        Accepts:
        - plain JSON string
        - JSON-with-fences string
        - python-dict-like string: "{'cluster_id': np.int64(0), 'raw': '...'}"
        - dict with cluster_id/raw
        - dict already in the desired shape
        """

        # Case 1: already a dict (e.g. if your client lib parsed it)
        if isinstance(resp, dict):
            # If it's the "wrapper" dict
            if "raw" in resp and any(
                k in resp for k in ("cluster_id", "cluster", "id")
            ):
                return self.parse_cluster_response(
                    resp["raw"]
                )  # recurse on inner content

            # If it looks like the final JSON (either failure or success shape)
            if {"theme", "status"} <= resp.keys():
                return self._normalize_cluster(resp)

            # Otherwise, just try to normalize whatever is there
            return self._normalize_cluster(resp)

        # Case 2: string
        raw = resp.strip()

        # 2a) maybe it's clean JSON
        obj = self._try_json(raw)
        if isinstance(obj, dict):
            # could be inner dict or wrapper dict
            if "raw" in obj and any(k in obj for k in ("cluster_id", "cluster", "id")):
                return self.parse_cluster_response(obj["raw"])
            return self._normalize_cluster(obj)

        # 2b) maybe it's a python dict string with np.int64
        obj = self._try_python_dict(raw)
        if isinstance(obj, dict):
            if "raw" in obj and any(k in obj for k in ("cluster_id", "cluster", "id")):
                return self.parse_cluster_response(obj["raw"])
            if {"theme", "status"} <= obj.keys():
                return self._normalize_cluster(obj)

        # 2c) last resort: strip fences / extra text and JSON-parse
        json_text = self._extract_json_text(raw)
        obj = json.loads(json_text)
        return self._normalize_cluster(obj)

    def _chain_analysis(
        self, reps_texts: list[str], dimension_name: str = "unknown_metric"
    ) -> str:
        sample_block = "\n".join(f"- {t}" for t in reps_texts)

        # Step 1: Analyst
        analysis_prompt = ANALYSIS_PROMPT.format(
            samples=sample_block, dimension_name=dimension_name
        )
        analysis_out = self._call_llm(analysis_prompt)

        # Step 2: Strategist
        synthesis_prompt = SYNTHESIS_PROMPT.format(analysis=analysis_out)
        synthesis_out = self._call_llm(synthesis_prompt)

        # Step 3: Critic
        critic_prompt = CRITIC_PROMPT.format(draft=synthesis_out, samples=sample_block)
        final_json = self._call_llm(critic_prompt)

        return final_json

    def summarize_cluster(
        self,
        cluster_id: int,
        reps_texts: list[str],
        use_chain_analysis=True,
        dimension_name: str = "unknown_metric",
    ) -> dict:
        if use_chain_analysis:
            raw = self._chain_analysis(reps_texts, dimension_name=dimension_name)
        else:
            sample_block = "\n".join(f"- {t}" for t in reps_texts)
            prompt = CLUSTER_PATTERN_PROMPT.format(
                size=len(reps_texts), samples=sample_block
            )
            raw = self._call_llm(prompt)
        try:
            return self.parse_cluster_response(raw)
        except Exception:
            logger.warning(f"Failed to parse cluster {cluster_id} summary: {raw}")
            traceback.print_exc()
            return {"cluster_id": cluster_id, "raw": raw}

    # --------------------------
    # Orchestration
    # --------------------------

    def evaluate(
        self,
        explanation: list[Any],
        eval_name: str = None,
        eval_criteria: str = None,
        eval_values: list[str] = None,
        per_cluster_samples: int = 10,
        pct: float = 0.08,  # 8% of n for min_cluster_size
        floor: int = 5,  # at least 5 per cluster on small datasets
        min_samples: int = 2,  # density strictness: 1 (loose) .. 3+ (strict)
        min_final_size: int = 5,  # prune clusters smaller than this
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        use_chain_analysis=True,
    ):
        # check if explanation list is empty
        if not explanation:
            logger.error(
                "Explanation list is empty. Cannot run critical issues evaluation."
            )
            return []

        # Normalize explanations into parallel lists of texts and IDs.
        # Supports both legacy `list[str]` and new `list[dict]` shapes that
        # carry a call/explanation identifier.
        texts: list[str] = []
        explanation_ids: list[str] = []

        for idx, item in enumerate(explanation):
            text_val = None
            item_id = None

            if isinstance(item, dict):
                # Prefer explicit text fields; fall back to common reason keys
                text_val = (
                    item.get("text")
                    or item.get("eval_reason")
                    or item.get("reason")
                    or item.get("explanation")
                )

                # Try to capture a stable identifier for this explanation
                item_id = (
                    item.get("id")
                    or item.get("call_execution_id")
                    or item.get("callExecutionId")
                    or item.get("call_id")
                )
            else:
                text_val = str(item)

            if not text_val or not str(text_val).strip():
                continue

            texts.append(str(text_val))
            explanation_ids.append(str(item_id) if item_id is not None else str(idx))

        if len(texts) < 5:
            logger.error(
                "Explanation list is too short after normalization. "
                "Cannot run critical issues evaluation."
            )
            return []

        print(f"[1/5] Embedding {len(texts)} explanations...")
        X = self._embed(texts)
        print("[2/5] Clustering (HDBSCAN)...")
        labels, model, meta = self.simple_hdbscan(
            X,
            pct=pct,
            floor=floor,
            min_samples=min_samples,
            min_final_size=min_final_size,
        )
        print(f"Chosen: mcs={meta['min_cluster_size']}, ms={meta['min_samples']}")
        print("Cluster label counts (noise=-1 included):")
        print(pd.Series(labels).value_counts().sort_index())

        print("[3/5] Selecting representative explanations per cluster...")
        reps_idx = self.representative_indices(
            X, labels, per_cluster=per_cluster_samples
        )
        print(f"Found {len(reps_idx)} non-noise clusters with representatives.")

        # Map cluster_id -> all member explanation IDs (non-noise only)
        cluster_member_ids: dict[int, list[str]] = {}
        for label, expl_id in zip(labels, explanation_ids):
            if label == -1:
                continue
            cid = int(label)
            cluster_member_ids.setdefault(cid, []).append(expl_id)

        print("[4/5] Summarizing clusters with LLM...")
        summaries = []
        for c, idxs in tqdm(reps_idx.items(), desc="Clusters"):
            reps_texts = [texts[i] for i in idxs]
            s = self.summarize_cluster(
                c,
                reps_texts,
                use_chain_analysis,
                dimension_name=eval_name,
            )
            s["cluster_id"] = int(c)
            s["size"] = int((labels == c).sum())
            # Attach member IDs for traceability back to calls/explanations
            # Each entry in memberIds is a call_execution_id whose eval_reason was clustered into that specific theme.
            s["member_ids"] = cluster_member_ids.get(int(c), [])
            # representativeIds is the subset of those call IDs that were used as representative examples when summarizing the cluster.
            s["representative_ids"] = [explanation_ids[i] for i in idxs]
            if eval_name:
                s["eval_name"] = eval_name if eval_name else ""
            if s:
                summaries.append(s)

        single_list_of_clusters: list[ExplanationAgent.ClusterSummary] = []

        for s in summaries:
            theme_lower = str(s.get("theme", "")).lower()
            status_lower = str(s.get("status", "")).lower()

            # Drop fallback / rejected clusters
            if (
                "no dominant theme detected" in theme_lower
                or "no dominant pattern detected" in theme_lower
            ):
                continue
            if "rejected" in status_lower:
                continue

            # Ensure downstream consumers always see the full schema, even if fields are empty
            defaults = {
                "theme": "",
                "status": "",
                "kind": "none",
                "issues": [],
                "triggers": [],
                "capabilities": [],
                "application_contexts": [],
                "evidence_summary": "",
                "guidance": "",
                "confidence": "",
            }
            for key, default_val in defaults.items():
                if key not in s or s.get(key) is None:
                    s[key] = default_val

            kind = s.get("kind", "none")
            if kind in {"failure", "success"}:
                single_list_of_clusters.append(s)
            else:
                continue

        print("[5/5] Done.")
        return single_list_of_clusters
