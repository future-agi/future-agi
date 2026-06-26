import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  currentUserId,
  isUuid,
  requireMutations,
} from "../lib/api-client.mjs";

const execFileAsync = promisify(execFile);

export const agentPlaygroundJourneys = [
  {
    id: "AGT-API-001",
    title:
      "Agent playground graph, node, dataset, version, execution list, and delete lifecycle",
    tags: ["agents", "agent-playground", "mutating", "data-integrity"],
    async run({
      client,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const marker = runId.replace(/[^a-z0-9]/gi, "").slice(0, 18);
      const graphName = `api journey agent ${marker}`;
      const updatedGraphName = `${graphName} updated`;
      const sourceNodeName = `api_source_${marker}`.slice(0, 80);
      const targetNodeName = `api_target_${marker}`.slice(0, 80);
      const restoreNodeName = `api_restore_${marker}`.slice(0, 80);

      const templateListPayload = await client.get(
        apiPath("/agent-playground/node-templates/"),
      );
      const nodeTemplates = Array.isArray(templateListPayload?.node_templates)
        ? templateListPayload.node_templates
        : asArray(templateListPayload);
      assert(
        nodeTemplates.length > 0,
        "Agent node template list returned no rows.",
      );

      const llmTemplate = nodeTemplates.find(
        (item) => item.name === "llm_prompt",
      );
      assert(
        llmTemplate?.id,
        "Agent node templates did not include the seeded llm_prompt template.",
      );

      const llmTemplateDetail = await client.get(
        apiPath("/agent-playground/node-templates/{id}/", {
          id: llmTemplate.id,
        }),
      );
      assert(
        llmTemplateDetail.name === "llm_prompt",
        "Node template detail did not return the requested llm_prompt template.",
      );
      assert(
        llmTemplateDetail.input_mode === "dynamic",
        "llm_prompt template input_mode should be dynamic.",
      );

      const createdGraph = await client.post(
        apiPath("/agent-playground/graphs/"),
        {
          name: graphName,
          description: `Disposable API journey graph ${runId}`,
        },
      );
      const graphId = createdGraph.id;
      const draftVersionId = createdGraph.active_version?.id;
      assert(isUuid(graphId), "Agent graph create did not return a graph id.");
      assert(
        isUuid(draftVersionId),
        "Agent graph create did not return an initial draft version id.",
      );

      cleanup.defer("hard delete disposable agent graph", () =>
        hardDeleteAgentGraph({
          graphId,
          graphNames: [graphName, updatedGraphName],
          promptNames: [sourceNodeName, targetNodeName, restoreNodeName],
          organizationId,
          workspaceId,
        }),
      );

      const graphList = await client.get(apiPath("/agent-playground/graphs/"), {
        query: {
          search: graphName,
          pinned_ids: graphId,
          page: 1,
          page_size: 10,
        },
      });
      const graphs = Array.isArray(graphList?.graphs)
        ? graphList.graphs
        : asArray(graphList);
      assert(
        graphs.some((graph) => graph.id === graphId),
        "Agent graph list/search did not include the created graph.",
      );

      const graphDetail = await client.get(
        apiPath("/agent-playground/graphs/{id}/", { id: graphId }),
      );
      assert(graphDetail.id === graphId, "Agent graph detail id mismatch.");
      assert(
        graphDetail.active_version?.id === draftVersionId,
        "Agent graph detail did not expose the initial draft version.",
      );

      const initialVersions = await client.get(
        apiPath("/agent-playground/graphs/{id}/versions/", { id: graphId }),
      );
      assert(
        (initialVersions.versions || []).some(
          (version) => version.id === draftVersionId,
        ),
        "Agent graph version list did not include the initial draft.",
      );

      const sourceNode = await client.post(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/nodes/", {
          id: graphId,
          version_id: draftVersionId,
        }),
        {
          id: randomUUID(),
          type: "atomic",
          name: sourceNodeName,
          node_template_id: llmTemplate.id,
          position: { x: 40, y: 100 },
          prompt_template: {
            messages: [
              {
                id: "msg-source",
                role: "user",
                content: [
                  {
                    type: "text",
                    text: "Write one concise fact about {{topic}}.",
                  },
                ],
              },
            ],
            response_format: "text",
            model: "gpt-4o-mini",
            temperature: 0,
            metadata: { api_journey: "AGT-API-001", run_id: runId },
          },
        },
      );
      assert(isUuid(sourceNode.id), "Source node create did not return an id.");
      const sourceOutput = findPort(sourceNode, {
        direction: "output",
        displayName: "response",
      });
      assert(
        sourceOutput,
        "Source node did not expose a response output port.",
      );

      const targetNode = await client.post(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/nodes/", {
          id: graphId,
          version_id: draftVersionId,
        }),
        {
          id: randomUUID(),
          type: "atomic",
          name: targetNodeName,
          node_template_id: llmTemplate.id,
          source_node_id: sourceNode.id,
          position: { x: 320, y: 100 },
          prompt_template: {
            messages: [
              {
                id: "msg-target",
                role: "user",
                content: [
                  {
                    type: "text",
                    text: `Summarize this answer in five words: {{${sourceNodeName}.response}}`,
                  },
                ],
              },
            ],
            response_format: "text",
            model: "gpt-4o-mini",
            temperature: 0,
            metadata: { api_journey: "AGT-API-001", run_id: runId },
          },
        },
      );
      assert(isUuid(targetNode.id), "Target node create did not return an id.");
      assert(
        targetNode.node_connection?.source_node_id === sourceNode.id,
        "Target node create did not return the source node connection.",
      );
      assert(
        isUuid(sourceNode.prompt_template?.prompt_template_id) &&
          isUuid(sourceNode.prompt_template?.prompt_version_id) &&
          isUuid(targetNode.prompt_template?.prompt_version_id),
        "Created prompt nodes did not return prompt template/version ids.",
      );
      const mismatchedPromptVersion = await expectApiError(
        () =>
          client.patch(
            apiPath(
              "/agent-playground/graphs/{id}/versions/{version_id}/nodes/{node_id}/",
              {
                id: graphId,
                version_id: draftVersionId,
                node_id: sourceNode.id,
              },
            ),
            {
              prompt_template: {
                messages: [
                  {
                    id: "msg-mismatched-version",
                    role: "user",
                    content: [
                      {
                        type: "text",
                        text: "This update must not accept a foreign version.",
                      },
                    ],
                  },
                ],
                response_format: "text",
                prompt_template_id:
                  sourceNode.prompt_template.prompt_template_id,
                prompt_version_id: targetNode.prompt_template.prompt_version_id,
              },
            },
          ),
        [400],
        "Agent node prompt update accepted a version from another prompt template.",
      );

      const targetDetail = await client.get(
        apiPath(
          "/agent-playground/graphs/{id}/versions/{version_id}/nodes/{node_id}/",
          {
            id: graphId,
            version_id: draftVersionId,
            node_id: targetNode.id,
          },
        ),
      );
      assert(
        findPort(targetDetail, {
          direction: "input",
          displayName: `${sourceNodeName}.response`,
        }),
        "Target node detail did not expose the dot-notation source input port.",
      );

      const possibleMappings = asArray(
        await client.get(
          apiPath(
            "/agent-playground/graphs/{id}/versions/{version_id}/nodes/{node_id}/possible-edge-mappings/",
            {
              id: graphId,
              version_id: draftVersionId,
              node_id: targetNode.id,
            },
          ),
        ),
      );
      assert(
        possibleMappings.some(
          (mapping) => mapping.source_node_id === sourceNode.id,
        ),
        "Possible edge mappings did not include the source node.",
      );

      const renamedOutput = await client.patch(
        apiPath(
          "/agent-playground/graphs/{id}/versions/{version_id}/ports/{port_id}/",
          {
            id: graphId,
            version_id: draftVersionId,
            port_id: sourceOutput.id,
          },
        ),
        { display_name: "raw_response" },
      );
      assert(
        renamedOutput.display_name === "raw_response",
        "Port update did not return the renamed output port.",
      );

      await client.patch(
        apiPath("/agent-playground/graphs/{id}/", { id: graphId }),
        {
          name: updatedGraphName,
          description: "Updated by AGT-API-001",
        },
      );
      const updatedGraph = await client.get(
        apiPath("/agent-playground/graphs/{id}/", { id: graphId }),
      );
      assert(
        updatedGraph.name === updatedGraphName,
        "Agent graph metadata update did not persist.",
      );

      const datasetBeforeRowCreate = await client.get(
        apiPath("/agent-playground/graphs/{graph_id}/dataset/", {
          graph_id: graphId,
        }),
        { query: { version_id: draftVersionId, page: 1, page_size: 10 } },
      );
      assert(
        datasetBeforeRowCreate.columns?.some(
          (column) => column.name === "topic",
        ),
        "Graph dataset did not include the exposed input column.",
      );
      assert(
        datasetBeforeRowCreate.rows?.length >= 1,
        "Graph dataset did not include the minimum input row.",
      );

      const createdRow = await client.post(
        apiPath("/agent-playground/graphs/{graph_id}/dataset/rows/", {
          graph_id: graphId,
        }),
      );
      assert(
        isUuid(createdRow.id),
        "Graph dataset row create did not return an id.",
      );
      const topicCell = (createdRow.cells || []).find((cell) =>
        datasetBeforeRowCreate.columns?.some(
          (column) => column.id === cell.column_id && column.name === "topic",
        ),
      );
      assert(
        topicCell?.id,
        "Created graph dataset row did not include a topic cell.",
      );

      const updatedCell = await client.put(
        apiPath(
          "/agent-playground/graphs/{graph_id}/dataset/cells/{cell_id}/",
          {
            graph_id: graphId,
            cell_id: topicCell.id,
          },
        ),
        { value: "agent-playground API journey" },
      );
      assert(
        updatedCell.value === "agent-playground API journey",
        "Graph dataset cell update did not persist the value.",
      );

      await client.request(
        "DELETE",
        apiPath("/agent-playground/graphs/{graph_id}/dataset/rows/delete/", {
          graph_id: graphId,
        }),
        { body: { row_ids: [createdRow.id] } },
      );
      const datasetAfterRowDelete = await client.get(
        apiPath("/agent-playground/graphs/{graph_id}/dataset/", {
          graph_id: graphId,
        }),
        { query: { version_id: draftVersionId, page: 1, page_size: 10 } },
      );
      assert(
        !datasetAfterRowDelete.rows?.some((row) => row.id === createdRow.id),
        "Graph dataset row delete left the deleted row visible.",
      );

      const inactiveExecute = await expectApiError(
        () =>
          client.post(
            apiPath("/agent-playground/graphs/{graph_id}/dataset/execute/", {
              graph_id: graphId,
            }),
            {},
          ),
        [404],
        "Draft graph dataset execute unexpectedly succeeded.",
      );

      const secondVersion = await client.post(
        apiPath("/agent-playground/graphs/{id}/versions/", { id: graphId }),
        { commit_message: "temporary draft for delete coverage" },
      );
      assert(
        isUuid(secondVersion.id),
        "Second graph version create returned no id.",
      );
      const secondVersionDetail = await client.get(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/", {
          id: graphId,
          version_id: secondVersion.id,
        }),
      );
      assert(
        secondVersionDetail.id === secondVersion.id,
        "Second graph version detail id mismatch.",
      );
      await client.delete(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/", {
          id: graphId,
          version_id: secondVersion.id,
        }),
      );

      const activatedVersion = await client.patch(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/", {
          id: graphId,
          version_id: draftVersionId,
        }),
        { status: "active", commit_message: "activate AGT-API-001 graph" },
      );
      assert(
        activatedVersion.status === "active",
        "Graph version activation did not return active status.",
      );

      const restoreDraftVersion = await client.post(
        apiPath("/agent-playground/graphs/{id}/versions/", { id: graphId }),
        {
          commit_message: "temporary active version before restore coverage",
          nodes: [
            {
              id: randomUUID(),
              type: "atomic",
              name: restoreNodeName,
              node_template_id: llmTemplate.id,
              position: { x: 80, y: 220 },
              prompt_template: {
                messages: [
                  {
                    id: "msg-restore",
                    role: "user",
                    content: [
                      {
                        type: "text",
                        text: "Respond with one short sentence about {{topic}}.",
                      },
                    ],
                  },
                ],
                response_format: "text",
                model: "gpt-4o-mini",
                temperature: 0,
                metadata: { api_journey: "AGT-API-001", run_id: runId },
              },
            },
          ],
        },
      );
      assert(
        isUuid(restoreDraftVersion.id) &&
          restoreDraftVersion.status === "draft",
        "Temporary restore version create did not return a draft version.",
      );
      const restoreVersion = await client.patch(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/", {
          id: graphId,
          version_id: restoreDraftVersion.id,
        }),
        {
          status: "active",
          commit_message: "temporary active version before restore coverage",
        },
      );
      assert(
        isUuid(restoreVersion.id) && restoreVersion.status === "active",
        "Temporary restore version activation did not return active status.",
      );
      const inactiveDraftDetail = await client.get(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/", {
          id: graphId,
          version_id: draftVersionId,
        }),
      );
      assert(
        inactiveDraftDetail.status === "inactive",
        "Creating a second active graph version did not demote the prior active version.",
      );

      const restoredVersion = await client.post(
        apiPath(
          "/agent-playground/graphs/{id}/versions/{version_id}/activate/",
          {
            id: graphId,
            version_id: draftVersionId,
          },
        ),
      );
      assert(
        restoredVersion.id === draftVersionId &&
          restoredVersion.status === "active",
        "Graph restore activate endpoint did not restore the prior version.",
      );
      assert(
        restoredVersion.nodes?.some((node) => node.id === sourceNode.id) &&
          restoredVersion.nodes?.some((node) => node.id === targetNode.id),
        "Restored graph version did not return the original nodes.",
      );
      const demotedRestoreVersion = await client.get(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/", {
          id: graphId,
          version_id: restoreVersion.id,
        }),
      );
      assert(
        demotedRestoreVersion.status === "inactive",
        "Restoring the prior graph version did not demote the temporary active version.",
      );
      const activeRestoreError = await expectApiError(
        () =>
          client.post(
            apiPath(
              "/agent-playground/graphs/{id}/versions/{version_id}/activate/",
              { id: graphId, version_id: draftVersionId },
            ),
          ),
        [400],
        "Activating an already-active graph version unexpectedly succeeded.",
      );
      await client.delete(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/", {
          id: graphId,
          version_id: restoreVersion.id,
        }),
      );

      const referenceableGraphs = await client.get(
        apiPath("/agent-playground/graphs/{id}/referenceable-graphs/", {
          id: graphId,
        }),
      );
      assert(
        Array.isArray(referenceableGraphs.graphs),
        "Referenceable graphs endpoint did not return a graphs array.",
      );

      const executions = await client.get(
        apiPath("/agent-playground/graphs/{graph_id}/executions/", {
          graph_id: graphId,
        }),
        { query: { page: 1, page_size: 10 } },
      );
      assert(
        Array.isArray(executions.executions) || Array.isArray(executions),
        "Graph execution list did not return a list payload.",
      );

      const missingExecution = await expectApiError(
        () =>
          client.get(
            apiPath(
              "/agent-playground/graphs/{graph_id}/executions/{execution_id}/",
              { graph_id: graphId, execution_id: randomUUID() },
            ),
          ),
        [404],
        "Missing graph execution detail unexpectedly succeeded.",
      );

      const preDeleteAudit = await loadAgentGraphDbAudit({ graphId });
      assertAgentGraphPreDeleteAudit(preDeleteAudit, {
        organizationId,
        workspaceId,
      });

      await client.post(apiPath("/agent-playground/graphs/delete/"), {
        ids: [graphId],
      });
      const deletedGraphDetail = await expectApiError(
        () =>
          client.get(
            apiPath("/agent-playground/graphs/{id}/", { id: graphId }),
          ),
        [404],
        "Deleted agent graph detail unexpectedly succeeded.",
      );

      const postDeleteAudit = await loadAgentGraphDbAudit({ graphId });
      assertAgentGraphPostDeleteAudit(postDeleteAudit);

      evidence.push({
        graph_id: graphId,
        draft_version_id: draftVersionId,
        source_node_id: sourceNode.id,
        target_node_id: targetNode.id,
        template_count: nodeTemplates.length,
        dataset_id: datasetBeforeRowCreate.dataset_id,
        inactive_execute_status: inactiveExecute.status,
        restored_version_id: restoredVersion.id,
        demoted_restore_version_id: restoreVersion.id,
        active_restore_error_status: activeRestoreError.status,
        mismatched_prompt_version_status: mismatchedPromptVersion.status,
        missing_execution_status: missingExecution.status,
        deleted_graph_status: deletedGraphDetail.status,
        pre_delete_audit: preDeleteAudit,
        post_delete_audit: postDeleteAudit,
      });
    },
  },
  {
    id: "AGT-API-002",
    title: "Agent playground execution and node execution detail readback",
    tags: ["agents", "agent-playground", "execution", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const marker = runId.replace(/[^a-z0-9]/gi, "").slice(0, 18);
      const graphName = `api journey agent exec ${marker}`;
      const nodeName = `api_exec_node_${marker}`.slice(0, 80);
      const inputValue = `agent execution input ${runId}`;
      const outputValue = `agent execution output ${runId}`;
      const graphExecutionId = randomUUID();
      const nodeExecutionId = randomUUID();
      const inputDataId = randomUUID();
      const outputDataId = randomUUID();
      let graphDeleted = false;

      const templateListPayload = await client.get(
        apiPath("/agent-playground/node-templates/"),
      );
      const nodeTemplates = Array.isArray(templateListPayload?.node_templates)
        ? templateListPayload.node_templates
        : asArray(templateListPayload);
      const llmTemplate = nodeTemplates.find(
        (item) => item.name === "llm_prompt",
      );
      assert(
        llmTemplate?.id,
        "Agent node templates did not include the seeded llm_prompt template.",
      );

      const createdGraph = await client.post(
        apiPath("/agent-playground/graphs/"),
        {
          name: graphName,
          description: `Disposable execution readback graph ${runId}`,
        },
      );
      const graphId = createdGraph.id;
      const draftVersionId = createdGraph.active_version?.id;
      assert(isUuid(graphId), "Agent graph create did not return a graph id.");
      assert(
        isUuid(draftVersionId),
        "Agent graph create did not return an initial draft version id.",
      );

      cleanup.defer("hard delete disposable agent execution graph", () =>
        hardDeleteAgentGraph({
          graphId,
          graphNames: [graphName],
          promptNames: [nodeName],
          organizationId,
          workspaceId,
        }),
      );

      const sourceNode = await client.post(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/nodes/", {
          id: graphId,
          version_id: draftVersionId,
        }),
        {
          id: randomUUID(),
          type: "atomic",
          name: nodeName,
          node_template_id: llmTemplate.id,
          position: { x: 80, y: 120 },
          prompt_template: {
            messages: [
              {
                id: "msg-exec",
                role: "user",
                content: [
                  {
                    type: "text",
                    text: "Return one deterministic sentence about {{topic}}.",
                  },
                ],
              },
            ],
            response_format: "text",
            model: "gpt-4o-mini",
            temperature: 0,
            metadata: { api_journey: "AGT-API-002", run_id: runId },
          },
        },
      );
      assert(
        isUuid(sourceNode.id),
        "Execution graph node create returned no id.",
      );
      const inputPort = findPort(sourceNode, {
        direction: "input",
        displayName: "topic",
      });
      const outputPort = findPort(sourceNode, {
        direction: "output",
        displayName: "response",
      });
      assert(inputPort?.id, "Execution graph node did not expose topic input.");
      assert(
        outputPort?.id,
        "Execution graph node did not expose response output.",
      );

      const activatedVersion = await client.patch(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/", {
          id: graphId,
          version_id: draftVersionId,
        }),
        { status: "active", commit_message: "activate AGT-API-002 graph" },
      );
      assert(
        activatedVersion.status === "active",
        "Graph version activation did not return active status.",
      );

      const seedAudit = await seedAgentExecutionFixture({
        graphExecutionId,
        nodeExecutionId,
        inputDataId,
        outputDataId,
        graphVersionId: draftVersionId,
        nodeId: sourceNode.id,
        inputPortId: inputPort.id,
        outputPortId: outputPort.id,
        inputValue,
        outputValue,
      });
      assert(
        seedAudit.graph_execution_visible === 1 &&
          seedAudit.node_execution_visible === 1 &&
          seedAudit.execution_data_visible === 2,
        "Seeded agent execution fixture was not DB-visible.",
      );

      const executionList = await client.get(
        apiPath("/agent-playground/graphs/{graph_id}/executions/", {
          graph_id: graphId,
        }),
        { query: { page: 1, page_size: 10 } },
      );
      const executions = executionList.executions || [];
      const listedExecution = executions.find(
        (execution) => execution.id === graphExecutionId,
      );
      assert(
        listedExecution?.status === "success",
        "Agent execution list did not include the seeded successful execution.",
      );

      const executionDetail = await client.get(
        apiPath(
          "/agent-playground/graphs/{graph_id}/executions/{execution_id}/",
          {
            graph_id: graphId,
            execution_id: graphExecutionId,
          },
        ),
      );
      assert(
        executionDetail.id === graphExecutionId,
        "Agent execution detail id mismatch.",
      );
      assert(
        executionDetail.status === "success",
        "Agent execution detail status mismatch.",
      );
      assert(
        executionDetail.input_payload?.topic === inputValue,
        "Agent execution detail did not expose the input payload.",
      );
      assert(
        executionDetail.output_payload?.response === outputValue,
        "Agent execution detail did not expose the output payload.",
      );
      const detailedNode = (executionDetail.nodes || []).find(
        (node) => node.id === sourceNode.id,
      );
      assert(
        detailedNode?.node_execution?.id === nodeExecutionId,
        "Agent execution detail did not attach the node execution.",
      );

      const wrongGraphExecution = await expectApiError(
        () =>
          client.get(
            apiPath(
              "/agent-playground/graphs/{graph_id}/executions/{execution_id}/",
              { graph_id: randomUUID(), execution_id: graphExecutionId },
            ),
          ),
        [404],
        "Agent execution detail ignored the graph id boundary.",
      );

      const nodeExecutionDetail = await client.get(
        apiPath(
          "/agent-playground/executions/{execution_id}/nodes/{node_execution_id}/",
          {
            execution_id: graphExecutionId,
            node_execution_id: nodeExecutionId,
          },
        ),
      );
      assert(
        nodeExecutionDetail.node_execution_id === nodeExecutionId,
        "Node execution detail id mismatch.",
      );
      assert(
        nodeExecutionDetail.node_id === sourceNode.id,
        "Node execution detail node id mismatch.",
      );
      assert(
        nodeExecutionDetail.status === "success",
        "Node execution detail status mismatch.",
      );
      assert(
        nodeExecutionDetail.duration_seconds >= 6,
        "Node execution detail duration was not calculated.",
      );
      assert(
        nodeExecutionDetail.inputs?.some(
          (entry) =>
            entry.port_id === inputPort.id &&
            entry.port_key === inputPort.key &&
            entry.payload === inputValue &&
            entry.is_valid === true,
        ),
        "Node execution detail did not return the seeded input payload.",
      );
      assert(
        nodeExecutionDetail.outputs?.some(
          (entry) =>
            entry.port_id === outputPort.id &&
            entry.port_key === outputPort.key &&
            entry.payload === outputValue &&
            entry.is_valid === true,
        ),
        "Node execution detail did not return the seeded output payload.",
      );

      const missingNodeExecution = await expectApiError(
        () =>
          client.get(
            apiPath(
              "/agent-playground/executions/{execution_id}/nodes/{node_execution_id}/",
              {
                execution_id: graphExecutionId,
                node_execution_id: randomUUID(),
              },
            ),
          ),
        [404],
        "Missing node execution detail unexpectedly succeeded.",
      );

      await client.post(apiPath("/agent-playground/graphs/delete/"), {
        ids: [graphId],
      });
      graphDeleted = true;

      const postDeleteAudit = await loadAgentExecutionDbAudit({
        graphId,
        graphExecutionId,
        nodeExecutionId,
      });
      assert(
        postDeleteAudit.graph_execution_visible === 0 &&
          postDeleteAudit.node_execution_visible === 0 &&
          postDeleteAudit.execution_data_visible === 0,
        "Public graph delete left execution rows visible.",
      );

      const deletedExecutionDetail = await expectApiError(
        () =>
          client.get(
            apiPath(
              "/agent-playground/graphs/{graph_id}/executions/{execution_id}/",
              { graph_id: graphId, execution_id: graphExecutionId },
            ),
          ),
        [404],
        "Deleted graph execution detail unexpectedly succeeded.",
      );

      evidence.push({
        graph_id: graphId,
        graph_deleted: graphDeleted,
        graph_execution_id: graphExecutionId,
        node_execution_id: nodeExecutionId,
        node_id: sourceNode.id,
        input_port_id: inputPort.id,
        output_port_id: outputPort.id,
        execution_list_count: executions.length,
        node_duration_seconds: nodeExecutionDetail.duration_seconds,
        wrong_graph_status: wrongGraphExecution.status,
        missing_node_execution_status: missingNodeExecution.status,
        deleted_execution_status: deletedExecutionDetail.status,
        seed_audit: seedAudit,
        post_delete_audit: postDeleteAudit,
      });
    },
  },
  {
    id: "AGT-API-003",
    title:
      "Agent playground active dataset execute creates pollable execution state",
    tags: ["agents", "agent-playground", "execution", "mutating", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const marker = runId.replace(/[^a-z0-9]/gi, "").slice(0, 18);
      const graphName = `api journey agent active exec ${marker}`;
      const nodeName = `api_active_exec_${marker}`.slice(0, 80);
      const inputValue = `agent active execution input ${runId}`;

      const templateListPayload = await client.get(
        apiPath("/agent-playground/node-templates/"),
      );
      const nodeTemplates = Array.isArray(templateListPayload?.node_templates)
        ? templateListPayload.node_templates
        : asArray(templateListPayload);
      const llmTemplate = nodeTemplates.find(
        (item) => item.name === "llm_prompt",
      );
      assert(
        llmTemplate?.id,
        "Agent node templates did not include the seeded llm_prompt template.",
      );

      const createdGraph = await client.post(
        apiPath("/agent-playground/graphs/"),
        {
          name: graphName,
          description: `Disposable active execution graph ${runId}`,
        },
      );
      const graphId = createdGraph.id;
      const draftVersionId = createdGraph.active_version?.id;
      assert(isUuid(graphId), "Agent graph create did not return a graph id.");
      assert(
        isUuid(draftVersionId),
        "Agent graph create did not return an initial draft version id.",
      );

      cleanup.defer("hard delete disposable active execution graph", () =>
        hardDeleteAgentGraph({
          graphId,
          graphNames: [graphName],
          promptNames: [nodeName],
          organizationId,
          workspaceId,
        }),
      );

      const node = await client.post(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/nodes/", {
          id: graphId,
          version_id: draftVersionId,
        }),
        {
          id: randomUUID(),
          type: "atomic",
          name: nodeName,
          node_template_id: llmTemplate.id,
          position: { x: 80, y: 120 },
          prompt_template: {
            messages: [
              {
                id: "msg-active-exec",
                role: "user",
                content: [
                  {
                    type: "text",
                    text: "Return exactly this topic value: {{topic}}.",
                  },
                ],
              },
            ],
            response_format: "text",
            model: "gpt-4o-mini",
            temperature: 0,
            metadata: { api_journey: "AGT-API-003", run_id: runId },
          },
        },
      );
      assert(isUuid(node.id), "Active execution node create returned no id.");
      const inputPort = findPort(node, {
        direction: "input",
        displayName: "topic",
      });
      const outputPort = findPort(node, {
        direction: "output",
        displayName: "response",
      });
      assert(
        inputPort?.id,
        "Active execution node did not expose topic input.",
      );
      assert(
        outputPort?.id,
        "Active execution node did not expose response output.",
      );

      const dataset = await client.get(
        apiPath("/agent-playground/graphs/{graph_id}/dataset/", {
          graph_id: graphId,
        }),
        { query: { version_id: draftVersionId, page: 1, page_size: 10 } },
      );
      const topicColumn = (dataset.columns || []).find(
        (column) => column.name === "topic",
      );
      assert(topicColumn?.id, "Graph dataset did not include topic column.");
      const inputRow = (dataset.rows || [])[0];
      assert(inputRow?.id, "Graph dataset did not include an input row.");
      const topicCell = (inputRow.cells || []).find(
        (cell) => cell.column_id === topicColumn.id,
      );
      assert(topicCell?.id, "Graph dataset row did not include a topic cell.");

      await client.put(
        apiPath(
          "/agent-playground/graphs/{graph_id}/dataset/cells/{cell_id}/",
          {
            graph_id: graphId,
            cell_id: topicCell.id,
          },
        ),
        { value: inputValue },
      );

      const activatedVersion = await client.patch(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/", {
          id: graphId,
          version_id: draftVersionId,
        }),
        { status: "active", commit_message: "activate AGT-API-003 graph" },
      );
      assert(
        activatedVersion.status === "active",
        "Graph version activation did not return active status.",
      );

      const emptyRowsExecute = await expectApiError(
        () =>
          client.post(
            apiPath("/agent-playground/graphs/{graph_id}/dataset/execute/", {
              graph_id: graphId,
            }),
            { row_ids: [], task_queue: "tasks_l" },
          ),
        [400],
        "Active graph dataset execute accepted an explicit empty row_ids list.",
      );
      const missingRowsExecute = await expectApiError(
        () =>
          client.post(
            apiPath("/agent-playground/graphs/{graph_id}/dataset/execute/", {
              graph_id: graphId,
            }),
            { row_ids: [randomUUID()], task_queue: "tasks_l" },
          ),
        [404],
        "Active graph dataset execute accepted a missing dataset row id.",
      );
      const preDispatchAudit = await loadAgentActiveExecutionAudit({
        graphId,
      });
      assert(
        preDispatchAudit.execution_count === 0,
        "Invalid active dataset execute row_ids created a graph execution row.",
      );

      let workflowDispatch = "started";
      let executionId = null;
      let terminalDetail = null;
      let executeErrorStatus = null;
      try {
        const execute = await client.post(
          apiPath("/agent-playground/graphs/{graph_id}/dataset/execute/", {
            graph_id: graphId,
          }),
          { row_ids: [inputRow.id], task_queue: "tasks_l" },
        );
        const executionIds = execute.execution_ids || [];
        executionId = executionIds[0];
        assert(
          isUuid(executionId),
          "Active graph dataset execute did not return an execution id.",
        );
        terminalDetail = await pollAgentExecutionDetail({
          client,
          graphId,
          executionId,
        });
        workflowDispatch = terminalDetail.terminal
          ? "terminal"
          : "started_pending";
      } catch (error) {
        assert(
          error?.status === 500,
          `Active graph dataset execute failed before persistence with unexpected status: ${
            error?.status || error.message
          }`,
        );
        executeErrorStatus = error.status;
        workflowDispatch = "failed_to_start";
      }

      const dispatchAudit = await loadAgentActiveExecutionAudit({
        graphId,
      });
      assert(
        dispatchAudit.execution_count === 1,
        "Active dataset execute did not create exactly one graph execution row.",
      );
      if (!executionId) {
        executionId = dispatchAudit.latest_execution_id;
      }
      assert(
        isUuid(executionId),
        "DB audit did not find a graph execution id.",
      );
      assert(
        dispatchAudit.latest_input_topic === inputValue,
        "Graph execution input payload did not persist the dataset row value.",
      );
      if (workflowDispatch === "failed_to_start") {
        assert(
          dispatchAudit.latest_status === "failed",
          "Temporal dispatch failure left GraphExecution non-failed.",
        );
        assert(
          String(dispatchAudit.latest_error_message || "").includes(
            "Failed to start graph execution workflow",
          ),
          "Temporal dispatch failure did not persist an actionable error message.",
        );
      }
      if (terminalDetail?.terminal) {
        assert(
          ["success", "failed"].includes(terminalDetail.status),
          "Terminal execution detail returned an unexpected status.",
        );
        assert(
          (terminalDetail.nodes || []).some(
            (detailNode) => detailNode.id === node.id,
          ),
          "Terminal execution detail did not include the executed node.",
        );
      }

      await client.post(apiPath("/agent-playground/graphs/delete/"), {
        ids: [graphId],
      });

      const postDeleteAudit = await loadAgentExecutionDbAudit({
        graphId,
        graphExecutionId: executionId,
        nodeExecutionId:
          terminalDetail?.nodes?.find((detailNode) => detailNode.id === node.id)
            ?.node_execution?.id || randomUUID(),
      });
      assert(
        postDeleteAudit.graph_execution_visible === 0,
        "Public graph delete left active execution graph row visible.",
      );

      evidence.push({
        graph_id: graphId,
        version_id: draftVersionId,
        node_id: node.id,
        input_port_id: inputPort.id,
        output_port_id: outputPort.id,
        input_row_id: inputRow.id,
        empty_row_ids_status: emptyRowsExecute.status,
        missing_row_ids_status: missingRowsExecute.status,
        execution_id: executionId,
        workflow_dispatch: workflowDispatch,
        execute_error_status: executeErrorStatus,
        latest_status: dispatchAudit.latest_status,
        latest_error_message_present: Boolean(
          dispatchAudit.latest_error_message,
        ),
        terminal_status: terminalDetail?.terminal
          ? terminalDetail.status
          : null,
        post_delete_audit: postDeleteAudit,
      });
    },
  },
  {
    id: "AGT-API-004",
    title:
      "Agent playground direct graph, version, node, and connection mutation routes",
    tags: [
      "agents",
      "agent-playground",
      "mutating",
      "compatibility",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const marker = runId.replace(/[^a-z0-9]/gi, "").slice(0, 18);
      const graphName = `api journey agent direct ${marker}`;
      const updatedGraphName = `${graphName} updated`;
      const sourceNodeName = `api_direct_source_${marker}`.slice(0, 80);
      const targetNodeName = `api_direct_target_${marker}`.slice(0, 80);

      const templateListPayload = await client.get(
        apiPath("/agent-playground/node-templates/"),
      );
      const nodeTemplates = Array.isArray(templateListPayload?.node_templates)
        ? templateListPayload.node_templates
        : asArray(templateListPayload);
      const llmTemplate = nodeTemplates.find(
        (item) => item.name === "llm_prompt",
      );
      assert(
        llmTemplate?.id,
        "Agent node templates did not include the seeded llm_prompt template.",
      );

      const createdGraph = await client.post(
        apiPath("/agent-playground/graphs/"),
        {
          name: graphName,
          description: `Disposable direct-route graph ${runId}`,
        },
      );
      const graphId = createdGraph.id;
      const draftVersionId = createdGraph.active_version?.id;
      assert(isUuid(graphId), "Agent graph create did not return a graph id.");
      assert(
        isUuid(draftVersionId),
        "Agent graph create did not return an initial draft version id.",
      );

      cleanup.defer("hard delete disposable direct-route graph", () =>
        hardDeleteAgentGraph({
          graphId,
          graphNames: [graphName, updatedGraphName],
          promptNames: [sourceNodeName, targetNodeName],
          organizationId,
          workspaceId,
        }),
      );

      const sourceNode = await client.post(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/nodes/", {
          id: graphId,
          version_id: draftVersionId,
        }),
        {
          id: randomUUID(),
          type: "atomic",
          name: sourceNodeName,
          node_template_id: llmTemplate.id,
          position: { x: 80, y: 100 },
          prompt_template: {
            messages: [
              {
                id: "msg-direct-source",
                role: "user",
                content: [
                  {
                    type: "text",
                    text: "Return exactly this topic value: {{topic}}.",
                  },
                ],
              },
            ],
            response_format: "text",
            model: "gpt-4o-mini",
            temperature: 0,
            metadata: { api_journey: "AGT-API-004", run_id: runId },
          },
        },
      );
      const targetNode = await client.post(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/nodes/", {
          id: graphId,
          version_id: draftVersionId,
        }),
        {
          id: randomUUID(),
          type: "atomic",
          name: targetNodeName,
          node_template_id: llmTemplate.id,
          position: { x: 360, y: 100 },
          prompt_template: {
            messages: [
              {
                id: "msg-direct-target",
                role: "user",
                content: [
                  {
                    type: "text",
                    text: `Blend {{topic}} with {{${sourceNodeName}.response}}.`,
                  },
                ],
              },
            ],
            response_format: "text",
            model: "gpt-4o-mini",
            temperature: 0,
            metadata: { api_journey: "AGT-API-004", run_id: runId },
          },
        },
      );
      assert(
        isUuid(sourceNode.id) && isUuid(targetNode.id),
        "Direct-route node setup did not return node ids.",
      );

      const targetInputPort = findPort(targetNode, {
        direction: "input",
        displayName: `${sourceNodeName}.response`,
      });
      assert(
        targetInputPort?.id,
        "Target node did not expose the source response input port.",
      );

      const nodeConnectionId = randomUUID();
      const nodeConnection = await client.post(
        apiPath(
          "/agent-playground/graphs/{id}/versions/{version_id}/node-connections/",
          { id: graphId, version_id: draftVersionId },
        ),
        {
          id: nodeConnectionId,
          source_node_id: sourceNode.id,
          target_node_id: targetNode.id,
        },
      );
      assert(
        nodeConnection.id === nodeConnectionId,
        "Direct node connection create returned the wrong id.",
      );

      const afterConnectionAudit = await loadAgentGraphDbAudit({ graphId });
      assert(
        afterConnectionAudit.node_visible === 2,
        "Direct-route graph should have two visible nodes after setup.",
      );
      assert(
        afterConnectionAudit.node_connection_visible === 1,
        "Direct node connection create did not persist a visible connection.",
      );

      const possibleMappings = asArray(
        await client.get(
          apiPath(
            "/agent-playground/graphs/{id}/versions/{version_id}/nodes/{node_id}/possible-edge-mappings/",
            {
              id: graphId,
              version_id: draftVersionId,
              node_id: targetNode.id,
            },
          ),
        ),
      );
      assert(
        possibleMappings.some(
          (mapping) =>
            mapping.node_connection_id === nodeConnectionId &&
            mapping.source_node_id === sourceNode.id,
        ),
        "Possible edge mappings did not reflect the direct node connection.",
      );

      await client.delete(
        apiPath(
          "/agent-playground/graphs/{id}/versions/{version_id}/node-connections/{nc_id}/",
          {
            id: graphId,
            version_id: draftVersionId,
            nc_id: nodeConnectionId,
          },
        ),
      );
      const afterConnectionDeleteAudit = await loadAgentGraphDbAudit({
        graphId,
      });
      assert(
        afterConnectionDeleteAudit.node_connection_visible === 0,
        "Direct node connection delete left a visible connection.",
      );

      const possibleMappingsAfterDelete = asArray(
        await client.get(
          apiPath(
            "/agent-playground/graphs/{id}/versions/{version_id}/nodes/{node_id}/possible-edge-mappings/",
            {
              id: graphId,
              version_id: draftVersionId,
              node_id: targetNode.id,
            },
          ),
        ),
      );
      assert(
        !possibleMappingsAfterDelete.some(
          (mapping) => mapping.node_connection_id === nodeConnectionId,
        ),
        "Possible edge mappings still exposed the deleted node connection.",
      );

      await client.delete(
        apiPath(
          "/agent-playground/graphs/{id}/versions/{version_id}/nodes/{node_id}/",
          {
            id: graphId,
            version_id: draftVersionId,
            node_id: targetNode.id,
          },
        ),
      );
      const deletedNodeDetail = await expectApiError(
        () =>
          client.get(
            apiPath(
              "/agent-playground/graphs/{id}/versions/{version_id}/nodes/{node_id}/",
              {
                id: graphId,
                version_id: draftVersionId,
                node_id: targetNode.id,
              },
            ),
          ),
        [404],
        "Deleted agent node detail unexpectedly succeeded.",
      );
      const afterNodeDeleteAudit = await loadAgentGraphDbAudit({ graphId });
      assert(
        afterNodeDeleteAudit.node_visible === 1,
        "Direct node delete did not leave exactly one visible node.",
      );

      const putGraph = await client.put(
        apiPath("/agent-playground/graphs/{id}/", { id: graphId }),
        {
          name: updatedGraphName,
          description: "Updated by AGT-API-004 direct PUT",
        },
      );
      assert(
        putGraph.name === updatedGraphName,
        "Direct graph PUT did not persist the updated name.",
      );

      const versionMetadata = await client.put(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/", {
          id: graphId,
          version_id: draftVersionId,
        }),
        { commit_message: "metadata PUT before activation" },
      );
      assert(
        versionMetadata.commit_message === "metadata PUT before activation",
        "Direct graph version PUT did not persist commit metadata.",
      );

      const activatedVersion = await client.put(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/", {
          id: graphId,
          version_id: draftVersionId,
        }),
        { status: "active", commit_message: "activate AGT-API-004 graph" },
      );
      assert(
        activatedVersion.status === "active",
        "Direct graph version PUT did not activate the draft version.",
      );

      const preDeleteAudit = await loadAgentGraphDbAudit({ graphId });
      assert(
        preDeleteAudit.graph_visible === 1 &&
          preDeleteAudit.version_visible === 1 &&
          preDeleteAudit.node_visible === 1,
        "Direct-route graph was not visible before direct graph DELETE.",
      );

      await client.delete(
        apiPath("/agent-playground/graphs/{id}/", { id: graphId }),
      );
      const deletedGraphDetail = await expectApiError(
        () =>
          client.get(
            apiPath("/agent-playground/graphs/{id}/", { id: graphId }),
          ),
        [404],
        "Deleted agent graph detail unexpectedly succeeded.",
      );
      const postDeleteAudit = await loadAgentGraphDbAudit({ graphId });
      assertAgentGraphPostDeleteAudit(postDeleteAudit);

      evidence.push({
        graph_id: graphId,
        version_id: draftVersionId,
        source_node_id: sourceNode.id,
        target_node_id: targetNode.id,
        node_connection_id: nodeConnectionId,
        target_input_port_id: targetInputPort.id,
        deleted_node_status: deletedNodeDetail.status,
        deleted_graph_status: deletedGraphDetail.status,
        after_connection_audit: afterConnectionAudit,
        after_connection_delete_audit: afterConnectionDeleteAudit,
        after_node_delete_audit: afterNodeDeleteAudit,
        post_delete_audit: postDeleteAudit,
      });
    },
  },
  {
    id: "AGT-API-005",
    title: "Agent playground trace import lifecycle and workspace guard",
    tags: [
      "agents",
      "agent-playground",
      "trace-import",
      "mutating",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      user,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );
      const userId = currentUserId(user);
      assert(
        isUuid(userId),
        "Authenticated context did not resolve a user id.",
      );

      const marker = runId.replace(/[^a-z0-9]/gi, "").slice(0, 18);
      const fixture = await seedAgentTraceImportFixture({
        marker,
        organizationId,
        workspaceId,
        userId,
      });
      cleanup.defer("hard delete trace-import fixture rows", () =>
        hardDeleteAgentTraceImportFixture({
          projectIds: [fixture.activeProjectId, fixture.hiddenProjectId],
          traceIds: [fixture.activeTraceId, fixture.hiddenTraceId],
          hiddenWorkspaceId: fixture.hiddenWorkspaceId,
        }),
      );

      const hiddenTraceImport = await expectApiError(
        () =>
          client.post(apiPath("/agent-playground/graphs/from-trace/"), {
            trace_id: fixture.hiddenTraceId,
          }),
        [404],
        "Trace import unexpectedly accepted a same-org other-workspace trace.",
      );
      const hiddenImportAudit = await loadAgentTraceImportGuardAudit({
        hiddenTraceId: fixture.hiddenTraceId,
      });
      assert(
        hiddenImportAudit.hidden_import_graph_visible === 0,
        "Hidden trace import created a visible graph.",
      );

      const imported = await client.post(
        apiPath("/agent-playground/graphs/from-trace/"),
        {
          trace_id: fixture.activeTraceId,
        },
      );
      const graphId = imported.graph_id;
      const versionId = imported.version_id;
      assert(isUuid(graphId), "Trace import did not return a graph id.");
      assert(isUuid(versionId), "Trace import did not return a version id.");

      cleanup.defer("hard delete trace-import graph", () =>
        hardDeleteAgentGraph({
          graphId,
          graphNames: [`Iterate: ${fixture.activeTraceName}`],
          promptNames: [fixture.rootSpanName, fixture.childSpanName],
          organizationId,
          workspaceId,
        }),
      );

      const graphDetail = await client.get(
        apiPath("/agent-playground/graphs/{id}/", { id: graphId }),
      );
      assert(
        graphDetail.id === graphId,
        "Trace import graph detail id mismatch.",
      );
      assert(
        graphDetail.active_version?.id === versionId,
        "Trace import graph detail did not expose the imported version.",
      );
      const importedNodes = graphDetail.active_version?.nodes || [];
      assert(
        importedNodes.some((node) => node.name === fixture.rootSpanName) &&
          importedNodes.some((node) => node.name === fixture.childSpanName),
        "Trace import graph did not expose the seeded LLM span nodes.",
      );

      const graphAudit = await loadAgentGraphDbAudit({ graphId });
      assert(
        graphAudit.organization_id === organizationId &&
          graphAudit.workspace_id === workspaceId,
        "Trace import graph ownership did not match the active context.",
      );
      assert(
        graphAudit.node_visible === 2,
        "Trace import graph should have two visible nodes.",
      );
      assert(
        graphAudit.node_connection_visible === 1,
        "Trace import graph should have one visible node connection.",
      );
      assert(
        graphAudit.graph_dataset_visible === 1 &&
          graphAudit.dataset_visible === 1,
        "Trace import graph dataset was not visible.",
      );

      await client.post(apiPath("/agent-playground/graphs/delete/"), {
        ids: [graphId],
      });
      const deletedGraphDetail = await expectApiError(
        () =>
          client.get(
            apiPath("/agent-playground/graphs/{id}/", { id: graphId }),
          ),
        [404],
        "Trace-imported graph detail unexpectedly succeeded after delete.",
      );
      const postDeleteAudit = await loadAgentGraphDbAudit({ graphId });
      assertAgentGraphPostDeleteAudit(postDeleteAudit);

      evidence.push({
        active_trace_id: fixture.activeTraceId,
        hidden_trace_id: fixture.hiddenTraceId,
        hidden_import_status: hiddenTraceImport.status,
        graph_id: graphId,
        version_id: versionId,
        imported_node_count: importedNodes.length,
        deleted_graph_status: deletedGraphDetail.status,
        hidden_import_audit: hiddenImportAudit,
        graph_audit: graphAudit,
        post_delete_audit: postDeleteAudit,
      });
    },
  },
];

