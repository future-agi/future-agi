import { execFile } from "node:child_process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  isUuid,
  requireMutations,
  skip,
} from "../lib/api-client.mjs";

const execFileAsync = promisify(execFile);

export const simulationAgentccJourneys = [
  {
    id: "SIM-API-001",
    title: "Simulation persona create, search, update, retrieve, and delete lifecycle",
    tags: ["simulation", "personas", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const name = `api journey persona ${runId}`;

      const created = await client.post(apiPath("/simulate/api/personas/"), {
        name,
        description: "Temporary persona for API journey regression.",
        gender: ["male"],
        age_group: ["25-32"],
        location: ["United States"],
        profession: ["Engineer"],
        personality: ["Friendly and cooperative"],
        communication_style: ["Direct and concise"],
        accent: ["american"],
        language: ["English"],
        conversation_speed: ["1.0"],
        background_sound: false,
        finished_speaking_sensitivity: ["5"],
        interrupt_sensitivity: ["5"],
        keywords: ["api-journey"],
        custom_properties: { source: "api-journey" },
        additional_instruction: "Answer concisely.",
        simulation_type: "voice",
      });
      assert(created?.id, "Persona create did not return id.");
      cleanup.defer("delete API journey persona", () =>
        ignoreNotFound(() =>
          client.delete(apiPath("/simulate/api/personas/{id}/", { id: created.id })),
        ),
      );

      const searched = asArray(
        await client.get(apiPath("/simulate/api/personas/"), {
          query: { search: name, page_size: 10 },
        }),
      );
      assert(
        searched.some((persona) => persona.id === created.id),
        "Created persona was not visible through list/search.",
      );

      const updated = await client.patch(
        apiPath("/simulate/api/personas/{id}/", { id: created.id }),
        {
          description: "Updated temporary persona for API journey regression.",
          keywords: ["api-journey", "updated"],
        },
      );
      assert(
        updated.description.includes("Updated temporary persona"),
        "Persona update did not persist description.",
      );
      assert(
        asArray(updated.keywords).includes("updated"),
        "Persona update did not persist keywords.",
      );

      const detail = await client.get(
        apiPath("/simulate/api/personas/{id}/", { id: created.id }),
      );
      assert(detail?.id === created.id, "Persona detail returned wrong id.");

      await client.delete(apiPath("/simulate/api/personas/{id}/", { id: created.id }));
      const afterDelete = asArray(
        await client.get(apiPath("/simulate/api/personas/"), {
          query: { search: name, page_size: 10 },
        }),
      );
      assert(
        !afterDelete.some((persona) => persona.id === created.id),
        "Deleted persona was still visible through list/search.",
      );

      evidence.push({ persona_id: created.id, persona_name: name });
    },
  },
  {
    id: "SIM-API-002",
    title: "Simulation agent definition operations create, update, retrieve, and delete lifecycle",
    tags: ["simulation", "agents", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const name = `api journey agent ${runId}`;

      const created = await client.post(
        apiPath("/simulate/api/agent-definition-operations/"),
        {
          agent_name: name,
          agent_type: "text",
          inbound: true,
          description: "Temporary agent definition for API journey regression.",
          provider: "others",
          language: "en",
          languages: ["en"],
          authentication_method: "api_key",
          model: "gpt-4o-mini",
          model_details: { source: "api-journey" },
        },
      );
      assert(created?.id, "Agent definition create did not return id.");
      cleanup.defer("delete API journey agent definition", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/simulate/api/agent-definition-operations/{id}/", {
              id: created.id,
            }),
          ),
        ),
      );

      const updated = await client.patch(
        apiPath("/simulate/api/agent-definition-operations/{id}/", {
          id: created.id,
        }),
        {
          description: "Updated temporary agent definition for API journey regression.",
          model_details: { source: "api-journey", updated: true },
        },
      );
      assert(
        updated.description.includes("Updated temporary agent"),
        "Agent definition update did not persist description.",
      );
      assert(
        updated.model_details?.updated === true,
        "Agent definition update did not persist model_details.",
      );

      const detail = await client.get(
        apiPath("/simulate/api/agent-definition-operations/{id}/", {
          id: created.id,
        }),
      );
      assert(detail?.id === created.id, "Agent definition detail returned wrong id.");
      assert(detail?.agent_name === name, "Agent definition detail returned wrong name.");

      await client.delete(
        apiPath("/simulate/api/agent-definition-operations/{id}/", {
          id: created.id,
        }),
      );
      const listed = asArray(
        await client.get(apiPath("/simulate/api/agent-definition-operations/"), {
          query: { search: name, limit: 10 },
        }),
      );
      assert(
        !listed.some((agent) => agent.id === created.id),
        "Deleted agent definition was still visible through list/search.",
      );

      evidence.push({ agent_definition_id: created.id, agent_name: name });
    },
  },
  {
    id: "SIM-API-003",
    title: "Simulation run, execution, call, transcript, and SDK read surfaces",
    tags: [
      "simulation",
      "run-tests",
      "test-executions",
      "call-executions",
      "safe",
      "db-audit",
      "security",
    ],
    async run({ client, evidence, organizationId, workspaceId }) {
      const [
        runTestsPayload,
        apiRunTestsPayload,
        apiTestExecutionsPayload,
        apiCallExecutionsPayload,
      ] = await Promise.all([
        client.get(apiPath("/simulate/run-tests/"), {
          query: { limit: 5, page: 1 },
        }),
        client.get(apiPath("/simulate/api/run-tests/"), {
          query: { limit: 5, page: 1 },
        }),
        client.get(apiPath("/simulate/api/test-executions/"), {
          query: { limit: 5, page: 1 },
        }),
        client.get(apiPath("/simulate/api/call-executions/"), {
          query: { limit: 5, page: 1 },
        }),
      ]);

      const runTests = collectionRows(runTestsPayload);
      const apiRunTests = collectionRows(apiRunTestsPayload);
      const apiTestExecutions = collectionRows(apiTestExecutionsPayload);
      const apiCallExecutions = collectionRows(apiCallExecutionsPayload);
      assert(Array.isArray(runTests), "Simulation run-test list was not array-like.");
      assert(
        Array.isArray(apiRunTests),
        "Simulation API run-test list was not array-like.",
      );
      assert(
        Array.isArray(apiTestExecutions),
        "Simulation API test-execution list was not array-like.",
      );
      assert(
        Array.isArray(apiCallExecutions),
        "Simulation API call-execution list was not array-like.",
      );

      const runFromExecution = apiTestExecutions.find((row) =>
        isUuid(row?.run_test),
      );
      const runTest =
        runTests.find((row) => row?.id === runFromExecution?.run_test) ||
        apiRunTests.find((row) => row?.id === runFromExecution?.run_test) ||
        runTests.find((row) => isUuid(row?.id)) ||
        apiRunTests.find((row) => isUuid(row?.id));
      if (!runTest) skip("No simulation run tests found for read-surface coverage.");

      const runTestId = runTest.id;
      assert(isUuid(runTestId), "Selected simulation run test id was not a UUID.");
      if (apiRunTests.length > 0) {
        assert(
          apiRunTests.some((row) => row.id === runTestId) ||
            runTests.some((row) => row.id === runTestId),
          "Simulation run-test API lists did not expose the selected run.",
        );
      }

      const dbAudit = await loadSimulationRunDbAudit(
        runTestId,
        organizationId,
        workspaceId,
      );
      assert(
        dbAudit.run_test_id === runTestId,
        "Simulation DB audit returned a different run test id.",
      );

      const detail = await client.get(
        apiPath("/simulate/run-tests/{run_test_id}/", {
          run_test_id: runTestId,
        }),
      );
      assert(detail?.id === runTestId, "Run-test detail returned the wrong id.");
      const runTestName = detail.name || runTest.name;
      assert(runTestName, "Run-test detail did not include a name.");

      const nameLookup = await client.get(
        apiPath("/simulate/run-tests/get-id-by-name/{run_test_name}/", {
          run_test_name: runTestName,
        }),
      );
      assert(
        nameLookup?.run_test_id === runTestId,
        "Run-test name lookup did not return the selected run.",
      );

      const statusPayload = await client.get(
        apiPath("/simulate/run-tests/{run_test_id}/status/", {
          run_test_id: runTestId,
        }),
      );
      assert(
        statusPayload?.run_test_id === runTestId,
        "Run-test status returned the wrong run_test_id.",
      );

      const analytics = await client.get(
        apiPath("/simulate/run-tests/{run_test_id}/analytics/", {
          run_test_id: runTestId,
        }),
      );
      assert(
        analytics?.run_test_info && analytics?.summary_stats,
        "Run-test analytics did not include run_test_info/summary_stats.",
      );

      const [
        runCallsPayload,
        executionsPayload,
        scenariosPayload,
        sdkPayload,
        evalSummary,
      ] = await Promise.all([
        client.get(
          apiPath("/simulate/run-tests/{run_test_id}/call-executions/", {
            run_test_id: runTestId,
          }),
          { query: { limit: 5, page: 1 } },
        ),
        client.get(
          apiPath("/simulate/run-tests/{run_test_id}/executions/", {
            run_test_id: runTestId,
          }),
          { query: { limit: 5, page: 1 } },
        ),
        client.get(
          apiPath("/simulate/run-tests/{run_test_id}/scenarios/", {
            run_test_id: runTestId,
          }),
        ),
        client.get(
          apiPath("/simulate/run-tests/{run_test_id}/sdk-code/", {
            run_test_id: runTestId,
          }),
        ),
        client.get(
          apiPath("/simulate/run-tests/{run_test_id}/eval-summary/", {
            run_test_id: runTestId,
          }),
        ),
      ]);

      const runCalls = collectionRows(runCallsPayload);
      const executions = collectionRows(executionsPayload);
      const scenarios = collectionRows(scenariosPayload);
      assert(Array.isArray(runCalls), "Run-test call executions were not array-like.");
      assert(Array.isArray(executions), "Run-test executions were not array-like.");
      assert(Array.isArray(scenarios), "Run-test scenarios were not array-like.");
      assert(
        Array.isArray(asArray(evalSummary)),
        "Run-test eval summary was not array-like.",
      );
      assert(
        Number(dbAudit.test_execution_count) >= executions.length,
        "DB test-execution count was lower than the API execution page.",
      );
      assert(
        Number(dbAudit.call_execution_count) >= runCalls.length,
        "DB call-execution count was lower than the API call page.",
      );
      assertSimulationSdkCodeSafe(sdkPayload);
      assert(
        sdkPayload.run_test_id === runTestId && sdkPayload.run_test_name === runTestName,
        "Run-test SDK payload returned the wrong run id/name.",
      );

      const testExecutionId =
        firstUuid(statusPayload.execution_id) ||
        firstUuid(executions.find((row) => isUuid(row?.id))?.id) ||
        firstUuid(
          apiTestExecutions.find((row) => row?.run_test === runTestId)?.id,
        );
      assert(
        !testExecutionId || dbAudit.test_execution_ids.includes(testExecutionId),
        "Selected test execution was not present in the DB audit.",
      );

      let testDetail = null;
      let testTranscripts = null;
      let testKpis = null;
      if (testExecutionId) {
        testDetail = await client.get(
          apiPath("/simulate/test-executions/{test_execution_id}/", {
            test_execution_id: testExecutionId,
          }),
        );
        assert(
          collectionRows(testDetail).length >= 0 || testDetail?.status,
          "Test-execution detail did not return a detail/list shape.",
        );

        const [
          testAnalytics,
          kpis,
          performance,
          transcripts,
          explanation,
          optimiser,
          comparison,
        ] = await Promise.all([
          client.get(
            apiPath("/simulate/test-executions/{test_execution_id}/analytics/", {
              test_execution_id: testExecutionId,
            }),
          ),
          client.get(
            apiPath("/simulate/test-executions/{test_execution_id}/kpis/", {
              test_execution_id: testExecutionId,
            }),
          ),
          client.get(
            apiPath(
              "/simulate/test-executions/{test_execution_id}/performance-summary/",
              { test_execution_id: testExecutionId },
            ),
          ),
          client.get(
            apiPath("/simulate/test-executions/{test_execution_id}/transcripts/", {
              test_execution_id: testExecutionId,
            }),
          ),
          client.get(
            apiPath(
              "/simulate/test-executions/{test_execution_id}/eval-explanation-summary/",
              { test_execution_id: testExecutionId },
            ),
          ),
          client.get(
            apiPath(
              "/simulate/test-executions/{test_execution_id}/optimiser-analysis/",
              { test_execution_id: testExecutionId },
            ),
          ),
          client.get(
            apiPath(
              "/simulate/run-tests/{run_test_id}/eval-summary-comparison/",
              { run_test_id: runTestId },
            ),
            { query: { execution_ids: JSON.stringify([testExecutionId]) } },
          ),
        ]);
        assert(
          testAnalytics?.metadata,
          "Test-execution analytics did not include metadata.",
        );
        assert(
          typeof kpis?.total_calls === "number",
          "Test-execution KPIs did not include total_calls.",
        );
        assert(
          performance?.test_run_performance_metrics,
          "Test-execution performance summary was missing metrics.",
        );
        assert(
          transcripts?.test_execution_id === testExecutionId,
          "Test-execution transcripts returned the wrong execution id.",
        );
        assert(
          Array.isArray(transcripts?.calls),
          "Test-execution transcripts did not include calls.",
        );
        assert(
          Object.prototype.hasOwnProperty.call(explanation, "status"),
          "Eval explanation summary did not include status.",
        );
        assert(
          Object.prototype.hasOwnProperty.call(optimiser, "status"),
          "Optimiser analysis did not include status.",
        );
        assert(
          Array.isArray(asArray(comparison)) || typeof comparison === "object",
          "Eval-summary comparison did not return an object/array shape.",
        );
        testTranscripts = transcripts;
        testKpis = kpis;
      }

      const callExecution =
        runCalls.find((row) => isUuid(row?.id)) ||
        apiCallExecutions.find((row) => isUuid(row?.id));
      if (!callExecution) {
        evidence.push({
          run_test_id: runTestId,
          run_test_name: runTestName,
          test_execution_id: testExecutionId || null,
          db_test_executions: Number(dbAudit.test_execution_count),
          db_call_executions: Number(dbAudit.call_execution_count),
          note: "No call execution was available for call detail readback.",
        });
        return;
      }

      const callExecutionId = callExecution.id;
      assert(
        dbAudit.call_execution_ids.includes(callExecutionId) ||
          apiCallExecutions.some((row) => row.id === callExecutionId),
        "Selected call execution was not present in the DB audit or global API list.",
      );

      const [callDetail, callTranscripts, callLogs, errorTasks, branchAnalysis] =
        await Promise.all([
          client.get(
            apiPath("/simulate/call-executions/{call_execution_id}/", {
              call_execution_id: callExecutionId,
            }),
          ),
          client.get(
            apiPath("/simulate/call-executions/{call_execution_id}/transcripts/", {
              call_execution_id: callExecutionId,
            }),
          ),
          client.get(
            apiPath("/simulate/call-executions/{call_execution_id}/logs/", {
              call_execution_id: callExecutionId,
            }),
            { query: { limit: 5, page: 1 } },
          ),
          client.get(
            apiPath(
              "/simulate/call-executions/{call_execution_id}/error-localizer-tasks/",
              { call_execution_id: callExecutionId },
            ),
          ),
          client.get(
            apiPath("/simulate/call-executions/{call_execution_id}/branch-analysis/", {
              call_execution_id: callExecutionId,
            }),
          ),
        ]);

      assert(
        callDetail?.id === callExecutionId,
        "Call-execution detail returned the wrong id.",
      );
      assert(
        callTranscripts?.call_execution_id === callExecutionId,
        "Call-execution transcripts returned the wrong call id.",
      );
      assert(
        Array.isArray(callTranscripts?.transcripts),
        "Call-execution transcripts did not include transcripts.",
      );
      assert(
        Array.isArray(asArray(callLogs)),
        "Call-execution logs were not array-like.",
      );
      assert(
        Array.isArray(errorTasks?.error_localizer_tasks),
        "Call-execution error-localizer tasks did not include an array.",
      );
      assert(
        branchAnalysis?.call_execution_id === callExecutionId,
        "Call-execution branch analysis returned the wrong call id.",
      );

      evidence.push({
        run_test_id: runTestId,
        run_test_name: runTestName,
        test_execution_id: testExecutionId || null,
        call_execution_id: callExecutionId,
        scenarios: scenarios.length,
        run_call_rows: runCalls.length,
        api_test_execution_rows: apiTestExecutions.length,
        api_call_execution_rows: apiCallExecutions.length,
        db_test_executions: Number(dbAudit.test_execution_count),
        db_call_executions: Number(dbAudit.call_execution_count),
        db_transcripts: Number(dbAudit.transcript_count),
        test_total_calls: testKpis?.total_calls ?? null,
        test_transcript_calls: testTranscripts?.total_calls ?? null,
        call_transcripts: callTranscripts.total_transcripts,
        sdk_code_length: String(sdkPayload.sdk_code || "").length,
      });
    },
  },
  {
    id: "SIM-API-004",
    title:
      "Simulation run-test create, chat execution setup, status guards, and cleanup",
    tags: [
      "simulation",
      "run-tests",
      "test-executions",
      "call-executions",
      "mutating",
      "data-roundtrip",
      "db-audit",
      "security",
    ],
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
      requireMutations();

      const seed = await selectSimulationRunTestSeed(client);
      if (!seed) {
        skip(
          "No completed text simulation seed with an agent definition, version, and runnable scenario was available.",
        );
      }

      const runName = `api journey sim lifecycle ${runId}`;
      const createdRunIds = [];
      let testExecutionId = null;
      let callExecutionId = null;

      cleanup.defer("delete disposable simulation run tests", async () => {
        for (const runTestId of createdRunIds) {
          await ignoreNotFound(() =>
            client.delete(
              apiPath("/simulate/run-tests/{run_test_id}/delete/", {
                run_test_id: runTestId,
              }),
            ),
          );
        }
      });
      cleanup.defer("delete disposable simulation test execution", async () => {
        if (!testExecutionId) return;
        await ignoreNotFound(() =>
          client.delete(
            apiPath("/simulate/test-executions/{test_execution_id}/delete/", {
              test_execution_id: testExecutionId,
            }),
          ),
        );
      });
      cleanup.defer("delete disposable simulation call execution", async () => {
        if (!callExecutionId) return;
        await ignoreNotFound(() =>
          client.delete(
            apiPath("/simulate/call-executions/{call_execution_id}/delete/", {
              call_execution_id: callExecutionId,
            }),
          ),
        );
      });

      const created = await client.post(apiPath("/simulate/run-tests/create/"), {
        name: runName,
        description: "Temporary run test for API journey lifecycle coverage.",
        agent_definition_id: seed.agentDefinitionId,
        agent_version: seed.agentVersionId,
        scenario_ids: [seed.scenarioId],
        enable_tool_evaluation: false,
      });
      assert(isUuid(created?.id), "Run-test create did not return a UUID id.");
      createdRunIds.push(created.id);
      assert(
        asArray(created.scenarios).map(String).includes(seed.scenarioId) ||
          asArray(created.scenarios_detail).some((scenario) => scenario.id === seed.scenarioId),
        "Run-test create did not attach the selected scenario.",
      );

      let dbAudit = await loadDisposableSimulationLifecycleDbAudit({
        runTestIds: [created.id],
        testExecutionId: created.id,
        organizationId,
        workspaceId,
      });
      const createdAudit = collectionRows(dbAudit.run_tests).find(
        (runTest) => runTest.id === created.id,
      );
      assert(
        createdAudit?.workspace_id === workspaceId,
        "Created run test did not persist the active workspace.",
      );
      assert(
        createdAudit?.organization_id === organizationId,
        "Created run test did not persist the active organization.",
      );

      const patched = await client.patch(
        apiPath("/simulate/run-tests/{run_test_id}/", { run_test_id: created.id }),
        {
          description: "Updated temporary run test for API journey lifecycle coverage.",
        },
      );
      assert(
        String(patched.description || "").includes("Updated temporary"),
        "Run-test PATCH did not persist description.",
      );

      const components = await client.patch(
        apiPath("/simulate/run-tests/{run_test_id}/components/", {
          run_test_id: created.id,
        }),
        {
          scenarios: [seed.scenarioId],
          enable_tool_evaluation: false,
        },
      );
      assert(
        asArray(components.scenarios).map(String).includes(seed.scenarioId) ||
          asArray(components.scenarios_detail).some(
            (scenario) => scenario.id === seed.scenarioId,
          ),
        "Run-test components update did not preserve the selected scenario.",
      );
      assert(
        components.enable_tool_evaluation === false,
        "Run-test components update did not preserve enable_tool_evaluation=false.",
      );

      const activeTests = await client.get(apiPath("/simulate/run-tests/active/"));
      assert(
        typeof activeTests.total_active === "number" &&
          activeTests.active_tests &&
          typeof activeTests.active_tests === "object",
        "Active run-tests endpoint did not return active_tests and total_active.",
      );

      const chatExecution = await client.post(
        apiPath("/simulate/run-tests/{run_test_id}/chat-execute/", {
          run_test_id: created.id,
        }),
        {},
      );
      testExecutionId = firstUuid(chatExecution.execution_id);
      assert(testExecutionId, "Chat execute did not return a test execution id.");
      assert(
        chatExecution.run_test_id === created.id,
        "Chat execute returned the wrong run_test_id.",
      );

      const columnOrder = [
        { id: "status", column_name: "Status", visible: true },
        { id: "scenario_name", column_name: "Scenario", visible: true },
        { id: "overall_score", column_name: "Score", visible: false },
      ];
      const columnOrderResponse = await client.put(
        apiPath("/simulate/test-executions/{test_execution_id}/column-order/", {
          test_execution_id: testExecutionId,
        }),
        { column_order: columnOrder },
      );
      const returnedColumnOrder = asArray(columnOrderResponse.column_order);
      assert(
        returnedColumnOrder.length === columnOrder.length &&
          returnedColumnOrder.every((column, index) => {
            const expected = columnOrder[index];
            return (
              column.id === expected.id &&
              column.column_name === expected.column_name &&
              column.visible === expected.visible
            );
          }),
        "Column order update did not round-trip the submitted order.",
      );

      const batch = await client.post(
        apiPath(
          "/simulate/test-executions/{test_execution_id}/chat/call-executions/batch/",
          { test_execution_id: testExecutionId },
        ),
        {},
      );
      const callExecutionIds = asArray(batch.call_execution_ids).filter(isUuid);
      assert(
        callExecutionIds.length > 0,
        "Chat call-execution batch did not create any call executions.",
      );
      callExecutionId = callExecutionIds[0];

      const rawEndedReason = `raw stack trace ${runId} should not persist`;
      const failedCall = await client.patch(
        apiPath("/simulate/call-executions/{call_execution_id}/", {
          call_execution_id: callExecutionId,
        }),
        {
          status: "failed",
          ended_reason: rawEndedReason,
        },
      );
      assert(failedCall.status === "failed", "Call-execution PATCH did not set failed.");
      assert(
        failedCall.ended_reason === "Error processing simulation",
        "Failed call-execution PATCH did not sanitize ended_reason.",
      );
      assert(
        !JSON.stringify(failedCall).includes(rawEndedReason),
        "Call-execution PATCH response leaked the raw ended_reason.",
      );

      const comparisonError = await expectApiError(
        () =>
          client.get(
            apiPath("/simulate/call-executions/{call_execution_id}/session-comparison/", {
              call_execution_id: callExecutionId,
            }),
          ),
        [400],
        "Session comparison accepted a non-completed disposable call execution.",
      );
      assert(
        errorText(comparisonError).toLowerCase().includes("completed"),
        "Session-comparison guard did not explain that the call must be completed.",
      );

      await client.delete(
        apiPath("/simulate/call-executions/{call_execution_id}/delete/", {
          call_execution_id: callExecutionId,
        }),
      );

      const cancelled = await client.post(
        apiPath("/simulate/test-executions/{test_execution_id}/cancel/", {
          test_execution_id: testExecutionId,
        }),
        {},
      );
      assert(cancelled.success === true, "Test-execution cancel did not return success.");
      assert(
        cancelled.test_execution_id === testExecutionId,
        "Test-execution cancel returned the wrong id.",
      );

      const bulkDeleted = await client.post(
        apiPath("/simulate/run-tests/{run_test_id}/delete-test-executions/", {
          run_test_id: created.id,
        }),
        { test_execution_ids: [testExecutionId] },
      );
      assert(
        bulkDeleted.deleted_count === 1 &&
          asArray(bulkDeleted.deleted_ids).includes(testExecutionId),
        "Bulk test-execution delete did not delete the disposable execution.",
      );

      await client.delete(
        apiPath("/simulate/run-tests/{run_test_id}/delete/", {
          run_test_id: created.id,
        }),
      );

      const detailDeleted = await client.post(apiPath("/simulate/run-tests/create/"), {
        name: `${runName} detail delete`,
        description: "Temporary run test for direct detail DELETE coverage.",
        agent_definition_id: seed.agentDefinitionId,
        agent_version: seed.agentVersionId,
        scenario_ids: [seed.scenarioId],
        enable_tool_evaluation: false,
      });
      assert(
        isUuid(detailDeleted?.id),
        "Second run-test create did not return a UUID id.",
      );
      createdRunIds.push(detailDeleted.id);
      await client.delete(
        apiPath("/simulate/run-tests/{run_test_id}/", {
          run_test_id: detailDeleted.id,
        }),
      );

      dbAudit = await loadDisposableSimulationLifecycleDbAudit({
        runTestIds: [created.id, detailDeleted.id],
        testExecutionId,
        organizationId,
        workspaceId,
      });
      const deletedRuns = new Map(
        collectionRows(dbAudit.run_tests).map((runTest) => [runTest.id, runTest]),
      );
      for (const runTestId of [created.id, detailDeleted.id]) {
        const row = deletedRuns.get(runTestId);
        assert(row?.deleted === true, "Disposable run test was not soft-deleted.");
        assert(
          row?.deleted_at_set === true,
          "Disposable run test deleted_at was not stamped.",
        );
        assert(
          row?.workspace_id === workspaceId,
          "Disposable run test workspace changed before delete.",
        );
      }
      assert(
        Number(dbAudit.active_test_execution_count) === 0,
        "Disposable test executions remained active after cleanup.",
      );
      assert(
        Number(dbAudit.active_call_execution_count) === 0,
        "Disposable call executions remained active after cleanup.",
      );

      evidence.push({
        seed_run_test_id: seed.runTestId,
        seed_scenario_id: seed.scenarioId,
        run_test_id: created.id,
        detail_deleted_run_test_id: detailDeleted.id,
        test_execution_id: testExecutionId,
        call_execution_id: callExecutionId,
        chat_batch_calls: callExecutionIds.length,
        active_total: activeTests.total_active,
        run_tests_deleted: collectionRows(dbAudit.run_tests).length,
      });
    },
  },
  {
    id: "SIM-API-005",
    title:
      "Simulation run-test eval config add, structure, update, duplicate guard, and cleanup",
    tags: [
      "simulation",
      "run-tests",
      "eval-configs",
      "evals",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
      requireMutations();

      const seed = await selectSimulationRunTestSeed(client);
      if (!seed) {
        skip(
          "No completed text simulation seed with an agent definition, version, and runnable scenario was available.",
        );
      }

      const template = await findEvalTemplateDetailByName(
        client,
        "word_count_in_range",
      );
      if (!template) {
        skip("System eval word_count_in_range was not available.");
      }

      const runName = `api journey sim eval configs ${runId}`;
      const primaryName = `sim_eval_primary_${runId.replace(/[^a-z0-9]/gi, "_")}`;
      const secondaryName = `sim_eval_secondary_${runId.replace(/[^a-z0-9]/gi, "_")}`;
      const updatedName = `${primaryName}_updated`;
      const requiredKeys = simulationEvalRequiredKeys(template);
      const mapping = buildSimulationEvalConfigMapping(requiredKeys);
      const initialParams = simulationEvalParamsForTemplate(template, {
        min_words: "2",
        max_words: "8",
        k: "3",
      });
      const updatedParams = simulationEvalParamsForTemplate(template, {
        min_words: "3",
        max_words: "12",
        k: "4",
      });
      const createdRunIds = [];
      let runDeleted = false;

      cleanup.defer("delete disposable simulation eval-config run tests", async () => {
        if (runDeleted) return null;
        for (const runTestId of createdRunIds) {
          await ignoreNotFound(() =>
            client.delete(
              apiPath("/simulate/run-tests/{run_test_id}/delete/", {
                run_test_id: runTestId,
              }),
            ),
          );
        }
      });

      const created = await client.post(apiPath("/simulate/run-tests/create/"), {
        name: runName,
        description: "Temporary run test for eval-config API journey coverage.",
        agent_definition_id: seed.agentDefinitionId,
        agent_version: seed.agentVersionId,
        scenario_ids: [seed.scenarioId],
        enable_tool_evaluation: false,
      });
      assert(isUuid(created?.id), "Run-test create did not return a UUID id.");
      createdRunIds.push(created.id);

      const addPayload = {
        evaluations_config: [
          {
            template_id: template.id,
            name: primaryName,
            mapping,
            config: {
              params: initialParams.submitted,
              run_config: { pass_threshold: 0.7 },
            },
            filters: [
              {
                column_id: "status",
                filter_config: {
                  filter_type: "text",
                  filter_op: "equals",
                  filter_value: "completed",
                },
              },
            ],
            error_localizer: false,
            model: "turing_small",
          },
          {
            template_id: template.id,
            name: secondaryName,
            mapping,
            config: {
              params: initialParams.submitted,
              run_config: { pass_threshold: 0.6 },
            },
            filters: [],
            error_localizer: false,
            model: "turing_small",
          },
        ],
      };

      const addResponse = await client.post(
        apiPath("/simulate/run-tests/{run_test_id}/eval-configs/", {
          run_test_id: created.id,
        }),
        addPayload,
      );
      const createdConfigs = asArray(addResponse.created_eval_configs);
      assert(
        createdConfigs.length === 2,
        "Eval-config add did not create both submitted configs.",
      );
      const primaryConfig = createdConfigs.find((config) => config.name === primaryName);
      const secondaryConfig = createdConfigs.find(
        (config) => config.name === secondaryName,
      );
      assert(isUuid(primaryConfig?.id), "Primary eval config did not return a UUID.");
      assert(isUuid(secondaryConfig?.id), "Secondary eval config did not return a UUID.");
      assertSimulationEvalMapping(primaryConfig.mapping, mapping);
      assertSimulationEvalParams(primaryConfig.config?.params, initialParams.expected);

      let dbAudit = await loadSimulationEvalConfigDbAudit({
        runTestId: created.id,
        evalConfigIds: [primaryConfig.id, secondaryConfig.id],
        organizationId,
        workspaceId,
      });
      assert(
        Number(dbAudit.active_eval_config_count) === 2,
        "DB audit did not find both active eval configs after create.",
      );
      const primaryAudit = collectionRows(dbAudit.eval_configs).find(
        (config) => config.id === primaryConfig.id,
      );
      assert(
        primaryAudit?.template_id === template.id,
        "DB audit did not persist the selected eval template id.",
      );
      assert(
        primaryAudit?.run_test_workspace_id === workspaceId,
        "Eval config DB audit did not link to the active workspace run test.",
      );

      const structure = await client.get(
        apiPath(
          "/simulate/run-tests/{run_test_id}/eval-configs/{eval_config_id}/get-structure/",
          { run_test_id: created.id, eval_config_id: primaryConfig.id },
        ),
      );
      const structureEval = structure.eval || structure.result?.eval;
      assert(structureEval?.id === primaryConfig.id, "Eval structure returned wrong id.");
      assert(
        structureEval.template_id === template.id,
        "Eval structure returned wrong template id.",
      );
      assertSimulationEvalMapping(structureEval.mapping, mapping);
      assertSimulationEvalParams(structureEval.params, initialParams.expected);

      const duplicateAddError = await expectApiError(
        () =>
          client.post(
            apiPath("/simulate/run-tests/{run_test_id}/eval-configs/", {
              run_test_id: created.id,
            }),
            {
              evaluations_config: [
                {
                  template_id: template.id,
                  name: primaryName,
                  mapping,
                },
              ],
            },
          ),
        [400],
        "Eval-config add accepted a duplicate active name.",
      );
      assert(
        errorText(duplicateAddError).includes("already exists"),
        "Duplicate eval-config add did not explain the duplicate name.",
      );

      const duplicateUpdateError = await expectApiError(
        () =>
          client.post(
            apiPath(
              "/simulate/run-tests/{run_test_id}/eval-configs/{eval_config_id}/update/",
              { run_test_id: created.id, eval_config_id: secondaryConfig.id },
            ),
            { name: primaryName },
          ),
        [400],
        "Eval-config update accepted a duplicate active name.",
      );
      assert(
        errorText(duplicateUpdateError).includes("already exists"),
        "Duplicate eval-config update did not explain the duplicate name.",
      );

      const updateResponse = await client.post(
        apiPath(
          "/simulate/run-tests/{run_test_id}/eval-configs/{eval_config_id}/update/",
          { run_test_id: created.id, eval_config_id: primaryConfig.id },
        ),
        {
          name: updatedName,
          mapping,
          config: {
            params: updatedParams.submitted,
            run_config: { pass_threshold: 0.9 },
          },
          error_localizer: true,
          model: "turing_large",
          run: false,
        },
      );
      assert(
        updateResponse.eval_config_id === primaryConfig.id,
        "Eval-config update returned the wrong eval_config_id.",
      );

      const updatedStructure = await client.get(
        apiPath(
          "/simulate/run-tests/{run_test_id}/eval-configs/{eval_config_id}/get-structure/",
          { run_test_id: created.id, eval_config_id: primaryConfig.id },
        ),
      );
      const updatedStructureEval = updatedStructure.eval || updatedStructure.result?.eval;
      assert(
        updatedStructureEval?.name === updatedName,
        "Eval structure did not return updated name.",
      );
      assert(
        updatedStructureEval.error_localizer === true,
        "Eval structure did not return updated error_localizer.",
      );
      assert(
        updatedStructureEval.selected_model === "turing_large",
        "Eval structure did not return updated model.",
      );
      assertSimulationEvalParams(updatedStructureEval.params, updatedParams.expected);

      await client.delete(
        apiPath("/simulate/run-tests/{run_test_id}/eval-configs/{eval_config_id}/", {
          run_test_id: created.id,
          eval_config_id: secondaryConfig.id,
        }),
      );

      await client.delete(
        apiPath("/simulate/run-tests/{run_test_id}/delete/", {
          run_test_id: created.id,
        }),
      );
      runDeleted = true;

      dbAudit = await loadSimulationEvalConfigDbAudit({
        runTestId: created.id,
        evalConfigIds: [primaryConfig.id, secondaryConfig.id],
        organizationId,
        workspaceId,
      });
      assert(dbAudit.run_test_deleted === true, "Disposable run test was not deleted.");
      assert(
        Number(dbAudit.active_eval_config_count) === 0,
        "Active eval configs remained after run-test cleanup.",
      );
      for (const config of collectionRows(dbAudit.eval_configs)) {
        assert(config.deleted === true, "Disposable eval config was not soft-deleted.");
        assert(
          config.deleted_at_set === true,
          "Disposable eval config deleted_at was not stamped.",
        );
      }

      evidence.push({
        seed_run_test_id: seed.runTestId,
        seed_scenario_id: seed.scenarioId,
        run_test_id: created.id,
        eval_template_id: template.id,
        eval_config_id: primaryConfig.id,
        secondary_eval_config_id: secondaryConfig.id,
        params: updatedParams.expected,
        active_eval_config_count: Number(dbAudit.active_eval_config_count),
      });
    },
  },
  {
    id: "SIM-API-006",
    title:
      "Simulation call chat send-message guard and branch-analysis create response",
    tags: [
      "simulation",
      "call-executions",
      "chat",
      "branch-analysis",
      "mutating",
      "guards",
      "db-audit",
    ],
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
      requireMutations();

      const seed = await selectSimulationRunTestSeed(client);
      if (!seed) {
        skip(
          "No completed text simulation seed with an agent definition, version, and runnable scenario was available.",
        );
      }

      const runName = `api journey sim call actions ${runId}`;
      const createdRunIds = [];
      let testExecutionId = null;
      let callExecutionId = null;

      cleanup.defer("delete disposable simulation call-action run tests", async () => {
        for (const runTestId of createdRunIds) {
          await ignoreNotFound(() =>
            client.delete(
              apiPath("/simulate/run-tests/{run_test_id}/delete/", {
                run_test_id: runTestId,
              }),
            ),
          );
        }
      });
      cleanup.defer("delete disposable simulation call-action test execution", async () => {
        if (!testExecutionId) return;
        await ignoreNotFound(() =>
          client.delete(
            apiPath("/simulate/test-executions/{test_execution_id}/delete/", {
              test_execution_id: testExecutionId,
            }),
          ),
        );
      });
      cleanup.defer("delete disposable simulation call-action call execution", async () => {
        if (!callExecutionId) return;
        await ignoreNotFound(() =>
          client.delete(
            apiPath("/simulate/call-executions/{call_execution_id}/delete/", {
              call_execution_id: callExecutionId,
            }),
          ),
        );
      });

      const created = await client.post(apiPath("/simulate/run-tests/create/"), {
        name: runName,
        description: "Temporary run test for chat/branch action coverage.",
        agent_definition_id: seed.agentDefinitionId,
        agent_version: seed.agentVersionId,
        scenario_ids: [seed.scenarioId],
        enable_tool_evaluation: false,
      });
      assert(isUuid(created?.id), "Run-test create did not return a UUID id.");
      createdRunIds.push(created.id);

      const chatExecution = await client.post(
        apiPath("/simulate/run-tests/{run_test_id}/chat-execute/", {
          run_test_id: created.id,
        }),
        {},
      );
      testExecutionId = firstUuid(chatExecution.execution_id);
      assert(testExecutionId, "Chat execute did not return a test execution id.");

      const batch = await client.post(
        apiPath(
          "/simulate/test-executions/{test_execution_id}/chat/call-executions/batch/",
          { test_execution_id: testExecutionId },
        ),
        {},
      );
      const callExecutionIds = asArray(batch.call_execution_ids).filter(isUuid);
      assert(
        callExecutionIds.length > 0,
        "Chat call-execution batch did not create a call execution.",
      );
      callExecutionId = callExecutionIds[0];

      const chatGuardError = await expectApiError(
        () =>
          client.post(
            apiPath("/simulate/call-executions/{call_execution_id}/chat/send-message/", {
              call_execution_id: callExecutionId,
            }),
            {
              initiate_chat: false,
              messages: [{ role: "user", content: "hello from api journey" }],
            },
          ),
        [400],
        "Chat send-message accepted a message while the test execution was not running.",
      );
      assert(
        errorText(chatGuardError).includes("not running or evaluating"),
        "Chat send-message guard did not explain the execution status requirement.",
      );

      const branchAnalysis = await client.get(
        apiPath("/simulate/call-executions/{call_execution_id}/branch-analysis/", {
          call_execution_id: callExecutionId,
        }),
      );
      assert(
        branchAnalysis.call_execution_id === callExecutionId,
        "Branch-analysis GET returned the wrong call execution id.",
      );
      assert(
        branchAnalysis.analysis?.analysis_summary ||
          branchAnalysis.analysis?.expected_path,
        "Branch-analysis GET did not return an analysis shape.",
      );

      const branchUnknownBody = await expectApiError(
        () =>
          client.post(
            apiPath("/simulate/call-executions/{call_execution_id}/branch-analysis/", {
              call_execution_id: callExecutionId,
            }),
            { legacy_extra: "should-not-be-accepted" },
          ),
        [400],
        "Branch-analysis POST accepted an unknown request field.",
      );
      assert(
        errorText(branchUnknownBody).includes("legacy_extra"),
        "Branch-analysis unknown-field guard did not mention the legacy field.",
      );

      const branchCreate = await client.post(
        apiPath("/simulate/call-executions/{call_execution_id}/branch-analysis/", {
          call_execution_id: callExecutionId,
        }),
        {},
      );
      assert(
        branchCreate.call_execution_id === callExecutionId,
        "Branch-analysis POST returned the wrong call execution id.",
      );
      assert(
        isUuid(branchCreate.scenario_graph_id),
        "Branch-analysis POST did not return a scenario graph id.",
      );
      assert(
        branchCreate.deviation_data?.analysis_summary ||
          branchCreate.deviation_data?.expected_path,
        "Branch-analysis POST did not return deviation analysis data.",
      );

      await client.delete(
        apiPath("/simulate/call-executions/{call_execution_id}/delete/", {
          call_execution_id: callExecutionId,
        }),
      );
      await client.delete(
        apiPath("/simulate/test-executions/{test_execution_id}/delete/", {
          test_execution_id: testExecutionId,
        }),
      );
      await client.delete(
        apiPath("/simulate/run-tests/{run_test_id}/delete/", {
          run_test_id: created.id,
        }),
      );

      const dbAudit = await loadDisposableSimulationLifecycleDbAudit({
        runTestIds: [created.id],
        testExecutionId,
        organizationId,
        workspaceId,
      });
      assert(
        Number(dbAudit.active_test_execution_count) === 0,
        "Disposable test execution remained active after call-action cleanup.",
      );
      assert(
        Number(dbAudit.active_call_execution_count) === 0,
        "Disposable call execution remained active after call-action cleanup.",
      );

      evidence.push({
        seed_run_test_id: seed.runTestId,
        seed_scenario_id: seed.scenarioId,
        run_test_id: created.id,
        test_execution_id: testExecutionId,
        call_execution_id: callExecutionId,
        chat_guard_status: chatGuardError.status,
        branch_create_scenario_graph_id: branchCreate.scenario_graph_id,
        branch_create_message: branchCreate.message,
        active_test_execution_count: Number(dbAudit.active_test_execution_count),
        active_call_execution_count: Number(dbAudit.active_call_execution_count),
      });
    },
  },
  {
    id: "SIM-API-007",
    title: "Simulation execution rerun refresh and eval action guards",
    tags: [
      "simulation",
      "run-tests",
      "test-executions",
      "rerun",
      "refresh",
      "mutating",
      "guards",
      "db-audit",
    ],
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
      requireMutations();

      const seed = await selectSimulationRunTestSeed(client);
      if (!seed) {
        skip(
          "No completed text simulation seed with an agent definition, version, and runnable scenario was available.",
        );
      }

      const runName = `api journey sim execution guards ${runId}`;
      const createdRunIds = [];
      let testExecutionId = null;
      let callExecutionId = null;

      cleanup.defer("delete disposable simulation execution-guard run tests", async () => {
        for (const runTestId of createdRunIds) {
          await ignoreNotFound(() =>
            client.delete(
              apiPath("/simulate/run-tests/{run_test_id}/delete/", {
                run_test_id: runTestId,
              }),
            ),
          );
        }
      });
      cleanup.defer(
        "delete disposable simulation execution-guard test execution",
        async () => {
          if (!testExecutionId) return;
          await ignoreNotFound(() =>
            client.delete(
              apiPath("/simulate/test-executions/{test_execution_id}/delete/", {
                test_execution_id: testExecutionId,
              }),
            ),
          );
        },
      );
      cleanup.defer("delete disposable simulation execution-guard call execution", async () => {
        if (!callExecutionId) return;
        await ignoreNotFound(() =>
          client.delete(
            apiPath("/simulate/call-executions/{call_execution_id}/delete/", {
              call_execution_id: callExecutionId,
            }),
          ),
        );
      });

      const created = await client.post(apiPath("/simulate/run-tests/create/"), {
        name: runName,
        description: "Temporary run test for execution action guard coverage.",
        agent_definition_id: seed.agentDefinitionId,
        agent_version: seed.agentVersionId,
        scenario_ids: [seed.scenarioId],
        enable_tool_evaluation: false,
      });
      assert(isUuid(created?.id), "Run-test create did not return a UUID id.");
      createdRunIds.push(created.id);

      const executeUnknownBody = await expectApiError(
        () =>
          client.post(
            apiPath("/simulate/run-tests/{run_test_id}/execute/", {
              run_test_id: created.id,
            }),
            { legacy_extra: "should-not-be-accepted" },
          ),
        [400],
        "Run-test execute accepted an unknown request field.",
      );
      assert(
        errorText(executeUnknownBody).includes("legacy_extra"),
        "Run-test execute unknown-field guard did not mention the legacy field.",
      );

      const chatExecution = await client.post(
        apiPath("/simulate/run-tests/{run_test_id}/chat-execute/", {
          run_test_id: created.id,
        }),
        {},
      );
      testExecutionId = firstUuid(chatExecution.execution_id);
      assert(testExecutionId, "Chat execute did not return a test execution id.");

      const batch = await client.post(
        apiPath(
          "/simulate/test-executions/{test_execution_id}/chat/call-executions/batch/",
          { test_execution_id: testExecutionId },
        ),
        {},
      );
      const callExecutionIds = asArray(batch.call_execution_ids).filter(isUuid);
      assert(
        callExecutionIds.length > 0,
        "Chat call-execution batch did not create a call execution.",
      );
      callExecutionId = callExecutionIds[0];

      const evalRefreshUnknownBody = await expectApiError(
        () =>
          client.post(
            apiPath(
              "/simulate/test-executions/{test_execution_id}/eval-explanation-summary/refresh/",
              { test_execution_id: testExecutionId },
            ),
            { legacy_extra: "should-not-be-accepted" },
          ),
        [400],
        "Eval explanation refresh accepted an unknown request field.",
      );
      assert(
        errorText(evalRefreshUnknownBody).includes("legacy_extra"),
        "Eval explanation refresh unknown-field guard did not mention the legacy field.",
      );

      const optimiserRefreshUnknownBody = await expectApiError(
        () =>
          client.post(
            apiPath(
              "/simulate/test-executions/{test_execution_id}/optimiser-analysis/refresh/",
              { test_execution_id: testExecutionId },
            ),
            { legacy_extra: "should-not-be-accepted" },
          ),
        [400],
        "Optimiser analysis refresh accepted an unknown request field.",
      );
      assert(
        errorText(optimiserRefreshUnknownBody).includes("legacy_extra"),
        "Optimiser analysis refresh unknown-field guard did not mention the legacy field.",
      );

      const callRerunPendingGuard = await expectApiError(
        () =>
          client.post(
            apiPath("/simulate/test-executions/{test_execution_id}/rerun-calls/", {
              test_execution_id: testExecutionId,
            }),
            { rerun_type: "eval_only", select_all: true },
          ),
        [400],
        "Call rerun accepted a pending disposable test execution.",
      );
      assert(
        errorText(callRerunPendingGuard).includes("pending"),
        "Call rerun pending-execution guard did not mention the execution status.",
      );

      const testExecutionRerunTextGuard = await expectApiError(
        () =>
          client.post(
            apiPath("/simulate/run-tests/{run_test_id}/rerun-test-executions/", {
              run_test_id: created.id,
            }),
            { rerun_type: "call_and_eval", select_all: true },
          ),
        [400],
        "Test-execution rerun accepted call_and_eval for a text simulation run.",
      );
      assert(
        errorText(testExecutionRerunTextGuard).includes("Text/Chat agents"),
        "Test-execution rerun text-agent guard did not explain the rerun type limit.",
      );

      const runNewEvalsEmptyConfigs = await expectApiError(
        () =>
          client.post(
            apiPath("/simulate/run-tests/{run_test_id}/run-new-evals/", {
              run_test_id: created.id,
            }),
            { select_all: true, eval_config_ids: [] },
          ),
        [400],
        "Run-new-evals accepted an empty eval_config_ids list.",
      );
      assert(
        errorText(runNewEvalsEmptyConfigs).includes("eval_config_ids"),
        "Run-new-evals empty-config guard did not mention eval_config_ids.",
      );

      await client.delete(
        apiPath("/simulate/call-executions/{call_execution_id}/delete/", {
          call_execution_id: callExecutionId,
        }),
      );
      await client.delete(
        apiPath("/simulate/test-executions/{test_execution_id}/delete/", {
          test_execution_id: testExecutionId,
        }),
      );
      await client.delete(
        apiPath("/simulate/run-tests/{run_test_id}/delete/", {
          run_test_id: created.id,
        }),
      );

      const dbAudit = await loadDisposableSimulationLifecycleDbAudit({
        runTestIds: [created.id],
        testExecutionId,
        organizationId,
        workspaceId,
      });
      assert(
        Number(dbAudit.active_test_execution_count) === 0,
        "Disposable test execution remained active after execution-guard cleanup.",
      );
      assert(
        Number(dbAudit.active_call_execution_count) === 0,
        "Disposable call execution remained active after execution-guard cleanup.",
      );

      evidence.push({
        seed_run_test_id: seed.runTestId,
        seed_scenario_id: seed.scenarioId,
        run_test_id: created.id,
        test_execution_id: testExecutionId,
        call_execution_id: callExecutionId,
        execute_unknown_status: executeUnknownBody.status,
        eval_refresh_unknown_status: evalRefreshUnknownBody.status,
        optimiser_refresh_unknown_status: optimiserRefreshUnknownBody.status,
        call_rerun_pending_status: callRerunPendingGuard.status,
        test_execution_rerun_text_guard_status: testExecutionRerunTextGuard.status,
        run_new_evals_empty_configs_status: runNewEvalsEmptyConfigs.status,
        active_test_execution_count: Number(dbAudit.active_test_execution_count),
        active_call_execution_count: Number(dbAudit.active_call_execution_count),
      });
    },
  },
  {
    id: "AGENTCC-API-001",
    title: "Gateway blocklist create, add words, remove words, update, and delete lifecycle",
    tags: ["gateway", "agentcc", "blocklists", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const name = `api_journey_blocklist_${runId.replace(/[^a-z0-9]/gi, "_")}`;

      const created = await client.post(apiPath("/agentcc/blocklists/"), {
        name,
        description: "Temporary blocklist for API journey regression.",
        words: ["blocked-alpha"],
        is_active: true,
      });
      assert(created?.id, "Blocklist create did not return id.");
      cleanup.defer("delete API journey blocklist", () =>
        ignoreNotFound(() =>
          client.delete(apiPath("/agentcc/blocklists/{id}/", { id: created.id })),
        ),
      );

      const withWords = await client.post(
        apiPath("/agentcc/blocklists/{id}/add-words/", { id: created.id }),
        { words: ["blocked-alpha", "blocked-beta"] },
      );
      assert(
        asArray(withWords.words).includes("blocked-beta"),
        "Blocklist add-words did not persist the added word.",
      );

      const withoutAlpha = await client.post(
        apiPath("/agentcc/blocklists/{id}/remove-words/", { id: created.id }),
        { words: ["blocked-alpha"] },
      );
      assert(
        !asArray(withoutAlpha.words).includes("blocked-alpha") &&
          asArray(withoutAlpha.words).includes("blocked-beta"),
        "Blocklist remove-words did not remove only the requested word.",
      );

      const updated = await client.patch(
        apiPath("/agentcc/blocklists/{id}/", { id: created.id }),
        {
          description: "Updated temporary blocklist for API journey regression.",
          is_active: false,
        },
      );
      assert(updated.is_active === false, "Blocklist update did not persist is_active.");

      await client.delete(apiPath("/agentcc/blocklists/{id}/", { id: created.id }));
      const listed = asArray(await client.get(apiPath("/agentcc/blocklists/")));
      assert(
        !listed.some((blocklist) => blocklist.id === created.id),
        "Deleted blocklist was still visible in list.",
      );

      evidence.push({ blocklist_id: created.id, blocklist_name: name });
    },
  },
  {
    id: "AGENTCC-API-002",
    title: "Gateway custom property schema create, validate, update, and delete lifecycle",
    tags: ["gateway", "agentcc", "custom-properties", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const name = `api_journey_property_${runId.replace(/[^a-z0-9]/gi, "_")}`;

      const created = await client.post(apiPath("/agentcc/custom-properties/"), {
        name,
        description: "Temporary custom property for API journey regression.",
        property_type: "enum",
        required: true,
        allowed_values: ["alpha", "beta"],
        default_value: "alpha",
      });
      assert(created?.id, "Custom property schema create did not return id.");
      cleanup.defer("delete API journey custom property", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/agentcc/custom-properties/{id}/", { id: created.id }),
          ),
        ),
      );

      const valid = await client.post(apiPath("/agentcc/custom-properties/validate/"), {
        properties: { [name]: "beta" },
      });
      assert(valid.valid === true, "Custom property validate rejected a valid value.");

      const invalid = await client.post(
        apiPath("/agentcc/custom-properties/validate/"),
        { properties: { [name]: "gamma" } },
      );
      assert(
        invalid.valid === false && asArray(invalid.errors).length > 0,
        "Custom property validate accepted an invalid enum value.",
      );

      const updated = await client.patch(
        apiPath("/agentcc/custom-properties/{id}/", { id: created.id }),
        {
          description: "Updated custom property for API journey regression.",
          required: false,
          allowed_values: ["alpha", "beta", "gamma"],
        },
      );
      assert(
        updated.required === false &&
          asArray(updated.allowed_values).includes("gamma"),
        "Custom property update did not persist required/allowed_values.",
      );

      await client.delete(apiPath("/agentcc/custom-properties/{id}/", { id: created.id }));
      const listed = asArray(await client.get(apiPath("/agentcc/custom-properties/")));
      assert(
        !listed.some((schema) => schema.id === created.id),
        "Deleted custom property schema was still visible in list.",
      );

      evidence.push({ custom_property_id: created.id, custom_property_name: name });
    },
  },
  {
    id: "AGENTCC-API-003",
    title: "Gateway API key create, update, revoke, and delete lifecycle",
    tags: ["gateway", "agentcc", "api-keys", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const name = `api_journey_key_${runId.replace(/[^a-z0-9]/gi, "_")}`;

      const created = await createGatewayApiKeyOrSkip(client, {
        name,
        owner: "api-journey",
        allowed_models: ["gpt-4o-mini"],
        allowed_providers: ["openai"],
        metadata: { source: "api-journey", runId },
      });
      assert(created?.id, "Gateway API key create did not return id.");
      assert(
        typeof created.key === "string" && created.key.length > 0,
        "Gateway API key create did not return the one-time raw key.",
      );
      cleanup.defer("delete API journey gateway API key", () =>
        ignoreNotFound(() =>
          client.delete(apiPath("/agentcc/api-keys/{id}/", { id: created.id })),
        ),
      );

      const updated = await client.patch(
        apiPath("/agentcc/api-keys/{id}/", { id: created.id }),
        {
          name: `${name}_updated`,
          owner: "api-journey-updated",
          allowed_models: ["gpt-4o-mini", "gpt-4.1-mini"],
          metadata: { source: "api-journey", updated: true },
        },
      );
      assert(
        updated.name === `${name}_updated` &&
          asArray(updated.allowed_models).includes("gpt-4.1-mini"),
        "Gateway API key update did not persist name/allowed_models.",
      );

      const detail = await client.get(
        apiPath("/agentcc/api-keys/{id}/", { id: created.id }),
      );
      assert(detail?.id === created.id, "Gateway API key detail returned wrong id.");
      assert(
        !Object.prototype.hasOwnProperty.call(detail, "key"),
        "Gateway API key detail leaked the raw key after creation.",
      );

      const revoked = await client.post(
        apiPath("/agentcc/api-keys/{id}/revoke/", { id: created.id }),
        {},
      );
      assert(revoked.status === "revoked", "Gateway API key revoke did not persist.");

      await client.delete(apiPath("/agentcc/api-keys/{id}/", { id: created.id }));
      const listed = asArray(await client.get(apiPath("/agentcc/api-keys/")));
      assert(
        !listed.some((key) => key.id === created.id),
        "Deleted gateway API key was still visible in list.",
      );

      evidence.push({ api_key_id: created.id, key_name: name });
    },
  },
  {
    id: "AGENTCC-API-004",
    title: "Gateway webhook create, update, list, event list, and delete lifecycle",
    tags: ["gateway", "agentcc", "webhooks", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const name = `api_journey_webhook_${runId.replace(/[^a-z0-9]/gi, "_")}`;

      const created = await client.post(apiPath("/agentcc/webhooks/"), {
        name,
        url: "https://example.com/futureagi-api-journey-webhook",
        secret: `secret-${runId}`,
        events: ["request.completed", "error.occurred"],
        is_active: true,
        headers: { "X-API-Journey": runId },
        description: "Temporary webhook for API journey regression.",
      });
      assert(created?.id, "Gateway webhook create did not return id.");
      cleanup.defer("delete API journey gateway webhook", () =>
        ignoreNotFound(() =>
          client.delete(apiPath("/agentcc/webhooks/{id}/", { id: created.id })),
        ),
      );

      const updated = await client.patch(
        apiPath("/agentcc/webhooks/{id}/", { id: created.id }),
        {
          description: "Updated temporary webhook for API journey regression.",
          events: ["request.completed"],
          is_active: false,
        },
      );
      assert(
        updated.is_active === false &&
          asArray(updated.events).length === 1 &&
          asArray(updated.events).includes("request.completed"),
        "Gateway webhook update did not persist active/events fields.",
      );

      const detail = await client.get(
        apiPath("/agentcc/webhooks/{id}/", { id: created.id }),
      );
      assert(detail?.id === created.id, "Gateway webhook detail returned wrong id.");
      assert(
        !Object.prototype.hasOwnProperty.call(detail, "secret"),
        "Gateway webhook detail leaked the write-only secret.",
      );

      const events = asArray(
        await client.get(apiPath("/agentcc/webhook-events/"), {
          query: { webhook_id: created.id },
        }),
      );
      assert(Array.isArray(events), "Gateway webhook events list was not an array.");

      await client.delete(apiPath("/agentcc/webhooks/{id}/", { id: created.id }));
      const listed = asArray(await client.get(apiPath("/agentcc/webhooks/")));
      assert(
        !listed.some((webhook) => webhook.id === created.id),
        "Deleted gateway webhook was still visible in list.",
      );

      evidence.push({ webhook_id: created.id, webhook_name: name });
    },
  },
  {
    id: "AGENTCC-API-005",
    title: "Gateway routing policy create, activate, list, and delete lifecycle",
    tags: ["gateway", "agentcc", "routing-policies", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const name = `api_journey_routing_${runId.replace(/[^a-z0-9]/gi, "_")}`;

      const created = await client.post(apiPath("/agentcc/routing-policies/"), {
        name,
        description: "Temporary routing policy for API journey regression.",
        config: {
          rules: [
            {
              when: { provider: "openai" },
              route_to: { provider: "openai", model: "gpt-4o-mini" },
            },
          ],
        },
        is_active: true,
      });
      assert(created?.id, "Gateway routing policy create did not return id.");
      cleanup.defer("delete API journey routing policy", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/agentcc/routing-policies/{id}/", { id: created.id }),
          ),
        ),
      );
      assert(created.version >= 1, "Gateway routing policy did not get a version.");

      const activated = await client.post(
        apiPath("/agentcc/routing-policies/{id}/activate/", { id: created.id }),
        {},
      );
      assert(
        activated.id === created.id && activated.is_active === true,
        "Gateway routing policy activate did not return the active policy.",
      );

      const activePolicies = asArray(
        await client.get(apiPath("/agentcc/routing-policies/"), {
          query: { active_only: true },
        }),
      );
      assert(
        activePolicies.some((policy) => policy.id === created.id),
        "Gateway routing policy was not visible in active-only list.",
      );

      await client.delete(
        apiPath("/agentcc/routing-policies/{id}/", { id: created.id }),
      );
      const listed = asArray(await client.get(apiPath("/agentcc/routing-policies/")));
      assert(
        !listed.some((policy) => policy.id === created.id),
        "Deleted gateway routing policy was still visible in list.",
      );

      evidence.push({ routing_policy_id: created.id, routing_policy_name: name });
    },
  },
  {
    id: "AGENTCC-API-006",
    title: "Gateway session create, update, close, requests, and delete lifecycle",
    tags: ["gateway", "agentcc", "sessions", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const sessionId = `api_journey_session_${runId.replace(/[^a-z0-9]/gi, "_")}`;

      const created = await client.post(apiPath("/agentcc/sessions/"), {
        session_id: sessionId,
        name: "API journey session",
        status: "active",
        metadata: { source: "api-journey", runId },
      });
      assert(created?.id, "Gateway session create did not return id.");
      cleanup.defer("delete API journey gateway session", () =>
        ignoreNotFound(() =>
          client.delete(apiPath("/agentcc/sessions/{id}/", { id: created.id })),
        ),
      );

      const activeSessions = asArray(
        await client.get(apiPath("/agentcc/sessions/"), {
          query: { status: "active", limit: 100 },
        }),
      );
      assert(
        activeSessions.some((session) => session.id === created.id),
        "Created active gateway session was not visible through status filter.",
      );

      const updated = await client.patch(
        apiPath("/agentcc/sessions/{id}/", { id: created.id }),
        {
          name: "API journey session updated",
          metadata: { source: "api-journey", runId, updated: true },
        },
      );
      assert(
        updated.name === "API journey session updated" &&
          updated.metadata?.updated === true,
        "Gateway session update did not persist name/metadata.",
      );

      const detail = await client.get(
        apiPath("/agentcc/sessions/{id}/", { id: created.id }),
      );
      assert(detail?.id === created.id, "Gateway session detail returned wrong id.");
      assert(
        detail?.stats && typeof detail.stats.request_count === "number",
        "Gateway session detail did not include request stats.",
      );

      const requests = asArray(
        await client.get(apiPath("/agentcc/sessions/{id}/requests/", { id: created.id })),
      );
      assert(Array.isArray(requests), "Gateway session requests did not return an array.");

      const closed = await client.post(
        apiPath("/agentcc/sessions/{id}/close/", { id: created.id }),
        {},
      );
      assert(closed.status === "closed", "Gateway session close did not persist.");

      const closedSessions = asArray(
        await client.get(apiPath("/agentcc/sessions/"), {
          query: { status: "closed", limit: 100 },
        }),
      );
      assert(
        closedSessions.some((session) => session.id === created.id),
        "Closed gateway session was not visible through status filter.",
      );

      await client.delete(apiPath("/agentcc/sessions/{id}/", { id: created.id }));
      const listed = asArray(
        await client.get(apiPath("/agentcc/sessions/"), { query: { limit: 100 } }),
      );
      assert(
        !listed.some((session) => session.id === created.id),
        "Deleted gateway session was still visible in list.",
      );

      evidence.push({ session_id: created.id, gateway_session_id: sessionId });
    },
  },
  {
    id: "AGENTCC-API-007",
    title: "Gateway request logs, sessions aggregate, analytics, filters, and detail read consistency",
    tags: ["gateway", "agentcc", "request-logs", "analytics", "safe"],
    async run({ client, evidence }) {
      const logs = asArray(
        await client.get(apiPath("/agentcc/request-logs/"), {
          query: { limit: 5 },
        }),
      );
      assert(Array.isArray(logs), "Gateway request log list did not return an array.");

      const analyticsOverview = await client.get(apiPath("/agentcc/analytics/overview/"));
      assert(
        analyticsOverview?.total_requests &&
          typeof analyticsOverview.total_requests.value !== "undefined",
        "Gateway analytics overview did not include total_requests KPI.",
      );

      const usage = await client.get(apiPath("/agentcc/analytics/usage-timeseries/"), {
        query: { granularity: "day" },
      });
      assert(Array.isArray(usage?.series), "Gateway usage analytics missing series.");

      const cost = await client.get(apiPath("/agentcc/analytics/cost-breakdown/"), {
        query: { group_by: "model", top_n: 5 },
      });
      assert(Array.isArray(cost?.breakdown), "Gateway cost analytics missing breakdown.");

      const latency = await client.get(apiPath("/agentcc/analytics/latency-stats/"));
      assert(
        latency?.summary && Array.isArray(latency?.timeseries),
        "Gateway latency analytics missing summary/timeseries.",
      );

      const errors = await client.get(apiPath("/agentcc/analytics/error-breakdown/"), {
        query: { group_by: "status_code" },
      });
      assert(
        Array.isArray(errors?.breakdown) && Array.isArray(errors?.error_timeseries),
        "Gateway error analytics missing breakdown/timeseries.",
      );

      const models = await client.get(apiPath("/agentcc/analytics/model-comparison/"));
      assert(Array.isArray(models?.models), "Gateway model comparison missing models array.");

      const sessionAggregates = asArray(
        await client.get(apiPath("/agentcc/request-logs/sessions/"), {
          query: { limit: 5 },
        }),
      );
      assert(
        Array.isArray(sessionAggregates),
        "Gateway request-log session aggregate did not return an array.",
      );

      if (logs.length === 0) {
        evidence.push({
          request_log_count: 0,
          note: "Read surfaces returned valid empty states; no request-log detail/filter row was available.",
        });
        return;
      }

      const sample = logs[0];
      const detail = await client.get(
        apiPath("/agentcc/request-logs/{id}/", { id: sample.id }),
      );
      assert(
        detail?.id === sample.id && detail?.request_id === sample.request_id,
        "Gateway request-log detail did not match list row.",
      );

      const byRequestId = asArray(
        await client.get(apiPath("/agentcc/request-logs/"), {
          query: { request_id: sample.request_id, limit: 10 },
        }),
      );
      assert(
        byRequestId.some((row) => row.id === sample.id),
        "Gateway request-log request_id filter did not return the sample row.",
      );

      if (sample.provider) {
        const byProvider = asArray(
          await client.get(apiPath("/agentcc/request-logs/"), {
            query: { provider: sample.provider, limit: 10 },
          }),
        );
        assert(
          byProvider.some((row) => row.id === sample.id),
          "Gateway request-log provider filter did not return the sample row.",
        );
      }

      if (typeof sample.status_code === "number") {
        const byStatusCode = asArray(
          await client.get(apiPath("/agentcc/request-logs/"), {
            query: { status_code: sample.status_code, limit: 10 },
          }),
        );
        assert(
          byStatusCode.some((row) => row.id === sample.id),
          "Gateway request-log status_code filter did not return the sample row.",
        );

        const byStatusRange = asArray(
          await client.get(apiPath("/agentcc/request-logs/"), {
            query: {
              min_status_code: sample.status_code,
              max_status_code: sample.status_code,
              limit: 10,
            },
          }),
        );
        assert(
          byStatusRange.some((row) => row.id === sample.id),
          "Gateway request-log status-code range filter did not return the sample row.",
        );
      }

      const searchTerm =
        String(sample.model || sample.provider || sample.request_id || "").slice(0, 8);
      if (searchTerm.length >= 2) {
        const searched = asArray(
          await client.get(apiPath("/agentcc/request-logs/search/"), {
            query: { q: searchTerm, limit: 10 },
          }),
        );
        assert(
          searched.some((row) => row.id === sample.id),
          "Gateway request-log search did not return the sample row.",
        );
      }

      if (sample.session_id) {
        const sessionDetail = asArray(
          await client.get(
            apiPath("/agentcc/request-logs/sessions/{session_id}/", {
              session_id: sample.session_id,
            }),
            { query: { limit: 10 } },
          ),
        );
        assert(
          sessionDetail.some((row) => row.id === sample.id),
          "Gateway request-log session detail did not return the sample row.",
        );
      }

      const sessionsByRequests = asArray(
        await client.get(apiPath("/agentcc/request-logs/sessions/"), {
          query: { ordering: "-request_count", limit: 10 },
        }),
      );
      for (let i = 1; i < sessionsByRequests.length; i += 1) {
        assert(
          Number(sessionsByRequests[i - 1].request_count) >=
            Number(sessionsByRequests[i].request_count),
          "Gateway request-log sessions were not sorted by request_count descending.",
        );
      }

      evidence.push({
        request_log_count: logs.length,
        sample_request_log_id: sample.id,
        sample_request_id: sample.request_id,
      });
    },
  },
  {
    id: "AGENTCC-API-008",
    title: "Gateway provider credential create, mask, rotate, update, and delete lifecycle",
    tags: [
      "gateway",
      "agentcc",
      "provider-credentials",
      "mutating",
      "data-roundtrip",
      "db-audit",
      "security",
    ],
    async run({ client, cleanup, runId, evidence, organizationId }) {
      requireMutations();
      const suffix = runId.replace(/[^a-z0-9]/gi, "_");
      const providerName = `api_journey_provider_${suffix}`;
      const initialKey = `sk-api-journey-${suffix}-initial-secret-value`;
      const rotatedKey = `sk-api-journey-${suffix}-rotated-secret-value`;

      const created = await client.post(apiPath("/agentcc/provider-credentials/"), {
        provider_name: providerName,
        display_name: "API journey provider",
        credentials: { api_key: initialKey },
        api_format: "openai",
        models_list: ["gpt-4o-mini"],
        default_timeout_seconds: 30,
        max_concurrent: 4,
        conn_pool_size: 8,
        extra_config: { source: "api-journey", runId },
      });
      assert(created?.id, "Provider credential create did not return id.");
      cleanup.defer("delete API journey provider credential", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/agentcc/provider-credentials/{id}/", { id: created.id }),
          ),
        ),
      );
      assertProviderCredentialSecretMasked(created, initialKey);

      let dbAudit = await loadAgentccProviderCredentialDbAudit({
        credentialId: created.id,
        organizationId,
        rawKey: initialKey,
      });
      assert(
        dbAudit.id === created.id &&
          dbAudit.provider_name === providerName &&
          dbAudit.organization_id === organizationId,
        "Provider credential DB audit did not find the created org-scoped row.",
      );
      assert(
        Number(dbAudit.encrypted_credentials_bytes) > 0 &&
          dbAudit.raw_key_present_in_ciphertext === false,
        "Provider credential DB audit did not store the secret as encrypted bytes.",
      );

      const listed = asArray(
        await client.get(apiPath("/agentcc/provider-credentials/"), {
          query: { provider_name: providerName },
        }),
      );
      assert(
        listed.some((credential) => credential.id === created.id),
        "Created provider credential was not visible through provider_name filter.",
      );

      const detail = await client.get(
        apiPath("/agentcc/provider-credentials/{id}/", { id: created.id }),
      );
      assert(detail?.id === created.id, "Provider credential detail returned wrong id.");
      assertProviderCredentialSecretMasked(detail, initialKey);

      const updated = await client.patch(
        apiPath("/agentcc/provider-credentials/{id}/", { id: created.id }),
        {
          display_name: "API journey provider updated",
          base_url: "https://api.example.com/v1",
          models_list: ["gpt-4o-mini", "gpt-4.1-mini"],
          is_active: false,
          extra_config: { source: "api-journey", runId, updated: true },
        },
      );
      assert(
        updated.display_name === "API journey provider updated" &&
          updated.is_active === false &&
          updated.base_url === "https://api.example.com/v1" &&
          asArray(updated.models_list).includes("gpt-4.1-mini"),
        "Provider credential update did not persist display/base_url/models/is_active.",
      );
      assertProviderCredentialSecretMasked(updated, initialKey);

      const rotated = await client.post(
        apiPath("/agentcc/provider-credentials/{id}/rotate/", { id: created.id }),
        { credentials: { api_key: rotatedKey } },
      );
      assert(rotated.last_rotated_at, "Provider credential rotate did not set last_rotated_at.");
      assertProviderCredentialSecretMasked(rotated, rotatedKey);

      dbAudit = await loadAgentccProviderCredentialDbAudit({
        credentialId: created.id,
        organizationId,
        rawKey: rotatedKey,
      });
      assert(
        dbAudit.deleted === false &&
          Number(dbAudit.encrypted_credentials_bytes) > 0 &&
          dbAudit.raw_key_present_in_ciphertext === false,
        "Provider credential DB audit did not preserve encrypted active state after rotate.",
      );

      await client.delete(apiPath("/agentcc/provider-credentials/{id}/", { id: created.id }));
      const afterDelete = asArray(
        await client.get(apiPath("/agentcc/provider-credentials/"), {
          query: { provider_name: providerName },
        }),
      );
      assert(
        !afterDelete.some((credential) => credential.id === created.id),
        "Deleted provider credential was still visible through list/filter.",
      );

      dbAudit = await loadAgentccProviderCredentialDbAudit({
        credentialId: created.id,
        organizationId,
        rawKey: rotatedKey,
      });
      assert(
        dbAudit.deleted === true && dbAudit.deleted_at_set === true,
        "Provider credential DB audit did not show soft-delete state.",
      );

      evidence.push({
        provider_credential_id: created.id,
        provider_name: providerName,
        masked_secret: rotated.credentials?.api_key || null,
        encrypted_credentials_bytes: Number(dbAudit.encrypted_credentials_bytes),
      });
    },
  },
  {
    id: "AGENTCC-API-009",
    title: "Gateway guardrail policy create, encrypted secret preservation, sync, and delete lifecycle",
    tags: [
      "gateway",
      "agentcc",
      "guardrails",
      "mutating",
      "data-roundtrip",
      "db-audit",
      "security",
    ],
    async run({ client, cleanup, runId, evidence, organizationId }) {
      requireMutations();
      const suffix = runId.replace(/[^a-z0-9]/gi, "_");
      const name = `api_journey_guardrail_${suffix}`;
      const checkName = `api_journey_check_${suffix}`;
      const checkSecret = `gr-api-journey-${suffix}-secret-value`;

      const topics = await client.get(apiPath("/agentcc/guardrail-configs/topics/"));
      assert(Array.isArray(asArray(topics)), "Guardrail topics catalog was not array-like.");
      const piiEntities = await client.get(
        apiPath("/agentcc/guardrail-configs/pii-entities/"),
      );
      assert(
        Array.isArray(asArray(piiEntities)),
        "Guardrail PII entities catalog was not array-like.",
      );

      const created = await client.post(apiPath("/agentcc/guardrail-policies/"), {
        name,
        description: "Temporary guardrail policy for API journey regression.",
        scope: "global",
        mode: "monitor",
        is_active: false,
        priority: 997,
        checks: [
          {
            name: checkName,
            type: "regex",
            enabled: true,
            config: {
              pattern: "api-journey",
              action: "flag",
              api_key: checkSecret,
            },
          },
        ],
      });
      assert(created?.id, "Guardrail policy create did not return id.");
      cleanup.defer("delete API journey guardrail policy", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/agentcc/guardrail-policies/{id}/", { id: created.id }),
          ),
        ),
      );
      assertGuardrailSecretSanitized(created, checkName, checkSecret);

      let dbAudit = await loadAgentccGuardrailPolicyDbAudit({
        policyId: created.id,
        organizationId,
        rawSecret: checkSecret,
      });
      assert(
        dbAudit.id === created.id &&
          dbAudit.organization_id === organizationId &&
          dbAudit.check_secret_value === "__encrypted__",
        "Guardrail policy DB audit did not find sanitized created row.",
      );
      assert(
        dbAudit.encrypted_check_configs_present === true &&
          dbAudit.raw_secret_present_in_ciphertext === false,
        "Guardrail policy DB audit did not preserve the check secret encrypted.",
      );

      const detail = await client.get(
        apiPath("/agentcc/guardrail-policies/{id}/", { id: created.id }),
      );
      assert(detail?.id === created.id, "Guardrail policy detail returned wrong id.");
      assertGuardrailSecretSanitized(detail, checkName, checkSecret);

      const updated = await client.patch(
        apiPath("/agentcc/guardrail-policies/{id}/", { id: created.id }),
        {
          description: "Updated guardrail policy for API journey regression.",
          priority: 996,
          checks: [
            {
              name: checkName,
              type: "regex",
              enabled: true,
              config: {
                pattern: "api-journey-updated",
                action: "flag",
                api_key: "__encrypted__",
              },
            },
          ],
        },
      );
      assert(
        updated.priority === 996 &&
          updated.description.includes("Updated guardrail policy"),
        "Guardrail policy patch did not persist priority/description.",
      );
      assertGuardrailSecretSanitized(updated, checkName, checkSecret);

      const syncResult = await client.post(apiPath("/agentcc/guardrail-policies/sync/"), {});
      assert(
        syncResult.synced === true &&
          Object.prototype.hasOwnProperty.call(syncResult, "gateway_synced"),
        "Guardrail policy sync did not return synced/gateway_synced status.",
      );

      dbAudit = await loadAgentccGuardrailPolicyDbAudit({
        policyId: created.id,
        organizationId,
        rawSecret: checkSecret,
      });
      assert(
        dbAudit.deleted === false &&
          dbAudit.check_pattern === "api-journey-updated" &&
          dbAudit.encrypted_check_configs_present === true,
        "Guardrail policy DB audit did not preserve sanitized secret after patch.",
      );

      await client.delete(apiPath("/agentcc/guardrail-policies/{id}/", { id: created.id }));
      const listed = asArray(await client.get(apiPath("/agentcc/guardrail-policies/")));
      assert(
        !listed.some((policy) => policy.id === created.id),
        "Deleted guardrail policy was still visible in list.",
      );

      dbAudit = await loadAgentccGuardrailPolicyDbAudit({
        policyId: created.id,
        organizationId,
        rawSecret: checkSecret,
      });
      assert(
        dbAudit.deleted === true && dbAudit.deleted_at_set === true,
        "Guardrail policy DB audit did not show soft-delete state.",
      );

      evidence.push({
        guardrail_policy_id: created.id,
        guardrail_policy_name: name,
        sync_gateway_synced: syncResult.gateway_synced,
        encrypted_check_configs_present: dbAudit.encrypted_check_configs_present,
      });
    },
  },
  {
    id: "AGENTCC-API-010",
    title: "Gateway overview, budget, alerting, fallback, MCP, and org-config restore lifecycle",
    tags: [
      "gateway",
      "agentcc",
      "org-config",
      "budgets",
      "monitoring",
      "fallbacks",
      "mcp",
      "mutating",
      "db-audit",
    ],
    async run({ client, cleanup, runId, evidence, organizationId }) {
      requireMutations();
      const suffix = runId.replace(/[^a-z0-9]/gi, "_").toLowerCase();
      const gatewayId = "default";
      const budgetLevel = `api_journey_budget_${suffix}`;
      const alertRuleName = `api_journey_alert_${suffix}`;
      const alertChannelName = `api_journey_channel_${suffix}`;
      const primaryModel = `api-journey-primary-${suffix}`;
      const fallbackModel = `api-journey-fallback-${suffix}`;
      const mcpServerId = `api_journey_mcp_${suffix}`;

      const originalActiveConfig = await client.get(
        apiPath("/agentcc/org-configs/active/"),
      );
      assert(
        originalActiveConfig?.id && originalActiveConfig?.is_active === true,
        "AgentCC org config active endpoint did not return an active baseline config.",
      );
      const beforeConfigIds = new Set(
        collectionRows(await client.get(apiPath("/agentcc/org-configs/")))
          .map((config) => config?.id)
          .filter(Boolean),
      );
      const restoreOrgConfig = createAgentccOrgConfigRestorer({
        client,
        beforeConfigIds,
        originalActiveConfigId: originalActiveConfig.id,
      });
      cleanup.defer("restore AgentCC gateway org config versions", restoreOrgConfig);

      const gateways = asArray(await client.get(apiPath("/agentcc/gateways/")));
      assert(
        gateways.some((gateway) => gateway.id === gatewayId),
        "Gateway overview list did not include the default gateway.",
      );
      const gatewayDetail = await client.get(
        apiPath("/agentcc/gateways/{id}/", { id: gatewayId }),
      );
      assert(
        gatewayDetail?.id === gatewayId && gatewayDetail?.base_url,
        "Gateway detail did not return the default gateway/base_url.",
      );
      const health = await client.post(
        apiPath("/agentcc/gateways/{id}/health_check/", { id: gatewayId }),
        {},
      );
      assert(
        health?.status === "healthy" && typeof health.provider_count === "number",
        "Gateway health check did not return healthy provider counters.",
      );
      const providerSummary = await client.get(
        apiPath("/agentcc/gateways/{id}/providers/", { id: gatewayId }),
      );
      assert(
        Array.isArray(asArray(providerSummary?.providers)),
        "Gateway providers endpoint did not return a providers array.",
      );
      const protectTemplates = asArray(
        await client.get(apiPath("/agentcc/gateways/protect-templates/")),
      );
      assert(
        Array.isArray(protectTemplates),
        "Gateway protect templates endpoint was not array-like.",
      );

      const setBudget = await client.post(
        apiPath("/agentcc/gateways/{id}/set-budget/", { id: gatewayId }),
        {
          level: budgetLevel,
          config: {
            limit: 12.34,
            period: "monthly",
            action: "warn",
            alert_threshold: 75,
            hard_limit: false,
            source: "api-journey",
          },
        },
      );
      assert(
        setBudget?.action === "set" && setBudget.budget === budgetLevel,
        "Gateway budget set response did not echo the budget level/action.",
      );

      let gatewayConfig = await client.get(
        apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
      );
      assert(
        gatewayConfig.budgets?.[budgetLevel]?.limit === 12.34,
        "Gateway config did not expose the disposable budget after set-budget.",
      );

      const updateConfig = await client.post(
        apiPath("/agentcc/gateways/{id}/update-config/", { id: gatewayId }),
        {
          alerting: {
            rules: {
              [alertRuleName]: {
                name: alertRuleName,
                metric: "cost",
                condition: "greater_than",
                threshold: 99.99,
                window: "1h",
                severity: "warning",
                enabled: false,
              },
            },
            channels: {
              [alertChannelName]: {
                name: alertChannelName,
                type: "webhook",
                url: "https://example.com/futureagi-api-journey-alert",
                enabled: false,
              },
            },
          },
          routing: {
            strategy: "fallback",
            fallback_enabled: true,
            model_fallbacks: {
              [primaryModel]: [fallbackModel],
            },
          },
          audit: {
            enabled: true,
            min_severity: "info",
            categories: ["config", "budget", "mcp"],
          },
        },
      );
      assert(
        Number(updateConfig?.version) > Number(setBudget.version),
        "Gateway update-config did not create a newer org config version.",
      );

      gatewayConfig = await client.get(
        apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
      );
      assert(
        gatewayConfig.alerting?.rules?.[alertRuleName]?.threshold === 99.99 &&
          gatewayConfig.alerting?.channels?.[alertChannelName]?.type === "webhook",
        "Gateway config did not expose the disposable alert rule/channel.",
      );
      assert(
        gatewayConfig.routing?.fallback_enabled === true &&
          asArray(gatewayConfig.routing?.model_fallbacks?.[primaryModel]).includes(
            fallbackModel,
          ),
        "Gateway config did not expose the disposable fallback routing rule.",
      );

      const mcpServer = await client.post(
        apiPath("/agentcc/gateways/{id}/update-mcp-server/", { id: gatewayId }),
        {
          server_id: mcpServerId,
          config: {
            name: mcpServerId,
            url: "https://example.com/futureagi-api-journey-mcp",
            transport: "http",
            enabled: false,
            timeout_seconds: 3,
          },
        },
      );
      assert(
        mcpServer?.action === "updated" && mcpServer.server === mcpServerId,
        "Gateway MCP server update did not echo the server/action.",
      );

      const mcpGuardrails = await client.post(
        apiPath("/agentcc/gateways/{id}/update-mcp-guardrails/", { id: gatewayId }),
        {
          config: {
            enabled: true,
            mode: "monitor",
            blocked_tools: [`dangerous_${suffix}`],
            server_ids: [mcpServerId],
          },
        },
      );
      assert(
        mcpGuardrails?.action === "updated",
        "Gateway MCP guardrail update did not return updated action.",
      );

      gatewayConfig = await client.get(
        apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
      );
      assert(
        gatewayConfig.mcp?.servers?.[mcpServerId]?.url ===
          "https://example.com/futureagi-api-journey-mcp",
        "Gateway config did not expose the disposable MCP server.",
      );
      assert(
        asArray(gatewayConfig.mcp?.guardrails?.server_ids).includes(mcpServerId),
        "Gateway config did not expose MCP guardrail server binding.",
      );

      const mcpStatus = await client.get(
        apiPath("/agentcc/gateways/{id}/mcp-status/", { id: gatewayId }),
      );
      assert(
        typeof mcpStatus?.enabled === "boolean" &&
          Array.isArray(asArray(mcpStatus.servers)),
        "Gateway MCP status response did not include enabled/servers.",
      );
      const mcpTools = asArray(
        await client.get(apiPath("/agentcc/gateways/{id}/mcp-tools/", { id: gatewayId })),
      );
      const mcpResources = asArray(
        await client.get(
          apiPath("/agentcc/gateways/{id}/mcp-resources/", { id: gatewayId }),
        ),
      );
      const mcpPrompts = asArray(
        await client.get(
          apiPath("/agentcc/gateways/{id}/mcp-prompts/", { id: gatewayId }),
        ),
      );
      assert(
        Array.isArray(mcpTools) &&
          Array.isArray(mcpResources) &&
          Array.isArray(mcpPrompts),
        "Gateway MCP tools/resources/prompts endpoints were not array-like.",
      );

      const removedBudget = await client.post(
        apiPath("/agentcc/gateways/{id}/remove-budget/", { id: gatewayId }),
        { level: budgetLevel },
      );
      assert(
        removedBudget?.action === "removed" && removedBudget.budget === budgetLevel,
        "Gateway budget remove response did not echo the budget level/action.",
      );
      gatewayConfig = await client.get(
        apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
      );
      assert(
        !Object.prototype.hasOwnProperty.call(gatewayConfig.budgets || {}, budgetLevel),
        "Gateway config still exposed the disposable budget after remove-budget.",
      );

      const removedMcpServer = await client.post(
        apiPath("/agentcc/gateways/{id}/remove-mcp-server/", { id: gatewayId }),
        { server_id: mcpServerId },
      );
      assert(
        removedMcpServer?.action === "removed" &&
          removedMcpServer.server === mcpServerId,
        "Gateway MCP server remove response did not echo the server/action.",
      );
      gatewayConfig = await client.get(
        apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
      );
      assert(
        !Object.prototype.hasOwnProperty.call(
          gatewayConfig.mcp?.servers || {},
          mcpServerId,
        ),
        "Gateway config still exposed the disposable MCP server after removal.",
      );

      const restoreEvidence = await restoreOrgConfig();
      const dbAudit = await loadAgentccOrgConfigDbAudit({
        organizationId,
        originalConfigId: originalActiveConfig.id,
        createdConfigIds: restoreEvidence.deleted_config_ids,
        budgetLevel,
        alertRuleName,
        alertChannelName,
        mcpServerId,
      });
      assert(
        dbAudit.active_config_is_original === true &&
          dbAudit.active_budget_present === false &&
          dbAudit.active_alert_rule_present === false &&
          dbAudit.active_alert_channel_present === false &&
          dbAudit.active_mcp_server_present === false,
        "AgentCC org config DB audit did not show the original active config restored.",
      );
      assert(
        Number(dbAudit.created_config_count) ===
          restoreEvidence.deleted_config_ids.length &&
          Number(dbAudit.created_config_deleted_count) ===
            restoreEvidence.deleted_config_ids.length,
        "AgentCC org config DB audit did not show disposable config versions deleted.",
      );

      evidence.push({
        gateway_id: gatewayId,
        original_org_config_id: originalActiveConfig.id,
        original_org_config_version: originalActiveConfig.version,
        set_budget_version: setBudget.version,
        update_config_version: updateConfig.version,
        mcp_server_version: mcpServer.version,
        mcp_guardrails_version: mcpGuardrails.version,
        remove_budget_version: removedBudget.version,
        remove_mcp_server_version: removedMcpServer.version,
        gateway_synced_values: [
          setBudget.gateway_synced,
          updateConfig.gateway_synced,
          mcpServer.gateway_synced,
          mcpGuardrails.gateway_synced,
          removedBudget.gateway_synced,
          removedMcpServer.gateway_synced,
        ],
        created_config_deleted_count: Number(dbAudit.created_config_deleted_count),
      });
    },
  },
  {
    id: "AGENTCC-API-011",
    title: "Gateway email alert create, mask, patch, validation-only test, and delete lifecycle",
    tags: [
      "gateway",
      "agentcc",
      "email-alerts",
      "settings",
      "mutating",
      "data-roundtrip",
      "db-audit",
      "security",
    ],
    async run({ client, cleanup, runId, evidence, organizationId }) {
      requireMutations();
      const suffix = runId.replace(/[^a-z0-9]/gi, "_").toLowerCase();
      const name = `api_journey_email_alert_${suffix}`;
      const initialApiKey = `ea-initial-${suffix}-secret-value`;
      const initialPassword = `smtp-initial-${suffix}-password-value`;
      const rotatedApiKey = `ea-rotated-${suffix}-secret-value`;
      const rotatedPassword = `smtp-rotated-${suffix}-password-value`;

      let created;
      try {
        created = await client.post(apiPath("/agentcc/email-alerts/"), {
          name,
          recipients: ["api-journey@example.com"],
          events: ["budget.exceeded", "error.occurred"],
          thresholds: {
            budget_percent: 75,
            error_rate_percent: 5,
          },
          provider: "smtp",
          provider_config: {
            host: "smtp.example.com",
            port: 587,
            username: `api_journey_${suffix}`,
            password: initialPassword,
            api_key: initialApiKey,
          },
          is_active: false,
          cooldown_minutes: 17,
        });
      } catch (error) {
        const text = errorText(error).toLowerCase();
        if (
          error?.status === 402 ||
          text.includes("gateway_email_alerts") ||
          text.includes("gateway email alerts") ||
          text.includes("upgrade") ||
          text.includes("limit")
        ) {
          skip(
            "Gateway email alert create is blocked by the local entitlement/limit configuration.",
          );
        }
        throw error;
      }
      assert(created?.id, "Email alert create did not return id.");
      cleanup.defer("delete API journey email alert", () =>
        ignoreNotFound(() =>
          client.delete(apiPath("/agentcc/email-alerts/{id}/", { id: created.id })),
        ),
      );
      assertEmailAlertProviderConfigMasked(created, [initialApiKey, initialPassword]);

      let dbAudit = await loadAgentccEmailAlertDbAudit({
        alertId: created.id,
        organizationId,
        rawSecrets: [initialApiKey, initialPassword],
      });
      assert(
        dbAudit.id === created.id &&
          dbAudit.organization_id === organizationId &&
          Number(dbAudit.encrypted_config_bytes) > 0 &&
          dbAudit.raw_secret_present_in_ciphertext === false,
        "Email alert DB audit did not find encrypted created config.",
      );

      const listed = asArray(await client.get(apiPath("/agentcc/email-alerts/")));
      assert(
        listed.some((alert) => alert.id === created.id),
        "Created email alert was not visible in list.",
      );
      assertEmailAlertProviderConfigMasked(
        listed.find((alert) => alert.id === created.id),
        [initialApiKey, initialPassword],
      );

      const detail = await client.get(
        apiPath("/agentcc/email-alerts/{id}/", { id: created.id }),
      );
      assert(detail?.id === created.id, "Email alert detail returned wrong id.");
      assertEmailAlertProviderConfigMasked(detail, [initialApiKey, initialPassword]);

      const invalidTest = await expectApiError(
        () =>
          client.post(apiPath("/agentcc/email-alerts/{id}/test/", { id: created.id }), {
            recipient_override: "not-an-email",
          }),
        [400],
        "Expected email-alert test endpoint to reject invalid recipient without sending email.",
      );
      assert(
        errorText(invalidTest).includes("valid email") ||
          errorText(invalidTest).includes("Enter a valid email"),
        "Email alert validation-only test did not return recipient validation detail.",
      );

      const updated = await client.patch(
        apiPath("/agentcc/email-alerts/{id}/", { id: created.id }),
        {
          recipients: ["api-journey-updated@example.com"],
          events: ["guardrail.triggered", "latency.spike"],
          thresholds: {
            latency_ms: 1500,
            guardrail_events: 1,
          },
          provider_config: {
            host: "smtp-updated.example.com",
            port: 2525,
            username: `api_journey_updated_${suffix}`,
            password: rotatedPassword,
            api_key: rotatedApiKey,
          },
          is_active: true,
          cooldown_minutes: 23,
        },
      );
      assert(
        updated.is_active === true &&
          updated.cooldown_minutes === 23 &&
          asArray(updated.events).includes("latency.spike") &&
          asArray(updated.recipients).includes("api-journey-updated@example.com"),
        "Email alert patch did not persist active/cooldown/events/recipients.",
      );
      assertEmailAlertProviderConfigMasked(updated, [rotatedApiKey, rotatedPassword]);

      dbAudit = await loadAgentccEmailAlertDbAudit({
        alertId: created.id,
        organizationId,
        rawSecrets: [rotatedApiKey, rotatedPassword],
      });
      assert(
        dbAudit.deleted === false &&
          dbAudit.is_active === true &&
          dbAudit.cooldown_minutes === 23 &&
          dbAudit.raw_secret_present_in_ciphertext === false,
        "Email alert DB audit did not preserve encrypted updated config.",
      );
      assert(
        updated.provider_config?.host === "smtp-updated.example.com",
        "Email alert API readback did not preserve non-secret SMTP host config.",
      );

      await client.delete(apiPath("/agentcc/email-alerts/{id}/", { id: created.id }));
      const afterDelete = asArray(await client.get(apiPath("/agentcc/email-alerts/")));
      assert(
        !afterDelete.some((alert) => alert.id === created.id),
        "Deleted email alert was still visible in list.",
      );

      dbAudit = await loadAgentccEmailAlertDbAudit({
        alertId: created.id,
        organizationId,
        rawSecrets: [rotatedApiKey, rotatedPassword],
      });
      assert(
        dbAudit.deleted === true && dbAudit.deleted_at_set === true,
        "Email alert DB audit did not show soft-delete state.",
      );

      evidence.push({
        email_alert_id: created.id,
        email_alert_name: name,
        masked_api_key: updated.provider_config?.api_key || null,
        masked_password: updated.provider_config?.password || null,
        invalid_test_status: invalidTest.status,
        encrypted_config_bytes: Number(dbAudit.encrypted_config_bytes),
      });
    },
  },
];

