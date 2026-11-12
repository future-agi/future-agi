"""
Add eval_type field to EvalTemplate.

Stores the evaluator type: "agent", "llm", or "code".
Previously this was derived from eval_tags which mixed category tags
with type indicators. This field provides clean separation.

Migration also backfills existing records based on config.eval_type_id
and eval_tags.
"""

from django.db import migrations, models


def backfill_eval_type(apps, schema_editor):
    """
    Backfill eval_type from existing config.eval_type_id and eval_tags.
    """
    EvalTemplate = apps.get_model("model_hub", "EvalTemplate")

    # Known code eval type IDs (FunctionEvalTypeId members)
    CODE_EVAL_IDS = {
        "CustomCodeEval", "Regex", "Contains", "ContainsAll", "ContainsAny",
        "ContainsNone", "Equals", "StartsWith", "EndsWith", "IsJson",
        "IsEmail", "ContainsLink", "ContainsValidLink", "NoInvalidLinks",
        "ContainsJson", "ContainsEmail", "LengthLessThan", "LengthGreaterThan",
        "LengthBetween", "OneLineLessThan", "JsonSchema", "JsonValidation",
        "ApiCall", "SafeForWorkText", "NotGibberishText",
        "OpenaiContentModeration", "PiiDetection", "PromptInjection",
        "ProfanityFree", "RougeScore", "BleuScore", "FidScore", "ClipScore",
        "RecallScore", "RecallAtK", "PrecisionAtK", "NdcgAtK", "Mrr",
        "HitRate", "LevenshteinSimilarity", "NumericSimilarity",
        "EmbeddingSimilarity", "SemanticListContains",
    }
    AGENT_TAGS = {"agent", "agentic", "agent_eval"}

    for template in EvalTemplate.objects.using(schema_editor.connection.alias).all().iterator(chunk_size=500):
        eval_type_id = (template.config or {}).get("eval_type_id", "")
        tags = {t.lower() for t in (template.eval_tags or [])}

        if tags & AGENT_TAGS or eval_type_id == "AgentEvaluator":
            template.eval_type = "agent"
        elif eval_type_id in CODE_EVAL_IDS:
            template.eval_type = "code"
        else:
            template.eval_type = "llm"

        template.save(using=schema_editor.connection.alias, update_fields=["eval_type"])


class Migration(migrations.Migration):

    dependencies = [
        ("model_hub", "0076_add_eval_summary_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="evaltemplate",
            name="eval_type",
            field=models.CharField(
                choices=[("agent", "Agent"), ("llm", "LLM"), ("code", "Code")],
                default="llm",
                help_text="Evaluator type: agent (Falcon AI powered), llm (LLM-as-a-judge), code (custom code)",
                max_length=10,
            ),
        ),
        migrations.RunPython(backfill_eval_type, migrations.RunPython.noop),
    ]