function findPort(node, { direction, displayName }) {
  return (node.ports || []).find(
    (port) =>
      port.direction === direction &&
      (displayName ? port.display_name === displayName : true),
  );
}

async function expectApiError(fn, expectedStatuses, message) {
  try {
    await fn();
  } catch (error) {
    if (expectedStatuses.includes(error.status)) {
      return { status: error.status, body: error.body };
    }
    throw error;
  }
  throw new Error(message);
}

async function loadAgentGraphDbAudit({ graphId }) {
  const sql = `
WITH target_graph AS (
  SELECT id, organization_id, workspace_id, deleted
  FROM agent_playground_graph
  WHERE id = ${sqlUuid(graphId)}
),
target_versions AS (
  SELECT id, deleted FROM agent_playground_graph_version
  WHERE graph_id IN (SELECT id FROM target_graph)
),
target_nodes AS (
  SELECT id, deleted FROM agent_playground_node
  WHERE graph_version_id IN (SELECT id FROM target_versions)
),
target_datasets AS (
  SELECT gd.id AS graph_dataset_id, gd.dataset_id, gd.deleted AS graph_dataset_deleted
  FROM agent_playground_graph_dataset gd
  WHERE gd.graph_id IN (SELECT id FROM target_graph)
)
SELECT json_build_object(
  'graph_total', (SELECT count(*) FROM target_graph),
  'graph_visible', (SELECT count(*) FROM target_graph WHERE deleted = false),
  'graph_deleted', COALESCE((SELECT bool_or(deleted) FROM target_graph), false),
  'organization_id', (SELECT organization_id FROM target_graph LIMIT 1),
  'workspace_id', (SELECT workspace_id FROM target_graph LIMIT 1),
  'version_total', (SELECT count(*) FROM target_versions),
  'version_visible', (SELECT count(*) FROM target_versions WHERE deleted = false),
  'node_total', (SELECT count(*) FROM target_nodes),
  'node_visible', (SELECT count(*) FROM target_nodes WHERE deleted = false),
  'port_total', (
    SELECT count(*) FROM agent_playground_port
    WHERE node_id IN (SELECT id FROM target_nodes)
  ),
  'port_visible', (
    SELECT count(*) FROM agent_playground_port
    WHERE node_id IN (SELECT id FROM target_nodes) AND deleted = false
  ),
  'node_connection_total', (
    SELECT count(*) FROM agent_playground_node_connection
    WHERE graph_version_id IN (SELECT id FROM target_versions)
  ),
  'node_connection_visible', (
    SELECT count(*) FROM agent_playground_node_connection
    WHERE graph_version_id IN (SELECT id FROM target_versions) AND deleted = false
  ),
  'edge_total', (
    SELECT count(*) FROM agent_playground_edge
    WHERE graph_version_id IN (SELECT id FROM target_versions)
  ),
  'edge_visible', (
    SELECT count(*) FROM agent_playground_edge
    WHERE graph_version_id IN (SELECT id FROM target_versions) AND deleted = false
  ),
  'prompt_template_node_total', (
    SELECT count(*) FROM agent_playground_prompt_template_node
    WHERE node_id IN (SELECT id FROM target_nodes)
  ),
  'prompt_template_node_visible', (
    SELECT count(*) FROM agent_playground_prompt_template_node
    WHERE node_id IN (SELECT id FROM target_nodes) AND deleted = false
  ),
  'graph_dataset_total', (SELECT count(*) FROM target_datasets),
  'graph_dataset_visible', (
    SELECT count(*) FROM target_datasets WHERE graph_dataset_deleted = false
  ),
  'dataset_visible', (
    SELECT count(*) FROM model_hub_dataset
    WHERE id IN (SELECT dataset_id FROM target_datasets) AND deleted = false
  ),
  'column_visible', (
    SELECT count(*) FROM model_hub_column
    WHERE dataset_id IN (SELECT dataset_id FROM target_datasets) AND deleted = false
  ),
  'row_visible', (
    SELECT count(*) FROM model_hub_row
    WHERE dataset_id IN (SELECT dataset_id FROM target_datasets) AND deleted = false
  ),
  'cell_visible', (
    SELECT count(*) FROM model_hub_cell
    WHERE dataset_id IN (SELECT dataset_id FROM target_datasets) AND deleted = false
  )
);
`;
  return runPostgresJson(sql);
}