async function ignoreNotFound(fn) {
  try {
    return await fn();
  } catch (error) {
    const message = String(error?.message || "").toLowerCase();
    if (
      error?.status === 404 ||
      message.includes("not found") ||
      message.includes("does not exist") ||
      (message.includes("no ") && message.includes(" matches "))
    ) {
      return null;
    }
    throw error;
  }
}

async function createGatewayApiKeyOrSkip(client, payload) {
  try {
    return await client.post(apiPath("/agentcc/api-keys/"), payload);
  } catch (error) {
    const message = String(error?.message || "");
    if (
      message.includes("Cannot connect to gateway") ||
      message.includes("Gateway error:")
    ) {
      skip(
        "AgentCC gateway admin API is unreachable in this environment; API key lifecycle requires a live gateway.",
      );
    }
    throw error;
  }
}

async function expectApiError(fn, expectedStatuses, successMessage) {
  try {
    await fn();
  } catch (error) {
    if (expectedStatuses.includes(error?.status)) return error;
    throw error;
  }
  throw new Error(successMessage);
}

function errorText(error) {
  return [error?.message, JSON.stringify(error?.body || {})].join(" ");
}

function collectionRows(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.results)) return value.results;
  if (Array.isArray(value?.data)) return value.data;
  if (Array.isArray(value?.run_tests)) return value.run_tests;
  if (Array.isArray(value?.test_executions)) return value.test_executions;
  if (Array.isArray(value?.call_executions)) return value.call_executions;
  if (Array.isArray(value?.calls)) return value.calls;
  return asArray(value);
}

