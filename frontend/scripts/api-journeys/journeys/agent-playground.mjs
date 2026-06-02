import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
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
    async run({ client, cleanup, runId, organizationId, workspaceId, evidence }) {
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
      assert(nodeTemplates.length > 0, "Agent node template list returned no rows.");

      const llmTemplate = nodeTemplates.find((item) => item.name === "llm_prompt");
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

      const createdGraph = await client.post(apiPath("/agent-playground/graphs/"), {
        name: graphName,
        description: `Disposable API journey graph ${runId}`,
      });
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
      assert(sourceOutput, "Source node did not expose a response output port.");

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
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/ports/{port_id}/", {
          id: graphId,
          version_id: draftVersionId,
          port_id: sourceOutput.id,
        }),
        { display_name: "raw_response" },
      );
      assert(
        renamedOutput.display_name === "raw_response",
        "Port update did not return the renamed output port.",
      );

      await client.patch(apiPath("/agent-playground/graphs/{id}/", { id: graphId }), {
        name: updatedGraphName,
        description: "Updated by AGT-API-001",
      });
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
        datasetBeforeRowCreate.columns?.some((column) => column.name === "topic"),
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
      assert(isUuid(createdRow.id), "Graph dataset row create did not return an id.");
      const topicCell = (createdRow.cells || []).find((cell) =>
        datasetBeforeRowCreate.columns?.some(
          (column) => column.id === cell.column_id && column.name === "topic",
        ),
      );
      assert(topicCell?.id, "Created graph dataset row did not include a topic cell.");

      const updatedCell = await client.put(
        apiPath("/agent-playground/graphs/{graph_id}/dataset/cells/{cell_id}/", {
          graph_id: graphId,
          cell_id: topicCell.id,
        }),
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
      assert(isUuid(secondVersion.id), "Second graph version create returned no id.");
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
        isUuid(restoreDraftVersion.id) && restoreDraftVersion.status === "draft",
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
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/activate/", {
          id: graphId,
          version_id: draftVersionId,
        }),
      );
      assert(
        restoredVersion.id === draftVersionId && restoredVersion.status === "active",
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
          client.get(apiPath("/agent-playground/graphs/{id}/", { id: graphId })),
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
        missing_execution_status: missingExecution.status,
        deleted_graph_status: deletedGraphDetail.status,
        pre_delete_audit: preDeleteAudit,
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

function assertAgentGraphPreDeleteAudit(audit, { organizationId, workspaceId }) {
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
  assert(audit.node_visible === 2, "Agent graph should have two visible nodes.");
  assert(audit.port_visible >= 4, "Agent graph should have visible node ports.");
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
  assert(audit.graph_total === 1, "Deleted agent graph row was missing from DB.");
  assert(audit.graph_visible === 0, "Deleted agent graph remained visible.");
  assert(audit.graph_deleted === true, "Agent graph was not soft-deleted.");
  assert(audit.version_visible === 0, "Deleted agent graph versions remained visible.");
  assert(audit.node_visible === 0, "Deleted agent graph nodes remained visible.");
  assert(audit.port_visible === 0, "Deleted agent graph ports remained visible.");
  assert(
    audit.node_connection_visible === 0,
    "Deleted agent graph node connections remained visible.",
  );
  assert(audit.edge_visible === 0, "Deleted agent graph edges remained visible.");
  assert(
    audit.prompt_template_node_visible === 0,
    "Deleted agent graph prompt-template node links remained visible.",
  );
  assert(
    audit.graph_dataset_visible === 0,
    "Deleted agent graph dataset link remained visible.",
  );
  assert(audit.dataset_visible === 0, "Deleted agent graph dataset remained visible.");
  assert(audit.column_visible === 0, "Deleted agent graph columns remained visible.");
  assert(audit.row_visible === 0, "Deleted agent graph rows remained visible.");
  assert(audit.cell_visible === 0, "Deleted agent graph cells remained visible.");
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

function sqlTextLiteral(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function sqlTextArray(values) {
  const rows = values || [];
  assert(rows.length > 0, "SQL text array cannot be empty.");
  return `ARRAY[${rows.map((value) => sqlTextLiteral(value)).join(", ")}]::text[]`;
}