function assertAgentGraphPreDeleteAudit(
  audit,
  { organizationId, workspaceId },
) {
  assert(audit.graph_visible === 1, "Created agent graph was not DB-visible.");
  assert(
    audit.organization_id === organizationId,
    "Created agent graph organization_id mismatch.",
  );
  assert(
    audit.workspace_id === workspaceId,
    "Created agent graph workspace_id mismatch.",
  );
  assert(audit.version_visible >= 1, "Agent graph had no visible version.");
  assert(
    audit.node_visible === 2,
    "Agent graph should have two visible nodes.",
  );
  assert(
    audit.port_visible >= 4,
    "Agent graph should have visible node ports.",
  );
  assert(
    audit.node_connection_visible === 1,
    "Agent graph should have one visible node connection.",
  );
  assert(
    audit.edge_visible === 1,
    "Agent graph should have one visible edge after node connection.",
  );
  assert(
    audit.prompt_template_node_visible === 2,
    "Agent graph should have two visible prompt-template node links.",
  );
  assert(
    audit.graph_dataset_visible === 1,
    "Agent graph should have a visible graph dataset link.",
  );
  assert(audit.dataset_visible === 1, "Agent graph dataset should be visible.");
  assert(audit.column_visible >= 1, "Agent graph dataset should have columns.");
  assert(audit.row_visible >= 1, "Agent graph dataset should have rows.");
  assert(audit.cell_visible >= 1, "Agent graph dataset should have cells.");
}