async function selectSimulationRunTestSeed(client) {
  const candidates = [];
  for (const source of [
    apiPath("/simulate/run-tests/"),
    apiPath("/simulate/api/run-tests/"),
  ]) {
    for (const page of [1, 2]) {
      const payload = await client.get(source, { query: { limit: 25, page } });
      candidates.push(...collectionRows(payload));
    }
  }

  for (const runTest of candidates) {
    const agentDefinitionId =
      firstUuid(runTest.agent_definition) ||
      firstUuid(runTest.agent_definition_detail?.id);
    const agentVersionId = extractSimulationAgentVersionId(runTest);
    if (!agentDefinitionId || !agentVersionId) continue;
    if (!isTextSimulationAgent(runTest)) continue;

    const scenario = collectionRows(runTest.scenarios_detail).find((item) =>
      isRunnableSimulationScenario(item),
    );
    if (!scenario) continue;

    return {
      runTestId: runTest.id || null,
      agentDefinitionId,
      agentVersionId,
      scenarioId: scenario.id,
    };
  }

  const [agentPayload, scenarioPayload] = await Promise.all([
    client.get(apiPath("/simulate/api/agent-definition-operations/"), {
      query: { limit: 50, page: 1 },
    }),
    client.get(apiPath("/simulate/scenarios/"), {
      query: { limit: 50, page: 1, agent_type: "text" },
    }),
  ]);
  const textAgent = collectionRows(agentPayload).find(
    (agent) =>
      firstUuid(agent?.id) &&
      String(agent?.agent_type || "").toLowerCase() === "text" &&
      extractSimulationAgentVersionId(agent),
  );
  const scenario = collectionRows(scenarioPayload).find((item) =>
    isRunnableSimulationScenario(item),
  );
  if (!textAgent || !scenario) return null;

  return {
    runTestId: null,
    agentDefinitionId: textAgent.id,
    agentVersionId: extractSimulationAgentVersionId(textAgent),
    scenarioId: scenario.id,
  };
}

