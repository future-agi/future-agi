from ai_tools.registry import registry


class TestReadSchemaTool:
    def test_read_schema_evaluations(self, tool_context):
        tool = registry.get("read_schema")
        result = tool.run({"entity_type": "evaluations"}, tool_context)

        assert not result.is_error
        assert "Evaluation Schema" in result.content
        assert "status" in result.content
        assert "eval_template" in result.content

    def test_read_schema_datasets(self, tool_context):
        tool = registry.get("read_schema")
        result = tool.run({"entity_type": "datasets"}, tool_context)

        assert not result.is_error
        assert "Dataset Schema" in result.content
        assert "Column Schema" in result.content

    def test_read_schema_traces(self, tool_context):
        tool = registry.get("read_schema")
        result = tool.run({"entity_type": "traces"}, tool_context)

        assert not result.is_error
        assert "Trace Schema" in result.content
        assert "Span Schema" in result.content

    def test_read_schema_invalid_type(self, tool_context):
        tool = registry.get("read_schema")
        result = tool.run({"entity_type": "invalid"}, tool_context)

        assert result.is_error


class TestReadTaxonomyTool:
    def test_taxonomy_dataset_sources(self, tool_context):
        tool = registry.get("read_taxonomy")
        result = tool.run({"category": "dataset_sources"}, tool_context)

        assert not result.is_error
        assert "BUILD" in result.content
        assert "UPLOAD" in result.content
        assert "API" in result.content
        assert "SYNTHETIC" in result.content

    def test_taxonomy_trace_span_types(self, tool_context):
        tool = registry.get("read_taxonomy")
        result = tool.run({"category": "trace_span_types"}, tool_context)

        assert not result.is_error
        assert "llm" in result.content
        assert "tool" in result.content
        assert "agent" in result.content

    def test_taxonomy_model_types(self, tool_context):
        tool = registry.get("read_taxonomy")
        result = tool.run({"category": "model_types"}, tool_context)

        assert not result.is_error
        assert "GENERATIVE_LLM" in result.content

    def test_taxonomy_eval_types(self, tool_context):
        tool = registry.get("read_taxonomy")
        result = tool.run({"category": "eval_types"}, tool_context)

        # May have no templates in test db, but should not error
        assert not result.is_error

    def test_taxonomy_invalid_category(self, tool_context):
        tool = registry.get("read_taxonomy")
        result = tool.run({"category": "invalid"}, tool_context)

        assert result.is_error