function assertAgentGraphPostDeleteAudit(audit) {
  assert(
    audit.graph_total === 1,
    "Deleted agent graph row was missing from DB.",
  );
  assert(audit.graph_visible === 0, "Deleted agent graph remained visible.");
  assert(audit.graph_deleted === true, "Agent graph was not soft-deleted.");
  assert(
    audit.version_visible === 0,
    "Deleted agent graph versions remained visible.",
  );
  assert(
    audit.node_visible === 0,
    "Deleted agent graph nodes remained visible.",
  );
  assert(
    audit.port_visible === 0,
    "Deleted agent graph ports remained visible.",
  );
  assert(
    audit.node_connection_visible === 0,
    "Deleted agent graph node connections remained visible.",
  );
  assert(
    audit.edge_visible === 0,
    "Deleted agent graph edges remained visible.",
  );
  assert(
    audit.prompt_template_node_visible === 0,
    "Deleted agent graph prompt-template node links remained visible.",
  );
  assert(
    audit.graph_dataset_visible === 0,
    "Deleted agent graph dataset link remained visible.",
  );
  assert(
    audit.dataset_visible === 0,
    "Deleted agent graph dataset remained visible.",
  );
  assert(
    audit.column_visible === 0,
    "Deleted agent graph columns remained visible.",
  );
  assert(audit.row_visible === 0, "Deleted agent graph rows remained visible.");
  assert(
    audit.cell_visible === 0,
    "Deleted agent graph cells remained visible.",
  );
}