function extractSimulationAgentVersionId(value) {
  return (
    firstUuid(value?.agent_version?.id) ||
    firstUuid(value?.agent_version) ||
    firstUuid(value?.latest_version?.id) ||
    firstUuid(value?.agent_definition_detail?.latest_version?.id)
  );
}

function isTextSimulationAgent(value) {
  const agentType = String(
    value?.agent_definition_detail?.agent_type ||
      value?.agent_version?.configuration_snapshot?.agent_type ||
      value?.latest_version?.configuration_snapshot?.agent_type ||
      value?.agent_type ||
      "",
  ).toLowerCase();
  return agentType === "text" || agentType === "chat";
}

function isRunnableSimulationScenario(scenario) {
  if (!firstUuid(scenario?.id)) return false;
  if (!statusIsCompleted(scenario?.status)) return false;
  const datasetId = firstUuid(scenario?.dataset);
  const rowCount = Number(scenario?.dataset_rows ?? scenario?.row_count ?? 0);
  return !datasetId || rowCount > 0;
}

function statusIsCompleted(value) {
  return String(value || "").toLowerCase() === "completed";
}

async function findEvalTemplateDetailByName(client, name) {
  const payload = await client.post(apiPath("/model-hub/eval-templates/list/"), {
    page: 0,
    page_size: 10,
    owner_filter: "all",
    search: name,
    sort_by: "updated_at",
    sort_order: "desc",
  });
  const row = asArray(payload?.items).find(
    (item) => item?.name === name && isUuid(item?.id),
  );
  if (!row) return null;
  return client.get(
    apiPath("/model-hub/eval-templates/{template_id}/detail/", {
      template_id: row.id,
    }),
  );
}

function simulationEvalRequiredKeys(detail) {
  return asArray(
    detail?.required_keys ||
      detail?.eval_required_keys ||
      detail?.config?.required_keys,
  ).filter((key) => typeof key === "string" && key.length > 0);
}

function simulationEvalParamsForTemplate(detail, values = {}) {
  const schema = detail?.config?.function_params_schema || {};
  const submitted = {};
  const expected = {};
  for (const [key, definition] of Object.entries(schema)) {
    const fallback = definition?.default;
    const rawValue = Object.prototype.hasOwnProperty.call(values, key)
      ? values[key]
      : fallback;
    if (rawValue === undefined || rawValue === null) continue;
    submitted[key] = rawValue;
    expected[key] =
      definition?.type === "integer" || definition?.type === "number"
        ? Number(rawValue)
        : rawValue;
  }
  return { submitted, expected };
}

function buildSimulationEvalConfigMapping(requiredKeys) {
  const fallbackByKey = {
    actual_json: "text",
    expected: "expected",
    expected_json: "expected",
    expected_output: "expected",
    ground_truth: "expected",
    hypothesis: "text",
    input: "text",
    output: "text",
    query: "text",
    reference: "expected",
    response: "text",
    text: "text",
  };
  const mapping = {};
  for (const key of requiredKeys) {
    mapping[key] = fallbackByKey[key] || "text";
  }
  return mapping;
}