async function seedAgentTraceImportFixture({
  marker,
  organizationId,
  workspaceId,
  userId,
}) {
  const hiddenWorkspaceId = randomUUID();
  const activeProjectId = randomUUID();
  const hiddenProjectId = randomUUID();
  const activeTraceId = randomUUID();
  const hiddenTraceId = randomUUID();
  const rootSpanName = `agt_trace_root_${marker}`.slice(0, 80);
  const childSpanName = `agt_trace_child_${marker}`.slice(0, 80);
  const activeTraceName = `api journey trace import ${marker}`;
  const hiddenTraceName = `api journey hidden trace import ${marker}`;
  const rootSpanId = `agt_${marker}_root`;
  const childSpanId = `agt_${marker}_child`;
  const hiddenSpanId = `agt_${marker}_hidden`;

  const sql = `
WITH hidden_workspace AS (
  INSERT INTO accounts_workspace (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    display_name,
    description,
    organization_id,
    is_active,
    is_default,
    created_by_id
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(hiddenWorkspaceId)},
    ${sqlTextLiteral(`agt_trace_import_hidden_${marker}`)},
    ${sqlTextLiteral(`AGT trace import hidden ${marker}`)},
    'Temporary hidden workspace for AGT-API-005.',
    ${sqlUuid(organizationId)},
    true,
    false,
    ${sqlUuid(userId)}
  )
  RETURNING id
),
active_project AS (
  INSERT INTO tracer_project (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    organization_id,
    workspace_id,
    model_type,
    name,
    trace_type,
    metadata,
    config,
    session_config,
    user_id,
    source,
    tags
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(activeProjectId)},
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    'GenerativeLLM',
    ${sqlTextLiteral(`agt trace import active ${marker}`)},
    'observe',
    ${sqlJson({ api_journey: "AGT-API-005", marker, scope: "active" })},
    '[]'::jsonb,
    '[]'::jsonb,
    ${sqlUuid(userId)},
    'prototype',
    '[]'::jsonb
  )
  RETURNING id
),
hidden_project AS (
  INSERT INTO tracer_project (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    organization_id,
    workspace_id,
    model_type,
    name,
    trace_type,
    metadata,
    config,
    session_config,
    user_id,
    source,
    tags
  )
  SELECT
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(hiddenProjectId)},
    ${sqlUuid(organizationId)},
    hw.id,
    'GenerativeLLM',
    ${sqlTextLiteral(`agt trace import hidden ${marker}`)},
    'observe',
    ${sqlJson({ api_journey: "AGT-API-005", marker, scope: "hidden" })},
    '[]'::jsonb,
    '[]'::jsonb,
    ${sqlUuid(userId)},
    'prototype',
    '[]'::jsonb
  FROM hidden_workspace hw
  RETURNING id
),
active_trace AS (
  INSERT INTO tracer_trace (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    project_id,
    project_version_id,
    name,
    metadata,
    input,
    output,
    error,
    session_id,
    external_id,
    tags,
    error_analysis_status
  )
  SELECT
    now() - interval '2 minutes',
    now(),
    false,
    NULL,
    ${sqlUuid(activeTraceId)},
    ap.id,
    NULL,
    ${sqlTextLiteral(activeTraceName)},
    ${sqlJson({ api_journey: "AGT-API-005", marker })},
    ${sqlJson({ prompt: "Plan a safe trace import." })},
    ${sqlJson({ response: "Trace import response." })},
    NULL,
    NULL,
    ${sqlTextLiteral(`agt-trace-import-${marker}`)},
    '[]'::jsonb,
    'completed'
  FROM active_project ap
  RETURNING id, project_id
),
hidden_trace AS (
  INSERT INTO tracer_trace (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    project_id,
    project_version_id,
    name,
    metadata,
    input,
    output,
    error,
    session_id,
    external_id,
    tags,
    error_analysis_status
  )
  SELECT
    now() - interval '2 minutes',
    now(),
    false,
    NULL,
    ${sqlUuid(hiddenTraceId)},
    hp.id,
    NULL,
    ${sqlTextLiteral(hiddenTraceName)},
    ${sqlJson({ api_journey: "AGT-API-005", marker })},
    ${sqlJson({ prompt: "Hidden trace input." })},
    ${sqlJson({ response: "Hidden trace output." })},
    NULL,
    NULL,
    ${sqlTextLiteral(`agt-hidden-trace-import-${marker}`)},
    '[]'::jsonb,
    'completed'
  FROM hidden_project hp
  RETURNING id, project_id
),
inserted_spans AS (
  INSERT INTO tracer_observation_span (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    project_id,
    project_version_id,
    trace_id,
    parent_span_id,
    name,
    observation_type,
    operation_name,
    start_time,
    end_time,
    input,
    output,
    model,
    model_parameters,
    latency_ms,
    org_id,
    org_user_id,
    prompt_tokens,
    completion_tokens,
    total_tokens,
    response_time,
    eval_id,
    cost,
    status,
    status_message,
    tags,
    metadata,
    span_events,
    provider,
    input_images,
    eval_input,
    eval_attributes,
    custom_eval_config_id,
    eval_status,
    end_user_id,
    prompt_version_id,
    prompt_label_id,
    span_attributes,
    resource_attributes,
    semconv_source
  )
  SELECT
	    now() - interval '2 minutes',
	    now() - interval '110 seconds',
	    false,
	    NULL::timestamptz,
	    ${sqlTextLiteral(rootSpanId)},
	    t.project_id,
	    NULL::uuid,
	    t.id,
	    NULL,
    ${sqlTextLiteral(rootSpanName)},
    'llm',
    'chat',
    now() - interval '2 minutes',
    now() - interval '110 seconds',
    ${sqlJson({
      messages: [{ role: "user", content: "Summarize {{topic}}." }],
    })},
    ${sqlJson({ choices: [{ message: { role: "assistant", content: "Summary." } }] })},
    'gpt-4o-mini',
    ${sqlJson({ temperature: 0 })},
	    1000,
	    ${sqlUuid(organizationId)},
	    NULL::uuid,
	    8,
	    12,
	    20,
	    1.0,
	    NULL::text,
	    0,
	    'OK',
	    NULL::text,
	    '[]'::jsonb,
    ${sqlJson({ api_journey: "AGT-API-005", marker })},
    '[]'::jsonb,
    'futureagi',
    '[]'::jsonb,
	    ${sqlJson({ topic: "trace import" })},
	    '{}'::jsonb,
	    NULL::uuid,
	    'inactive',
	    NULL::uuid,
	    NULL::uuid,
	    NULL::uuid,
	    ${sqlJson({ template_variables: { topic: "trace import" } })},
	    '{}'::jsonb,
	    'traceai'
  FROM active_trace t
  UNION ALL
  SELECT
	    now() - interval '105 seconds',
	    now() - interval '100 seconds',
	    false,
	    NULL::timestamptz,
	    ${sqlTextLiteral(childSpanId)},
	    t.project_id,
	    NULL::uuid,
	    t.id,
    ${sqlTextLiteral(rootSpanId)},
    ${sqlTextLiteral(childSpanName)},
    'llm',
    'chat',
    now() - interval '105 seconds',
    now() - interval '100 seconds',
    ${sqlJson({
      messages: [{ role: "user", content: "Rewrite {{topic}} clearly." }],
    })},
    ${sqlJson({ choices: [{ message: { role: "assistant", content: "Clear rewrite." } }] })},
    'gpt-4o-mini',
    ${sqlJson({ temperature: 0.1 })},
	    900,
	    ${sqlUuid(organizationId)},
	    NULL::uuid,
	    7,
	    10,
	    17,
	    0.9,
	    NULL::text,
	    0,
	    'OK',
	    NULL::text,
	    '[]'::jsonb,
    ${sqlJson({ api_journey: "AGT-API-005", marker })},
    '[]'::jsonb,
    'futureagi',
    '[]'::jsonb,
	    ${sqlJson({ topic: "trace import" })},
	    '{}'::jsonb,
	    NULL::uuid,
	    'inactive',
	    NULL::uuid,
	    NULL::uuid,
	    NULL::uuid,
	    ${sqlJson({ template_variables: { topic: "trace import" } })},
	    '{}'::jsonb,
	    'traceai'
  FROM active_trace t
  UNION ALL
  SELECT
	    now() - interval '2 minutes',
	    now() - interval '110 seconds',
	    false,
	    NULL::timestamptz,
	    ${sqlTextLiteral(hiddenSpanId)},
	    t.project_id,
	    NULL::uuid,
	    t.id,
    NULL,
    ${sqlTextLiteral(`agt_trace_hidden_${marker}`)},
    'llm',
    'chat',
    now() - interval '2 minutes',
    now() - interval '110 seconds',
    ${sqlJson({ messages: [{ role: "user", content: "Hidden." }] })},
    ${sqlJson({ choices: [{ message: { role: "assistant", content: "Hidden." } }] })},
    'gpt-4o-mini',
    '{}'::jsonb,
	    1000,
	    ${sqlUuid(organizationId)},
	    NULL::uuid,
	    1,
	    1,
	    2,
	    1.0,
	    NULL::text,
	    0,
	    'OK',
	    NULL::text,
	    '[]'::jsonb,
    ${sqlJson({ api_journey: "AGT-API-005", marker })},
    '[]'::jsonb,
    'futureagi',
    '[]'::jsonb,
	    '{}'::jsonb,
	    '{}'::jsonb,
	    NULL::uuid,
	    'inactive',
	    NULL::uuid,
	    NULL::uuid,
	    NULL::uuid,
	    '{}'::jsonb,
    '{}'::jsonb,
    'traceai'
  FROM hidden_trace t
  RETURNING id
)
SELECT json_build_object(
  'active_project_id', ${sqlTextLiteral(activeProjectId)},
  'hidden_project_id', ${sqlTextLiteral(hiddenProjectId)},
  'active_trace_id', ${sqlTextLiteral(activeTraceId)},
  'hidden_trace_id', ${sqlTextLiteral(hiddenTraceId)},
  'hidden_workspace_id', ${sqlTextLiteral(hiddenWorkspaceId)},
  'span_count', (SELECT count(*) FROM inserted_spans)
);
`;
  const audit = await runPostgresJson(sql);
  assert(audit.span_count === 3, "Trace import fixture did not seed spans.");

  return {
    activeProjectId,
    hiddenProjectId,
    activeTraceId,
    hiddenTraceId,
    hiddenWorkspaceId,
    rootSpanName,
    childSpanName,
    activeTraceName,
    hiddenTraceName,
    audit,
  };
}

async function loadAgentTraceImportGuardAudit({ hiddenTraceId }) {
  const sql = `
SELECT json_build_object(
  'hidden_import_graph_visible', (
    SELECT count(*) FROM agent_playground_graph
    WHERE description = ${sqlTextLiteral(`Created from trace ${hiddenTraceId}`)}
      AND deleted = false
  )
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteAgentTraceImportFixture({
  projectIds,
  traceIds,
  hiddenWorkspaceId,
}) {
  const sql = `
BEGIN;
DELETE FROM tracer_observation_span
WHERE trace_id = ANY(${sqlUuidArray(traceIds)});
DELETE FROM tracer_trace
WHERE id = ANY(${sqlUuidArray(traceIds)});
DELETE FROM tracer_project
WHERE id = ANY(${sqlUuidArray(projectIds)});
DELETE FROM accounts_workspace
WHERE id = ${sqlUuid(hiddenWorkspaceId)};
SELECT json_build_object(
  'remaining_projects', (
    SELECT count(*) FROM tracer_project
    WHERE id = ANY(${sqlUuidArray(projectIds)})
  ),
  'remaining_traces', (
    SELECT count(*) FROM tracer_trace
    WHERE id = ANY(${sqlUuidArray(traceIds)})
  ),
  'remaining_workspaces', (
    SELECT count(*) FROM accounts_workspace
    WHERE id = ${sqlUuid(hiddenWorkspaceId)}
  )
);
COMMIT;
`;
  return runPostgresJson(sql);
}

async function hardDeleteAgentGraph({
  graphId,
  graphNames,
  promptNames,
  organizationId,
  workspaceId,
}) {
  const sql = `
BEGIN;
CREATE TEMP TABLE _agt_graph_ids(id uuid) ON COMMIT DROP;
INSERT INTO _agt_graph_ids
SELECT id FROM agent_playground_graph
WHERE id = ${sqlUuid(graphId)}
   OR name = ANY(${sqlTextArray(graphNames)});

CREATE TEMP TABLE _agt_version_ids(id uuid) ON COMMIT DROP;
INSERT INTO _agt_version_ids
SELECT id FROM agent_playground_graph_version
WHERE graph_id IN (SELECT id FROM _agt_graph_ids);

CREATE TEMP TABLE _agt_node_ids(id uuid) ON COMMIT DROP;
INSERT INTO _agt_node_ids
SELECT id FROM agent_playground_node
WHERE graph_version_id IN (SELECT id FROM _agt_version_ids);

CREATE TEMP TABLE _agt_dataset_ids(id uuid) ON COMMIT DROP;
INSERT INTO _agt_dataset_ids
SELECT dataset_id FROM agent_playground_graph_dataset
WHERE graph_id IN (SELECT id FROM _agt_graph_ids);

CREATE TEMP TABLE _agt_prompt_template_ids(id uuid) ON COMMIT DROP;
INSERT INTO _agt_prompt_template_ids
SELECT DISTINCT prompt_template_id FROM agent_playground_prompt_template_node
WHERE node_id IN (SELECT id FROM _agt_node_ids);
INSERT INTO _agt_prompt_template_ids
SELECT id FROM model_hub_prompttemplate
WHERE organization_id = ${sqlUuid(organizationId)}
  AND workspace_id = ${sqlUuid(workspaceId)}
  AND name = ANY(${sqlTextArray(promptNames)});

CREATE TEMP TABLE _agt_prompt_version_ids(id uuid) ON COMMIT DROP;
INSERT INTO _agt_prompt_version_ids
SELECT id FROM model_hub_promptversion
WHERE original_template_id IN (SELECT id FROM _agt_prompt_template_ids);

DELETE FROM model_hub_cell
WHERE dataset_id IN (SELECT id FROM _agt_dataset_ids);
DELETE FROM model_hub_row
WHERE dataset_id IN (SELECT id FROM _agt_dataset_ids);
DELETE FROM model_hub_column
WHERE dataset_id IN (SELECT id FROM _agt_dataset_ids);
DELETE FROM agent_playground_graph_dataset
WHERE graph_id IN (SELECT id FROM _agt_graph_ids);
DELETE FROM model_hub_dataset
WHERE id IN (SELECT id FROM _agt_dataset_ids);

DELETE FROM agent_playground_execution_data
WHERE node_execution_id IN (
  SELECT id FROM agent_playground_node_execution
  WHERE graph_execution_id IN (
    SELECT id FROM agent_playground_graph_execution
    WHERE graph_version_id IN (SELECT id FROM _agt_version_ids)
  )
);
DELETE FROM agent_playground_node_execution
WHERE graph_execution_id IN (
  SELECT id FROM agent_playground_graph_execution
  WHERE graph_version_id IN (SELECT id FROM _agt_version_ids)
);
DELETE FROM agent_playground_graph_execution
WHERE graph_version_id IN (SELECT id FROM _agt_version_ids);
DELETE FROM agent_playground_edge
WHERE graph_version_id IN (SELECT id FROM _agt_version_ids);
DELETE FROM agent_playground_node_connection
WHERE graph_version_id IN (SELECT id FROM _agt_version_ids);
DELETE FROM agent_playground_port
WHERE node_id IN (SELECT id FROM _agt_node_ids);
DELETE FROM agent_playground_prompt_template_node
WHERE node_id IN (SELECT id FROM _agt_node_ids);
DELETE FROM agent_playground_node
WHERE id IN (SELECT id FROM _agt_node_ids);
DELETE FROM agent_playground_graph_version
WHERE id IN (SELECT id FROM _agt_version_ids);
DELETE FROM agent_playground_graph_collaborators
WHERE graph_id IN (SELECT id FROM _agt_graph_ids);
DELETE FROM agent_playground_graph
WHERE id IN (SELECT id FROM _agt_graph_ids);

DELETE FROM model_hub_promptversion_labels
WHERE promptversion_id IN (SELECT id FROM _agt_prompt_version_ids);
DELETE FROM model_hub_prompttemplate_collaborators
WHERE prompttemplate_id IN (SELECT id FROM _agt_prompt_template_ids);
DELETE FROM model_hub_promptversion
WHERE id IN (SELECT id FROM _agt_prompt_version_ids);
DELETE FROM model_hub_prompttemplate
WHERE id IN (SELECT id FROM _agt_prompt_template_ids);

SELECT json_build_object(
  'remaining_graphs', (
    SELECT count(*) FROM agent_playground_graph
    WHERE id = ${sqlUuid(graphId)} OR name = ANY(${sqlTextArray(graphNames)})
  ),
  'remaining_prompt_templates', (
    SELECT count(*) FROM model_hub_prompttemplate
    WHERE organization_id = ${sqlUuid(organizationId)}
      AND workspace_id = ${sqlUuid(workspaceId)}
      AND name = ANY(${sqlTextArray(promptNames)})
  )
);
COMMIT;
`;
  return runPostgresJson(sql);
}

async function seedAgentExecutionFixture({
  graphExecutionId,
  nodeExecutionId,
  inputDataId,
  outputDataId,
  graphVersionId,
  nodeId,
  inputPortId,
  outputPortId,
  inputValue,
  outputValue,
}) {
  const sql = `
INSERT INTO agent_playground_graph_execution (
  id, graph_version_id, status, input_payload, output_payload,
  started_at, completed_at, created_at, updated_at, deleted
) VALUES (
  ${sqlUuid(graphExecutionId)},
  ${sqlUuid(graphVersionId)},
  'success',
  jsonb_build_object('topic', ${sqlTextLiteral(inputValue)}),
  jsonb_build_object('response', ${sqlTextLiteral(outputValue)}),
  now() - interval '7 seconds',
  now(),
  now(),
  now(),
  false
);

INSERT INTO agent_playground_node_execution (
  id, graph_execution_id, node_id, status,
  started_at, completed_at, created_at, updated_at, deleted
) VALUES (
  ${sqlUuid(nodeExecutionId)},
  ${sqlUuid(graphExecutionId)},
  ${sqlUuid(nodeId)},
  'success',
  now() - interval '7 seconds',
  now(),
  now(),
  now(),
  false
);

INSERT INTO agent_playground_execution_data (
  id, node_execution_id, node_id, port_id, payload,
  validation_errors, is_valid, created_at, updated_at, deleted
) VALUES
  (
    ${sqlUuid(inputDataId)},
    ${sqlUuid(nodeExecutionId)},
    ${sqlUuid(nodeId)},
    ${sqlUuid(inputPortId)},
    to_jsonb(${sqlTextLiteral(inputValue)}::text),
    NULL,
    true,
    now(),
    now(),
    false
  ),
  (
    ${sqlUuid(outputDataId)},
    ${sqlUuid(nodeExecutionId)},
    ${sqlUuid(nodeId)},
    ${sqlUuid(outputPortId)},
    to_jsonb(${sqlTextLiteral(outputValue)}::text),
    NULL,
    true,
    now(),
    now(),
    false
  );

${agentExecutionAuditSql({ graphExecutionId, nodeExecutionId })}
`;
  return runPostgresJson(sql);
}

async function loadAgentExecutionDbAudit({
  graphId,
  graphExecutionId,
  nodeExecutionId,
}) {
  const sql = `
SELECT json_build_object(
  'graph_visible', (
    SELECT count(*) FROM agent_playground_graph
    WHERE id = ${sqlUuid(graphId)} AND deleted = false
  ),
  ${agentExecutionAuditFields({ graphExecutionId, nodeExecutionId })}
);
`;
  return runPostgresJson(sql);
}

async function loadAgentActiveExecutionAudit({ graphId }) {
  const sql = `
WITH target_versions AS (
  SELECT id FROM agent_playground_graph_version
  WHERE graph_id = ${sqlUuid(graphId)}
),
target_executions AS (
  SELECT id, status, input_payload, error_message, created_at
  FROM agent_playground_graph_execution
  WHERE graph_version_id IN (SELECT id FROM target_versions)
    AND deleted = false
),
latest_execution AS (
  SELECT * FROM target_executions
  ORDER BY created_at DESC
  LIMIT 1
)
SELECT json_build_object(
  'execution_count', (SELECT count(*) FROM target_executions),
  'latest_execution_id', (SELECT id::text FROM latest_execution),
  'latest_status', (SELECT status FROM latest_execution),
  'latest_input_topic', (SELECT input_payload->>'topic' FROM latest_execution),
  'latest_error_message', (SELECT error_message FROM latest_execution),
  'latest_node_execution_count', (
    SELECT count(*)
    FROM agent_playground_node_execution
    WHERE graph_execution_id = (SELECT id FROM latest_execution)
      AND deleted = false
  )
);
`;
  return runPostgresJson(sql);
}

async function pollAgentExecutionDetail({
  client,
  graphId,
  executionId,
  attempts = 30,
  intervalMs = 2000,
}) {
  let lastDetail = null;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    lastDetail = await client.get(
      apiPath(
        "/agent-playground/graphs/{graph_id}/executions/{execution_id}/",
        {
          graph_id: graphId,
          execution_id: executionId,
        },
      ),
    );
    if (["success", "failed", "cancelled"].includes(lastDetail.status)) {
      return { ...lastDetail, terminal: true };
    }
    await sleep(intervalMs);
  }
  return { ...(lastDetail || {}), terminal: false };
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function agentExecutionAuditSql({ graphExecutionId, nodeExecutionId }) {
  return `SELECT json_build_object(
  ${agentExecutionAuditFields({ graphExecutionId, nodeExecutionId })}
);`;
}

function agentExecutionAuditFields({ graphExecutionId, nodeExecutionId }) {
  return `
  'graph_execution_visible', (
    SELECT count(*) FROM agent_playground_graph_execution
    WHERE id = ${sqlUuid(graphExecutionId)} AND deleted = false
  ),
  'node_execution_visible', (
    SELECT count(*) FROM agent_playground_node_execution
    WHERE id = ${sqlUuid(nodeExecutionId)} AND deleted = false
  ),
  'execution_data_visible', (
    SELECT count(*) FROM agent_playground_execution_data
    WHERE node_execution_id = ${sqlUuid(nodeExecutionId)} AND deleted = false
  )`;
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFileAsync(
    "docker",
    ["exec", container, "psql", "-qAt", "-U", user, "-d", database, "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const jsonLine = stdout
    .trim()
    .split(/\r?\n/)
    .reverse()
    .find((line) => line.trim().startsWith("{"));
  assert(jsonLine, "Postgres DB audit returned no JSON object.");
  return JSON.parse(jsonLine);
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlUuidArray(values) {
  const rows = values || [];
  assert(rows.length > 0, "SQL UUID array cannot be empty.");
  return `ARRAY[${rows.map((value) => sqlUuid(value)).join(", ")}]::uuid[]`;
}

function sqlTextLiteral(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function sqlJson(value) {
  return `${sqlTextLiteral(JSON.stringify(value ?? null))}::jsonb`;
}

function sqlTextArray(values) {
  const rows = values || [];
  assert(rows.length > 0, "SQL text array cannot be empty.");
  return `ARRAY[${rows.map((value) => sqlTextLiteral(value)).join(", ")}]::text[]`;
}