function assertSimulationEvalMapping(actual, expected) {
  for (const [key, value] of Object.entries(expected)) {
    assert(
      actual?.[key] === value,
      `Simulation eval config mapping did not preserve ${key}.`,
    );
  }
}

function assertSimulationEvalParams(actual, expected) {
  for (const [key, value] of Object.entries(expected)) {
    assert(
      actual?.[key] === value,
      `Simulation eval config params did not preserve ${key}.`,
    );
  }
}

function firstUuid(value) {
  return isUuid(value) ? String(value) : null;
}

function assertSimulationSdkCodeSafe(payload) {
  const sdkCode = String(payload?.sdk_code || "");
  assert(sdkCode, "Run-test SDK payload did not include sdk_code.");
  assert(
    sdkCode.includes('FI_API_KEY="<YOUR_FI_API_KEY>"') &&
      sdkCode.includes('FI_SECRET_KEY="<YOUR_FI_SECRET_KEY>"'),
    "Run-test SDK code did not include credential placeholders.",
  );
  const leakedCredentials = findSdkCredentialLiterals(sdkCode);
  assert(
    leakedCredentials.length === 0,
    `Run-test SDK code exposed credential-shaped literals: ${leakedCredentials
      .map((item) => item.name)
      .join(", ")}`,
  );
}

function findSdkCredentialLiterals(sdkCode) {
  const findings = [];
  const assignmentPattern = /\b(FI_API_KEY|FI_SECRET_KEY)\s*=\s*["']([^"']+)["']/g;
  for (const match of sdkCode.matchAll(assignmentPattern)) {
    const [, name, value] = match;
    if (value.startsWith("<") || value.includes("YOUR_")) continue;
    if (/^[A-Za-z0-9_-]{16,}$/.test(value)) {
      findings.push({ name, length: value.length });
    }
  }
  return findings;
}

function assertProviderCredentialSecretMasked(payload, rawKey) {
  const serialized = JSON.stringify(payload || {});
  assert(
    !serialized.includes(rawKey),
    "Provider credential API response leaked the raw API key.",
  );
  const masked = payload?.credentials?.api_key;
  assert(
    typeof masked === "string" && masked.length > 0 && masked !== rawKey,
    "Provider credential response did not return a masked api_key value.",
  );
}

function assertGuardrailSecretSanitized(payload, checkName, rawSecret) {
  const serialized = JSON.stringify(payload || {});
  assert(
    !serialized.includes(rawSecret),
    "Guardrail policy API response leaked the raw check secret.",
  );
  const check = asArray(payload?.checks).find((item) => item?.name === checkName);
  assert(check, "Guardrail policy response did not include the created check.");
  assert(
    check.config?.api_key === "__encrypted__",
    "Guardrail policy response did not sanitize the check api_key.",
  );
}

function assertEmailAlertProviderConfigMasked(payload, rawSecrets) {
  const serialized = JSON.stringify(payload || {});
  for (const rawSecret of rawSecrets) {
    assert(
      !serialized.includes(rawSecret),
      "Email alert API response leaked a raw provider_config secret.",
    );
  }
  const config = payload?.provider_config || {};
  for (const key of ["api_key", "password"]) {
    assert(
      typeof config[key] === "string" &&
        config[key].length > 0 &&
        !rawSecrets.includes(config[key]),
      `Email alert provider_config.${key} was not masked.`,
    );
  }
}

function createAgentccOrgConfigRestorer({
  client,
  beforeConfigIds,
  originalActiveConfigId,
}) {
  let completed = false;
  let lastEvidence = null;

  return async () => {
    if (completed) {
      return { ...lastEvidence, skipped: true };
    }

    const restoreEvidence = {
      original_config_id: originalActiveConfigId,
      activated_original: false,
      deleted_config_ids: [],
      deleted_config_versions: [],
    };

    const activeConfig = await client.get(apiPath("/agentcc/org-configs/active/"));
    if (activeConfig?.id !== originalActiveConfigId) {
      await client.post(
        apiPath("/agentcc/org-configs/{id}/activate/", {
          id: originalActiveConfigId,
        }),
        {},
      );
      restoreEvidence.activated_original = true;
    }

    const configs = collectionRows(await client.get(apiPath("/agentcc/org-configs/")));
    const disposableConfigs = configs.filter(
      (config) =>
        config?.id &&
        config.id !== originalActiveConfigId &&
        !beforeConfigIds.has(config.id),
    );

    for (const config of disposableConfigs) {
      await ignoreNotFound(() =>
        client.delete(apiPath("/agentcc/org-configs/{id}/", { id: config.id })),
      );
      restoreEvidence.deleted_config_ids.push(config.id);
      restoreEvidence.deleted_config_versions.push(config.version);
    }

    completed = true;
    lastEvidence = restoreEvidence;
    return restoreEvidence;
  };
}

async function loadSimulationRunDbAudit(runTestId, organizationId, workspaceId) {
  const sql = `
WITH selected_run AS (
  SELECT id, name
  FROM simulate_run_test
  WHERE id = ${sqlUuid(runTestId)}
    AND organization_id = ${sqlUuid(organizationId)}
    AND workspace_id = ${sqlUuid(workspaceId)}
    AND deleted = false
),
test_rows AS (
  SELECT te.id, te.status, te.created_at
  FROM simulate_test_execution te
  JOIN selected_run rt ON rt.id = te.run_test_id
  WHERE te.deleted = false
),
call_rows AS (
  SELECT ce.id, ce.status, ce.created_at
  FROM simulate_call_execution ce
  JOIN test_rows te ON te.id = ce.test_execution_id
  WHERE ce.deleted = false
)
SELECT json_build_object(
  'run_test_id', (SELECT id::text FROM selected_run),
  'run_test_name', (SELECT name FROM selected_run),
  'test_execution_count', (SELECT count(*) FROM test_rows),
  'call_execution_count', (SELECT count(*) FROM call_rows),
  'transcript_count', (
    SELECT count(*)
    FROM simulate_call_transcript ct
    JOIN call_rows cr ON cr.id = ct.call_execution_id
    WHERE ct.deleted = false
  ),
  'test_execution_ids', (
    SELECT COALESCE(json_agg(id::text ORDER BY created_at DESC), '[]'::json)
    FROM test_rows
  ),
  'call_execution_ids', (
    SELECT COALESCE(json_agg(id::text ORDER BY created_at DESC), '[]'::json)
    FROM call_rows
  ),
  'call_status_counts', (
    SELECT COALESCE(json_object_agg(status_counts.status, status_counts.count), '{}'::json)
    FROM (
      SELECT status, count(*) AS count
      FROM call_rows
      GROUP BY status
    ) status_counts
  )
)
FROM selected_run;
`;
  return runPostgresJson(sql);
}

async function loadDisposableSimulationLifecycleDbAudit({
  runTestIds,
  testExecutionId,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH selected_run_ids AS (
  SELECT unnest(${sqlUuidArray(runTestIds)}) AS id
),
selected_runs AS (
  SELECT rt.*
  FROM simulate_run_test rt
  JOIN selected_run_ids ids ON ids.id = rt.id
  WHERE rt.organization_id = ${sqlUuid(organizationId)}
),
selected_test_executions AS (
  SELECT te.*
  FROM simulate_test_execution te
  JOIN selected_runs rt ON rt.id = te.run_test_id
),
selected_call_executions AS (
  SELECT ce.*
  FROM simulate_call_execution ce
  JOIN selected_test_executions te ON te.id = ce.test_execution_id
)
SELECT json_build_object(
  'run_tests', (
    SELECT COALESCE(json_agg(json_build_object(
      'id', id::text,
      'name', name,
      'organization_id', organization_id::text,
      'workspace_id', workspace_id::text,
      'workspace_matches', workspace_id = ${sqlUuid(workspaceId)},
      'deleted', deleted,
      'deleted_at_set', deleted_at IS NOT NULL
    ) ORDER BY created_at), '[]'::json)
    FROM selected_runs
  ),
  'active_test_execution_count', (
    SELECT count(*)
    FROM selected_test_executions
    WHERE deleted = false
  ),
  'active_call_execution_count', (
    SELECT count(*)
    FROM selected_call_executions
    WHERE deleted = false
  ),
  'test_execution_row_exists', (
    SELECT EXISTS (
      SELECT 1 FROM simulate_test_execution WHERE id = ${sqlUuid(testExecutionId)}
    )
  ),
  'selected_run_count', (SELECT count(*) FROM selected_runs)
);
`;
  return runPostgresJson(sql);
}

async function loadSimulationEvalConfigDbAudit({
  runTestId,
  evalConfigIds,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH selected_run AS (
  SELECT *
  FROM simulate_run_test
  WHERE id = ${sqlUuid(runTestId)}
    AND organization_id = ${sqlUuid(organizationId)}
    AND workspace_id = ${sqlUuid(workspaceId)}
),
selected_config_ids AS (
  SELECT unnest(${sqlUuidArray(evalConfigIds)}) AS id
),
selected_configs AS (
  SELECT sec.*
  FROM simulate_eval_config sec
  JOIN selected_config_ids ids ON ids.id = sec.id
  JOIN selected_run rt ON rt.id = sec.run_test_id
)
SELECT json_build_object(
  'run_test_id', (SELECT id::text FROM selected_run),
  'run_test_deleted', COALESCE((SELECT deleted FROM selected_run), false),
  'eval_configs', (
    SELECT COALESCE(json_agg(json_build_object(
      'id', sec.id::text,
      'name', sec.name,
      'run_test_id', sec.run_test_id::text,
      'run_test_workspace_id', rt.workspace_id::text,
      'template_id', sec.eval_template_id::text,
      'config', sec.config,
      'mapping', sec.mapping,
      'filters', sec.filters,
      'error_localizer', sec.error_localizer,
      'model', sec.model,
      'status', sec.status,
      'deleted', sec.deleted,
      'deleted_at_set', sec.deleted_at IS NOT NULL
    ) ORDER BY sec.created_at), '[]'::json)
    FROM selected_configs sec
    JOIN selected_run rt ON rt.id = sec.run_test_id
  ),
  'active_eval_config_count', (
    SELECT count(*)
    FROM selected_configs
    WHERE deleted = false
  )
);
`;
  return runPostgresJson(sql);
}

async function loadAgentccProviderCredentialDbAudit({
  credentialId,
  organizationId,
  rawKey,
}) {
  const sql = `
SELECT COALESCE((
  SELECT json_build_object(
    'id', id::text,
    'organization_id', organization_id::text,
    'workspace_id', workspace_id::text,
    'provider_name', provider_name,
    'display_name', display_name,
    'base_url', base_url,
    'api_format', api_format,
    'models_list', models_list,
    'is_active', is_active,
    'deleted', deleted,
    'deleted_at_set', deleted_at IS NOT NULL,
    'encrypted_credentials_bytes', octet_length(encrypted_credentials),
    'raw_key_present_in_ciphertext',
      position(${sqlString(rawKey)} in encode(encrypted_credentials, 'escape')) > 0
  )
  FROM agentcc_provider_credential
  WHERE id = ${sqlUuid(credentialId)}
    AND organization_id = ${sqlUuid(organizationId)}
), '{}'::json);
`;
  return runPostgresJson(sql);
}

async function loadAgentccGuardrailPolicyDbAudit({
  policyId,
  organizationId,
  rawSecret,
}) {
  const sql = `
SELECT COALESCE((
  SELECT json_build_object(
    'id', id::text,
    'organization_id', organization_id::text,
    'name', name,
    'mode', mode,
    'scope', scope,
    'is_active', is_active,
    'priority', priority,
    'check_secret_value', checks #>> '{0,config,api_key}',
    'check_pattern', checks #>> '{0,config,pattern}',
    'encrypted_check_configs_present', encrypted_check_configs IS NOT NULL,
    'raw_secret_present_in_ciphertext',
      COALESCE(position(${sqlString(rawSecret)} in encode(encrypted_check_configs, 'escape')) > 0, false),
    'deleted', deleted,
    'deleted_at_set', deleted_at IS NOT NULL
  )
  FROM agentcc_guardrail_policy
  WHERE id = ${sqlUuid(policyId)}
    AND organization_id = ${sqlUuid(organizationId)}
), '{}'::json);
`;
  return runPostgresJson(sql);
}

async function loadAgentccOrgConfigDbAudit({
  organizationId,
  originalConfigId,
  createdConfigIds,
  budgetLevel,
  alertRuleName,
  alertChannelName,
  mcpServerId,
}) {
  const sql = `
WITH created_ids AS (
  SELECT unnest(${sqlUuidArray(createdConfigIds)}::uuid[]) AS id
),
active_config AS (
  SELECT id, version, budgets, alerting, mcp
  FROM agentcc_org_config
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND deleted = false
    AND is_active = true
  LIMIT 1
),
created_configs AS (
  SELECT c.id, c.version, c.deleted, c.is_active, c.change_description
  FROM agentcc_org_config c
  JOIN created_ids ci ON ci.id = c.id
  WHERE c.organization_id = ${sqlUuid(organizationId)}
)
SELECT json_build_object(
  'active_config_id', (SELECT id::text FROM active_config),
  'active_config_is_original',
    COALESCE((SELECT id = ${sqlUuid(originalConfigId)} FROM active_config), false),
  'active_version', (SELECT version FROM active_config),
  'active_budget_present',
    COALESCE((SELECT budgets ? ${sqlString(budgetLevel)} FROM active_config), false),
  'active_alert_rule_present',
    COALESCE((SELECT alerting->'rules' ? ${sqlString(alertRuleName)} FROM active_config), false),
  'active_alert_channel_present',
    COALESCE((SELECT alerting->'channels' ? ${sqlString(alertChannelName)} FROM active_config), false),
  'active_mcp_server_present',
    COALESCE((SELECT mcp->'servers' ? ${sqlString(mcpServerId)} FROM active_config), false),
  'created_config_count', (SELECT count(*) FROM created_configs),
  'created_config_deleted_count', (
    SELECT count(*)
    FROM created_configs
    WHERE deleted = true
  ),
  'created_config_active_count', (
    SELECT count(*)
    FROM created_configs
    WHERE is_active = true
  ),
  'created_change_descriptions', (
    SELECT COALESCE(
      json_agg(change_description ORDER BY version),
      '[]'::json
    )
    FROM created_configs
  )
);
`;
  return runPostgresJson(sql);
}

async function loadAgentccEmailAlertDbAudit({
  alertId,
  organizationId,
  rawSecrets,
}) {
  const rawSecretChecks = asArray(rawSecrets)
    .map(
      (secret) =>
        `COALESCE(position(${sqlString(secret)} in encode(encrypted_config, 'escape')) > 0, false)`,
    )
    .join(" OR ");
  const sql = `
SELECT COALESCE((
  SELECT json_build_object(
    'id', id::text,
    'organization_id', organization_id::text,
    'name', name,
    'provider', provider,
    'recipients', recipients,
    'events', events,
    'thresholds', thresholds,
    'is_active', is_active,
    'cooldown_minutes', cooldown_minutes,
    'encrypted_config_bytes', octet_length(encrypted_config),
    'raw_secret_present_in_ciphertext', ${rawSecretChecks || "false"},
    'deleted', deleted,
    'deleted_at_set', deleted_at IS NOT NULL
  )
  FROM agentcc_email_alert
  WHERE id = ${sqlUuid(alertId)}
    AND organization_id = ${sqlUuid(organizationId)}
), '{}'::json);
`;
  return runPostgresJson(sql);
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFileAsync(
    "docker",
    ["exec", container, "psql", "-U", user, "-d", database, "-At", "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const text = stdout.trim();
  assert(text, "Postgres DB audit returned no JSON output.");
  return JSON.parse(text);
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlUuidArray(values) {
  const uuids = asArray(values);
  for (const value of uuids) {
    assert(isUuid(value), "SQL UUID array values must be UUIDs.");
  }
  if (uuids.length === 0) {
    return "ARRAY[]::uuid[]";
  }
  return `ARRAY[${uuids.map((value) => sqlUuid(value)).join(", ")}]::uuid[]`;
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}
