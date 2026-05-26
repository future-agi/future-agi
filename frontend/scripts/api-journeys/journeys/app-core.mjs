import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  createApiClient,
  currentUserEmail,
  currentUserId,
  envFlag,
  isUuid,
  requireMutations,
  skip,
} from "../lib/api-client.mjs";

const execFileAsync = promisify(execFile);

export const appCoreJourneys = [
  {
    id: "CORE-API-001",
    title:
      "Core app account, workspace, model/provider, observe, and simulation catalogs load",
    tags: ["core", "safe", "smoke"],
    async run({ client, user, evidence }) {
      assert(
        currentUserId(user),
        "Authenticated user info did not include a user id.",
      );

      const workspaces = asArray(
        await client.get(apiPath("/accounts/workspace/list/")),
      );
      assert(workspaces.length > 0, "Workspace list returned no workspaces.");

      const providerStatus = await client.get(
        apiPath("/model-hub/develops/provider-status/"),
      );
      assert(
        Array.isArray(providerStatus?.providers),
        "Provider status did not include providers array.",
      );

      const models = asArray(
        await client.get(apiPath("/model-hub/api/models_list/")),
      );
      assert(models.length > 0, "Model catalog returned no models.");

      const projects = asArray(
        await client.get(apiPath("/tracer/project/list_projects/"), {
          query: { page_number: 0, page_size: 10 },
        }),
      );
      assert(projects.length > 0, "Observe project list returned no projects.");

      const personas = await client.get(apiPath("/simulate/api/personas/"), {
        query: { limit: 5 },
      });
      assert(
        Array.isArray(personas?.results) || Array.isArray(personas),
        "Persona catalog did not return a list payload.",
      );

      const agentDefinitions = asArray(
        await client.get(
          apiPath("/simulate/api/agent-definition-operations/"),
          {
            query: { limit: 5 },
          },
        ),
      );
      const scenarios = asArray(
        await client.get(apiPath("/simulate/scenarios/")),
      );
      const runTests = asArray(
        await client.get(apiPath("/simulate/run-tests/")),
      );

      evidence.push({
        workspace_count: workspaces.length,
        provider_count: providerStatus.providers.length,
        model_count: models.length,
        observe_project_count: projects.length,
        persona_count: asArray(personas).length,
        agent_definition_count: agentDefinitions.length,
        scenario_count: scenarios.length,
        run_test_count: runTests.length,
      });
    },
  },
  {
    id: "GST-API-001",
    title:
      "Get Started first-checks workspace parity and provider setup safety",
    tags: ["get-started", "core", "safe", "db-audit", "credential-safety"],
    async run({ client, user, organizationId, workspaceId, evidence }) {
      const userId = currentUserId(user);
      assert(userId, "Authenticated user info did not include a user id.");
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const userInfo = await client.get(apiPath("/accounts/user-info/"));
      assert(
        currentUserId(userInfo) === userId,
        "Get Started user-info readback returned a different user.",
      );

      const workspaces = asArray(
        await client.get(apiPath("/accounts/workspace/list/")),
      );
      const activeWorkspace = workspaces.find(
        (workspace) =>
          (workspace?.id || workspace?.workspace_id) === workspaceId,
      );
      assert(
        activeWorkspace,
        "Workspace list did not include the active workspace.",
      );

      const checks = await client.get(apiPath("/accounts/first-checks/"));
      assertGetStartedChecks(checks);

      const audit = await loadGetStartedFirstChecksDbAudit({
        userId,
        organizationId,
        workspaceId,
      });
      const expectedChecks = {
        keys: Number(audit.key_count) > 0,
        dataset: Number(audit.dataset_count) > 0,
        evaluation: Number(audit.evaluation_count) > 0,
        experiment: Number(audit.experiment_count) > 0,
        observe: Number(audit.observe_count) > 0,
        invite: Number(audit.invite_count) > 0,
      };
      for (const key of Object.keys(expectedChecks)) {
        assert(
          checks[key] === expectedChecks[key],
          `Get Started first-checks ${key}=${checks[key]} did not match DB expected ${expectedChecks[key]}.`,
        );
      }

      const providerStatus = await client.get(
        apiPath("/model-hub/develops/provider-status/"),
      );
      const providers = Array.isArray(providerStatus?.providers)
        ? providerStatus.providers
        : [];
      assert(providers.length > 0, "Provider status returned no providers.");
      for (const provider of providers) {
        assertProviderStatusRow(provider);
      }
      assertNoRawProviderSecretLeak(
        providerStatus,
        "Get Started provider status",
      );

      evidence.push({
        user_id: userId,
        workspace_id: workspaceId,
        active_workspace_name:
          activeWorkspace.name || activeWorkspace.display_name,
        checks,
        db_counts: audit,
        provider_count: providers.length,
        configured_provider_count: providers.filter(
          (provider) => provider.has_key,
        ).length,
      });
    },
  },
  {
    id: "PRT-API-001",
    title:
      "Prototype project list, search, create, detail, run list, update, and delete lifecycle",
    tags: [
      "prototype",
      "projects",
      "project-version",
      "mutating",
      "data-integrity",
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

      const list = await client.get(apiPath("/tracer/project/"), {
        query: {
          project_type: "experiment",
          page_number: 0,
          page_size: 10,
          sort_by: "created_at",
          sort_direction: "desc",
        },
      });
      const projects = Array.isArray(list?.projects) ? list.projects : [];
      assert(projects.length > 0, "Prototype project list returned no rows.");
      assert(
        Number(list.total_count || 0) >= projects.length,
        "Prototype project total_count was inconsistent.",
      );
      for (const project of projects) {
        assertPrototypeProjectListRow(project);
      }
      const listRowsWithoutWorkspace = projects.filter(
        (project) => project.workspace == null,
      ).length;

      const selectedProject =
        projects.find((project) => Number(project.run_count || 0) > 0) ||
        projects[0];
      const searchTerm = String(selectedProject.name || "").slice(0, 8);
      const search = await client.get(apiPath("/tracer/project/"), {
        query: {
          project_type: "experiment",
          name: searchTerm,
          page_number: 0,
          page_size: 10,
          sort_by: "name",
          sort_direction: "asc",
        },
      });
      assert(
        (search.projects || []).some(
          (project) => project.id === selectedProject.id,
        ),
        "Prototype project search did not return the selected project.",
      );

      const ascList = await client.get(apiPath("/tracer/project/"), {
        query: {
          project_type: "experiment",
          page_number: 0,
          page_size: 10,
          sort_by: "name",
          sort_direction: "asc",
        },
      });
      assertSortedByName(ascList.projects || [], "asc");

      const detail = await client.get(
        apiPath("/tracer/project/{id}/", { id: selectedProject.id }),
      );
      assertPrototypeProjectDetail(detail, {
        projectId: selectedProject.id,
        organizationId,
        workspaceId: selectedProject.workspace ?? null,
      });

      const runList = await client.get(
        apiPath("/tracer/project-version/list_runs/"),
        {
          query: {
            project_id: selectedProject.id,
            page_number: 0,
            page_size: 10,
            filters: [],
            sort_params: [],
          },
        },
      );
      assertPrototypeRunList(runList);
      const firstRun = (runList.table || []).find((row) => isUuid(row?.id));
      assert(
        firstRun,
        "Prototype run list did not return a run row for the selected project.",
      );

      const runDetail = await client.get(
        apiPath("/tracer/project-version/{id}/", { id: firstRun.id }),
      );
      assert(
        runDetail?.id === firstRun.id,
        "Prototype run detail id mismatch.",
      );
      assert(
        runDetail?.project === selectedProject.id,
        "Prototype run detail project id mismatch.",
      );
      assert(
        String(runDetail?.name || "").trim(),
        "Prototype run detail omitted name.",
      );
      assert(
        String(runDetail?.version || "").trim(),
        "Prototype run detail omitted version.",
      );

      const runIds = await client.get(
        apiPath("/tracer/project-version/get_project_version_ids/"),
        {
          query: {
            project_id: selectedProject.id,
            search_name: String(runDetail.name || "").slice(0, 10),
            page_number: 0,
            page_size: 10,
          },
        },
      );
      assert(
        (runIds.project_version_ids || []).some(
          (row) => row.id === firstRun.id,
        ),
        "Prototype run id search did not include the selected run.",
      );

      const projectIds = await client.get(
        apiPath("/tracer/project/list_project_ids/"),
      );
      assert(
        (projectIds.projects || []).some(
          (project) => project.id === selectedProject.id,
        ),
        "Project id catalog did not include the selected prototype project.",
      );

      const sdkCode = await client.get(
        apiPath("/tracer/project/project_sdk_code/"),
        {
          query: { project_type: "experiment" },
        },
      );
      assertPrototypeSdkCode(sdkCode);
      const sdkCodeHasRawFiKeys = prototypeSdkCodeHasRawFiKeys(sdkCode);
      assert(
        !sdkCodeHasRawFiKeys,
        "Prototype SDK code exposed raw FI key material.",
      );
      const invalidSdkType = await expectApiError(
        () =>
          client.get(apiPath("/tracer/project/project_sdk_code/"), {
            query: { project_type: "invalid" },
          }),
        [400],
        "Invalid prototype SDK project_type unexpectedly succeeded.",
      );

      const marker = runId.replace(/[^a-z0-9-]/gi, "").slice(0, 20);
      const projectName = `api journey prototype ${marker}`;
      const updatedProjectName = `${projectName} updated`;
      const created = await client.post(apiPath("/tracer/project/"), {
        name: projectName,
        model_type: "GenerativeLLM",
        trace_type: "experiment",
        metadata: { api_journey: "PRT-API-001", run_id: runId },
      });
      const createdProjectId = created.project_id;
      assert(
        isUuid(createdProjectId),
        "Prototype project create did not return project_id.",
      );
      cleanup.defer("hard delete disposable prototype project", () =>
        hardDeletePrototypeProject({
          projectId: createdProjectId,
          projectName,
          updatedProjectName,
        }),
      );

      const duplicateCreate = await expectApiError(
        () =>
          client.post(apiPath("/tracer/project/"), {
            name: projectName,
            model_type: "GenerativeLLM",
            trace_type: "experiment",
          }),
        [400],
        "Duplicate prototype project create unexpectedly succeeded.",
      );

      const createdAudit = await loadPrototypeProjectDbAudit({
        projectId: createdProjectId,
        organizationId,
        workspaceId,
      });
      assertPrototypeProjectDbAudit(createdAudit, {
        projectId: createdProjectId,
        name: projectName,
        organizationId,
        workspaceId,
        deleted: false,
      });

      const update = await client.post(
        apiPath("/tracer/project/update_project_name/"),
        {
          project_id: createdProjectId,
          name: updatedProjectName,
          sampling_rate: 0.25,
        },
      );
      assert(
        update.project_id === createdProjectId,
        "Prototype project rename response id mismatch.",
      );
      assert(
        update.project_name === updatedProjectName,
        "Prototype project rename response name mismatch.",
      );
      assert(
        Number(update.sampling_rate?.new_rate) === 0.25,
        "Prototype project sampling_rate update response mismatch.",
      );

      const updatedDetail = await client.get(
        apiPath("/tracer/project/{id}/", { id: createdProjectId }),
      );
      assert(
        updatedDetail.name === updatedProjectName,
        "Prototype project detail did not reflect rename.",
      );
      assert(
        Number(updatedDetail.sampling_rate) === 0.25,
        "Prototype project detail did not reflect sampling_rate.",
      );

      const updatedAudit = await loadPrototypeProjectDbAudit({
        projectId: createdProjectId,
        organizationId,
        workspaceId,
      });
      assertPrototypeProjectDbAudit(updatedAudit, {
        projectId: createdProjectId,
        name: updatedProjectName,
        organizationId,
        workspaceId,
        deleted: false,
        samplingRate: 0.25,
      });

      const missingDetail = await expectApiError(
        () =>
          client.get(
            apiPath("/tracer/project/{id}/", {
              id: "00000000-0000-4000-8000-000000000999",
            }),
          ),
        [400, 404],
        "Missing prototype project detail unexpectedly succeeded.",
      );

      const invalidRunList = await expectApiError(
        () =>
          client.get(apiPath("/tracer/project-version/list_runs/"), {
            query: {
              project_id: "00000000-0000-4000-8000-000000000999",
              page_number: 0,
              page_size: 10,
            },
          }),
        [400],
        "Missing prototype run list unexpectedly succeeded.",
      );

      await client.delete(apiPath("/tracer/project/"), {
        body: {
          project_ids: [createdProjectId],
          project_type: "experiment",
        },
      });

      const deletedAudit = await loadPrototypeProjectDbAudit({
        projectId: createdProjectId,
        organizationId,
        workspaceId,
      });
      assertPrototypeProjectDbAudit(deletedAudit, {
        projectId: createdProjectId,
        name: updatedProjectName,
        organizationId,
        workspaceId,
        deleted: true,
        samplingRate: 0.25,
      });

      const cleanupAudit = await hardDeletePrototypeProject({
        projectId: createdProjectId,
        projectName,
        updatedProjectName,
      });

      evidence.push({
        selected_project_id: selectedProject.id,
        selected_project_name: selectedProject.name,
        selected_project_workspace_id: selectedProject.workspace ?? null,
        project_count: list.total_count,
        search_count: search.total_count,
        list_rows_without_workspace: listRowsWithoutWorkspace,
        run_count: runList.metadata?.total_rows || 0,
        run_column_count: (runList.column_config || []).length,
        selected_run_id: firstRun.id,
        project_ids_count: (projectIds.projects || []).length,
        sdk_code_has_raw_fi_keys: sdkCodeHasRawFiKeys,
        invalid_sdk_project_type_status: invalidSdkType.status,
        created_project_id: createdProjectId,
        duplicate_create_status: duplicateCreate.status,
        updated_sampling_rate: updatedAudit.sampling_rate,
        missing_detail_status: missingDetail.status,
        invalid_run_list_status: invalidRunList.status,
        public_delete_deleted_at_set: deletedAudit.deleted_at_set,
        cleanup_project_count: cleanupAudit.project_count,
        cleanup_scan_config_count: cleanupAudit.scan_config_count,
      });
    },
  },
  {
    id: "PRT-API-002",
    title:
      "Prototype run CRUD, export, insights, winner, annotations, config, and delete lifecycle",
    tags: [
      "prototype",
      "project-version",
      "runs",
      "mutating",
      "data-integrity",
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

      const marker = runId.replace(/[^a-z0-9-]/gi, "").slice(0, 20);
      const projectName = `api journey pv ${marker}`;
      const alphaName = `api journey alpha ${marker}`;
      const alphaPatchedName = `${alphaName} patched`;
      const betaName = `api journey beta ${marker}`;
      const betaPutName = `${betaName} put`;

      const createdProject = await client.post(apiPath("/tracer/project/"), {
        name: projectName,
        model_type: "GenerativeLLM",
        trace_type: "experiment",
        metadata: { api_journey: "PRT-API-002", run_id: runId },
      });
      const projectId = createdProject.project_id;
      assert(
        isUuid(projectId),
        "Prototype project create did not return project_id.",
      );
      cleanup.defer("hard delete PRT-API-002 project-version artifacts", () =>
        hardDeleteProjectVersionJourneyArtifacts({ projectId, projectName }),
      );

      const alphaVersion = await createProjectVersionJourneyRun({
        client,
        projectId,
        name: alphaName,
        metadata: { lane: "alpha", run_id: runId },
      });
      const betaVersion = await createProjectVersionJourneyRun({
        client,
        projectId,
        name: betaName,
        metadata: { lane: "beta", run_id: runId },
      });
      assert(
        alphaVersion.version === "v1",
        "First disposable project version was not v1.",
      );
      assert(
        betaVersion.version === "v2",
        "Second disposable project version was not v2.",
      );

      const alphaDetail = await client.get(
        apiPath("/tracer/project-version/{id}/", { id: alphaVersion.id }),
      );
      assert(
        alphaDetail.id === alphaVersion.id,
        "Project-version detail id mismatch.",
      );
      assert(
        alphaDetail.project === projectId,
        "Project-version detail project mismatch.",
      );

      const genericList = asArray(
        await client.get(apiPath("/tracer/project-version/"), {
          query: { project_id: projectId },
        }),
      );
      assert(
        genericList.some((row) => row?.id === alphaVersion.id) &&
          genericList.some((row) => row?.id === betaVersion.id),
        "Project-version generic list omitted a disposable run.",
      );

      const patchedAlpha = await client.patch(
        apiPath("/tracer/project-version/{id}/", { id: alphaVersion.id }),
        {
          name: alphaPatchedName,
          metadata: { lane: "alpha", patched: true, run_id: runId },
        },
      );
      assert(
        patchedAlpha.name === alphaPatchedName,
        "Project-version PATCH did not update name.",
      );

      const putBeta = await client.put(
        apiPath("/tracer/project-version/{id}/", { id: betaVersion.id }),
        {
          project: projectId,
          name: betaPutName,
          metadata: { lane: "beta", put: true, run_id: runId },
          eval_tags: ["api-journey"],
          avg_eval_score: 0,
        },
      );
      assert(
        putBeta.name === betaPutName,
        "Project-version PUT did not update name.",
      );
      assert(
        putBeta.project === projectId,
        "Project-version PUT changed project unexpectedly.",
      );

      const alphaSeed = await seedProjectVersionJourneyTraceAndSpan({
        client,
        projectId,
        projectVersionId: alphaVersion.id,
        marker,
        label: "alpha",
        latencyMs: 100,
        cost: 0.001,
      });
      const betaSeed = await seedProjectVersionJourneyTraceAndSpan({
        client,
        projectId,
        projectVersionId: betaVersion.id,
        marker,
        label: "beta",
        latencyMs: 300,
        cost: 0.004,
      });

      const runList = await client.get(
        apiPath("/tracer/project-version/list_runs/"),
        {
          query: {
            project_id: projectId,
            page_number: 0,
            page_size: 10,
            filters: [],
            sort_params: [],
          },
        },
      );
      const runRows = asArray(runList);
      assert(
        runRows.some((row) => row?.id === alphaVersion.id),
        "Run list omitted alpha run.",
      );
      assert(
        runRows.some((row) => row?.id === betaVersion.id),
        "Run list omitted beta run.",
      );

      const runIds = await client.get(
        apiPath("/tracer/project-version/get_project_version_ids/"),
        {
          query: {
            project_id: projectId,
            page_number: 0,
            page_size: 10,
          },
        },
      );
      assert(
        (runIds.project_version_ids || []).some(
          (row) => row.id === alphaVersion.id,
        ) &&
          (runIds.project_version_ids || []).some(
            (row) => row.id === betaVersion.id,
          ),
        "Project-version id catalog omitted a disposable run.",
      );

      const exportCsv = await client.post(
        apiPath("/tracer/project-version/get_export_data/"),
        {
          project_id: projectId,
          runs_ids: [alphaVersion.id, betaVersion.id],
        },
      );
      assert(
        typeof exportCsv === "string",
        "Project-version export did not return CSV text.",
      );
      assert(
        exportCsv.includes(alphaPatchedName),
        "Project-version export omitted alpha run.",
      );
      assert(
        exportCsv.includes(betaPutName),
        "Project-version export omitted beta run.",
      );

      const alphaInsights = await client.get(
        apiPath("/tracer/project-version/get_run_insights/"),
        {
          query: { project_version_id: alphaVersion.id },
        },
      );
      assert(
        (alphaInsights.trace_ids || []).includes(alphaSeed.traceId),
        "Run insights omitted alpha trace id.",
      );
      assert(
        Number(alphaInsights.system_metrics?.avg_latency_ms || 0) > 0,
        "Run insights did not aggregate alpha latency.",
      );

      const configUpdate = await client.post(
        apiPath("/tracer/project-version/update_project_version_config/"),
        {
          project_version_id: alphaVersion.id,
          visibility: { latency: false },
        },
      );
      assert(
        configUpdate.project_version_id === alphaVersion.id,
        "Config update response id mismatch.",
      );

      const annotation = await client.post(
        apiPath("/tracer/project-version/add_annotations/"),
        {
          project_version_id: alphaVersion.id,
          annotation_values: {
            name: `api journey pv annotation ${marker}`,
          },
        },
      );
      const annotationId = annotation.annotation_id;
      assert(
        isUuid(annotationId),
        "Project-version add_annotations did not return annotation_id.",
      );

      const winner = await client.post(
        apiPath("/tracer/project-version/project_version_winner/"),
        {
          project_id: projectId,
          config: { avg_latency_ms: 1 },
        },
      );
      assert(
        winner.project_version_winner === alphaVersion.id,
        "Project-version winner did not choose the lower-latency alpha run.",
      );

      const preDeleteAudit = await loadProjectVersionJourneyDbAudit({
        projectId,
        organizationId,
        workspaceId,
        projectVersionIds: [alphaVersion.id, betaVersion.id],
        traceIds: [alphaSeed.traceId, betaSeed.traceId],
        spanIds: [alphaSeed.spanId, betaSeed.spanId],
        winnerVersionId: alphaVersion.id,
        annotationId,
      });
      assertProjectVersionJourneyDbAudit(preDeleteAudit, {
        projectId,
        organizationId,
        workspaceId,
        alphaVersionId: alphaVersion.id,
        betaVersionId: betaVersion.id,
        alphaName: alphaPatchedName,
        betaName: betaPutName,
        alphaDeleted: false,
        betaDeleted: false,
        alphaTraceId: alphaSeed.traceId,
        betaTraceId: betaSeed.traceId,
        alphaSpanId: alphaSeed.spanId,
        betaSpanId: betaSeed.spanId,
        traceDeleted: false,
        spanDeleted: false,
        annotationId,
        winnerVersionId: alphaVersion.id,
        latencyVisible: false,
      });

      await client.delete(
        apiPath("/tracer/project-version/{id}/", { id: alphaVersion.id }),
        {
          okStatuses: [204],
        },
      );
      const deleteRuns = await client.post(
        apiPath("/tracer/project-version/delete_runs/"),
        {
          ids: [betaVersion.id],
        },
      );
      assert(
        (deleteRuns.deleted_ids || []).includes(betaVersion.id),
        "delete_runs did not report the beta run as deleted.",
      );

      const postDeleteAudit = await loadProjectVersionJourneyDbAudit({
        projectId,
        organizationId,
        workspaceId,
        projectVersionIds: [alphaVersion.id, betaVersion.id],
        traceIds: [alphaSeed.traceId, betaSeed.traceId],
        spanIds: [alphaSeed.spanId, betaSeed.spanId],
        winnerVersionId: alphaVersion.id,
        annotationId,
      });
      assertProjectVersionJourneyDbAudit(postDeleteAudit, {
        projectId,
        organizationId,
        workspaceId,
        alphaVersionId: alphaVersion.id,
        betaVersionId: betaVersion.id,
        alphaName: alphaPatchedName,
        betaName: betaPutName,
        alphaDeleted: true,
        betaDeleted: true,
        alphaTraceId: alphaSeed.traceId,
        betaTraceId: betaSeed.traceId,
        alphaSpanId: alphaSeed.spanId,
        betaSpanId: betaSeed.spanId,
        traceDeleted: true,
        spanDeleted: true,
        annotationId,
        winnerVersionId: alphaVersion.id,
        latencyVisible: false,
      });

      const cleanupAudit = await hardDeleteProjectVersionJourneyArtifacts({
        projectId,
        projectName,
      });
      assert(
        cleanupAudit.project_count === 0,
        "Disposable project remained after hard cleanup.",
      );
      assert(
        cleanupAudit.project_version_count === 0,
        "Disposable project versions remained after hard cleanup.",
      );
      assert(
        cleanupAudit.trace_count === 0,
        "Disposable traces remained after hard cleanup.",
      );
      assert(
        cleanupAudit.span_count === 0,
        "Disposable spans remained after hard cleanup.",
      );

      evidence.push({
        project_id: projectId,
        alpha_project_version_id: alphaVersion.id,
        beta_project_version_id: betaVersion.id,
        alpha_trace_id: alphaSeed.traceId,
        beta_trace_id: betaSeed.traceId,
        alpha_span_id: alphaSeed.spanId,
        beta_span_id: betaSeed.spanId,
        annotation_id: annotationId,
        exported_csv_bytes: exportCsv.length,
        run_list_rows: runRows.length,
        winner_project_version_id: winner.project_version_winner,
        generic_delete_cascaded:
          postDeleteAudit.trace_deleted?.[alphaSeed.traceId] === true,
        delete_runs_cascaded:
          postDeleteAudit.trace_deleted?.[betaSeed.traceId] === true,
        cleanup_project_count: cleanupAudit.project_count,
      });
    },
  },
  {
    id: "PRT-API-003",
    title:
      "Prototype project detail PUT, PATCH, tags, config, graph reads, and detail delete lifecycle",
    tags: ["prototype", "projects", "mutating", "data-integrity"],
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

      const marker = runId.replace(/[^a-z0-9-]/gi, "").slice(0, 20);
      const projectName = `api journey project crud ${marker}`;
      const putName = `${projectName} put`;
      const patchName = `${projectName} patch`;
      const tag = `api-journey-${marker}`;
      const config = [
        { id: "run_name", name: "Run Name", is_visible: true, group_by: null },
        {
          id: "avg_cost",
          name: "Avg. Cost",
          is_visible: true,
          group_by: "System Metrics",
        },
      ];
      const sessionConfig = [
        {
          id: "session_input",
          name: "Session Input",
          is_visible: true,
          group_by: null,
        },
      ];

      const created = await client.post(apiPath("/tracer/project/"), {
        name: projectName,
        model_type: "GenerativeLLM",
        trace_type: "experiment",
        metadata: {
          api_journey: "PRT-API-003",
          run_id: runId,
          phase: "create",
        },
      });
      const projectId = created.project_id;
      assert(
        isUuid(projectId),
        "Project create did not return a valid project_id.",
      );
      cleanup.defer("hard delete PRT-API-003 project artifacts", () =>
        hardDeletePrototypeProject({
          projectId,
          projectName,
          updatedProjectName: patchName,
          extraNames: [putName],
        }),
      );

      const systemMetrics = asArray(
        await client.get(apiPath("/tracer/project/fetch_system_metrics/")),
      );
      assert(
        systemMetrics.includes("latency"),
        "Project system metrics omitted latency.",
      );
      assert(
        systemMetrics.includes("cost"),
        "Project system metrics omitted cost.",
      );

      const initialDetail = await client.get(
        apiPath("/tracer/project/{id}/", { id: projectId }),
      );
      assert(
        initialDetail.id === projectId,
        "Project detail id mismatch after create.",
      );
      assert(
        initialDetail.workspace === workspaceId,
        "Project detail workspace mismatch after create.",
      );

      const emptyGraph = await client.get(
        apiPath("/tracer/project/get_graph_data/"),
        {
          query: { project_id: projectId, interval: "hour", filters: [] },
        },
      );
      assert(
        emptyGraph?.system_metrics,
        "Project graph response omitted system_metrics.",
      );
      assert(
        emptyGraph?.evaluations,
        "Project graph response omitted evaluations.",
      );

      const put = await client.put(
        apiPath("/tracer/project/{id}/", { id: projectId }),
        {
          name: putName,
          model_type: "GenerativeLLM",
          trace_type: "experiment",
          metadata: { api_journey: "PRT-API-003", run_id: runId, phase: "put" },
          config,
          session_config: sessionConfig,
          tags: [],
        },
      );
      assert(put.id === projectId, "Project PUT response id mismatch.");
      assert(put.name === putName, "Project PUT did not persist the new name.");

      const patch = await client.patch(
        apiPath("/tracer/project/{id}/", { id: projectId }),
        {
          name: patchName,
          metadata: {
            api_journey: "PRT-API-003",
            run_id: runId,
            phase: "patch",
          },
        },
      );
      assert(patch.id === projectId, "Project PATCH response id mismatch.");
      assert(
        patch.name === patchName,
        "Project PATCH did not persist the new name.",
      );

      const configUpdate = await client.post(
        apiPath("/tracer/project/update_project_config/"),
        {
          project_id: projectId,
          visibility: { avg_cost: false },
        },
      );
      assert(
        configUpdate.project_id === projectId,
        "Project config update response id mismatch.",
      );

      const sessionConfigUpdate = await client.post(
        apiPath("/tracer/project/update_project_session_config/"),
        {
          project_id: projectId,
          visibility: { session_input: false },
        },
      );
      assert(
        sessionConfigUpdate.project_id === projectId,
        "Project session config update response id mismatch.",
      );

      const tags = await client.patch(
        apiPath("/tracer/project/{id}/tags/", { id: projectId }),
        {
          tags: ["api-journey", tag],
        },
      );
      assert(
        (tags.tags || []).includes(tag),
        "Project tag update did not return the new tag.",
      );

      const preDeleteAudit = await loadProjectCrudDbAudit({
        projectId,
        organizationId,
        workspaceId,
      });
      assertProjectCrudDbAudit(preDeleteAudit, {
        projectId,
        organizationId,
        workspaceId,
        name: patchName,
        deleted: false,
        tags: ["api-journey", tag],
        configVisibility: { avg_cost: false },
        sessionConfigVisibility: { session_input: false },
      });

      await client.delete(apiPath("/tracer/project/{id}/", { id: projectId }));
      const deletedAudit = await loadProjectCrudDbAudit({
        projectId,
        organizationId,
        workspaceId,
      });
      assertProjectCrudDbAudit(deletedAudit, {
        projectId,
        organizationId,
        workspaceId,
        name: patchName,
        deleted: true,
        deletedAtSet: true,
        tags: ["api-journey", tag],
        configVisibility: { avg_cost: false },
        sessionConfigVisibility: { session_input: false },
      });

      const cleanupAudit = await hardDeletePrototypeProject({
        projectId,
        projectName,
        updatedProjectName: patchName,
        extraNames: [putName],
      });
      assert(
        cleanupAudit.project_count === 0,
        "Disposable project remained after hard cleanup.",
      );

      evidence.push({
        project_id: projectId,
        system_metrics: systemMetrics,
        graph_metric_keys: Object.keys(emptyGraph.system_metrics || {}),
        config_avg_cost_visible: preDeleteAudit.config_visibility?.avg_cost,
        session_input_visible:
          preDeleteAudit.session_config_visibility?.session_input,
        tags: preDeleteAudit.tags,
        detail_delete_deleted_at_set: deletedAudit.deleted_at_set,
        cleanup_project_count: cleanupAudit.project_count,
      });
    },
  },
  {
    id: "CORE-API-004",
    title:
      "Account user-info, workspace list, user list, team search, auth refresh, and guards",
    tags: [
      "core",
      "accounts",
      "team",
      "workspace",
      "auth",
      "mutating",
      "data-integrity",
    ],
    async run({ client, user, tokens, organizationId, workspaceId, evidence }) {
      const userInfo = await client.get(apiPath("/accounts/user-info/"));
      const userId = currentUserId(userInfo) || currentUserId(user);
      const email = currentUserEmail(userInfo) || currentUserEmail(user);
      assert(
        isUuid(userId),
        "Authenticated user-info did not include a valid user id.",
      );
      assert(
        email.includes("@"),
        "Authenticated user-info did not include an email.",
      );
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const workspacesPayload = await client.get(
        apiPath("/accounts/workspace/list/"),
      );
      const workspaces = asArray(workspacesPayload);
      assert(workspaces.length > 0, "Workspace list returned no workspaces.");
      const currentWorkspace = workspaces.find(
        (workspace) =>
          workspace?.id === workspaceId ||
          workspace?.workspace_id === workspaceId,
      );
      assert(
        currentWorkspace,
        "Workspace list did not include the active workspace.",
      );

      const userListByEmail = await client.get(
        apiPath("/accounts/user/list/"),
        {
          query: {
            page: 1,
            limit: 10,
            search: email,
            workspace_id: workspaceId,
            filter_status: ["Active"],
            sort: "email",
          },
        },
      );
      const userRows = asArray(userListByEmail);
      const userListRow = findUserRow(userRows, userId, email);
      assert(
        userListRow,
        "User list search by email did not return the current user.",
      );
      assert(
        String(userListRow.status || "").toLowerCase() === "active",
        "User list active-status filter returned a non-active current user row.",
      );

      const audit = await loadAccountContextDbAudit({
        userId,
        organizationId,
        workspaceId,
      });
      assert(
        audit.user_id === userId,
        "DB audit did not resolve the current user row.",
      );
      assert(
        audit.email === email,
        "DB audit user email did not match user-info.",
      );
      assert(
        audit.active_org_membership_count >= 1,
        "DB audit did not find an active organization membership for the current user.",
      );
      assert(
        audit.workspace_organization_id === organizationId,
        "DB audit workspace organization did not match the active organization.",
      );
      assert(
        audit.workspace_active === true,
        "DB audit active workspace was not active.",
      );
      assert(
        audit.active_workspace_membership_count >= 1,
        "DB audit did not find an active workspace membership for the current user.",
      );

      const refreshEvidence = {
        refresh_token_available: Boolean(tokens?.refresh),
      };
      if (tokens?.refresh) {
        const refreshed = await client.post(
          apiPath("/accounts/token/refresh/"),
          {
            refresh: tokens.refresh,
          },
        );
        assert(
          typeof refreshed?.access === "string" && refreshed.access.length > 20,
          "Refresh token endpoint did not return a new access token.",
        );
        refreshEvidence.refresh_returned_access = true;
      }

      if (isOrgOwner(userInfo)) {
        requireMutations();

        const teamList = await client.get(apiPath("/accounts/team/users/"), {
          query: { page: 1, page_size: 10, is_active: "true" },
        });
        assert(
          getPayloadTotal(teamList) >= 1 && asArray(teamList).length > 0,
          "Owner team list returned no active users.",
        );

        const teamByEmail = await client.get(apiPath("/accounts/team/users/"), {
          query: {
            page: 1,
            page_size: 10,
            is_active: "true",
            search_query: email,
            workspace_id: workspaceId,
          },
        });
        const teamRows = asArray(teamByEmail);
        assert(
          findUserRow(teamRows, userId, email),
          "Owner team search by email did not return the current user.",
        );

        const invalidInviteError = await expectApiError(
          () =>
            client.post(apiPath("/accounts/team/users/"), {
              members: [
                {
                  email: "not-an-email",
                  name: "API Journey Invalid Invite",
                  role: "Member",
                },
              ],
            }),
          [400],
          "Invalid team invite payload unexpectedly succeeded.",
        );
        assert(
          errorText(invalidInviteError).toLowerCase().includes("email"),
          "Invalid team invite error did not identify the bad email field.",
        );

        const selfDeleteError = await expectApiError(
          () =>
            client.delete(
              apiPath("/accounts/team/users/{member_id}/", {
                member_id: userId,
              }),
            ),
          [400],
          "Self-removal through team member delete unexpectedly succeeded.",
        );
        assert(
          errorText(selfDeleteError).toLowerCase().includes("remove"),
          "Self-removal guard error did not explain the blocked removal.",
        );

        const missingDeleteError = await expectApiError(
          () =>
            client.delete(
              apiPath("/accounts/team/users/{member_id}/", {
                member_id: "00000000-0000-0000-0000-000000000000",
              }),
            ),
          [404],
          "Deleting a nonexistent team member unexpectedly succeeded.",
        );

        evidence.push({
          user_id: userId,
          email,
          workspace_count: workspaces.length,
          user_list_email_matches: userRows.length,
          team_email_matches: teamRows.length,
          db_active_org_memberships: audit.active_org_membership_count,
          db_active_workspace_memberships:
            audit.active_workspace_membership_count,
          invalid_invite_status: invalidInviteError.status,
          self_delete_status: selfDeleteError.status,
          missing_delete_status: missingDeleteError.status,
          ...refreshEvidence,
        });
      } else {
        const teamForbidden = await expectApiError(
          () => client.get(apiPath("/accounts/team/users/")),
          [403],
          "Non-owner team list unexpectedly succeeded.",
        );
        evidence.push({
          user_id: userId,
          email,
          workspace_count: workspaces.length,
          user_list_email_matches: userRows.length,
          db_active_org_memberships: audit.active_org_membership_count,
          db_active_workspace_memberships:
            audit.active_workspace_membership_count,
          team_owner_only_status: teamForbidden.status,
          ...refreshEvidence,
        });
      }
    },
  },
  {
    id: "CORE-API-005",
    title:
      "RBAC team invite, accept, role update, remove, reactivate, and cleanup lifecycle",
    tags: [
      "core",
      "accounts",
      "team",
      "rbac",
      "workspace",
      "mutating",
      "data-integrity",
    ],
    async run({
      client,
      user,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      apiBase,
      evidence,
    }) {
      requireMutations();
      if (!isOrgOwner(user)) {
        skip(
          "Current user is not an org owner; RBAC invite lifecycle cleanup is unsafe.",
        );
      }
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const marker = runId.replace(/[^a-z0-9-]/gi, "").slice(0, 20);
      const email = `api.journey.rbac.${marker}@futureagi.local`.toLowerCase();
      const cancelEmail =
        `api.journey.rbac.cancel.${marker}@futureagi.local`.toLowerCase();
      const password = `ApiJourney${marker.slice(0, 8)}123!`;

      cleanup.defer("delete disposable RBAC team user artifacts", () =>
        deleteDisposableRbacUserArtifacts(email),
      );
      cleanup.defer("delete disposable RBAC cancelled invite artifacts", () =>
        deleteDisposableRbacUserArtifacts(cancelEmail),
      );

      const invited = await client.post(
        apiPath("/accounts/organization/invite/"),
        {
          emails: [email],
          org_level: 3,
          workspace_access: [{ workspace_id: workspaceId, level: 3 }],
        },
      );
      assert(
        asArray(invited?.invited).includes(email),
        "RBAC invite create did not report the disposable email as invited.",
      );

      let memberRows = asArray(
        await client.get(apiPath("/accounts/organization/members/"), {
          query: { search: email, page: 1, limit: 10 },
        }),
      );
      const pendingInvite = findUserRow(memberRows, null, email);
      assert(
        pendingInvite?.type === "invite",
        "Pending invite did not appear in member list.",
      );
      assert(
        pendingInvite?.status === "Pending",
        "New invite did not have Pending status.",
      );
      assert(
        isUuid(pendingInvite.id),
        "Pending invite row did not include an invite id.",
      );

      let audit = await loadRbacMemberLifecycleAudit({
        email,
        organizationId,
        workspaceId,
      });
      assert(
        audit.user_count === 1,
        "RBAC invite did not dual-write a user row.",
      );
      assert(
        audit.user_active === false,
        "New invite user should remain inactive before acceptance.",
      );
      assert(
        audit.pending_invite_count === 1 &&
          audit.invite_statuses.includes("Pending"),
        "RBAC invite DB audit did not find exactly one pending invite.",
      );
      assert(
        audit.org_membership_count === 1,
        "RBAC invite did not dual-write org membership.",
      );
      assert(
        audit.workspace_membership_count === 1,
        "RBAC invite did not dual-write workspace membership.",
      );

      const resent = await client.post(
        apiPath("/accounts/organization/invite/resend/"),
        {
          invite_id: pendingInvite.id,
          org_level: 1,
        },
      );
      assert(
        String(resent?.message || "")
          .toLowerCase()
          .includes("resent"),
        "Invite resend did not return success message.",
      );

      memberRows = asArray(
        await client.get(apiPath("/accounts/organization/members/"), {
          query: { search: email, page: 1, limit: 10 },
        }),
      );
      const resentInvite = findUserRow(memberRows, null, email);
      assert(
        resentInvite?.org_level === 1,
        "Invite resend org_level update did not reload in member list.",
      );

      const tokenInfo = await resolveInviteAcceptanceToken(email);
      assert(
        isUuid(tokenInfo.user_id),
        "Invite token resolver did not return a user id.",
      );
      assert(
        tokenInfo.uidb64 && tokenInfo.token,
        "Invite token resolver returned incomplete token data.",
      );

      const acceptPath = apiPath(
        "/accounts/accept-invitation/{uidb64}/{token}/",
        {
          uidb64: tokenInfo.uidb64,
          token: tokenInfo.token,
        },
      );
      const preview = await unauthenticatedApiRequest(
        apiBase,
        "GET",
        acceptPath,
      );
      assert(
        preview?.valid === true && preview?.email === email,
        "Invite preview did not validate the disposable invite.",
      );

      const accepted = await unauthenticatedApiRequest(
        apiBase,
        "POST",
        acceptPath,
        {
          new_password: password,
          repeat_password: password,
        },
      );
      assert(
        typeof accepted?.access === "string" &&
          typeof accepted?.refresh === "string",
        "Invite accept did not return access and refresh tokens.",
      );

      memberRows = asArray(
        await client.get(apiPath("/accounts/organization/members/"), {
          query: { search: email, page: 1, limit: 10 },
        }),
      );
      const activeMember = findUserRow(memberRows, tokenInfo.user_id, email);
      assert(
        activeMember?.type === "member",
        "Accepted invite did not become a member row.",
      );
      assert(
        activeMember?.status === "Active",
        "Accepted invite member did not become Active.",
      );
      assert(
        activeMember?.org_level === 1,
        "Accepted invite did not preserve resent org level.",
      );

      audit = await loadRbacMemberLifecycleAudit({
        email,
        organizationId,
        workspaceId,
      });
      assert(
        audit.user_active === true,
        "Accepted invite user did not become active.",
      );
      assert(
        audit.accepted_invite_count === 1 &&
          audit.active_org_membership_count === 1,
        "Accepted invite DB audit did not find accepted invite plus active org membership.",
      );
      assert(
        audit.active_workspace_membership_count === 1,
        "Accepted invite did not activate workspace membership.",
      );

      const roleUpdated = await client.post(
        apiPath("/accounts/organization/members/role/"),
        {
          user_id: tokenInfo.user_id,
          org_level: 3,
          workspace_access: [{ workspace_id: workspaceId, level: 1 }],
        },
      );
      assert(
        roleUpdated?.changes?.org_level?.new === 3,
        "Org member role update did not report the new org level.",
      );

      memberRows = asArray(
        await client.get(apiPath("/accounts/organization/members/"), {
          query: { search: email, page: 1, limit: 10 },
        }),
      );
      let updatedMember = findUserRow(memberRows, tokenInfo.user_id, email);
      assert(
        updatedMember?.org_level === 3,
        "Member list did not show updated org level.",
      );
      assert(
        updatedMember?.ws_level === 1,
        "Member list did not show updated workspace access level.",
      );

      const wsRoleUpdated = await client.post(
        apiPath("/accounts/organization/members/role/"),
        {
          user_id: tokenInfo.user_id,
          ws_level: 3,
          workspace_id: workspaceId,
        },
      );
      assert(
        wsRoleUpdated?.changes?.ws_level?.new === 3,
        "Workspace member role update did not report the new workspace level.",
      );

      memberRows = asArray(
        await client.get(apiPath("/accounts/organization/members/"), {
          query: { search: email, page: 1, limit: 10 },
        }),
      );
      updatedMember = findUserRow(memberRows, tokenInfo.user_id, email);
      assert(
        updatedMember?.ws_level === 3,
        "Member list did not show restored workspace member level.",
      );

      const removed = await client.delete(
        apiPath("/accounts/organization/members/remove/"),
        {
          body: { user_id: tokenInfo.user_id },
        },
      );
      assert(
        String(removed?.message || "")
          .toLowerCase()
          .includes("removed"),
        "Member removal did not return success message.",
      );

      memberRows = asArray(
        await client.get(apiPath("/accounts/organization/members/"), {
          query: { search: email, page: 1, limit: 10 },
        }),
      );
      const deactivatedMember = findUserRow(
        memberRows,
        tokenInfo.user_id,
        email,
      );
      assert(
        deactivatedMember?.status === "Deactivated",
        "Removed member did not reload as Deactivated.",
      );

      const reactivated = await client.post(
        apiPath("/accounts/organization/members/reactivate/"),
        {
          user_id: tokenInfo.user_id,
        },
      );
      assert(
        String(reactivated?.message || "")
          .toLowerCase()
          .includes("reactivated"),
        "Member reactivation did not return success message.",
      );

      memberRows = asArray(
        await client.get(apiPath("/accounts/organization/members/"), {
          query: { search: email, page: 1, limit: 10 },
        }),
      );
      const reactivatedMember = findUserRow(
        memberRows,
        tokenInfo.user_id,
        email,
      );
      assert(
        reactivatedMember?.status === "Active",
        "Reactivated member did not reload as Active.",
      );

      const cancelCreated = await client.post(
        apiPath("/accounts/organization/invite/"),
        {
          emails: [cancelEmail],
          org_level: 1,
          workspace_access: [{ workspace_id: workspaceId, level: 1 }],
        },
      );
      assert(
        asArray(cancelCreated?.invited).includes(cancelEmail),
        "RBAC cancel-path invite create did not report the disposable email as invited.",
      );
      const cancelRowsBefore = asArray(
        await client.get(apiPath("/accounts/organization/members/"), {
          query: { search: cancelEmail, page: 1, limit: 10 },
        }),
      );
      const cancellableInvite = findUserRow(
        cancelRowsBefore,
        null,
        cancelEmail,
      );
      assert(
        cancellableInvite?.type === "invite",
        "Cancel-path invite did not appear as an invite row.",
      );
      assert(
        cancellableInvite?.status === "Pending",
        "Cancel-path invite was not Pending before cancel.",
      );

      const cancelled = await client.delete(
        apiPath("/accounts/organization/invite/cancel/"),
        {
          body: { invite_id: cancellableInvite.id },
        },
      );
      assert(
        String(cancelled?.message || "")
          .toLowerCase()
          .includes("cancel"),
        "Invite cancel did not return success message.",
      );
      const cancelRowsAfter = asArray(
        await client.get(apiPath("/accounts/organization/members/"), {
          query: { search: cancelEmail, page: 1, limit: 10 },
        }),
      );
      assert(
        !findUserRow(cancelRowsAfter, null, cancelEmail),
        "Cancelled invite was still visible in organization members list.",
      );
      const cancelAudit = await loadRbacMemberLifecycleAudit({
        email: cancelEmail,
        organizationId,
        workspaceId,
      });
      assert(
        cancelAudit.cancelled_invite_count === 1,
        "Cancelled invite DB audit did not preserve cancelled status.",
      );
      assert(
        cancelAudit.deleted_org_membership_count === 1,
        "Cancel did not soft-delete dual-written org membership.",
      );
      assert(
        cancelAudit.deleted_workspace_membership_count === 1,
        "Cancel did not soft-delete dual-written workspace membership.",
      );

      await client.delete(
        apiPath("/accounts/team/users/{member_id}/", {
          member_id: tokenInfo.user_id,
        }),
      );
      await deleteDisposableRbacUserArtifacts(email);
      await deleteDisposableRbacUserArtifacts(cancelEmail);
      audit = await loadRbacMemberLifecycleAudit({
        email,
        organizationId,
        workspaceId,
      });
      assert(
        audit.user_count === 0,
        "Disposable RBAC user row remained after cleanup.",
      );
      assert(
        audit.invite_count === 0,
        "Disposable RBAC invite row remained after cleanup.",
      );
      assert(
        audit.org_membership_count === 0,
        "Disposable RBAC org membership remained after cleanup.",
      );
      assert(
        audit.workspace_membership_count === 0,
        "Disposable RBAC workspace membership remained after cleanup.",
      );
      const cancelCleanupAudit = await loadRbacMemberLifecycleAudit({
        email: cancelEmail,
        organizationId,
        workspaceId,
      });
      assert(
        cancelCleanupAudit.user_count === 0,
        "Disposable cancelled invite user row remained after cleanup.",
      );
      assert(
        cancelCleanupAudit.invite_count === 0,
        "Disposable cancelled invite row remained after cleanup.",
      );

      evidence.push({
        email,
        cancel_email: cancelEmail,
        invite_id: pendingInvite.id,
        cancelled_invite_id: cancellableInvite.id,
        user_id: tokenInfo.user_id,
        invited_count: asArray(invited.invited).length,
        accepted_invite_count: 1,
        cancelled_invite_count: cancelAudit.cancelled_invite_count,
        final_org_level: updatedMember.org_level,
        final_workspace_level: updatedMember.ws_level,
        deactivated_status: deactivatedMember.status,
        reactivated_status: reactivatedMember.status,
        cleanup_user_count: audit.user_count,
        cleanup_invite_count: audit.invite_count,
        cleanup_cancel_user_count: cancelCleanupAudit.user_count,
        cleanup_cancel_invite_count: cancelCleanupAudit.invite_count,
      });
    },
  },
  {
    id: "CORE-API-014",
    title: "Legacy team aliases, direct login, and cleanup guards",
    tags: [
      "core",
      "accounts",
      "team",
      "auth",
      "legacy",
      "mutating",
      "data-integrity",
    ],
    async run({
      client,
      user,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      apiBase,
      evidence,
    }) {
      requireMutations();
      if (!isOrgOwner(user)) {
        skip(
          "Current user is not an org owner; legacy team mutation coverage is unsafe.",
        );
      }
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const marker = runId.replace(/[^a-z0-9-]/gi, "").slice(0, 20);
      const legacyEmail =
        `api.journey.legacy-team.${marker}@futureagi.local`.toLowerCase();
      const aliasEmail =
        `api.journey.legacy-team-alias.${marker}@futureagi.local`.toLowerCase();
      const loginEmail =
        `api.journey.login.${marker}@futureagi.local`.toLowerCase();
      const loginPassword = `ApiJourney${marker.slice(0, 8)}123!`;

      cleanup.defer("delete disposable legacy team user artifacts", () =>
        deleteDisposableRbacUserArtifacts(legacyEmail),
      );
      cleanup.defer("delete disposable legacy alias guard artifacts", () =>
        deleteDisposableRbacUserArtifacts(aliasEmail),
      );
      cleanup.defer("delete disposable login user artifacts", () =>
        deleteDisposableRbacUserArtifacts(loginEmail),
      );

      const legacyCreated = await client.post(
        apiPath("/accounts/team/users/"),
        {
          members: [
            {
              email: legacyEmail,
              name: "API Journey Legacy Team",
              role: "Member",
            },
          ],
        },
      );
      const legacyMember = asArray(legacyCreated?.created_members).find(
        (row) => String(row?.email || "").toLowerCase() === legacyEmail,
      );
      assert(
        isUuid(legacyMember?.id),
        "Legacy team create did not return a created member id.",
      );

      let legacyAudit = await loadLegacyTeamMemberAudit({
        email: legacyEmail,
        organizationId,
        workspaceId,
      });
      assert(
        legacyAudit.user_count === 1,
        "Legacy team create did not persist one disposable user.",
      );
      assert(
        legacyAudit.user_active === false,
        "Legacy team create should leave invited user inactive.",
      );
      assert(
        legacyAudit.active_workspace_membership_count === 1,
        "Legacy team create did not persist active workspace membership.",
      );

      const memberDetail = await client.get(
        apiPath("/accounts/team/users/{member_id}/", {
          member_id: legacyMember.id,
        }),
        {
          query: {
            workspace_id: workspaceId,
            is_active: "false",
            page: 1,
            page_size: 10,
          },
        },
      );
      const detailRows = asArray(memberDetail);
      assert(
        getPayloadTotal(memberDetail) === 1,
        "Member detail alias did not return exactly one row.",
      );
      assert(
        detailRows.length === 1 && detailRows[0]?.id === legacyMember.id,
        "Member detail alias did not filter to the requested member id.",
      );
      assert(
        detailRows[0]?.workspace_member === true,
        "Member detail alias did not report the active workspace membership.",
      );

      const missingMemberDetail = await expectApiError(
        () =>
          client.get(
            apiPath("/accounts/team/users/{member_id}/", {
              member_id: "00000000-0000-0000-0000-000000000000",
            }),
          ),
        [404],
        "Legacy team member detail unexpectedly returned the full team for a missing id.",
      );

      const memberSpecificCreate = await expectApiError(
        () =>
          client.post(
            apiPath("/accounts/team/users/{member_id}/", {
              member_id: legacyMember.id,
            }),
            {
              members: [
                {
                  email: aliasEmail,
                  name: "API Journey Alias Guard",
                  role: "Member",
                },
              ],
            },
          ),
        [400],
        "Member-specific team create unexpectedly mutated while ignoring member_id.",
      );
      const aliasAudit = await loadLegacyTeamMemberAudit({
        email: aliasEmail,
        organizationId,
        workspaceId,
      });
      assert(
        aliasAudit.user_count === 0,
        "Member-specific team create guard still created a user.",
      );

      const collectionDelete = await expectApiError(
        () => client.delete(apiPath("/accounts/team/users/")),
        [400],
        "Collection team delete unexpectedly succeeded without a member id.",
      );

      const loginInvite = await client.post(
        apiPath("/accounts/organization/invite/"),
        {
          emails: [loginEmail],
          org_level: 3,
          workspace_access: [{ workspace_id: workspaceId, level: 3 }],
        },
      );
      assert(
        asArray(loginInvite?.invited).includes(loginEmail),
        "Login invite did not create disposable user.",
      );

      const loginTokenInfo = await resolveInviteAcceptanceToken(loginEmail);
      const acceptPath = apiPath(
        "/accounts/accept-invitation/{uidb64}/{token}/",
        {
          uidb64: loginTokenInfo.uidb64,
          token: loginTokenInfo.token,
        },
      );
      await unauthenticatedApiRequest(apiBase, "POST", acceptPath, {
        new_password: loginPassword,
        repeat_password: loginPassword,
      });

      const directLogin = await unauthenticatedApiRequest(
        apiBase,
        "POST",
        apiPath("/accounts/token/"),
        {
          email: loginEmail.toUpperCase(),
          password: loginPassword,
          remember_me: true,
        },
      );
      assert(
        typeof directLogin?.access === "string" &&
          directLogin.access.length > 20,
        "Direct login did not return an access token.",
      );
      assert(
        typeof directLogin?.refresh === "string" &&
          directLogin.refresh.length > 20,
        "Direct login did not return a refresh token.",
      );

      const loginAudit = await loadLegacyTeamMemberAudit({
        email: loginEmail,
        organizationId,
        workspaceId,
      });
      assert(
        loginAudit.user_active === true,
        "Direct-login user was not active after invite acceptance.",
      );
      assert(
        loginAudit.active_access_token_count >= 1,
        "Direct login did not persist an active access token.",
      );
      assert(
        loginAudit.active_refresh_token_count >= 1,
        "Direct login did not persist an active refresh token.",
      );
      assert(
        loginAudit.selected_organization_id === organizationId,
        "Direct login did not persist selected organization context.",
      );

      await client.delete(
        apiPath("/accounts/team/users/{member_id}/", {
          member_id: legacyMember.id,
        }),
      );
      await deleteDisposableRbacUserArtifacts(legacyEmail);
      await deleteDisposableRbacUserArtifacts(aliasEmail);
      await deleteDisposableRbacUserArtifacts(loginEmail);
      legacyAudit = await loadLegacyTeamMemberAudit({
        email: legacyEmail,
        organizationId,
        workspaceId,
      });
      const loginCleanupAudit = await loadLegacyTeamMemberAudit({
        email: loginEmail,
        organizationId,
        workspaceId,
      });
      assert(
        legacyAudit.user_count === 0,
        "Disposable legacy team user remained after cleanup.",
      );
      assert(
        loginCleanupAudit.user_count === 0,
        "Disposable login user remained after cleanup.",
      );

      evidence.push({
        legacy_email: legacyEmail,
        legacy_user_id: legacyMember.id,
        legacy_detail_total: getPayloadTotal(memberDetail),
        missing_member_detail_status: missingMemberDetail.status,
        member_specific_create_status: memberSpecificCreate.status,
        collection_delete_status: collectionDelete.status,
        login_email: loginEmail,
        login_user_id: loginTokenInfo.user_id,
        active_access_token_count: loginAudit.active_access_token_count,
        active_refresh_token_count: loginAudit.active_refresh_token_count,
        cleanup_legacy_user_count: legacyAudit.user_count,
        cleanup_login_user_count: loginCleanupAudit.user_count,
      });
    },
  },
  {
    id: "CORE-API-006",
    title:
      "Profile name revert, timezone capture, 2FA status, passkey list, and org security policy",
    tags: [
      "core",
      "accounts",
      "settings",
      "profile",
      "security",
      "mutating",
      "data-integrity",
    ],
    async run({ client, user, cleanup, runId, organizationId, evidence }) {
      requireMutations();
      const userInfo = await client.get(apiPath("/accounts/user-info/"));
      const userId = currentUserId(userInfo) || currentUserId(user);
      const email = currentUserEmail(userInfo) || currentUserEmail(user);
      assert(
        isUuid(userId),
        "Authenticated user-info did not include a valid user id.",
      );
      assert(
        email.includes("@"),
        "Authenticated user-info did not include an email.",
      );
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );

      const originalProfile = await client.get(
        apiPath("/accounts/get-user-profile-details/"),
      );
      const originalName = String(originalProfile?.name || "").trim();
      assert(originalName, "Profile details did not include a current name.");
      assert(
        originalProfile?.email === email,
        "Profile details email did not match user-info.",
      );

      const originalAudit = await loadProfileSecurityDbAudit({
        userId,
        organizationId,
      });
      assert(
        originalAudit.user_id === userId,
        "Profile DB audit did not resolve the current user row.",
      );
      assert(
        originalAudit.email === email,
        "Profile DB audit user email did not match user-info.",
      );
      assert(
        originalAudit.name === originalName,
        "Profile DB audit name did not match profile details.",
      );

      cleanup.defer("restore current user profile name", async () => {
        await client.post(apiPath("/accounts/update-user-full-name/"), {
          name: originalName,
        });
      });
      cleanup.defer("restore current user timezone", async () => {
        await restoreUserTimezoneDb({
          userId,
          timezone: originalAudit.last_timezone,
        });
      });

      const marker = runId.replace(/[^a-z0-9-]/gi, "").slice(0, 12);
      const updatedName = `Kartik API ${marker}`;
      await client.post(apiPath("/accounts/update-user-full-name/"), {
        name: updatedName,
      });
      let profile = await client.get(
        apiPath("/accounts/get-user-profile-details/"),
      );
      assert(
        profile?.name === updatedName,
        "Profile details did not reflect updated full name.",
      );
      let audit = await loadProfileSecurityDbAudit({ userId, organizationId });
      assert(
        audit.name === updatedName,
        "DB audit did not persist updated full name.",
      );

      const invalidTimezoneError = await expectApiError(
        () =>
          client.post(apiPath("/accounts/me/timezone/"), {
            timezone: "Not/A_Real_Timezone",
          }),
        [400],
        "Invalid timezone unexpectedly succeeded.",
      );
      const timezone = "Asia/Kolkata";
      const timezoneResponse = await client.post(
        apiPath("/accounts/me/timezone/"),
        {
          timezone,
        },
      );
      assert(
        timezoneResponse?.timezone === timezone,
        "Timezone capture did not echo the saved timezone.",
      );
      audit = await loadProfileSecurityDbAudit({ userId, organizationId });
      assert(
        audit.last_timezone === timezone,
        "DB audit did not persist last_timezone.",
      );

      const twoFactorStatus = await client.get(
        apiPath("/accounts/2fa/status/"),
        {
          unwrap: false,
        },
      );
      assert(
        typeof twoFactorStatus?.two_factor_enabled === "boolean",
        "2FA status did not return canonical two_factor_enabled boolean.",
      );
      assert(
        twoFactorStatus?.twoFactorEnabled === undefined,
        "2FA status returned stale camelCase alias.",
      );
      assert(
        Object.prototype.hasOwnProperty.call(
          twoFactorStatus ?? {},
          "recovery_codes_remaining",
        ),
        "2FA status did not return canonical recovery_codes_remaining key.",
      );
      assert(
        twoFactorStatus?.recoveryCodesRemaining === undefined,
        "2FA status returned stale recoveryCodesRemaining alias.",
      );
      assert(
        typeof twoFactorStatus?.methods?.totp?.enabled === "boolean",
        "2FA status did not include TOTP enabled boolean.",
      );
      assert(
        typeof twoFactorStatus?.methods?.passkey?.enabled === "boolean" &&
          Number.isInteger(twoFactorStatus?.methods?.passkey?.count),
        "2FA status did not include passkey enabled/count fields.",
      );

      const passkeys = asArray(
        await client.get(apiPath("/accounts/passkeys/")),
      );
      assert(
        passkeys.every((passkey) => passkey?.id && passkey?.name),
        "Passkey list returned a row without id/name.",
      );
      assert(
        passkeys.length === audit.passkey_count,
        "Passkey API count did not match DB audit count.",
      );

      const orgPolicy = await client.get(
        apiPath("/accounts/organization/2fa-policy/"),
        {
          unwrap: false,
        },
      );
      assert(
        typeof orgPolicy?.require_2fa === "boolean",
        "Organization 2FA policy did not return require_2fa boolean.",
      );
      assert(
        Number.isInteger(orgPolicy?.require_2fa_grace_period_days),
        "Organization 2FA policy did not return grace period days.",
      );
      assert(
        orgPolicy?.require2fa === undefined,
        "Organization 2FA policy returned stale camelCase alias.",
      );

      await client.post(apiPath("/accounts/update-user-full-name/"), {
        name: originalName,
      });
      profile = await client.get(
        apiPath("/accounts/get-user-profile-details/"),
      );
      assert(
        profile?.name === originalName,
        "Profile full name did not revert through public API.",
      );
      await restoreUserTimezoneDb({
        userId,
        timezone: originalAudit.last_timezone,
      });
      audit = await loadProfileSecurityDbAudit({ userId, organizationId });
      assert(
        audit.name === originalName,
        "DB audit did not show reverted full name.",
      );
      assert(
        audit.last_timezone === originalAudit.last_timezone,
        "DB audit did not show restored last_timezone.",
      );

      evidence.push({
        user_id: userId,
        email,
        original_name: originalName,
        temporary_name: updatedName,
        reverted_name: profile.name,
        timezone,
        restored_timezone: audit.last_timezone,
        invalid_timezone_status: invalidTimezoneError.status,
        two_factor_enabled: twoFactorStatus.two_factor_enabled,
        totp_enabled: twoFactorStatus.methods.totp.enabled,
        passkey_count: passkeys.length,
        org_require_2fa: orgPolicy.require_2fa,
        org_2fa_grace_days: orgPolicy.require_2fa_grace_period_days,
      });
    },
  },
  {
    id: "CORE-API-007",
    title:
      "Workspace display name revert, workspace list, and member list contracts",
    tags: [
      "core",
      "accounts",
      "settings",
      "workspace",
      "members",
      "mutating",
      "data-integrity",
    ],
    async run({
      client,
      user,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      const userInfo = await client.get(apiPath("/accounts/user-info/"));
      const userId = currentUserId(userInfo) || currentUserId(user);
      const email = currentUserEmail(userInfo) || currentUserEmail(user);
      assert(
        isUuid(userId),
        "Authenticated user-info did not include a valid user id.",
      );
      assert(
        email.includes("@"),
        "Authenticated user-info did not include an email.",
      );
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const workspacesPayload = await client.get(
        apiPath("/accounts/workspace/list/"),
      );
      const workspaces = asArray(workspacesPayload);
      const workspace = workspaces.find((row) => row?.id === workspaceId);
      assert(workspace, "Workspace list did not include the active workspace.");
      const originalDisplayName = String(
        workspace.display_name || workspace.name || "",
      ).trim();
      assert(
        originalDisplayName,
        "Workspace list did not include a display name or name.",
      );
      assert(
        Number(workspace.user_ws_level ?? 0) >= 8,
        "Current user is not workspace admin for the active workspace.",
      );

      const originalAudit = await loadWorkspaceSettingsDbAudit({
        userId,
        organizationId,
        workspaceId,
      });
      assert(
        originalAudit.workspace_id === workspaceId,
        "Workspace DB audit did not resolve the active workspace.",
      );
      assert(
        originalAudit.workspace_organization_id === organizationId,
        "Workspace DB audit organization did not match request organization.",
      );
      assert(
        originalAudit.display_name === originalDisplayName,
        "Workspace DB audit display name did not match workspace list.",
      );
      assert(
        originalAudit.active_member_count > 0,
        "Workspace DB audit did not find any active workspace members.",
      );

      cleanup.defer("restore workspace display name", async () => {
        try {
          await client.put(
            apiPath("/accounts/workspaces/{workspace_id}/", {
              workspace_id: workspaceId,
            }),
            {
              display_name: originalDisplayName,
            },
          );
        } finally {
          await restoreWorkspaceDisplayNameDb({
            workspaceId,
            displayName: originalDisplayName,
          });
        }
      });

      const marker = runId.replace(/[^a-z0-9-]/gi, "").slice(0, 12);
      const updatedDisplayName = `${originalDisplayName} API ${marker}`.slice(
        0,
        120,
      );
      const updateResponse = await client.put(
        apiPath("/accounts/workspaces/{workspace_id}/", {
          workspace_id: workspaceId,
        }),
        { display_name: updatedDisplayName },
      );
      assert(
        updateResponse?.workspace?.display_name === updatedDisplayName,
        "Workspace update response did not return the updated display name.",
      );

      let audit = await loadWorkspaceSettingsDbAudit({
        userId,
        organizationId,
        workspaceId,
      });
      assert(
        audit.display_name === updatedDisplayName,
        "Workspace DB audit did not persist updated display name.",
      );

      const filteredWorkspaces = asArray(
        await client.get(apiPath("/accounts/workspace/list/"), {
          query: { search: updatedDisplayName },
        }),
      );
      assert(
        filteredWorkspaces.some((row) => row?.id === workspaceId),
        "Workspace list search did not find the updated display name.",
      );

      const legacyMembersPayload = await client.get(
        apiPath("/accounts/workspaces/{workspace_id}/members/", {
          workspace_id: workspaceId,
        }),
      );
      const legacyMembers = asArray(
        legacyMembersPayload?.members || legacyMembersPayload,
      );
      const legacyCurrentUser = findUserRow(legacyMembers, userId, email);
      assert(
        legacyCurrentUser,
        "Legacy workspace members endpoint did not return the current user.",
      );

      const rbacMembersPayload = await client.get(
        apiPath("/accounts/workspace/{workspace_id}/members/", {
          workspace_id: workspaceId,
        }),
        {
          query: {
            page: 1,
            limit: 10,
            search: email,
            filter_status: ["Active"],
            sort: "email",
          },
        },
      );
      const rbacMembers = asArray(rbacMembersPayload);
      const rbacCurrentUser = findUserRow(rbacMembers, userId, email);
      assert(
        rbacCurrentUser,
        "Current RBAC workspace members endpoint did not return the current user by email search.",
      );
      assert(
        rbacCurrentUser.status === "Active",
        "Current RBAC workspace member search returned a non-active current user.",
      );
      assert(
        Number(rbacCurrentUser.ws_level ?? 0) >= 8,
        "Current RBAC workspace member row did not expose workspace-admin level.",
      );

      await client.put(
        apiPath("/accounts/workspaces/{workspace_id}/", {
          workspace_id: workspaceId,
        }),
        {
          display_name: originalDisplayName,
        },
      );
      await restoreWorkspaceDisplayNameDb({
        workspaceId,
        displayName: originalDisplayName,
      });
      audit = await loadWorkspaceSettingsDbAudit({
        userId,
        organizationId,
        workspaceId,
      });
      assert(
        audit.display_name === originalDisplayName,
        "Workspace display name did not revert.",
      );

      evidence.push({
        workspace_id: workspaceId,
        original_display_name: originalDisplayName,
        temporary_display_name: updatedDisplayName,
        reverted_display_name: audit.display_name,
        workspace_search_matches: filteredWorkspaces.length,
        active_member_count: audit.active_member_count,
        legacy_member_count: legacyMembers.length,
        rbac_search_count: rbacMembers.length,
        current_user_ws_level: rbacCurrentUser.ws_level,
        current_user_org_level: rbacCurrentUser.org_level,
      });
    },
  },
  {
    id: "CORE-API-008",
    title:
      "Workspace usage summary, eval breakdown, month-boundary guard, and strict query contracts",
    tags: [
      "core",
      "settings",
      "workspace",
      "usage",
      "billing",
      "mutating",
      "data-integrity",
    ],
    async run({
      client,
      user,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      const userInfo = await client.get(apiPath("/accounts/user-info/"));
      const userId = currentUserId(userInfo) || currentUserId(user);
      assert(
        isUuid(userId),
        "Authenticated user-info did not include a valid user id.",
      );
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const now = new Date();
      const currentMonth = now.getUTCMonth() + 1;
      const currentYear = now.getUTCFullYear();
      const usageSummary = await client.get(
        apiPath("/usage/workspace-usage-summary/"),
        {
          query: { month: currentMonth, year: currentYear },
        },
      );
      assert(
        usageSummary?.organization_id === organizationId,
        "Workspace usage summary organization_id did not match context.",
      );
      assert(
        Number.isInteger(usageSummary?.total_workspaces),
        "Workspace usage summary did not return total_workspaces.",
      );
      const workspaces = asArray(usageSummary?.workspaces);
      assert(
        workspaces.length > 0,
        "Workspace usage summary returned no workspaces.",
      );
      const activeWorkspace = workspaces.find((row) => row?.id === workspaceId);
      assert(
        activeWorkspace,
        "Workspace usage summary did not include the active workspace.",
      );
      assertUsageWorkspaceRow(activeWorkspace, "active workspace usage row");

      const evalSummary = await client.get(
        apiPath("/usage/workspace-eval-summary/"),
        {
          query: {
            workspace_id: workspaceId,
            month: currentMonth,
            year: currentYear,
          },
        },
      );
      assertEvalSummaryPayload(evalSummary, "active workspace eval summary");

      const invalidMonthError = await expectApiError(
        () =>
          client.get(apiPath("/usage/workspace-usage-summary/"), {
            query: { month: 13, year: currentYear },
          }),
        [400],
        "Invalid workspace usage month unexpectedly succeeded.",
      );
      assert(
        errorText(invalidMonthError).toLowerCase().includes("month"),
        "Invalid month error did not identify month.",
      );

      const missingWorkspaceError = await expectApiError(
        () =>
          client.get(apiPath("/usage/workspace-eval-summary/"), {
            query: { month: currentMonth, year: currentYear },
          }),
        [400],
        "Workspace eval summary without workspace_id unexpectedly succeeded.",
      );
      assert(
        errorText(missingWorkspaceError).toLowerCase().includes("workspace_id"),
        "Missing workspace_id error did not identify workspace_id.",
      );

      const unknownQueryError = await expectApiError(
        () =>
          client.get(apiPath("/usage/workspace-usage-summary/"), {
            query: { month: currentMonth, year: currentYear, legacy: 1 },
          }),
        [400],
        "Workspace usage summary accepted an unknown query field.",
      );
      assert(
        errorText(unknownQueryError).toLowerCase().includes("unknown field"),
        "Unknown query field error did not mention unknown field.",
      );

      const marker = runId.replace(/[^a-z0-9-]/gi, "").slice(0, 20);
      const boundary = await seedWorkspaceUsageBoundaryData({
        marker,
        organizationId,
        userId,
      });
      cleanup.defer("delete disposable workspace usage boundary data", () =>
        deleteWorkspaceUsageBoundaryData({
          workspaceId: boundary.workspace_id,
          sourceIdPrefix: boundary.source_id_prefix,
        }),
      );

      const boundaryAudit = await loadWorkspaceUsageBoundaryDbAudit({
        workspaceId: boundary.workspace_id,
        sourceIdPrefix: boundary.source_id_prefix,
        targetMonth: boundary.target_month,
        targetYear: boundary.target_year,
      });
      assert(
        boundaryAudit.month_log_count === 1 &&
          boundaryAudit.next_month_log_count === 1,
        "DB audit did not create one in-month and one next-month usage log.",
      );
      assert(
        numbersClose(
          boundaryAudit.month_deducted_cost,
          boundary.expected_month_cost,
        ),
        "DB audit in-month usage cost did not match seed data.",
      );

      const boundaryUsageSummary = await client.get(
        apiPath("/usage/workspace-usage-summary/"),
        {
          query: { month: boundary.target_month, year: boundary.target_year },
        },
      );
      const boundaryWorkspace = asArray(boundaryUsageSummary?.workspaces).find(
        (row) => row?.id === boundary.workspace_id,
      );
      assert(
        boundaryWorkspace,
        "Boundary workspace did not appear in workspace usage summary.",
      );
      assertUsageWorkspaceRow(
        boundaryWorkspace,
        "boundary workspace usage row",
      );
      assert(
        boundaryWorkspace.evaluations.count === 1 &&
          numbersClose(
            boundaryWorkspace.evaluations.cost,
            boundary.expected_month_cost,
          ),
        "Workspace usage summary did not isolate the seeded calendar month.",
      );

      const boundaryEvalSummary = await client.get(
        apiPath("/usage/workspace-eval-summary/"),
        {
          query: {
            workspace_id: boundary.workspace_id,
            month: boundary.target_month,
            year: boundary.target_year,
          },
        },
      );
      assertEvalSummaryPayload(
        boundaryEvalSummary,
        "boundary workspace eval summary",
      );
      assert(
        boundaryEvalSummary.total.count === 1 &&
          numbersClose(
            boundaryEvalSummary.total.cost,
            boundary.expected_month_cost,
          ),
        "Workspace eval summary did not return exactly the seeded calendar-month usage.",
      );

      await deleteWorkspaceUsageBoundaryData({
        workspaceId: boundary.workspace_id,
        sourceIdPrefix: boundary.source_id_prefix,
      });
      const cleanupAudit = await loadWorkspaceUsageBoundaryDbAudit({
        workspaceId: boundary.workspace_id,
        sourceIdPrefix: boundary.source_id_prefix,
        targetMonth: boundary.target_month,
        targetYear: boundary.target_year,
      });
      assert(
        cleanupAudit.total_log_count === 0,
        "Disposable workspace usage logs remained after cleanup.",
      );
      assert(
        cleanupAudit.workspace_count === 0,
        "Disposable usage workspace remained after cleanup.",
      );

      evidence.push({
        workspace_id: workspaceId,
        current_month: currentMonth,
        current_year: currentYear,
        usage_workspace_count: workspaces.length,
        active_workspace_overall_count: activeWorkspace.overall.count,
        active_workspace_eval_count: evalSummary.total.count,
        invalid_month_status: invalidMonthError.status,
        missing_workspace_status: missingWorkspaceError.status,
        unknown_query_status: unknownQueryError.status,
        boundary_workspace_id: boundary.workspace_id,
        boundary_month: `${boundary.target_year}-${String(boundary.target_month).padStart(2, "0")}`,
        boundary_db_month_cost: boundaryAudit.month_deducted_cost,
        boundary_db_next_month_cost: boundaryAudit.next_month_deducted_cost,
        boundary_usage_cost: boundaryWorkspace.evaluations.cost,
        boundary_eval_cost: boundaryEvalSummary.total.cost,
        cleanup_log_count: cleanupAudit.total_log_count,
        cleanup_workspace_count: cleanupAudit.workspace_count,
      });
    },
  },
  {
    id: "CORE-API-009",
    title:
      "Workspace integration list, detail masking, sync-log isolation, and cleanup",
    tags: [
      "core",
      "settings",
      "workspace",
      "integrations",
      "mutating",
      "data-integrity",
    ],
    async run({
      client,
      user,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      const userInfo = await client.get(apiPath("/accounts/user-info/"));
      const userId = currentUserId(userInfo) || currentUserId(user);
      assert(
        isUuid(userId),
        "Authenticated user-info did not include a valid user id.",
      );
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const marker = runId.replace(/[^a-z0-9-]/gi, "").slice(0, 20);
      const seeded = await seedIntegrationConnectionData({
        marker,
        organizationId,
        workspaceId,
        userId,
      });
      cleanup.defer("delete disposable integration connection data", () =>
        deleteIntegrationConnectionData({ connectionId: seeded.connection_id }),
      );

      const connectionList = await client.get(
        apiPath("/integrations/connections/"),
        {
          query: { page_number: 0, page_size: 100 },
        },
      );
      assert(
        Number.isInteger(connectionList?.metadata?.total_count),
        "Integration connection list did not return metadata.total_count.",
      );
      const connections = Array.isArray(connectionList?.connections)
        ? connectionList.connections
        : [];
      const seededRow = connections.find(
        (row) => row?.id === seeded.connection_id,
      );
      assert(
        seededRow,
        "Integration connection list did not include the seeded connection.",
      );
      assert(
        seededRow.display_name === seeded.display_name,
        "Integration list row did not return seeded display_name.",
      );
      assert(
        seededRow.host_url === seeded.host_url,
        "Integration list row did not return seeded host_url.",
      );
      assert(
        seededRow.total_traces_synced === seeded.total_traces_synced,
        "Integration list row did not return seeded trace count.",
      );
      assert(
        seededRow.displayName === undefined && seededRow.hostUrl === undefined,
        "Integration list unexpectedly returned stale camelCase display aliases.",
      );
      assertNoCredentialLeak(seededRow, seeded, "integration list row");

      const detail = await client.get(
        apiPath("/integrations/connections/{id}/", {
          id: seeded.connection_id,
        }),
      );
      assert(
        detail?.id === seeded.connection_id,
        "Integration detail returned the wrong id.",
      );
      assert(
        detail?.display_name === seeded.display_name,
        "Integration detail display_name mismatch.",
      );
      assert(
        detail?.host_url === seeded.host_url,
        "Integration detail host_url mismatch.",
      );
      assert(
        detail?.public_key_display === seeded.expected_public_key_display,
        "Public key was not masked as expected.",
      );
      assert(
        detail?.secret_key_display === seeded.expected_secret_key_display,
        "Secret key was not masked as expected.",
      );
      assert(
        detail?.public_key === undefined && detail?.secret_key === undefined,
        "Integration detail leaked raw credential fields.",
      );
      assertNoCredentialLeak(detail, seeded, "integration detail");

      const syncLogsPayload = await client.get(
        apiPath("/integrations/sync-logs/"),
        {
          query: {
            connection_id: seeded.connection_id,
            page_number: 0,
            page_size: 10,
          },
        },
      );
      assert(
        syncLogsPayload?.metadata?.total_count === 1,
        "Filtered sync-log list did not return exactly the seeded log.",
      );
      const syncLogs = Array.isArray(syncLogsPayload?.sync_logs)
        ? syncLogsPayload.sync_logs
        : [];
      assert(
        syncLogs.length === 1,
        "Filtered sync-log page did not include one row.",
      );
      const syncLog = syncLogs[0];
      assert(
        syncLog.connection === seeded.connection_id,
        "Sync log row did not belong to seeded connection.",
      );
      assert(
        syncLog.status === "failed",
        "Seeded sync log did not reload with failed status.",
      );
      assertNoCredentialLeak(syncLog, seeded, "sync log row");

      const unknownQueryError = await expectApiError(
        () =>
          client.get(apiPath("/integrations/connections/"), {
            query: { legacyPage: 1 },
          }),
        [400],
        "Integration connection list accepted an unknown query field.",
      );
      const invalidConnectionError = await expectApiError(
        () =>
          client.get(apiPath("/integrations/sync-logs/"), {
            query: { connection_id: "not-a-uuid" },
          }),
        [400],
        "Sync-log list accepted an invalid connection_id.",
      );
      const missingDetailError = await expectApiError(
        () =>
          client.get(
            apiPath("/integrations/connections/{id}/", {
              id: "00000000-0000-0000-0000-000000000000",
            }),
          ),
        [404],
        "Missing integration detail unexpectedly succeeded.",
      );

      const audit = await loadIntegrationConnectionDbAudit({
        connectionId: seeded.connection_id,
        organizationId,
        workspaceId,
      });
      assert(
        audit.connection_count === 1,
        "DB audit did not find the seeded integration connection.",
      );
      assert(
        audit.sync_log_count === 1,
        "DB audit did not find the seeded integration sync log.",
      );
      assert(
        audit.workspace_id === workspaceId,
        "DB audit integration workspace did not match context.",
      );
      assert(
        audit.organization_id === organizationId,
        "DB audit integration organization did not match context.",
      );
      assert(
        audit.encrypted_credentials_bytes > 0,
        "DB audit found empty encrypted credentials.",
      );

      await deleteIntegrationConnectionData({
        connectionId: seeded.connection_id,
      });
      const cleanupAudit = await loadIntegrationConnectionDbAudit({
        connectionId: seeded.connection_id,
        organizationId,
        workspaceId,
      });
      assert(
        cleanupAudit.connection_count === 0,
        "Disposable integration connection remained after cleanup.",
      );
      assert(
        cleanupAudit.sync_log_count === 0,
        "Disposable integration sync log remained after cleanup.",
      );

      evidence.push({
        connection_id: seeded.connection_id,
        display_name: seeded.display_name,
        platform: detail.platform,
        status: detail.status,
        list_total_count: connectionList.metadata.total_count,
        sync_log_count: syncLogsPayload.metadata.total_count,
        public_key_display: detail.public_key_display,
        secret_key_display: detail.secret_key_display,
        unknown_query_status: unknownQueryError.status,
        invalid_connection_status: invalidConnectionError.status,
        missing_detail_status: missingDetailError.status,
        db_encrypted_credentials_bytes: audit.encrypted_credentials_bytes,
        cleanup_connection_count: cleanupAudit.connection_count,
        cleanup_sync_log_count: cleanupAudit.sync_log_count,
      });
    },
  },
  {
    id: "CORE-API-013",
    title:
      "Global settings integration detail update, pause, resume, sync guards, and delete lifecycle",
    tags: ["core", "settings", "integrations", "mutating", "data-integrity"],
    async run({
      client,
      user,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      const userInfo = await client.get(apiPath("/accounts/user-info/"));
      const userId = currentUserId(userInfo) || currentUserId(user);
      assert(
        isUuid(userId),
        "Authenticated user-info did not include a valid user id.",
      );
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const marker = runId.replace(/[^a-z0-9-]/gi, "").slice(0, 20);
      const seeded = await seedIntegrationConnectionData({
        marker,
        organizationId,
        workspaceId,
        userId,
        status: "active",
        includeProject: true,
        lastSyncedSecondsAgo: 20,
        displayNamePrefix: "API Journey Global Integration",
      });
      cleanup.defer(
        "delete disposable global integration connection data",
        () =>
          deleteIntegrationConnectionData({
            connectionId: seeded.connection_id,
          }),
      );

      const detail = await client.get(
        apiPath("/integrations/connections/{id}/", {
          id: seeded.connection_id,
        }),
      );
      assert(
        detail?.id === seeded.connection_id,
        "Global integration detail returned the wrong id.",
      );
      assert(
        detail?.status === "active",
        "Seeded global integration was not active.",
      );
      assert(
        detail?.project_name,
        "Seeded global integration detail did not include linked project name.",
      );
      assertNoCredentialLeak(detail, seeded, "global integration detail");

      const patchName = `${seeded.display_name} patched`;
      const patched = await client.patch(
        apiPath("/integrations/connections/{id}/", {
          id: seeded.connection_id,
        }),
        {
          display_name: patchName,
          sync_interval_seconds: 600,
        },
      );
      assert(
        patched?.display_name === patchName,
        "PATCH integration display_name did not persist.",
      );
      assert(
        patched?.sync_interval_seconds === 600,
        "PATCH integration sync interval did not persist.",
      );
      assert(
        patched?.status === "active",
        "PATCH unexpectedly changed integration status.",
      );
      assertNoCredentialLeak(patched, seeded, "patched integration response");

      const putName = `${seeded.display_name} put`;
      const putUpdated = await client.put(
        apiPath("/integrations/connections/{id}/", {
          id: seeded.connection_id,
        }),
        {
          display_name: putName,
        },
      );
      assert(
        putUpdated?.display_name === putName,
        "PUT integration display_name did not persist.",
      );
      assert(
        putUpdated?.status === "active",
        "PUT unexpectedly changed integration status.",
      );
      assertNoCredentialLeak(putUpdated, seeded, "put integration response");

      const forbiddenPutError = await expectApiError(
        () =>
          client.put(
            apiPath("/integrations/connections/{id}/", {
              id: seeded.connection_id,
            }),
            {
              display_name: "should not persist",
              status: "paused",
            },
          ),
        [400],
        "Integration PUT accepted direct status mutation.",
      );
      assert(
        errorText(forbiddenPutError).includes("status"),
        "Integration PUT status mutation error did not identify the status field.",
      );

      const paused = await client.post(
        apiPath("/integrations/connections/{id}/pause/", {
          id: seeded.connection_id,
        }),
      );
      assert(
        paused?.status === "paused",
        "Pause integration did not return paused status.",
      );

      const pausedSyncError = await expectApiError(
        () =>
          client.post(
            apiPath("/integrations/connections/{id}/sync_now/", {
              id: seeded.connection_id,
            }),
          ),
        [400],
        "Paused integration sync_now unexpectedly succeeded.",
      );
      assert(
        errorText(pausedSyncError).toLowerCase().includes("paused"),
        "Paused sync_now error did not explain the paused state.",
      );

      const resumed = await client.post(
        apiPath("/integrations/connections/{id}/resume/", {
          id: seeded.connection_id,
        }),
      );
      assert(
        resumed?.status === "active",
        "Resume integration did not return active status.",
      );

      const duplicateResumeError = await expectApiError(
        () =>
          client.post(
            apiPath("/integrations/connections/{id}/resume/", {
              id: seeded.connection_id,
            }),
          ),
        [400],
        "Active integration resume unexpectedly succeeded.",
      );
      assert(
        errorText(duplicateResumeError).toLowerCase().includes("paused"),
        "Duplicate resume error did not explain the paused-only contract.",
      );

      const cooldownSyncError = await expectApiError(
        () =>
          client.post(
            apiPath("/integrations/connections/{id}/sync_now/", {
              id: seeded.connection_id,
            }),
          ),
        [400],
        "Integration sync_now ignored the manual sync cooldown.",
      );
      assert(
        errorText(cooldownSyncError).toLowerCase().includes("wait"),
        "Cooldown sync_now error did not explain the wait period.",
      );

      const audit = await loadIntegrationConnectionDbAudit({
        connectionId: seeded.connection_id,
        organizationId,
        workspaceId,
      });
      assert(
        audit.connection_count === 1,
        "DB audit did not find global integration connection.",
      );
      assert(
        audit.sync_log_count === 1,
        "DB audit did not find global integration sync log.",
      );
      assert(
        audit.display_name === putName,
        "DB audit display_name did not match PUT update.",
      );
      assert(
        audit.status === "active",
        "DB audit status did not match resumed active state.",
      );
      assert(
        audit.sync_interval_seconds === 600,
        "DB audit sync interval did not match PATCH update.",
      );
      assert(
        audit.deleted === false,
        "DB audit found integration deleted before delete call.",
      );

      const deleted = await client.delete(
        apiPath("/integrations/connections/{id}/", {
          id: seeded.connection_id,
        }),
      );
      assert(
        deleted?.deleted === true,
        "Integration delete did not return deleted=true.",
      );

      const deletedDetailError = await expectApiError(
        () =>
          client.get(
            apiPath("/integrations/connections/{id}/", {
              id: seeded.connection_id,
            }),
          ),
        [404],
        "Deleted integration detail unexpectedly remained visible.",
      );

      const deleteAudit = await loadIntegrationConnectionDbAudit({
        connectionId: seeded.connection_id,
        organizationId,
        workspaceId,
      });
      assert(
        deleteAudit.connection_count === 1,
        "Deleted integration row was not retained for soft-delete audit.",
      );
      assert(
        deleteAudit.deleted === true,
        "Public delete did not soft-delete the integration row.",
      );
      assert(
        deleteAudit.deleted_at_set === true,
        "Public delete did not set deleted_at.",
      );

      await deleteIntegrationConnectionData({
        connectionId: seeded.connection_id,
      });
      const cleanupAudit = await loadIntegrationConnectionDbAudit({
        connectionId: seeded.connection_id,
        organizationId,
        workspaceId,
      });
      assert(
        cleanupAudit.connection_count === 0,
        "Disposable global integration connection remained after cleanup.",
      );
      assert(
        cleanupAudit.sync_log_count === 0,
        "Disposable global integration sync log remained after cleanup.",
      );

      evidence.push({
        connection_id: seeded.connection_id,
        display_name: putName,
        project_id: seeded.project_id,
        patch_sync_interval_seconds: patched.sync_interval_seconds,
        put_status_mutation_status: forbiddenPutError.status,
        paused_status: paused.status,
        paused_sync_status: pausedSyncError.status,
        resumed_status: resumed.status,
        duplicate_resume_status: duplicateResumeError.status,
        cooldown_sync_status: cooldownSyncError.status,
        deleted_detail_status: deletedDetailError.status,
        db_deleted_at_set: deleteAudit.deleted_at_set,
        cleanup_connection_count: cleanupAudit.connection_count,
        cleanup_sync_log_count: cleanupAudit.sync_log_count,
      });
    },
  },
  {
    id: "CORE-API-010",
    title:
      "Workspace AI provider status, masking, custom model list, and DB parity",
    tags: [
      "core",
      "settings",
      "workspace",
      "ai-providers",
      "data-integrity",
      "safe",
    ],
    async run({ client, organizationId, workspaceId, evidence }) {
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const providerStatus = await client.get(
        apiPath("/model-hub/develops/provider-status/"),
      );
      const providers = Array.isArray(providerStatus?.providers)
        ? providerStatus.providers
        : [];
      assert(providers.length > 0, "Provider status did not return providers.");
      assertNoRawProviderSecretLeak(providerStatus, "provider status");

      const configuredProviders = providers.filter(
        (provider) => provider?.has_key,
      );
      assert(
        configuredProviders.length > 0,
        "Provider status did not report any configured providers.",
      );
      for (const provider of providers) {
        assertProviderStatusRow(provider);
      }
      assert(
        providers.every(
          (provider) =>
            provider.hasKey === undefined &&
            provider.maskedKey === undefined &&
            provider.logoUrl === undefined,
        ),
        "Provider status unexpectedly returned stale camelCase aliases.",
      );

      const configuredTextProvider = configuredProviders.find(
        (provider) =>
          provider.type === "text" &&
          typeof provider.masked_key === "string" &&
          provider.masked_key.includes("*"),
      );
      assert(
        configuredTextProvider,
        "No configured text provider exposed a masked string key.",
      );

      const configuredJsonProvider = configuredProviders.find(
        (provider) =>
          provider.type === "json" &&
          provider.masked_key &&
          typeof provider.masked_key === "object",
      );
      if (configuredJsonProvider) {
        assert(
          Boolean(firstMaskedScalar(configuredJsonProvider.masked_key)),
          "Configured JSON provider did not expose any masked scalar values.",
        );
      }

      const customModelsRaw = await client.get(
        apiPath("/model-hub/custom-models/"),
        {
          query: { page_number: 0, page_size: 20 },
          unwrap: false,
        },
      );
      assertNoRawProviderSecretLeak(customModelsRaw, "custom model list");
      const customModelRows = asArray(customModelsRaw);
      for (const model of customModelRows) {
        assert(
          isUuid(model?.id),
          "Custom model list row did not include a valid id.",
        );
        assert(
          String(model?.user_model_id || model?.userModelId || "").trim(),
          "Custom model list row did not include a user model id.",
        );
      }

      const apiKeysRaw = await client.get(apiPath("/model-hub/api-keys/"), {
        query: { page: 1, page_size: 20 },
        unwrap: false,
      });
      assertNoRawProviderSecretLeak(apiKeysRaw, "api key list");

      const audit = await loadProviderKeyDbAudit({
        organizationId,
        workspaceId,
      });
      assert(
        audit.active_key_count >= configuredProviders.length,
        "Provider status reported more configured providers than scoped active key rows.",
      );
      for (const provider of configuredProviders) {
        assert(
          Number(audit.provider_counts?.[provider.provider] || 0) > 0,
          `Provider ${provider.provider} was marked configured but missing from DB audit.`,
        );
      }

      evidence.push({
        workspace_id: workspaceId,
        provider_count: providers.length,
        configured_provider_count: configuredProviders.length,
        configured_text_provider: configuredTextProvider.provider,
        configured_text_masked_key: configuredTextProvider.masked_key,
        configured_json_provider: configuredJsonProvider?.provider || null,
        custom_model_count: getPayloadTotal(customModelsRaw),
        api_key_list_rows: getPayloadTotal(apiKeysRaw),
        api_key_list_has_key_field:
          JSON.stringify(apiKeysRaw).includes('"key"'),
        db_active_key_count: audit.active_key_count,
        db_distinct_provider_count: audit.distinct_provider_count,
        db_duplicate_provider_row_count: audit.duplicate_provider_row_count,
      });
    },
  },
  {
    id: "CORE-API-011",
    title:
      "Billing pricing, invoices, budgets, payment methods, and EE license read contracts",
    tags: [
      "core",
      "settings",
      "billing",
      "pricing",
      "licenses",
      "data-integrity",
      "safe",
    ],
    async run({ client, user, organizationId, workspaceId, evidence }) {
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );
      const userInfo = await client.get(apiPath("/accounts/user-info/"));
      const email = currentUserEmail(userInfo) || currentUserEmail(user);
      assert(
        email.includes("@"),
        "Authenticated user-info did not include an email.",
      );

      const plans = await client.get(apiPath("/usage/v2/plans-and-addons/"));
      assert(
        String(plans?.current_plan || "").trim(),
        "Plans response omitted current_plan.",
      );
      assert(
        String(plans?.billing_interval || "").trim(),
        "Plans response omitted billing_interval.",
      );
      assert(
        Array.isArray(plans?.tiers) && plans.tiers.length >= 2,
        "Plans response did not include pricing tiers.",
      );
      assert(
        Array.isArray(plans?.addons) && plans.addons.length >= 3,
        "Plans response did not include add-ons.",
      );
      assert(
        plans?.pricing && typeof plans.pricing === "object",
        "Plans response omitted pricing dimensions.",
      );
      assert(
        Object.prototype.hasOwnProperty.call(plans, "isCustomPricing"),
        "Plans response did not expose canonical isCustomPricing.",
      );
      assert(
        Object.prototype.hasOwnProperty.call(plans, "customDetails"),
        "Plans response did not expose canonical customDetails.",
      );
      assert(
        plans.is_custom_pricing === undefined,
        "Plans response returned stale is_custom_pricing alias.",
      );
      assert(
        plans.custom_details === undefined,
        "Plans response returned stale custom_details alias.",
      );
      for (const tier of [...plans.tiers, ...plans.addons]) {
        assertPlanOptionRow(tier);
      }
      for (const [dimension, pricing] of Object.entries(plans.pricing)) {
        assertPricingDimension(dimension, pricing);
      }
      assertNoBillingCheckoutPayload(plans, "plans and add-ons");

      const billing = await client.get(apiPath("/usage/v2/billing-overview/"));
      assert(
        billing?.org_id === organizationId,
        "Billing overview organization did not match context.",
      );
      assert(
        billing?.period && /^\d{4}-\d{2}$/.test(billing.period),
        "Billing overview omitted YYYY-MM period.",
      );
      assert(
        billing?.plan === plans.current_plan,
        "Billing overview plan did not match plans current_plan.",
      );
      assertBillingMoneyFields(billing, "billing overview");
      assert(
        Array.isArray(billing?.line_items),
        "Billing overview did not include line_items array.",
      );
      for (const item of billing.line_items) {
        assertBillingLineItem(item, "billing overview line item");
      }
      assertNoBillingCheckoutPayload(billing, "billing overview");

      const invoicesPayload = await client.get(apiPath("/usage/v2/invoices/"));
      const invoices = Array.isArray(invoicesPayload?.invoices)
        ? invoicesPayload.invoices
        : [];
      for (const invoice of invoices) {
        assertInvoiceRow(invoice);
      }

      let invoiceDetailLineCount = null;
      if (invoices[0]?.id) {
        const invoiceDetail = await client.get(
          apiPath("/usage/v2/invoices/{invoice_id}/", {
            invoice_id: invoices[0].id,
          }),
        );
        assert(
          invoiceDetail?.invoice?.id === invoices[0].id,
          "Invoice detail returned the wrong invoice.",
        );
        assert(
          Array.isArray(invoiceDetail?.line_items),
          "Invoice detail did not include line_items array.",
        );
        for (const item of invoiceDetail.line_items) {
          assertBillingLineItem(item, "invoice detail line item");
        }
        invoiceDetailLineCount = invoiceDetail.line_items.length;
      }

      const missingInvoiceError = await expectApiError(
        () =>
          client.get(
            apiPath("/usage/v2/invoices/{invoice_id}/", {
              invoice_id: "00000000-0000-0000-0000-000000000000",
            }),
          ),
        [404],
        "Missing invoice detail unexpectedly succeeded.",
      );

      const notifications = await client.get(
        apiPath("/usage/v2/notifications/"),
      );
      const banners = Array.isArray(notifications?.banners)
        ? notifications.banners
        : [];
      for (const banner of banners) {
        assert(
          String(banner?.id || "").trim(),
          "Notification banner omitted id.",
        );
        assert(
          String(banner?.message || "").trim(),
          "Notification banner omitted message.",
        );
      }

      const budgetsPayload = await client.get(apiPath("/usage/v2/budgets/"));
      const budgets = Array.isArray(budgetsPayload?.budgets)
        ? budgetsPayload.budgets
        : [];
      for (const budget of budgets) {
        assert(isUuid(budget?.id), "Budget row omitted a valid id.");
        assert(String(budget?.name || "").trim(), "Budget row omitted name.");
        assert(
          ["notify", "warn", "pause"].includes(budget?.action),
          "Budget row returned unsupported action.",
        );
      }

      const paymentMethods = asArray(
        await client.get(apiPath("/usage/v2/payment-methods/")),
      );
      for (const method of paymentMethods) {
        assertPaymentMethodRow(method);
      }
      assertNoFullPaymentCardLeak(paymentMethods, "payment methods");

      const licensesPayload = await client.get(apiPath("/usage/ee/licenses/"));
      const licenses = Array.isArray(licensesPayload?.licenses)
        ? licensesPayload.licenses
        : [];
      for (const license of licenses) {
        assertEELicenseRow(license);
      }
      assertNoEELicenseSecretLeak(licensesPayload, "EE license list");

      const legacySubscriptionStatus = await client.get(
        apiPath("/usage/subscription-status/"),
        { unwrap: false },
      );
      assert(
        legacySubscriptionStatus?.status === true,
        "Legacy subscription-status endpoint did not return status true.",
      );
      assert(
        legacySubscriptionStatus?.result?.subscription_status,
        "Legacy subscription-status endpoint omitted subscription_status.",
      );

      const legacyBillingDetails = await client.get(
        apiPath("/usage/get-billing-details/"),
        { unwrap: false },
      );
      assert(
        legacyBillingDetails?.status === "success",
        "Legacy billing details endpoint did not return success.",
      );
      assert(
        legacyBillingDetails?.billing_info &&
          typeof legacyBillingDetails.billing_info === "object",
        "Legacy billing details endpoint omitted billing_info.",
      );

      const audit = await loadBillingLicenseDbAudit({ organizationId, email });
      if (audit.subscription_count > 0) {
        assert(
          audit.subscription_plan === plans.current_plan,
          "DB audit subscription plan did not match plans response.",
        );
      }
      assert(
        audit.invoice_count >= invoices.length,
        "DB audit invoice count was smaller than invoice API page.",
      );
      assert(
        audit.budget_count === budgets.length,
        "DB audit usage budget count did not match budgets API.",
      );
      assert(
        audit.email_license_count === licenses.length,
        "DB audit EE license count did not match license list API.",
      );

      evidence.push({
        workspace_id: workspaceId,
        current_plan: plans.current_plan,
        billing_interval: plans.billing_interval,
        tier_count: plans.tiers.length,
        addon_count: plans.addons.length,
        pricing_dimension_count: Object.keys(plans.pricing).length,
        is_custom_pricing: plans.isCustomPricing,
        billing_period: billing.period,
        billing_total: billing.total,
        billing_line_item_count: billing.line_items.length,
        invoice_count: invoices.length,
        invoice_detail_line_count: invoiceDetailLineCount,
        missing_invoice_status: missingInvoiceError.status,
        notification_banner_count: banners.length,
        budget_count: budgets.length,
        payment_method_count: paymentMethods.length,
        ee_license_count: licenses.length,
        legacy_subscription_status:
          legacySubscriptionStatus.result.subscription_status,
        legacy_billing_details_fields: Object.keys(
          legacyBillingDetails.billing_info,
        ).length,
        db_subscription_count: audit.subscription_count,
        db_invoice_count: audit.invoice_count,
        db_budget_count: audit.budget_count,
        db_email_license_count: audit.email_license_count,
      });
    },
  },
  {
    id: "CORE-API-012",
    title:
      "MCP server config, tools, sessions, analytics, and tool-group persistence",
    tags: ["core", "settings", "mcp", "data-integrity", "mutating"],
    async run({
      client,
      user,
      organizationId,
      workspaceId,
      cleanup,
      evidence,
    }) {
      const userInfo = await client.get(apiPath("/accounts/user-info/"));
      const userId = currentUserId(userInfo) || currentUserId(user);
      assert(
        isUuid(userId),
        "Authenticated user-info did not include a valid user id.",
      );
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const config = await client.get(apiPath("/mcp/config/"));
      assertMCPConnectionConfig(config);
      assertNoMCPConnectionSecretLeak(config, "MCP config");

      const toolGroups = await client.get(apiPath("/mcp/config/tool-groups/"));
      assertMCPToolGroupConfig(toolGroups);
      assertGroupSetsEqual(
        config.tool_config?.enabled_groups,
        toolGroups.enabled_groups,
        "MCP config enabled groups did not match tool-groups endpoint.",
      );

      const health = await client.get(apiPath("/mcp/health/"));
      assert(
        health?.healthy === true,
        "MCP health endpoint did not report healthy true.",
      );
      assert(
        Number.isInteger(health?.tool_count),
        "MCP health omitted integer tool_count.",
      );
      assert(
        String(health?.version || "").trim(),
        "MCP health omitted version.",
      );

      const toolsPayload = await client.get(apiPath("/mcp/internal/tools/"));
      const tools = Array.isArray(toolsPayload?.tools)
        ? toolsPayload.tools
        : [];
      assert(tools.length > 0, "MCP tool list returned no tools.");
      assert(
        toolsPayload.total === tools.length,
        "MCP tool list total did not match tool rows.",
      );
      assert(
        health.tool_count >= tools.length,
        "MCP health tool_count was smaller than enabled tool list.",
      );
      for (const tool of tools) {
        assertMCPToolRow(tool);
      }
      assertMCPToolsRespectEnabledGroups(tools, toolGroups.enabled_groups);

      const sessions = asArray(await client.get(apiPath("/mcp/sessions/")));
      for (const session of sessions) {
        assertMCPSessionRow(session);
      }
      const activeSessions = asArray(
        await client.get(apiPath("/mcp/sessions/"), {
          query: { status: "active" },
        }),
      );
      for (const session of activeSessions) {
        assert(
          session.status === "active",
          "Active MCP session filter returned a non-active row.",
        );
      }
      const missingSessionError = await expectApiError(
        () =>
          client.delete(
            apiPath("/mcp/sessions/{session_id}/", {
              session_id: "00000000-0000-0000-0000-000000000000",
            }),
          ),
        [404],
        "Deleting a missing MCP session unexpectedly succeeded.",
      );

      const analyticsSummary = await client.get(
        apiPath("/mcp/analytics/summary/"),
        {
          query: { days: 30 },
        },
      );
      assertMCPAnalyticsSummary(analyticsSummary);
      const analyticsTools = asArray(
        await client.get(apiPath("/mcp/analytics/tools/"), {
          query: { days: 30 },
        }),
      );
      for (const row of analyticsTools) {
        assertMCPAnalyticsToolRow(row);
      }
      const analyticsTimeline = asArray(
        await client.get(apiPath("/mcp/analytics/timeline/"), {
          query: { days: 30 },
        }),
      );
      for (const row of analyticsTimeline) {
        assertMCPAnalyticsTimelineRow(row);
      }

      const audit = await loadMCPSettingsDbAudit({
        userId,
        organizationId,
        workspaceId,
      });
      assert(
        audit.connection_count === 1,
        "DB audit did not find exactly one MCP connection for user/workspace.",
      );
      assert(
        audit.connection_id === config.id,
        "DB audit MCP connection did not match config response.",
      );
      assert(
        audit.connection_organization_id === organizationId,
        "DB audit MCP connection organization mismatch.",
      );
      assert(
        audit.connection_workspace_id === workspaceId,
        "DB audit MCP connection workspace mismatch.",
      );
      assert(
        audit.tool_config_count === 1,
        "DB audit did not find exactly one MCP tool config.",
      );
      assertGroupSetsEqual(
        audit.enabled_groups,
        toolGroups.enabled_groups,
        "DB audit enabled groups did not match API response.",
      );
      assert(
        audit.session_count >= sessions.length,
        "DB audit MCP session count was smaller than sessions API page.",
      );
      assert(
        audit.active_session_count === analyticsSummary.active_sessions,
        "DB audit active MCP session count did not match analytics summary.",
      );
      assert(
        audit.encrypted_token_count === 0 &&
          audit.encrypted_refresh_token_count === 0,
        "Dashboard MCP config unexpectedly exposed token-backed connection rows for this local user.",
      );

      const staleAliasError = await expectApiError(
        () =>
          client.put(apiPath("/mcp/config/tool-groups/"), {
            enabled_tool_groups: toolGroups.enabled_groups,
          }),
        [400],
        "Stale MCP enabled_tool_groups alias unexpectedly succeeded.",
      );

      const mutationEvidence = {
        tool_group_update_exercised: false,
        stale_alias_status: staleAliasError.status,
      };
      if (envFlag("API_JOURNEY_MUTATIONS")) {
        const originalEnabledGroups = [...toolGroups.enabled_groups];
        const originalDisabledTools = [...(toolGroups.disabled_tools || [])];
        const availableSlugs = toolGroups.available_groups.map(
          (group) => group.slug,
        );
        const groupToAdd = availableSlugs.find(
          (slug) => !originalEnabledGroups.includes(slug),
        );
        const nextEnabledGroups = groupToAdd
          ? [...originalEnabledGroups, groupToAdd]
          : originalEnabledGroups.slice(0, -1);
        assert(
          nextEnabledGroups.length !== originalEnabledGroups.length,
          "MCP tool-group mutation could not construct a changed group set.",
        );
        cleanup.defer("restore MCP tool groups", () =>
          client.put(apiPath("/mcp/config/tool-groups/"), {
            enabled_groups: originalEnabledGroups,
            disabled_tools: originalDisabledTools,
          }),
        );

        const updatedGroups = await client.put(
          apiPath("/mcp/config/tool-groups/"),
          {
            enabled_groups: nextEnabledGroups,
            disabled_tools: originalDisabledTools,
          },
        );
        assertGroupSetsEqual(
          updatedGroups.enabled_groups,
          nextEnabledGroups,
          "MCP tool-group update response did not persist selected groups.",
        );

        const updateAudit = await loadMCPSettingsDbAudit({
          userId,
          organizationId,
          workspaceId,
        });
        assertGroupSetsEqual(
          updateAudit.enabled_groups,
          nextEnabledGroups,
          "DB audit did not persist MCP tool-group update.",
        );
        const toolsAfterUpdatePayload = await client.get(
          apiPath("/mcp/internal/tools/"),
        );
        const toolsAfterUpdate = Array.isArray(toolsAfterUpdatePayload?.tools)
          ? toolsAfterUpdatePayload.tools
          : [];
        assertMCPToolsRespectEnabledGroups(toolsAfterUpdate, nextEnabledGroups);

        mutationEvidence.tool_group_update_exercised = true;
        mutationEvidence.original_enabled_group_count =
          originalEnabledGroups.length;
        mutationEvidence.updated_enabled_group_count = nextEnabledGroups.length;
        mutationEvidence.changed_group =
          groupToAdd || originalEnabledGroups.at(-1);
      }

      evidence.push({
        workspace_id: workspaceId,
        connection_id: config.id,
        mcp_url: config.mcp_url,
        enabled_group_count: toolGroups.enabled_groups.length,
        available_group_count: toolGroups.available_groups.length,
        disabled_tool_count: toolGroups.disabled_tools.length,
        tool_count: tools.length,
        health_tool_count: health.tool_count,
        session_count: sessions.length,
        active_session_count: activeSessions.length,
        missing_session_delete_status: missingSessionError.status,
        analytics_total_calls: analyticsSummary.total_calls,
        analytics_active_sessions: analyticsSummary.active_sessions,
        analytics_tool_count: analyticsTools.length,
        analytics_timeline_points: analyticsTimeline.length,
        db_session_count: audit.session_count,
        db_usage_count: audit.usage_count,
        ...mutationEvidence,
      });
    },
  },
  {
    id: "CORE-API-020",
    title: "MCP disposable session revoke and status-filter readback",
    tags: ["core", "settings", "mcp", "sessions", "data-integrity", "mutating"],
    async run({
      client,
      user,
      organizationId,
      workspaceId,
      cleanup,
      runId,
      evidence,
    }) {
      requireMutations();
      const userInfo = await client.get(apiPath("/accounts/user-info/"));
      const userId = currentUserId(userInfo) || currentUserId(user);
      assert(
        isUuid(userId),
        "Authenticated user-info did not include a valid user id.",
      );
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const config = await client.get(apiPath("/mcp/config/"));
      assertMCPConnectionConfig(config);

      const sessionId = randomUUID();
      const clientName = `api_journey_mcp_${String(runId)
        .replaceAll("-", "_")
        .slice(0, 40)}`;
      const createAudit = await createDisposableMCPSessionDb({
        sessionId,
        connectionId: config.id,
        userId,
        organizationId,
        workspaceId,
        clientName,
      });
      assert(
        createAudit.inserted_session_count === 1,
        "DB fixture did not create exactly one disposable MCP session.",
      );
      assert(
        createAudit.status === "active" &&
          createAudit.transport === "stdio" &&
          createAudit.client_name === clientName,
        "Disposable MCP session DB fixture did not persist the expected initial state.",
      );
      cleanup.defer("hard-delete disposable MCP session", async () => {
        const deleted = await deleteDisposableMCPSessionDb({ sessionId });
        assert(
          deleted.remaining_session_count === 0 &&
            deleted.remaining_usage_count === 0,
          "Disposable MCP session or usage rows remained after cleanup.",
        );
      });

      const activeSessions = asArray(
        await client.get(apiPath("/mcp/sessions/"), {
          query: { status: "active" },
        }),
      );
      const activeRow = activeSessions.find(
        (session) => session.id === sessionId,
      );
      assert(
        activeRow,
        "Active MCP sessions list did not include the disposable session.",
      );
      assertMCPSessionRow(activeRow);
      assert(
        activeRow.client_name === clientName,
        "Disposable active MCP session list row did not preserve client_name.",
      );

      const revokeResult = await client.delete(
        apiPath("/mcp/sessions/{session_id}/", {
          session_id: sessionId,
        }),
      );
      assert(
        revokeResult?.message === "Session revoked",
        "MCP session revoke response did not return the expected message.",
      );

      const activeAfterRevoke = asArray(
        await client.get(apiPath("/mcp/sessions/"), {
          query: { status: "active" },
        }),
      );
      assert(
        !activeAfterRevoke.some((session) => session.id === sessionId),
        "Revoked MCP session still appeared in the active session filter.",
      );

      const revokedSessions = asArray(
        await client.get(apiPath("/mcp/sessions/"), {
          query: { status: "revoked" },
        }),
      );
      const revokedRow = revokedSessions.find(
        (session) => session.id === sessionId,
      );
      assert(
        revokedRow,
        "Revoked MCP sessions list did not include the disposable session.",
      );
      assertMCPSessionRow(revokedRow);
      assert(
        revokedRow.status === "revoked" &&
          String(revokedRow.ended_at || "").trim(),
        "Revoked MCP session row did not include revoked status and ended_at.",
      );

      const audit = await loadDisposableMCPSessionDbAudit({
        sessionId,
        connectionId: config.id,
        userId,
        organizationId,
        workspaceId,
      });
      assert(
        audit.session_count === 1 &&
          audit.status === "revoked" &&
          audit.ended_at_set === true,
        "DB audit did not find the disposable MCP session revoked with ended_at.",
      );
      assert(
        audit.connection_id === config.id &&
          audit.user_id === userId &&
          audit.organization_id === organizationId &&
          audit.workspace_id === workspaceId,
        "DB audit found the disposable MCP session outside the active context.",
      );

      evidence.push({
        session_id: sessionId,
        connection_id: config.id,
        client_name: clientName,
        active_filter_count_before_revoke: activeSessions.length,
        revoked_filter_count_after_revoke: revokedSessions.length,
        revoke_message: revokeResult.message,
        db_status_after_revoke: audit.status,
        db_ended_at_set: audit.ended_at_set,
      });
    },
  },
  {
    id: "CORE-API-015",
    title:
      "Falcon MCP connector create, secret masking, tool selection, delete, and DB cleanup",
    tags: [
      "core",
      "settings",
      "falcon",
      "mcp-connectors",
      "mutating",
      "data-integrity",
      "credential-safety",
    ],
    async run({
      client,
      user,
      organizationId,
      workspaceId,
      cleanup,
      runId,
      evidence,
    }) {
      requireMutations();
      const userId = currentUserId(user);
      assert(
        isUuid(userId),
        "Falcon connector journey requires current user id.",
      );
      assert(
        isUuid(organizationId),
        "Falcon connector journey requires organization context.",
      );
      assert(
        isUuid(workspaceId),
        "Falcon connector journey requires workspace context.",
      );

      const suffix = runId.replace(/[^a-z0-9]/gi, "_");
      const namePrefix = `api_journey_falcon_connector_${suffix}`;
      const name = `${namePrefix}_primary`;
      const updatedName = `${namePrefix}_updated`;
      const rawSecret = `falcon-secret-${suffix}`;
      const rotatedSecret = `falcon-rotated-secret-${suffix}`;
      const toolName = `${namePrefix}_tool`;
      let hardCleaned = false;
      let createMode = "public_api";
      let createEntitlementStatus = null;
      let duplicateCreate = null;
      let invalidCreate = null;
      let otherWorkspace = null;

      await hardDeleteFalconConnectorFixturesDb({
        namePrefix,
        organizationId,
      });
      cleanup.defer("hard-delete Falcon connector fixture", () =>
        hardCleaned
          ? null
          : hardDeleteFalconConnectorFixturesDb({
              namePrefix,
              organizationId,
            }),
      );

      let created;
      try {
        created = await client.post(apiPath("/falcon-ai/mcp-connectors/"), {
          name,
          server_url: "https://example.com/futureagi-api-journey-mcp",
          transport: "streamable_http",
          auth_type: "api_key",
          auth_header_name: "X-FutureAGI-Test",
          auth_header_value: rawSecret,
        });
      } catch (error) {
        if (error?.status !== 402) throw error;
        createMode = "db_seeded_after_entitlement";
        createEntitlementStatus = error.status;
        created = await seedFalconConnectorDb({
          name,
          serverUrl: "https://example.com/futureagi-api-journey-mcp",
          transport: "streamable_http",
          authType: "api_key",
          authHeaderName: "X-FutureAGI-Test",
          rawSecret,
          organizationId,
          workspaceId,
          userId,
        });
      }
      assert(isUuid(created?.id), "Falcon connector create did not return id.");
      assert(
        created.name === name && created.auth_type === "api_key",
        "Falcon connector create response did not echo canonical fields.",
      );
      assertNoPayloadString(
        created,
        rawSecret,
        "Falcon connector create response",
      );

      if (createMode === "public_api") {
        duplicateCreate = await expectApiError(
          () =>
            client.post(apiPath("/falcon-ai/mcp-connectors/"), {
              name,
              server_url: "https://example.com/futureagi-api-journey-duplicate",
              transport: "streamable_http",
              auth_type: "none",
            }),
          [409],
          "Falcon connector create accepted a duplicate workspace name.",
        );
        assert(
          errorText(duplicateCreate).toLowerCase().includes("already exists") ||
            errorText(duplicateCreate).toLowerCase().includes("connector"),
          "Falcon connector duplicate guard did not explain the name conflict.",
        );

        invalidCreate = await expectApiError(
          () =>
            client.post(apiPath("/falcon-ai/mcp-connectors/"), {
              name: `${namePrefix}_invalid`,
              server_url: "not-a-url",
              transport: "websocket",
              auth_type: "none",
            }),
          [400],
          "Falcon connector create accepted invalid URL/transport values.",
        );
        assert(
          errorText(invalidCreate).toLowerCase().includes("server_url") ||
            errorText(invalidCreate).toLowerCase().includes("transport"),
          "Falcon connector invalid create did not explain URL/transport validation.",
        );
      } else {
        duplicateCreate = await expectApiError(
          () =>
            client.post(apiPath("/falcon-ai/mcp-connectors/"), {
              name,
              server_url: "https://example.com/futureagi-api-journey-duplicate",
              transport: "streamable_http",
              auth_type: "none",
            }),
          [402],
          "Falcon connector gated create unexpectedly bypassed entitlement checks.",
        );
        invalidCreate = await expectApiError(
          () =>
            client.post(apiPath("/falcon-ai/mcp-connectors/"), {
              name: `${namePrefix}_invalid`,
              server_url: "not-a-url",
              transport: "websocket",
              auth_type: "none",
            }),
          [402],
          "Falcon connector gated invalid create unexpectedly bypassed entitlement checks.",
        );
      }

      let dbAudit = await loadFalconConnectorDbAudit({
        connectorId: created.id,
        organizationId,
        workspaceId,
        userId,
        rawSecret,
      });
      assertFalconConnectorDbAudit(dbAudit, {
        name,
        organizationId,
        workspaceId,
        userId,
        deleted: false,
        expectedEnabledToolCount: 0,
      });
      assert(
        dbAudit.auth_value_is_encrypted === true &&
          dbAudit.auth_value_contains_raw_secret === false,
        "Falcon connector DB audit found unencrypted auth_header_value.",
      );
      const initialSecretHash = dbAudit.auth_value_hash;

      const list = asArray(
        await client.get(apiPath("/falcon-ai/mcp-connectors/")),
      );
      assert(
        list.some((connector) => connector.id === created.id),
        "Falcon connector list did not include the created connector.",
      );
      const listRow = list.find((connector) => connector.id === created.id);
      assert(
        Number(listRow.tool_count) === 0,
        "Falcon connector list tool_count did not start at zero.",
      );
      assertNoPayloadString(list, rawSecret, "Falcon connector list response");

      const detail = await client.get(
        apiPath("/falcon-ai/mcp-connectors/{connector_id}/", {
          connector_id: created.id,
        }),
      );
      assert(
        detail?.id === created.id &&
          detail.name === name &&
          detail.auth_header_name === "X-FutureAGI-Test",
        "Falcon connector detail did not return the created row.",
      );
      assertNoPayloadString(detail, rawSecret, "Falcon connector detail");

      const updated = await client.patch(
        apiPath("/falcon-ai/mcp-connectors/{connector_id}/", {
          connector_id: created.id,
        }),
        {
          name: updatedName,
          server_url: "https://example.com/futureagi-api-journey-mcp-updated",
          is_active: false,
          auth_header_name: "X-FutureAGI-Test-Updated",
        },
      );
      assert(
        updated.name === updatedName &&
          updated.is_active === false &&
          updated.auth_header_name === "X-FutureAGI-Test-Updated",
        "Falcon connector PATCH did not persist display fields.",
      );
      assertNoPayloadString(
        updated,
        rawSecret,
        "Falcon connector PATCH response",
      );

      dbAudit = await loadFalconConnectorDbAudit({
        connectorId: created.id,
        organizationId,
        workspaceId,
        userId,
        rawSecret,
      });
      assertFalconConnectorDbAudit(dbAudit, {
        name: updatedName,
        organizationId,
        workspaceId,
        userId,
        deleted: false,
        expectedEnabledToolCount: 0,
      });
      assert(
        dbAudit.auth_value_is_encrypted === true &&
          dbAudit.auth_value_contains_raw_secret === false &&
          dbAudit.auth_value_hash === initialSecretHash,
        "Falcon connector PATCH without auth_header_value did not preserve the encrypted secret.",
      );

      const rotated = await client.patch(
        apiPath("/falcon-ai/mcp-connectors/{connector_id}/", {
          connector_id: created.id,
        }),
        {
          auth_header_name: "X-FutureAGI-Test-Rotated",
          auth_header_value: rotatedSecret,
        },
      );
      assert(
        rotated.auth_header_name === "X-FutureAGI-Test-Rotated",
        "Falcon connector PATCH did not persist rotated auth header name.",
      );
      assertNoPayloadString(
        rotated,
        rawSecret,
        "Falcon connector rotate response",
      );
      assertNoPayloadString(
        rotated,
        rotatedSecret,
        "Falcon connector rotate response",
      );

      dbAudit = await loadFalconConnectorDbAudit({
        connectorId: created.id,
        organizationId,
        workspaceId,
        userId,
        rawSecret: rotatedSecret,
      });
      assertFalconConnectorDbAudit(dbAudit, {
        name: updatedName,
        organizationId,
        workspaceId,
        userId,
        deleted: false,
        expectedEnabledToolCount: 0,
      });
      assert(
        dbAudit.auth_value_is_encrypted === true &&
          dbAudit.auth_value_contains_raw_secret === false &&
          dbAudit.auth_value_hash !== initialSecretHash,
        "Falcon connector PATCH with auth_header_value did not rotate to encrypted storage.",
      );

      const unknownToolError = await expectApiError(
        () =>
          client.patch(
            apiPath("/falcon-ai/mcp-connectors/{connector_id}/tools/", {
              connector_id: created.id,
            }),
            { enabled_tool_names: [`${toolName}_missing`] },
          ),
        [400],
        "Falcon connector tools update accepted a tool that was never discovered.",
      );
      assert(
        errorText(unknownToolError).toLowerCase().includes("unknown tools"),
        "Falcon connector unknown-tool guard did not explain the validation error.",
      );

      const seededToolsAudit = await seedFalconConnectorToolsDb({
        connectorId: created.id,
        organizationId,
        workspaceId,
        tools: [
          {
            name: toolName,
            description: "Temporary Falcon connector tool for API journey.",
            inputSchema: {
              type: "object",
              properties: {
                query: { type: "string" },
              },
            },
          },
          {
            name: `${toolName}_secondary`,
            description: "Temporary disabled Falcon connector tool.",
            inputSchema: { type: "object", properties: {} },
          },
        ],
      });
      assert(
        Number(seededToolsAudit.discovered_tool_count) === 2,
        "Falcon connector DB tool seed did not persist discovered tools.",
      );

      otherWorkspace = await seedOtherWorkspaceFalconConnectorDb({
        namePrefix,
        organizationId,
        userId,
      });
      assert(
        isUuid(otherWorkspace?.workspace_id) &&
          isUuid(otherWorkspace?.connector_id),
        "Falcon connector other-workspace DB seed did not return ids.",
      );

      const toolUpdate = await client.patch(
        apiPath("/falcon-ai/mcp-connectors/{connector_id}/tools/", {
          connector_id: created.id,
        }),
        { enabled_tool_names: [toolName] },
      );
      assert(
        asArray(toolUpdate.enabled_tool_names).includes(toolName),
        "Falcon connector tools update did not persist enabled_tool_names.",
      );
      assertNoPayloadString(
        toolUpdate,
        rawSecret,
        "Falcon connector tools update",
      );
      assertNoPayloadString(
        toolUpdate,
        rotatedSecret,
        "Falcon connector tools update",
      );

      const listAfterOtherWorkspace = asArray(
        await client.get(apiPath("/falcon-ai/mcp-connectors/")),
      );
      assert(
        !listAfterOtherWorkspace.some(
          (connector) => connector.id === otherWorkspace.connector_id,
        ),
        "Falcon connector list leaked a same-org other-workspace connector.",
      );

      const otherWorkspaceDetail = await expectApiError(
        () =>
          client.get(
            apiPath("/falcon-ai/mcp-connectors/{connector_id}/", {
              connector_id: otherWorkspace.connector_id,
            }),
          ),
        [404],
        "Falcon connector detail leaked a same-org other-workspace connector.",
      );
      const otherWorkspacePatch = await expectApiError(
        () =>
          client.patch(
            apiPath("/falcon-ai/mcp-connectors/{connector_id}/", {
              connector_id: otherWorkspace.connector_id,
            }),
            { name: `${namePrefix}_other_workspace_mutated` },
          ),
        [404],
        "Falcon connector PATCH mutated a same-org other-workspace connector.",
      );
      const otherWorkspaceTools = await expectApiError(
        () =>
          client.patch(
            apiPath("/falcon-ai/mcp-connectors/{connector_id}/tools/", {
              connector_id: otherWorkspace.connector_id,
            }),
            { enabled_tool_names: [otherWorkspace.tool_name] },
          ),
        [404],
        "Falcon connector tools PATCH mutated a same-org other-workspace connector.",
      );
      const otherWorkspaceDiscover = await expectApiError(
        () =>
          client.post(
            apiPath("/falcon-ai/mcp-connectors/{connector_id}/discover/", {
              connector_id: otherWorkspace.connector_id,
            }),
            {},
          ),
        [404],
        "Falcon connector discover reached a same-org other-workspace connector.",
      );
      const otherWorkspaceTest = await expectApiError(
        () =>
          client.post(
            apiPath("/falcon-ai/mcp-connectors/{connector_id}/test/", {
              connector_id: otherWorkspace.connector_id,
            }),
            {},
          ),
        [404],
        "Falcon connector test reached a same-org other-workspace connector.",
      );
      const otherWorkspaceAuthenticate = await expectApiError(
        () =>
          client.post(
            apiPath("/falcon-ai/mcp-connectors/{connector_id}/authenticate/", {
              connector_id: otherWorkspace.connector_id,
            }),
            {},
          ),
        [404],
        "Falcon connector authenticate reached a same-org other-workspace connector.",
      );

      const missingDetail = await expectApiError(
        () =>
          client.get(
            apiPath("/falcon-ai/mcp-connectors/{connector_id}/", {
              connector_id: "00000000-0000-0000-0000-000000000000",
            }),
          ),
        [404],
        "Falcon connector missing detail unexpectedly succeeded.",
      );

      await client.delete(
        apiPath("/falcon-ai/mcp-connectors/{connector_id}/", {
          connector_id: created.id,
        }),
      );

      const listAfterDelete = asArray(
        await client.get(apiPath("/falcon-ai/mcp-connectors/")),
      );
      assert(
        !listAfterDelete.some((connector) => connector.id === created.id),
        "Deleted Falcon connector remained visible through list.",
      );

      dbAudit = await loadFalconConnectorDbAudit({
        connectorId: created.id,
        organizationId,
        workspaceId,
        userId,
        rawSecret: rotatedSecret,
      });
      assertFalconConnectorDbAudit(dbAudit, {
        name: updatedName,
        organizationId,
        workspaceId,
        userId,
        deleted: true,
        expectedEnabledToolCount: 1,
      });
      assert(
        dbAudit.deleted_at_set === true,
        "Falcon connector public delete did not stamp deleted_at.",
      );

      const cleanupAudit = await hardDeleteFalconConnectorFixturesDb({
        namePrefix,
        organizationId,
      });
      hardCleaned = true;
      assert(
        Number(cleanupAudit.remaining_connector_count) === 0 &&
          Number(cleanupAudit.remaining_workspace_count) === 0,
        `Falcon connector hard cleanup left disposable rows behind: ${JSON.stringify(cleanupAudit)}`,
      );

      evidence.push({
        connector_id: created.id,
        connector_name: updatedName,
        create_mode: createMode,
        create_entitlement_status: createEntitlementStatus,
        duplicate_create_status: duplicateCreate.status,
        invalid_create_status: invalidCreate.status,
        unknown_tool_status: unknownToolError.status,
        missing_detail_status: missingDetail.status,
        other_workspace_id: otherWorkspace.workspace_id,
        other_workspace_connector_id: otherWorkspace.connector_id,
        other_workspace_detail_status: otherWorkspaceDetail.status,
        other_workspace_patch_status: otherWorkspacePatch.status,
        other_workspace_tools_status: otherWorkspaceTools.status,
        other_workspace_discover_status: otherWorkspaceDiscover.status,
        other_workspace_test_status: otherWorkspaceTest.status,
        other_workspace_authenticate_status: otherWorkspaceAuthenticate.status,
        discovered_tool_count: seededToolsAudit.discovered_tool_count,
        enabled_tool_names: toolUpdate.enabled_tool_names,
        encrypted_secret_stored: dbAudit.auth_value_is_encrypted,
        auth_hash_preserved_without_secret: true,
        rotated_secret_encrypted: true,
        raw_secret_in_api: false,
        deleted_at_set: dbAudit.deleted_at_set,
        cleanup_remaining_connector_count:
          cleanupAudit.remaining_connector_count,
        cleanup_remaining_workspace_count:
          cleanupAudit.remaining_workspace_count,
      });
    },
  },
  {
    id: "CORE-API-016",
    title:
      "Falcon conversation create, list/detail, feedback, workspace isolation, delete, and DB cleanup",
    tags: [
      "core",
      "falcon",
      "conversations",
      "mutating",
      "data-integrity",
      "workspace-scope",
    ],
    async run({
      client,
      user,
      organizationId,
      workspaceId,
      cleanup,
      runId,
      evidence,
    }) {
      requireMutations();
      const userId = currentUserId(user);
      assert(
        isUuid(userId),
        "Falcon conversation journey requires current user id.",
      );
      assert(
        isUuid(organizationId),
        "Falcon conversation journey requires organization context.",
      );
      assert(
        isUuid(workspaceId),
        "Falcon conversation journey requires workspace context.",
      );

      const suffix = runId.replace(/[^a-z0-9]/gi, "_");
      const titlePrefix = `api_journey_falcon_conversation_${suffix}`;
      const title = `${titlePrefix} primary`;
      const renamedTitle = `${titlePrefix} renamed`;
      let hardCleaned = false;

      await hardDeleteFalconConversationFixturesDb({
        titlePrefix,
        organizationId,
      });
      cleanup.defer("hard-delete Falcon conversation fixture", () =>
        hardCleaned
          ? null
          : hardDeleteFalconConversationFixturesDb({
              titlePrefix,
              organizationId,
            }),
      );

      const created = await client.post(apiPath("/falcon-ai/conversations/"), {
        title,
        context_page: "/dashboard/falcon-ai",
      });
      assert(
        isUuid(created?.id) &&
          created.title === title &&
          created.context_page === "/dashboard/falcon-ai",
        "Falcon conversation create response did not echo canonical fields.",
      );
      assert(
        Array.isArray(created.messages) && created.messages.length === 0,
        "New Falcon conversation should start without messages.",
      );

      const invalidCreate = await expectApiError(
        () =>
          client.post(apiPath("/falcon-ai/conversations/"), {
            title: `${titlePrefix} invalid`,
            displayName: "legacy alias",
          }),
        [400],
        "Falcon conversation create accepted an unknown field.",
      );
      const invalidCreateShape = await expectApiError(
        () =>
          client.post(apiPath("/falcon-ai/conversations/"), {
            title: { bad: "shape" },
          }),
        [400],
        "Falcon conversation create accepted a non-string title.",
      );

      const seededMessages = await seedFalconConversationMessagesDb({
        conversationId: created.id,
        messages: [
          {
            role: "user",
            content: `User asks for Falcon list coverage ${suffix}`,
            thoughts: [],
            toolCalls: [],
            completionCard: null,
            files: [],
            feedback: "",
            tokenCount: 7,
            inputTokens: 7,
            outputTokens: 0,
            modelUsed: "",
            latencyMs: 0,
          },
          {
            role: "assistant",
            content: `Assistant response for Falcon list coverage ${suffix}`,
            thoughts: [{ title: "Read conversation state", status: "done" }],
            toolCalls: [
              {
                name: "read_context",
                arguments: { route: "/dashboard/falcon-ai" },
              },
            ],
            completionCard: {
              title: "Falcon conversation coverage",
              status: "completed",
            },
            files: [],
            feedback: "",
            tokenCount: 11,
            inputTokens: 2,
            outputTokens: 9,
            modelUsed: "api-journey-model",
            latencyMs: 42,
          },
        ],
      });
      assert(
        Number(seededMessages.inserted_message_count) === 2,
        "Falcon conversation message seed did not insert both messages.",
      );

      const hidden = await seedHiddenFalconConversationDb({
        title: `${titlePrefix} hidden`,
        organizationId,
        workspaceId,
        userId,
      });
      const otherWorkspace = await seedOtherWorkspaceFalconConversationDb({
        titlePrefix,
        organizationId,
        userId,
      });

      const listEnvelope = await client.get(
        apiPath("/falcon-ai/conversations/"),
        {
          query: { search: titlePrefix, limit: 5, offset: 0 },
          unwrap: false,
        },
      );
      const list = asArray(listEnvelope);
      assert(
        listEnvelope.total === 1 && list.length === 1,
        `Falcon conversation list should include only the active workspace visible conversation: ${JSON.stringify(listEnvelope)}`,
      );
      const listRow = list[0];
      assert(
        listRow.id === created.id &&
          listRow.title === title &&
          listRow.context_page === "/dashboard/falcon-ai",
        "Falcon conversation list row did not match the created conversation.",
      );
      assert(
        Number(listRow.message_count) === 2 &&
          String(listRow.last_message_at || "").trim(),
        "Falcon conversation list did not expose message_count and last_message_at.",
      );
      assert(
        !list.some(
          (conversation) =>
            conversation.id === hidden.id ||
            conversation.id === otherWorkspace.conversation_id,
        ),
        "Falcon conversation list leaked hidden or other-workspace conversations.",
      );

      const detail = await client.get(
        apiPath("/falcon-ai/conversations/{conversation_id}/", {
          conversation_id: created.id,
        }),
      );
      assertFalconConversationDetail(detail, {
        conversationId: created.id,
        title,
        workspaceId,
        messageCount: 2,
      });
      const assistantMessage = detail.messages.find(
        (message) => message.role === "assistant",
      );
      assert(
        isUuid(assistantMessage?.id),
        "Falcon conversation detail did not include assistant message id.",
      );
      assert(
        assistantMessage.content.includes(suffix) &&
          assistantMessage.model_used === "api-journey-model" &&
          assistantMessage.latency_ms === 42,
        "Falcon assistant message detail did not preserve seeded response metadata.",
      );

      const streamStatus = await client.get(
        apiPath("/falcon-ai/conversations/{conversation_id}/stream-status/", {
          conversation_id: created.id,
        }),
      );
      assert(
        streamStatus.stream_status === "none",
        "Falcon stream-status for inactive seeded conversation was not none.",
      );

      const feedback = await client.post(
        apiPath("/falcon-ai/messages/{message_id}/feedback/", {
          message_id: assistantMessage.id,
        }),
        { feedback: "thumbs_up" },
      );
      assert(
        feedback.feedback === "thumbs_up",
        "Falcon message feedback did not persist thumbs_up.",
      );
      const invalidFeedback = await expectApiError(
        () =>
          client.post(
            apiPath("/falcon-ai/messages/{message_id}/feedback/", {
              message_id: assistantMessage.id,
            }),
            { feedback: "invalid_value" },
          ),
        [400],
        "Falcon message feedback accepted an invalid value.",
      );
      const unknownFeedbackField = await expectApiError(
        () =>
          client.post(
            apiPath("/falcon-ai/messages/{message_id}/feedback/", {
              message_id: assistantMessage.id,
            }),
            { feedback: "thumbs_down", legacy_extra: true },
          ),
        [400],
        "Falcon message feedback accepted an unknown field.",
      );
      const clearedFeedback = await client.post(
        apiPath("/falcon-ai/messages/{message_id}/feedback/", {
          message_id: assistantMessage.id,
        }),
        { feedback: "" },
      );
      assert(
        clearedFeedback.feedback === "",
        "Falcon message feedback did not clear.",
      );

      const otherWorkspaceDetail = await expectApiError(
        () =>
          client.get(
            apiPath("/falcon-ai/conversations/{conversation_id}/", {
              conversation_id: otherWorkspace.conversation_id,
            }),
          ),
        [404],
        "Falcon conversation detail leaked a same-org other-workspace conversation.",
      );
      const otherWorkspaceStream = await expectApiError(
        () =>
          client.get(
            apiPath(
              "/falcon-ai/conversations/{conversation_id}/stream-status/",
              {
                conversation_id: otherWorkspace.conversation_id,
              },
            ),
          ),
        [404],
        "Falcon stream-status leaked a same-org other-workspace conversation.",
      );
      const otherWorkspaceFeedback = await expectApiError(
        () =>
          client.post(
            apiPath("/falcon-ai/messages/{message_id}/feedback/", {
              message_id: otherWorkspace.message_id,
            }),
            { feedback: "thumbs_up" },
          ),
        [404],
        "Falcon message feedback leaked a same-org other-workspace message.",
      );

      const invalidPatch = await expectApiError(
        () =>
          client.patch(
            apiPath("/falcon-ai/conversations/{conversation_id}/", {
              conversation_id: created.id,
            }),
            { title: renamedTitle, displayName: "legacy alias" },
          ),
        [400],
        "Falcon conversation PATCH accepted an unknown field.",
      );
      const invalidPatchShape = await expectApiError(
        () =>
          client.patch(
            apiPath("/falcon-ai/conversations/{conversation_id}/", {
              conversation_id: created.id,
            }),
            { title: { bad: "shape" } },
          ),
        [400],
        "Falcon conversation PATCH accepted a non-string title.",
      );
      const renamed = await client.patch(
        apiPath("/falcon-ai/conversations/{conversation_id}/", {
          conversation_id: created.id,
        }),
        { title: renamedTitle },
      );
      assert(
        renamed.title === renamedTitle,
        "Falcon conversation PATCH did not persist renamed title.",
      );

      const oldSearchEnvelope = await client.get(
        apiPath("/falcon-ai/conversations/"),
        {
          query: { search: title, limit: 5, offset: 0 },
          unwrap: false,
        },
      );
      assert(
        oldSearchEnvelope.total === 0,
        "Falcon conversation list still found the old title after rename.",
      );
      const renamedSearchEnvelope = await client.get(
        apiPath("/falcon-ai/conversations/"),
        {
          query: { search: renamedTitle, limit: 5, offset: 0 },
          unwrap: false,
        },
      );
      assert(
        renamedSearchEnvelope.total === 1 &&
          asArray(renamedSearchEnvelope)[0]?.id === created.id,
        "Falcon conversation list did not find the renamed title.",
      );

      const missingDetail = await expectApiError(
        () =>
          client.get(
            apiPath("/falcon-ai/conversations/{conversation_id}/", {
              conversation_id: "00000000-0000-0000-0000-000000000000",
            }),
          ),
        [404],
        "Falcon missing conversation detail unexpectedly succeeded.",
      );
      const missingFeedback = await expectApiError(
        () =>
          client.post(
            apiPath("/falcon-ai/messages/{message_id}/feedback/", {
              message_id: "00000000-0000-0000-0000-000000000000",
            }),
            { feedback: "thumbs_up" },
          ),
        [404],
        "Falcon missing message feedback unexpectedly succeeded.",
      );

      await client.delete(
        apiPath("/falcon-ai/conversations/{conversation_id}/", {
          conversation_id: created.id,
        }),
      );
      const deletedDetail = await expectApiError(
        () =>
          client.get(
            apiPath("/falcon-ai/conversations/{conversation_id}/", {
              conversation_id: created.id,
            }),
          ),
        [404],
        "Falcon deleted conversation detail unexpectedly remained visible.",
      );
      const afterDeleteList = await client.get(
        apiPath("/falcon-ai/conversations/"),
        {
          query: { search: renamedTitle, limit: 5, offset: 0 },
          unwrap: false,
        },
      );
      assert(
        afterDeleteList.total === 0,
        "Falcon deleted conversation remained visible in list.",
      );

      const deleteAudit = await loadFalconConversationDbAudit({
        conversationIds: [
          created.id,
          hidden.id,
          otherWorkspace.conversation_id,
        ],
        messageIds: [...seededMessages.message_ids, otherWorkspace.message_id],
        organizationId,
        workspaceId,
        userId,
      });
      assert(
        deleteAudit.conversation_count === 3 &&
          deleteAudit.deleted_conversation_count === 1 &&
          deleteAudit.deleted_at_count === 1,
        `Falcon conversation DB audit did not find expected soft-delete state: ${JSON.stringify(deleteAudit)}`,
      );
      assert(
        deleteAudit.message_count === 3 &&
          deleteAudit.feedback_thumbs_up_count === 0,
        "Falcon conversation DB audit did not preserve messages with cleared feedback.",
      );

      const cleanupAudit = await hardDeleteFalconConversationFixturesDb({
        titlePrefix,
        organizationId,
      });
      hardCleaned = true;
      assert(
        cleanupAudit.remaining_conversation_count === 0 &&
          cleanupAudit.remaining_message_count === 0 &&
          cleanupAudit.remaining_workspace_count === 0,
        `Falcon conversation hard cleanup left disposable rows behind: ${JSON.stringify(cleanupAudit)}`,
      );

      evidence.push({
        conversation_id: created.id,
        renamed_title: renamedTitle,
        message_ids: seededMessages.message_ids,
        hidden_conversation_id: hidden.id,
        other_workspace_id: otherWorkspace.workspace_id,
        other_workspace_conversation_id: otherWorkspace.conversation_id,
        list_total_after_search: listEnvelope.total,
        message_count: listRow.message_count,
        stream_status: streamStatus.stream_status,
        invalid_create_status: invalidCreate.status,
        invalid_create_shape_status: invalidCreateShape.status,
        invalid_feedback_status: invalidFeedback.status,
        unknown_feedback_field_status: unknownFeedbackField.status,
        other_workspace_detail_status: otherWorkspaceDetail.status,
        other_workspace_stream_status: otherWorkspaceStream.status,
        other_workspace_feedback_status: otherWorkspaceFeedback.status,
        invalid_patch_status: invalidPatch.status,
        invalid_patch_shape_status: invalidPatchShape.status,
        missing_detail_status: missingDetail.status,
        missing_feedback_status: missingFeedback.status,
        deleted_detail_status: deletedDetail.status,
        db_deleted_at_count: deleteAudit.deleted_at_count,
        cleanup_remaining_conversation_count:
          cleanupAudit.remaining_conversation_count,
        cleanup_remaining_message_count: cleanupAudit.remaining_message_count,
      });
    },
  },
  {
    id: "CORE-API-017",
    title:
      "Falcon memory and skill create/list/detail/update/delete workspace isolation and cleanup",
    tags: [
      "core",
      "falcon",
      "memory",
      "skills",
      "mutating",
      "data-integrity",
      "workspace-scope",
    ],
    async run({
      client,
      user,
      organizationId,
      workspaceId,
      cleanup,
      runId,
      evidence,
    }) {
      requireMutations();
      const userId = currentUserId(user);
      assert(isUuid(userId), "Falcon memory/skill journey requires user id.");
      assert(
        isUuid(organizationId),
        "Falcon memory/skill journey requires organization context.",
      );
      assert(
        isUuid(workspaceId),
        "Falcon memory/skill journey requires workspace context.",
      );

      const suffix = runId.replace(/[^a-z0-9]/gi, "-").toLowerCase();
      const marker = `api-journey-falcon-ms-${suffix}`;
      const memoryKey = `${marker}-memory`;
      const skillName = marker;
      let hardCleaned = false;

      await hardDeleteFalconMemorySkillFixturesDb({ marker, organizationId });
      cleanup.defer("hard-delete Falcon memory/skill fixture", () =>
        hardCleaned
          ? null
          : hardDeleteFalconMemorySkillFixturesDb({ marker, organizationId }),
      );

      const invalidMemoryCreate = await expectApiError(
        () =>
          client.post(apiPath("/falcon-ai/memory/"), {
            key: `${marker}-invalid`,
            value: "invalid",
            displayName: "legacy alias",
          }),
        [400],
        "Falcon memory create accepted an unknown field.",
      );
      const missingMemoryValue = await expectApiError(
        () =>
          client.post(apiPath("/falcon-ai/memory/"), {
            key: `${marker}-missing-value`,
          }),
        [400],
        "Falcon memory create accepted a missing value.",
      );

      const memory = await client.post(apiPath("/falcon-ai/memory/"), {
        key: memoryKey,
        value: "Initial Falcon memory value",
      });
      assert(
        isUuid(memory?.id) &&
          memory.key === memoryKey &&
          memory.value === "Initial Falcon memory value" &&
          memory.source === "user",
        "Falcon memory create did not persist expected fields.",
      );

      const updatedMemory = await client.post(apiPath("/falcon-ai/memory/"), {
        key: memoryKey,
        value: "Updated Falcon memory value",
      });
      assert(
        updatedMemory.id === memory.id &&
          updatedMemory.value === "Updated Falcon memory value",
        "Falcon memory upsert did not update the existing key.",
      );

      const seeded = await seedFalconMemorySkillIsolationDb({
        marker,
        organizationId,
        userId,
      });

      const memories = asArray(await client.get(apiPath("/falcon-ai/memory/")));
      const matchingMemories = memories.filter((row) =>
        String(row.key || "").startsWith(marker),
      );
      assert(
        matchingMemories.length === 1 &&
          matchingMemories[0].id === memory.id &&
          matchingMemories[0].value === "Updated Falcon memory value",
        `Falcon memory list should expose only active-workspace fixture row: ${JSON.stringify(matchingMemories)}`,
      );
      assert(
        !memories.some((row) => row.id === seeded.other_memory_id),
        "Falcon memory list leaked another workspace memory.",
      );

      const otherMemoryDelete = await expectApiError(
        () =>
          client.delete(
            apiPath("/falcon-ai/memory/{memory_id}/", {
              memory_id: seeded.other_memory_id,
            }),
          ),
        [404],
        "Falcon memory delete removed another workspace memory.",
      );
      const missingMemoryDelete = await expectApiError(
        () =>
          client.delete(
            apiPath("/falcon-ai/memory/{memory_id}/", {
              memory_id: "00000000-0000-0000-0000-000000000000",
            }),
          ),
        [404],
        "Falcon missing memory delete unexpectedly succeeded.",
      );

      const invalidSkillCreate = await expectApiError(
        () =>
          client.post(apiPath("/falcon-ai/skills/"), {
            name: `${marker} invalid`,
            description: "Invalid skill",
            instructions: "Never created",
            trigger_phrases: ["invalid skill"],
            displayName: "legacy alias",
          }),
        [400],
        "Falcon skill create accepted an unknown field.",
      );
      const invalidSkillTrigger = await expectApiError(
        () =>
          client.post(apiPath("/falcon-ai/skills/"), {
            name: `${marker} invalid trigger`,
            description: "Invalid skill",
            instructions: "Never created",
            trigger_phrases: [],
          }),
        [400],
        "Falcon skill create accepted empty trigger phrases.",
      );

      const skill = await client.post(apiPath("/falcon-ai/skills/"), {
        name: skillName,
        description: "Falcon skill API journey",
        icon: "mdi:test-tube",
        instructions: "Use the test memory safely.",
        tool_names: ["list_memories"],
        trigger_phrases: [`${marker} trigger`],
      });
      assert(
        isUuid(skill?.id) &&
          skill.name === skillName &&
          skill.slug === marker &&
          skill.is_builtin === false,
        "Falcon skill create did not persist expected fields.",
      );

      const duplicateSkill = await expectApiError(
        () =>
          client.post(apiPath("/falcon-ai/skills/"), {
            name: skillName,
            description: "Duplicate skill",
            instructions: "Duplicate",
            trigger_phrases: [`${marker} duplicate`],
          }),
        [409],
        "Falcon skill create accepted a duplicate slug.",
      );

      const skills = asArray(await client.get(apiPath("/falcon-ai/skills/")));
      const matchingSkills = skills.filter((row) =>
        String(row.slug || "").startsWith(marker),
      );
      assert(
        matchingSkills.some((row) => row.id === skill.id) &&
          matchingSkills.some((row) => row.id === seeded.builtin_skill_id) &&
          !matchingSkills.some((row) => row.id === seeded.other_skill_id),
        `Falcon skill list should include active custom/global builtin and exclude other workspace: ${JSON.stringify(matchingSkills)}`,
      );

      const skillDetail = await client.get(
        apiPath("/falcon-ai/skills/{skill_id}/", { skill_id: skill.id }),
      );
      assert(
        skillDetail.id === skill.id &&
          skillDetail.instructions === "Use the test memory safely." &&
          asArray(skillDetail.tool_names).includes("list_memories"),
        "Falcon skill detail did not return created custom skill fields.",
      );
      const builtinDetail = await client.get(
        apiPath("/falcon-ai/skills/{skill_id}/", {
          skill_id: seeded.builtin_skill_id,
        }),
      );
      assert(
        builtinDetail.is_builtin === true &&
          builtinDetail.slug === seeded.builtin_slug,
        "Falcon skill detail did not expose global builtin skill.",
      );

      const builtinPatch = await expectApiError(
        () =>
          client.patch(
            apiPath("/falcon-ai/skills/{skill_id}/", {
              skill_id: seeded.builtin_skill_id,
            }),
            { description: "should not mutate" },
          ),
        [403],
        "Falcon skill PATCH edited a builtin skill.",
      );
      const builtinDelete = await expectApiError(
        () =>
          client.delete(
            apiPath("/falcon-ai/skills/{skill_id}/", {
              skill_id: seeded.builtin_skill_id,
            }),
          ),
        [403],
        "Falcon skill DELETE removed a builtin skill.",
      );
      const otherSkillDetail = await expectApiError(
        () =>
          client.get(
            apiPath("/falcon-ai/skills/{skill_id}/", {
              skill_id: seeded.other_skill_id,
            }),
          ),
        [404],
        "Falcon skill detail leaked another workspace skill.",
      );
      const otherSkillPatch = await expectApiError(
        () =>
          client.patch(
            apiPath("/falcon-ai/skills/{skill_id}/", {
              skill_id: seeded.other_skill_id,
            }),
            { description: "should not mutate" },
          ),
        [404],
        "Falcon skill PATCH mutated another workspace skill.",
      );
      const otherSkillDelete = await expectApiError(
        () =>
          client.delete(
            apiPath("/falcon-ai/skills/{skill_id}/", {
              skill_id: seeded.other_skill_id,
            }),
          ),
        [404],
        "Falcon skill DELETE removed another workspace skill.",
      );

      const invalidSkillPatch = await expectApiError(
        () =>
          client.patch(
            apiPath("/falcon-ai/skills/{skill_id}/", { skill_id: skill.id }),
            { description: "Updated", displayName: "legacy alias" },
          ),
        [400],
        "Falcon skill PATCH accepted an unknown field.",
      );
      const invalidSkillToolNames = await expectApiError(
        () =>
          client.patch(
            apiPath("/falcon-ai/skills/{skill_id}/", { skill_id: skill.id }),
            { tool_names: "not-a-list" },
          ),
        [400],
        "Falcon skill PATCH accepted invalid tool_names shape.",
      );
      const patchedSkill = await client.patch(
        apiPath("/falcon-ai/skills/{skill_id}/", { skill_id: skill.id }),
        {
          description: "Updated Falcon skill API journey",
          tool_names: ["list_memories", "save_memory"],
          trigger_phrases: [`${marker} updated`],
          is_active: false,
        },
      );
      assert(
        patchedSkill.description === "Updated Falcon skill API journey" &&
          asArray(patchedSkill.tool_names).includes("save_memory") &&
          patchedSkill.is_active === false,
        "Falcon skill PATCH did not persist updated fields.",
      );
      const inactiveSkills = asArray(
        await client.get(apiPath("/falcon-ai/skills/")),
      );
      assert(
        !inactiveSkills.some((row) => row.id === skill.id) &&
          inactiveSkills.some((row) => row.id === seeded.builtin_skill_id),
        "Falcon skill list did not hide inactive custom skill while keeping builtin.",
      );

      await client.delete(
        apiPath("/falcon-ai/memory/{memory_id}/", { memory_id: memory.id }),
      );
      await client.delete(
        apiPath("/falcon-ai/skills/{skill_id}/", { skill_id: skill.id }),
      );
      const deletedSkillDetail = await expectApiError(
        () =>
          client.get(
            apiPath("/falcon-ai/skills/{skill_id}/", { skill_id: skill.id }),
          ),
        [404],
        "Falcon deleted skill detail unexpectedly remained visible.",
      );

      const deleteAudit = await loadFalconMemorySkillDbAudit({
        memoryIds: [memory.id, seeded.other_memory_id],
        skillIds: [skill.id, seeded.other_skill_id, seeded.builtin_skill_id],
        organizationId,
        workspaceId,
      });
      assert(
        deleteAudit.memory_count === 2 &&
          deleteAudit.deleted_memory_count === 1 &&
          deleteAudit.memory_deleted_at_count === 1,
        `Falcon memory DB audit did not find expected delete state: ${JSON.stringify(deleteAudit)}`,
      );
      assert(
        deleteAudit.skill_count === 3 &&
          deleteAudit.deleted_skill_count === 1 &&
          deleteAudit.skill_deleted_at_count === 1 &&
          deleteAudit.builtin_skill_count === 1,
        `Falcon skill DB audit did not find expected delete state: ${JSON.stringify(deleteAudit)}`,
      );

      const cleanupAudit = await hardDeleteFalconMemorySkillFixturesDb({
        marker,
        organizationId,
      });
      hardCleaned = true;
      assert(
        cleanupAudit.remaining_memory_count === 0 &&
          cleanupAudit.remaining_skill_count === 0 &&
          cleanupAudit.remaining_workspace_count === 0,
        `Falcon memory/skill hard cleanup left rows behind: ${JSON.stringify(cleanupAudit)}`,
      );

      evidence.push({
        memory_id: memory.id,
        other_memory_id: seeded.other_memory_id,
        skill_id: skill.id,
        builtin_skill_id: seeded.builtin_skill_id,
        other_skill_id: seeded.other_skill_id,
        invalid_memory_create_status: invalidMemoryCreate.status,
        missing_memory_value_status: missingMemoryValue.status,
        other_memory_delete_status: otherMemoryDelete.status,
        missing_memory_delete_status: missingMemoryDelete.status,
        invalid_skill_create_status: invalidSkillCreate.status,
        invalid_skill_trigger_status: invalidSkillTrigger.status,
        duplicate_skill_status: duplicateSkill.status,
        builtin_patch_status: builtinPatch.status,
        builtin_delete_status: builtinDelete.status,
        other_skill_detail_status: otherSkillDetail.status,
        other_skill_patch_status: otherSkillPatch.status,
        other_skill_delete_status: otherSkillDelete.status,
        invalid_skill_patch_status: invalidSkillPatch.status,
        invalid_skill_tool_names_status: invalidSkillToolNames.status,
        deleted_skill_detail_status: deletedSkillDetail.status,
        memory_deleted_at_count: deleteAudit.memory_deleted_at_count,
        skill_deleted_at_count: deleteAudit.skill_deleted_at_count,
        cleanup_remaining_memory_count: cleanupAudit.remaining_memory_count,
        cleanup_remaining_skill_count: cleanupAudit.remaining_skill_count,
      });
    },
  },
  {
    id: "CORE-API-018",
    title:
      "Falcon file upload multipart validation, MinIO storage, text extraction, DB audit, and cleanup",
    tags: [
      "core",
      "falcon",
      "files",
      "upload",
      "mutating",
      "data-integrity",
      "workspace-scope",
    ],
    async run({
      apiBase,
      tokens,
      organizationId,
      workspaceId,
      user,
      cleanup,
      runId,
      evidence,
    }) {
      requireMutations();
      const userId = currentUserId(user);
      assert(isUuid(userId), "Falcon file upload journey requires user id.");
      assert(
        isUuid(organizationId),
        "Falcon file upload journey requires organization context.",
      );
      assert(
        isUuid(workspaceId),
        "Falcon file upload journey requires workspace context.",
      );
      assert(
        tokens?.access,
        "Falcon file upload journey requires an access token.",
      );

      const suffix = runId.replace(/[^a-z0-9]/gi, "-").toLowerCase();
      const marker = `api-journey-falcon-file-${suffix}`;
      let hardCleaned = false;

      await hardDeleteFalconFileFixturesDb({ marker, organizationId });
      cleanup.defer("hard-delete Falcon file fixture", () =>
        hardCleaned
          ? null
          : hardDeleteFalconFileFixturesDb({ marker, organizationId }),
      );

      const missingFile = await expectApiError(
        () =>
          multipartAppCoreRequest({
            apiBase,
            accessToken: tokens.access,
            organizationId,
            workspaceId,
            method: "POST",
            pathName: apiPath("/falcon-ai/files/upload/"),
          }),
        [400],
        "Falcon file upload accepted a missing file.",
      );
      const unsupportedFile = await expectApiError(
        () =>
          multipartAppCoreRequest({
            apiBase,
            accessToken: tokens.access,
            organizationId,
            workspaceId,
            method: "POST",
            pathName: apiPath("/falcon-ai/files/upload/"),
            files: [
              {
                fieldName: "file",
                fileName: `${marker}-payload.bin`,
                content: "plain bytes",
                contentType: "application/octet-stream",
              },
            ],
          }),
        [400],
        "Falcon file upload accepted an unsupported binary file.",
      );
      const dangerousFile = await expectApiError(
        () =>
          multipartAppCoreRequest({
            apiBase,
            accessToken: tokens.access,
            organizationId,
            workspaceId,
            method: "POST",
            pathName: apiPath("/falcon-ai/files/upload/"),
            files: [
              {
                fieldName: "file",
                fileName: `${marker}-script.txt`,
                content: "#!/bin/sh\necho unsafe\n",
                contentType: "text/plain",
              },
            ],
          }),
        [400],
        "Falcon file upload accepted a script signature.",
      );

      const textUpload = await multipartAppCoreRequest({
        apiBase,
        accessToken: tokens.access,
        organizationId,
        workspaceId,
        method: "POST",
        pathName: apiPath("/falcon-ai/files/upload/"),
        files: [
          {
            fieldName: "file",
            fileName: `${marker}?notes.txt`,
            content: `Falcon upload text ${suffix}`,
            contentType: "text/plain",
          },
        ],
      });
      assert(
        isUuid(textUpload?.id) &&
          textUpload.name === `${marker}_notes.txt` &&
          textUpload.content_type === "text/plain" &&
          textUpload.size === `Falcon upload text ${suffix}`.length,
        `Falcon text upload response mismatch: ${JSON.stringify(textUpload)}`,
      );

      const jsonUpload = await multipartAppCoreRequest({
        apiBase,
        accessToken: tokens.access,
        organizationId,
        workspaceId,
        method: "POST",
        pathName: apiPath("/falcon-ai/files/upload/"),
        files: [
          {
            fieldName: "file",
            fileName: `${marker}-data.json`,
            content: JSON.stringify({ marker, ok: true }),
            contentType: "application/octet-stream",
          },
        ],
      });
      assert(
        isUuid(jsonUpload?.id) &&
          jsonUpload.name === `${marker}-data.json` &&
          jsonUpload.content_type === "application/json",
        `Falcon JSON upload response mismatch: ${JSON.stringify(jsonUpload)}`,
      );

      const dbAudit = await loadFalconFileDbAudit({
        fileIds: [textUpload.id, jsonUpload.id],
        organizationId,
        workspaceId,
        userId,
        marker,
      });
      assert(
        dbAudit.file_count === 2 &&
          dbAudit.active_workspace_file_count === 2 &&
          dbAudit.user_file_count === 2 &&
          dbAudit.text_content_match_count === 2 &&
          dbAudit.storage_key_count === 2,
        `Falcon file upload DB audit mismatch: ${JSON.stringify(dbAudit)}`,
      );
      assert(
        dbAudit.sanitized_name_count === 1 &&
          dbAudit.json_content_type_count === 1,
        `Falcon file upload DB audit did not capture sanitized/json rows: ${JSON.stringify(dbAudit)}`,
      );

      await assertFalconMinioObjectsExist(dbAudit.storage_keys);

      const cleanupAudit = await hardDeleteFalconFileFixturesDb({
        marker,
        organizationId,
      });
      hardCleaned = true;
      assert(
        cleanupAudit.remaining_file_count === 0,
        `Falcon file upload hard cleanup left DB rows behind: ${JSON.stringify(cleanupAudit)}`,
      );
      await assertFalconMinioObjectsAbsent(dbAudit.storage_keys);

      evidence.push({
        text_file_id: textUpload.id,
        json_file_id: jsonUpload.id,
        sanitized_name: textUpload.name,
        missing_file_status: missingFile.status,
        unsupported_file_status: unsupportedFile.status,
        dangerous_file_status: dangerousFile.status,
        file_count: dbAudit.file_count,
        sanitized_name_count: dbAudit.sanitized_name_count,
        json_content_type_count: dbAudit.json_content_type_count,
        text_content_match_count: dbAudit.text_content_match_count,
        cleanup_remaining_file_count: cleanupAudit.remaining_file_count,
        storage_object_count: dbAudit.storage_keys.length,
      });
    },
  },
  {
    id: "CORE-API-019",
    title: "System organization key bootstrap readback and API-key auth",
    tags: ["core", "keys", "system-keys", "data-roundtrip", "security"],
    async run({ apiBase, client, organizationId, workspaceId, evidence }) {
      const unauthClient = createApiClient({ apiBase });
      const unauthError = await expectApiError(
        () => unauthClient.get(apiPath("/accounts/keys/")),
        [401, 403],
        "Unauthenticated system-key bootstrap request unexpectedly succeeded.",
      );

      const beforeAudit = await loadSystemOrgKeyDbAudit({
        organizationId,
      });
      if (
        Number(beforeAudit.enabled_system_key_count) === 0 &&
        !envFlag("API_JOURNEY_MUTATIONS")
      ) {
        skip(
          "No existing enabled system org key; set API_JOURNEY_MUTATIONS=1 to allow /accounts/keys/ to create the bootstrap key.",
        );
      }

      const payload = await client.get(apiPath("/accounts/keys/"), {
        unwrap: false,
      });
      assert(
        payload?.status === "success",
        "System-key bootstrap endpoint did not return status=success.",
      );
      const key = payload.data || {};
      assert(isUuid(key.id), "System-key bootstrap did not return a key id.");
      assertRawDeveloperKeyMaterial(key.api_key, "system api_key");
      assertRawDeveloperKeyMaterial(key.secret_key, "system secret_key");

      const dbAudit = await loadSystemOrgKeyDbAudit({
        organizationId,
        keyId: key.id,
        apiKey: key.api_key,
        secretKey: key.secret_key,
      });
      assert(
        Number(dbAudit.enabled_system_key_count) === 1 &&
          Number(dbAudit.matching_key_count) === 1 &&
          dbAudit.matching_key_id === key.id &&
          dbAudit.type === "system" &&
          dbAudit.enabled === true &&
          dbAudit.deleted === false &&
          dbAudit.workspace_id === null &&
          dbAudit.user_id === null &&
          dbAudit.api_key_matches === true &&
          dbAudit.secret_key_matches === true,
        `System-key DB audit mismatch: ${JSON.stringify(dbAudit)}`,
      );

      const systemKeyClient = createApiClient({
        apiBase,
        organizationId,
        workspaceId,
      });
      const systemUserInfo = await systemKeyClient.get(
        apiPath("/accounts/user-info/"),
        {
          headers: {
            "X-Api-Key": key.api_key,
            "X-Secret-Key": key.secret_key,
          },
        },
      );
      const systemUserOrgId =
        systemUserInfo?.organization_id ||
        systemUserInfo?.organization?.id ||
        systemUserInfo?.selected_organization?.id;
      assert(
        currentUserId(systemUserInfo) && systemUserOrgId === organizationId,
        "System org key did not authenticate to the expected organization.",
      );

      evidence.push({
        key_id: key.id,
        unauth_status: unauthError.status,
        created_system_key: Number(beforeAudit.enabled_system_key_count) === 0,
        enabled_system_key_count: Number(dbAudit.enabled_system_key_count),
        api_key_length: String(key.api_key).length,
        secret_key_length: String(key.secret_key).length,
        system_key_user_info_auth: true,
      });
    },
  },
  {
    id: "CORE-API-002",
    title:
      "Developer secret key create, masked list, API auth, disable, enable, and delete lifecycle",
    tags: [
      "core",
      "keys",
      "mutating",
      "data-roundtrip",
      "security",
      "authentication",
    ],
    async run({
      apiBase,
      client,
      user,
      organizationId,
      workspaceId,
      cleanup,
      runId,
      evidence,
    }) {
      requireMutations();
      if (!canManageOrgSecrets(user)) {
        skip(
          "Current user is not an org owner/admin; secret key cleanup is unsafe.",
        );
      }
      const userId = currentUserId(user);
      assert(
        userId,
        "Developer key journey requires an authenticated user id.",
      );
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const keyName = `api journey key ${runId}`;
      const created = await client.post(
        apiPath("/accounts/key/generate_secret_key/"),
        {
          key_name: keyName,
        },
      );
      assert(created?.key_id, "Secret key create did not return key_id.");
      assert(
        created.key_name === keyName,
        "Secret key create response returned the wrong key_name.",
      );
      assertRawDeveloperKeyMaterial(created.api_key, "created api_key");
      assertRawDeveloperKeyMaterial(created.secret_key, "created secret_key");
      assertMaskedDeveloperKey(
        created.masked_api_key,
        "created masked_api_key",
      );
      assertMaskedDeveloperKey(
        created.masked_secret_key,
        "created masked_secret_key",
      );

      let hardCleaned = false;
      cleanup.defer("hard-delete developer secret key fixture", () =>
        hardCleaned
          ? null
          : hardDeleteDeveloperSecretKeyDb(created.key_id, organizationId),
      );

      let dbAudit = await loadDeveloperSecretKeyDbAudit(
        created.key_id,
        organizationId,
      );
      assert(
        dbAudit.id === created.key_id,
        "DB audit did not find created developer key.",
      );
      assert(dbAudit.name === keyName, "DB audit developer key name mismatch.");
      assert(
        dbAudit.organization_id === organizationId,
        "DB audit developer key organization mismatch.",
      );
      assert(
        dbAudit.type === "user",
        "DB audit developer key type was not user.",
      );
      assert(
        dbAudit.enabled === true,
        "DB audit developer key was not enabled after create.",
      );
      assert(
        dbAudit.deleted === false,
        "DB audit developer key was deleted after create.",
      );
      assert(
        dbAudit.api_key === created.api_key,
        "DB audit raw api_key did not match one-time create response.",
      );
      assert(
        dbAudit.secret_key === created.secret_key,
        "DB audit raw secret_key did not match one-time create response.",
      );

      const apiKeyClient = createApiClient({
        apiBase,
        organizationId,
        workspaceId,
      });
      const apiKeyHeaders = {
        "X-Api-Key": created.api_key,
        "X-Secret-Key": created.secret_key,
      };
      const apiKeyUserInfo = await apiKeyClient.get(
        apiPath("/accounts/user-info/"),
        { headers: apiKeyHeaders },
      );
      assert(
        currentUserId(apiKeyUserInfo) === userId,
        "Developer key auth returned a different user-info identity.",
      );

      let listed = asArray(
        await client.get(apiPath("/accounts/key/get_secret_keys/"), {
          query: { search: keyName, page_size: 10 },
        }),
      );
      let listedRow = listed.find((key) => key.id === created.key_id);
      assertDeveloperKeyListRow(listedRow, {
        keyName,
        enabled: true,
        rawApiKey: created.api_key,
        rawSecretKey: created.secret_key,
      });

      await client.post(apiPath("/accounts/key/disable_key/"), {
        key_id: created.key_id,
      });
      dbAudit = await loadDeveloperSecretKeyDbAudit(
        created.key_id,
        organizationId,
      );
      assert(
        dbAudit.enabled === false,
        "DB audit developer key was not disabled.",
      );
      const disabledAuthError = await expectApiError(
        () =>
          apiKeyClient.get(apiPath("/accounts/user-info/"), {
            headers: apiKeyHeaders,
          }),
        [401, 403],
        "Disabled developer key still authenticated to user-info.",
      );
      listed = asArray(
        await client.get(apiPath("/accounts/key/get_secret_keys/"), {
          query: { search: keyName, page_size: 10 },
        }),
      );
      listedRow = listed.find((key) => key.id === created.key_id);
      assertDeveloperKeyListRow(listedRow, {
        keyName,
        enabled: false,
        rawApiKey: created.api_key,
        rawSecretKey: created.secret_key,
      });

      await client.post(apiPath("/accounts/key/enable_key/"), {
        key_id: created.key_id,
      });
      dbAudit = await loadDeveloperSecretKeyDbAudit(
        created.key_id,
        organizationId,
      );
      assert(
        dbAudit.enabled === true,
        "DB audit developer key was not re-enabled.",
      );
      const reenabledUserInfo = await apiKeyClient.get(
        apiPath("/accounts/user-info/"),
        { headers: apiKeyHeaders },
      );
      assert(
        currentUserId(reenabledUserInfo) === userId,
        "Re-enabled developer key auth returned a different user-info identity.",
      );
      listed = asArray(
        await client.get(apiPath("/accounts/key/get_secret_keys/"), {
          query: { search: keyName, page_size: 10 },
        }),
      );
      listedRow = listed.find((key) => key.id === created.key_id);
      assertDeveloperKeyListRow(listedRow, {
        keyName,
        enabled: true,
        rawApiKey: created.api_key,
        rawSecretKey: created.secret_key,
      });

      await client.delete(apiPath("/accounts/key/delete_secret_key/"), {
        body: { key_id: created.key_id },
      });
      dbAudit = await loadDeveloperSecretKeyDbAudit(
        created.key_id,
        organizationId,
      );
      assert(
        dbAudit.deleted === true,
        "DB audit developer key was not soft-deleted.",
      );
      assert(
        dbAudit.deleted_at_set === true,
        "DB audit developer key deleted_at was not set.",
      );
      const deletedAuthError = await expectApiError(
        () =>
          apiKeyClient.get(apiPath("/accounts/user-info/"), {
            headers: apiKeyHeaders,
          }),
        [401, 403],
        "Deleted developer key still authenticated to user-info.",
      );
      const afterDelete = asArray(
        await client.get(apiPath("/accounts/key/get_secret_keys/"), {
          query: { search: keyName, page_size: 10 },
        }),
      );
      assert(
        !afterDelete.some((key) => key.id === created.key_id),
        "Deleted secret key was still visible through list/search.",
      );

      const hardCleanup = await hardDeleteDeveloperSecretKeyDb(
        created.key_id,
        organizationId,
      );
      hardCleaned = true;
      assert(
        hardCleanup.remaining_key_count === 0,
        `Developer key hard cleanup left rows behind: ${JSON.stringify(hardCleanup)}`,
      );

      evidence.push({
        key_name: keyName,
        key_id: created.key_id,
        create_returned_one_time_raw_key_material: true,
        list_api_key_masked: true,
        list_secret_key_masked: true,
        api_key_user_info_auth: true,
        disabled_auth_status: disabledAuthError.status,
        reenabled_user_info_auth: true,
        deleted_auth_status: deletedAuthError.status,
        db_deleted_at_set: dbAudit.deleted_at_set,
        hard_cleanup_remaining_key_count: hardCleanup.remaining_key_count,
      });
    },
  },
  {
    id: "CORE-API-003",
    title:
      "Model provider API key create, retrieve, update, list, and delete lifecycle",
    tags: ["core", "provider-keys", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const existingKeys = asArray(
        await client.get(apiPath("/model-hub/api-keys/")),
      );
      const existingProviders = new Set(
        existingKeys.map((key) => key.provider),
      );
      const provider = [
        "lmnt",
        "cartesia",
        "hume",
        "neuphonic",
        "rime",
        "inworld",
        "deepgram",
        "elevenlabs",
        "sagemaker",
        "ai21",
        "perplexity",
        "groq",
      ].find((candidate) => !existingProviders.has(candidate));
      if (!provider) {
        skip(
          "Every safe provider-key candidate already exists; refusing to overwrite a real provider key.",
        );
      }

      const created = await client.post(apiPath("/model-hub/api-keys/"), {
        provider,
        key: `secret-${runId}`,
      });
      assert(created?.id, "Model provider key create did not return id.");
      cleanup.defer("delete model provider key", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/api-keys/{id}/", { id: created.id }),
          ),
        ),
      );

      const detail = await client.get(
        apiPath("/model-hub/api-keys/{id}/", { id: created.id }),
      );
      assert(
        detail?.provider === provider,
        "Provider key detail returned wrong provider.",
      );
      assert(
        typeof detail.masked_actual_key === "string",
        "Provider key detail did not include a masked key.",
      );

      const updated = await client.put(
        apiPath("/model-hub/api-keys/{id}/", { id: created.id }),
        { provider, key: `secret-updated-${runId}` },
      );
      assert(
        updated?.provider === provider,
        "Provider key update did not return the provider.",
      );

      const listed = asArray(await client.get(apiPath("/model-hub/api-keys/")));
      assert(
        listed.some(
          (key) => key.id === created.id && key.provider === provider,
        ),
        "Updated provider key was not visible in list.",
      );

      await client.delete(
        apiPath("/model-hub/api-keys/{id}/", { id: created.id }),
      );
      const afterDelete = asArray(
        await client.get(apiPath("/model-hub/api-keys/")),
      );
      assert(
        !afterDelete.some((key) => key.id === created.id),
        "Deleted provider key was still visible in list.",
      );

      evidence.push({ provider, provider_key_id: created.id });
    },
  },
];

function canManageOrgSecrets(user) {
  const role = String(
    user?.organization_role || user?.role || "",
  ).toLowerCase();
  return (
    role.includes("owner") ||
    role.includes("admin") ||
    Number(user?.org_level) >= 10
  );
}

function assertRawDeveloperKeyMaterial(value, label) {
  assert(
    /^[0-9a-f]{32}$/i.test(String(value || "")),
    `${label} was not one-time raw key material.`,
  );
}

function assertMaskedDeveloperKey(value, label) {
  const text = String(value || "");
  assert(text.includes("*"), `${label} was not masked.`);
  assert(!/^[0-9a-f]{32}$/i.test(text), `${label} exposed raw key material.`);
}

function assertDeveloperKeyListRow(
  row,
  { keyName, enabled, rawApiKey, rawSecretKey },
) {
  assert(row, "Developer key was not visible through list/search.");
  assert(
    row.key_name === keyName,
    "Developer key list row returned the wrong key_name.",
  );
  assert(
    row.enabled === enabled,
    "Developer key list row returned the wrong enabled state.",
  );
  assert(
    row.api_key !== rawApiKey,
    "Developer key list row exposed the raw api_key.",
  );
  assert(
    row.secret_key !== rawSecretKey,
    "Developer key list row exposed the raw secret_key.",
  );
  assertMaskedDeveloperKey(row.api_key, "developer key list api_key");
  assertMaskedDeveloperKey(row.secret_key, "developer key list secret_key");
}

function isOrgOwner(user) {
  const role = String(
    user?.organization_role || user?.role || "",
  ).toLowerCase();
  return role.includes("owner") || Number(user?.org_level) >= 15;
}

function findUserRow(rows, userId, email) {
  const normalizedEmail = String(email || "").toLowerCase();
  return asArray(rows).find(
    (row) =>
      row?.id === userId ||
      String(row?.email || "").toLowerCase() === normalizedEmail,
  );
}

function getPayloadTotal(payload) {
  if (typeof payload?.total === "number") return payload.total;
  if (typeof payload?.count === "number") return payload.count;
  return asArray(payload).length;
}

function assertPrototypeProjectListRow(project) {
  assert(
    isUuid(project?.id),
    "Prototype project list row did not include a valid id.",
  );
  assert(
    String(project?.name || "").trim(),
    "Prototype project list row omitted name.",
  );
  assert(
    project.trace_type === "experiment",
    "Prototype project list row was not trace_type=experiment.",
  );
  assert(
    isUuid(project.organization),
    "Prototype project list row omitted organization id.",
  );
  if (project.workspace != null) {
    assert(
      isUuid(project.workspace),
      "Prototype project list row returned an invalid workspace id.",
    );
  }
  assert(
    typeof project.created_at === "string",
    "Prototype project list row omitted created_at.",
  );
  assert(
    typeof project.updated_at === "string",
    "Prototype project list row omitted updated_at.",
  );
  assert(
    Number.isFinite(Number(project.trace_count)),
    "Prototype project list row omitted trace_count.",
  );
  assert(
    Number.isFinite(Number(project.run_count)),
    "Prototype project list row omitted run_count.",
  );
}

function assertSortedByName(projects, direction) {
  if (projects.length < 2) return;
  const collator = new Intl.Collator("en-US", {
    sensitivity: "base",
    ignorePunctuation: true,
  });
  const names = projects.map((project) => String(project.name || ""));
  const sorted = [...names].sort(collator.compare);
  if (direction === "desc") sorted.reverse();
  assert(
    names.every((name, index) => name === sorted[index]),
    `Prototype project list was not sorted by name ${direction}.`,
  );
}

function assertPrototypeProjectDetail(
  project,
  { projectId, organizationId, workspaceId },
) {
  assert(project?.id === projectId, "Prototype project detail id mismatch.");
  assert(
    project.trace_type === "experiment",
    "Prototype project detail was not trace_type=experiment.",
  );
  assert(
    project.organization === organizationId,
    "Prototype project detail organization mismatch.",
  );
  assert(
    project.workspace === workspaceId,
    "Prototype project detail workspace mismatch.",
  );
  assert(
    Array.isArray(project.config),
    "Prototype project detail config was not an array.",
  );
  assert(
    project.config.some((column) => column?.id === "run_name"),
    "Prototype project detail config omitted run_name.",
  );
  assert(
    Number.isFinite(Number(project.sampling_rate)),
    "Prototype project detail omitted sampling_rate.",
  );
}

function assertPrototypeRunList(payload) {
  assert(
    Array.isArray(payload?.column_config),
    "Prototype run list omitted column_config.",
  );
  assert(Array.isArray(payload?.table), "Prototype run list omitted table.");
  assert(
    Number.isFinite(Number(payload?.metadata?.total_rows)),
    "Prototype run list metadata omitted total_rows.",
  );
  assert(
    payload.column_config.some((column) => column?.id === "run_name"),
    "Prototype run list column_config omitted run_name.",
  );
  for (const row of payload.table) {
    assert(isUuid(row?.id), "Prototype run list row omitted id.");
    assert(
      String(row?.run_name || "").trim(),
      "Prototype run list row omitted run_name.",
    );
    assert(
      Object.prototype.hasOwnProperty.call(row, "avg_cost"),
      "Prototype run list row omitted avg_cost.",
    );
    assert(
      Object.prototype.hasOwnProperty.call(row, "avg_latency"),
      "Prototype run list row omitted avg_latency.",
    );
    assert(
      Object.prototype.hasOwnProperty.call(row, "rank"),
      "Prototype run list row omitted rank.",
    );
  }
}

function assertPrototypeSdkCode(payload) {
  assert(
    payload?.installation_guide?.Python,
    "Prototype SDK code omitted Python install guide.",
  );
  assert(
    payload?.project_add_code?.Python,
    "Prototype SDK code omitted Python project_add_code.",
  );
  assert(payload?.keys?.Python, "Prototype SDK code omitted Python key setup.");
  assert(
    payload?.instruments?.openai?.Python,
    "Prototype SDK code omitted OpenAI Python instrument.",
  );
}

function prototypeSdkCodeHasRawFiKeys(payload) {
  const text = JSON.stringify(payload || {});
  return (
    /FI_API_KEY/.test(text) &&
    /FI_SECRET_KEY/.test(text) &&
    /[a-f0-9]{32}/i.test(text)
  );
}

function assertUsageWorkspaceRow(row, label) {
  assert(isUuid(row?.id), `${label} did not include a valid workspace id.`);
  assert(
    String(row?.name || "").trim(),
    `${label} did not include a workspace name.`,
  );
  for (const key of [
    "overall",
    "traces",
    "evaluations",
    "error_localizations",
    "agent_compass",
    "simulate",
  ]) {
    assertUsageMetric(row[key], `${label}.${key}`);
  }
}

function assertUsageMetric(metric, label) {
  assert(
    typeof metric?.cost === "number" && Number.isFinite(metric.cost),
    `${label} did not include numeric cost.`,
  );
  assert(
    Number.isInteger(metric?.count) && metric.count >= 0,
    `${label} did not include a non-negative integer count.`,
  );
}

function assertEvalSummaryPayload(payload, label) {
  const evaluations = asArray(payload?.evaluations);
  assert(
    Array.isArray(payload?.evaluations),
    `${label} did not include evaluations array.`,
  );
  assertUsageMetric(payload?.total, `${label}.total`);
  let summedCost = 0;
  let summedCount = 0;
  for (const evaluation of evaluations) {
    assert(
      String(evaluation?.name || "").trim(),
      `${label} included an evaluation without name.`,
    );
    assertUsageMetric(evaluation, `${label}.${evaluation.name}`);
    summedCost += evaluation.cost;
    summedCount += evaluation.count;
  }
  assert(
    summedCount === payload.total.count,
    `${label} total count did not match evaluation rows.`,
  );
  assert(
    numbersClose(summedCost, payload.total.cost),
    `${label} total cost did not match evaluation rows.`,
  );
}

function numbersClose(left, right, tolerance = 0.000001) {
  return Math.abs(Number(left) - Number(right)) <= tolerance;
}

function assertNoCredentialLeak(payload, seeded, label) {
  const text = JSON.stringify(payload ?? {});
  assert(
    !text.includes(seeded.plain_public_key),
    `${label} leaked the raw public key.`,
  );
  assert(
    !text.includes(seeded.plain_secret_key),
    `${label} leaked the raw secret key.`,
  );
  assert(
    !/"public_key"|"secret_key"/.test(text),
    `${label} exposed raw credential field names.`,
  );
}

function assertNoPayloadString(payload, forbidden, label) {
  assert(
    !JSON.stringify(payload ?? {}).includes(forbidden),
    `${label} leaked a forbidden credential value.`,
  );
}

function assertProviderStatusRow(provider) {
  assert(
    String(provider?.provider || "").trim(),
    "Provider status row omitted provider.",
  );
  assert(
    String(provider?.display_name || "").trim(),
    `Provider ${provider?.provider} omitted display_name.`,
  );
  assert(
    provider.type === "text" || provider.type === "json",
    `Provider ${provider?.provider} returned unsupported type ${provider?.type}.`,
  );
  assert(
    typeof provider.has_key === "boolean",
    `Provider ${provider.provider} omitted boolean has_key.`,
  );
  assert(
    provider.masked_key === null ||
      typeof provider.masked_key === "string" ||
      typeof provider.masked_key === "object",
    `Provider ${provider.provider} returned invalid masked_key type.`,
  );
  if (provider.has_key) {
    assert(
      isUuid(provider.id),
      `Configured provider ${provider.provider} omitted valid id.`,
    );
    assert(
      provider.masked_key !== null,
      `Configured provider ${provider.provider} omitted masked_key.`,
    );
  }
}

function assertGetStartedChecks(checks) {
  const required = [
    "keys",
    "dataset",
    "evaluation",
    "experiment",
    "observe",
    "invite",
  ];
  for (const key of required) {
    assert(
      typeof checks?.[key] === "boolean",
      `Get Started first-checks omitted boolean ${key}.`,
    );
  }
}

function assertNoRawProviderSecretLeak(payload, label) {
  const text = JSON.stringify(payload ?? {});
  const secretPatterns = [
    /sk-[A-Za-z0-9_-]{12,}/,
    /AIza[A-Za-z0-9_-]{20,}/,
    /AKIA[A-Z0-9]{16}/,
    /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  ];
  for (const pattern of secretPatterns) {
    assert(
      !pattern.test(text),
      `${label} appears to contain a raw provider credential.`,
    );
  }
  assert(
    !/"actual_key"|"actual_json"/.test(text),
    `${label} exposed decrypted key field names.`,
  );
}

function firstMaskedScalar(value) {
  if (typeof value === "string") return value.includes("*") ? value : null;
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = firstMaskedScalar(item);
      if (found) return found;
    }
    return null;
  }
  if (value && typeof value === "object") {
    for (const item of Object.values(value)) {
      const found = firstMaskedScalar(item);
      if (found) return found;
    }
  }
  return null;
}

function assertPlanOptionRow(row) {
  assert(String(row?.key || "").trim(), "Plan option omitted key.");
  assert(
    String(row?.display_name || "").trim(),
    `Plan option ${row?.key} omitted display_name.`,
  );
  assert(
    typeof row?.platform_fee_monthly === "number" &&
      Number.isFinite(row.platform_fee_monthly),
    `Plan option ${row?.key} omitted numeric platform_fee_monthly.`,
  );
  assert(
    row?.features && typeof row.features === "object",
    `Plan option ${row?.key} omitted features.`,
  );
}

function assertPricingDimension(dimension, pricing) {
  assert(String(dimension || "").trim(), "Pricing dimension omitted key.");
  assert(
    String(pricing?.display_name || "").trim(),
    `Pricing dimension ${dimension} omitted display_name.`,
  );
  assert(
    String(pricing?.display_unit || "").trim(),
    `Pricing dimension ${dimension} omitted display_unit.`,
  );
  assert(
    Array.isArray(pricing?.tiers) && pricing.tiers.length > 0,
    `Pricing dimension ${dimension} omitted tier rows.`,
  );
  for (const tier of pricing.tiers) {
    assert(
      tier.up_to === null ||
        (typeof tier.up_to === "number" && Number.isFinite(tier.up_to)),
      `Pricing dimension ${dimension} returned invalid up_to.`,
    );
    assert(
      typeof tier.price_per_unit === "number" &&
        Number.isFinite(tier.price_per_unit),
      `Pricing dimension ${dimension} returned invalid price_per_unit.`,
    );
  }
}

function assertBillingMoneyFields(row, label) {
  for (const key of [
    "platform_fee",
    "usage_total",
    "credits_applied",
    "subtotal",
    "tax",
    "total",
  ]) {
    assert(
      typeof row?.[key] === "number" && Number.isFinite(row[key]),
      `${label} omitted numeric ${key}.`,
    );
  }
}

function assertBillingLineItem(item, label) {
  assert(String(item?.line_type || "").trim(), `${label} omitted line_type.`);
  assert(
    String(item?.description || "").trim(),
    `${label} omitted description.`,
  );
  assert(
    typeof item?.amount === "number" && Number.isFinite(item.amount),
    `${label} omitted numeric amount.`,
  );
}

function assertInvoiceRow(invoice) {
  assert(isUuid(invoice?.id), "Invoice row omitted a valid id.");
  assert(
    String(invoice?.period_start || "").trim(),
    `Invoice ${invoice?.id} omitted period_start.`,
  );
  assert(
    String(invoice?.period_end || "").trim(),
    `Invoice ${invoice?.id} omitted period_end.`,
  );
  assert(
    String(invoice?.plan || "").trim(),
    `Invoice ${invoice?.id} omitted plan.`,
  );
  assertBillingMoneyFields(invoice, `invoice ${invoice?.id}`);
  assert(
    String(invoice?.status || "").trim(),
    `Invoice ${invoice?.id} omitted status.`,
  );
}

function assertPaymentMethodRow(method) {
  assert(String(method?.id || "").trim(), "Payment method row omitted id.");
  assert(
    String(method?.brand || "").trim(),
    `Payment method ${method?.id} omitted brand.`,
  );
  assert(
    /^\d{4}$/.test(String(method?.last4 || "")),
    `Payment method ${method?.id} omitted last4.`,
  );
  assert(
    Number.isInteger(method?.exp_month),
    `Payment method ${method?.id} omitted exp_month.`,
  );
  assert(
    Number.isInteger(method?.exp_year),
    `Payment method ${method?.id} omitted exp_year.`,
  );
  assert(
    typeof method?.is_default === "boolean",
    `Payment method ${method?.id} omitted is_default.`,
  );
}

function assertEELicenseRow(license) {
  assert(isUuid(license?.id), "EE license row omitted valid id.");
  assert(
    String(license?.customer_name || "").trim(),
    `EE license ${license?.id} omitted customer_name.`,
  );
  assert(
    String(license?.band || "").trim(),
    `EE license ${license?.id} omitted band.`,
  );
  assert(
    String(license?.billing_interval || "").trim(),
    `EE license ${license?.id} omitted billing_interval.`,
  );
  assert(
    Array.isArray(license?.features),
    `EE license ${license?.id} omitted features array.`,
  );
  assert(
    String(license?.issued_at || "").trim(),
    `EE license ${license?.id} omitted issued_at.`,
  );
  assert(
    String(license?.expires_at || "").trim(),
    `EE license ${license?.id} omitted expires_at.`,
  );
  assert(
    String(license?.status || "").trim(),
    `EE license ${license?.id} omitted status.`,
  );
  assert(
    license.jwt_key === undefined,
    `EE license ${license?.id} exposed jwt_key.`,
  );
  assert(
    license.license_key_hash === undefined,
    `EE license ${license?.id} exposed license_key_hash.`,
  );
}

function assertNoBillingCheckoutPayload(payload, label) {
  const text = JSON.stringify(payload ?? {});
  assert(
    !/"checkout_url"/.test(text),
    `${label} exposed a checkout_url in a read response.`,
  );
}

function assertNoFullPaymentCardLeak(payload, label) {
  const text = JSON.stringify(payload ?? {});
  assert(
    !/\b(?:\d[ -]*?){12,19}\b/.test(text),
    `${label} appears to contain a full card number.`,
  );
  assert(
    !/"number"|"cvc"|"cvv"/i.test(text),
    `${label} exposed raw card field names.`,
  );
}

function assertNoEELicenseSecretLeak(payload, label) {
  const text = JSON.stringify(payload ?? {});
  assert(
    !/"jwt_key"|"license_key_hash"/.test(text),
    `${label} exposed license secret fields.`,
  );
  assert(
    !/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/.test(text),
    `${label} appears to contain a JWT license key.`,
  );
}

const MCP_CATEGORY_TO_GROUP = {
  context: "context",
  evaluations: "evaluations",
  datasets: "datasets",
  annotations: "annotations",
  optimization: "optimization",
  tracing: "observability",
  error_feed: "error_feed",
  experiments: "experiments",
  agents: "agents",
  simulation: "simulation",
  prompts: "prompts",
  users: "users",
  usage: "usage",
  docs: "docs",
};

function assertMCPConnectionConfig(config) {
  assert(isUuid(config?.id), "MCP config omitted a valid connection id.");
  assert(
    ["remote", "stdio"].includes(config?.connection_mode),
    `MCP config returned unsupported connection_mode ${config?.connection_mode}.`,
  );
  assert(
    typeof config?.is_active === "boolean",
    "MCP config omitted boolean is_active.",
  );
  assert(
    String(config?.created_at || "").trim(),
    "MCP config omitted created_at.",
  );
  assert(
    String(config?.updated_at || "").trim(),
    "MCP config omitted updated_at.",
  );
  assert(
    config?.tool_config && typeof config.tool_config === "object",
    "MCP config omitted tool_config.",
  );
  assertMCPToolGroupConfig(config.tool_config);
  assert(
    String(config?.mcp_url || "").startsWith("http"),
    "MCP config omitted absolute mcp_url.",
  );
  assert(
    new URL(config.mcp_url).pathname.replace(/\/$/, "") === "/mcp",
    "MCP config mcp_url did not point at /mcp.",
  );
}

function assertMCPToolGroupConfig(config) {
  assert(
    Array.isArray(config?.enabled_groups),
    "MCP tool config omitted enabled_groups array.",
  );
  assert(
    Array.isArray(config?.disabled_tools),
    "MCP tool config omitted disabled_tools array.",
  );
  assert(
    Array.isArray(config?.available_groups),
    "MCP tool config omitted available_groups array.",
  );
  assert(
    config.available_groups.length > 0,
    "MCP tool config returned no available groups.",
  );
  const availableSlugs = new Set();
  for (const group of config.available_groups) {
    assertMCPToolGroupRow(group);
    availableSlugs.add(group.slug);
    assert(
      group.enabled === config.enabled_groups.includes(group.slug),
      `MCP tool group ${group.slug} enabled flag did not match enabled_groups.`,
    );
  }
  for (const group of config.enabled_groups) {
    assert(
      availableSlugs.has(group),
      `MCP enabled group ${group} was not in available_groups.`,
    );
  }
}

function assertMCPToolGroupRow(group) {
  assert(String(group?.slug || "").trim(), "MCP tool group row omitted slug.");
  assert(
    String(group?.name || "").trim(),
    `MCP tool group ${group?.slug} omitted name.`,
  );
  assert(
    String(group?.description || "").trim(),
    `MCP tool group ${group?.slug} omitted description.`,
  );
  assert(
    typeof group?.enabled === "boolean",
    `MCP tool group ${group?.slug} omitted boolean enabled.`,
  );
  assert(
    !String(group.description)
      .toLowerCase()
      .includes("eval templates and groups"),
    `MCP tool group ${group.slug} still references legacy eval groups copy.`,
  );
}

function assertMCPToolRow(tool) {
  assert(String(tool?.name || "").trim(), "MCP tool row omitted name.");
  assert(
    String(tool?.description || "").trim(),
    `MCP tool ${tool?.name} omitted description.`,
  );
  assert(
    String(tool?.category || "").trim(),
    `MCP tool ${tool?.name} omitted category.`,
  );
  assert(
    tool?.input_schema && typeof tool.input_schema === "object",
    `MCP tool ${tool?.name} omitted input_schema object.`,
  );
}

function assertMCPToolsRespectEnabledGroups(tools, enabledGroups) {
  const enabled = new Set(enabledGroups || []);
  for (const tool of tools) {
    const group = MCP_CATEGORY_TO_GROUP[tool.category] || tool.category;
    assert(
      enabled.has(group),
      `MCP tool ${tool.name} from disabled group ${group} was visible.`,
    );
  }
}

function assertMCPSessionRow(session) {
  assert(isUuid(session?.id), "MCP session row omitted valid id.");
  assert(
    ["active", "idle", "disconnected", "revoked"].includes(session?.status),
    `MCP session ${session?.id} returned unsupported status ${session?.status}.`,
  );
  assert(
    ["streamable_http", "sse", "stdio"].includes(session?.transport),
    `MCP session ${session?.id} returned unsupported transport ${session?.transport}.`,
  );
  assert(
    String(session?.started_at || "").trim(),
    `MCP session ${session?.id} omitted started_at.`,
  );
  assert(
    String(session?.last_activity_at || "").trim(),
    `MCP session ${session?.id} omitted last_activity_at.`,
  );
  assert(
    Number.isInteger(session?.tool_call_count),
    `MCP session ${session?.id} omitted integer tool_call_count.`,
  );
  assert(
    Number.isInteger(session?.error_count),
    `MCP session ${session?.id} omitted integer error_count.`,
  );
}

function assertMCPAnalyticsSummary(summary) {
  for (const key of [
    "total_calls",
    "total_sessions",
    "avg_latency_ms",
    "error_rate",
    "active_sessions",
  ]) {
    assert(
      typeof summary?.[key] === "number" && Number.isFinite(summary[key]),
      `MCP analytics summary omitted numeric ${key}.`,
    );
  }
}

function assertMCPAnalyticsToolRow(row) {
  assert(
    String(row?.tool_name || "").trim(),
    "MCP analytics tool row omitted tool_name.",
  );
  assert(
    Number.isInteger(row?.call_count),
    `MCP analytics ${row?.tool_name} omitted integer call_count.`,
  );
  assert(
    typeof row?.avg_latency_ms === "number" &&
      Number.isFinite(row.avg_latency_ms),
    `MCP analytics ${row?.tool_name} omitted numeric avg_latency_ms.`,
  );
  assert(
    typeof row?.error_rate === "number" && Number.isFinite(row.error_rate),
    `MCP analytics ${row?.tool_name} omitted numeric error_rate.`,
  );
}

function assertMCPAnalyticsTimelineRow(row) {
  assert(
    String(row?.timestamp || "").trim(),
    "MCP analytics timeline row omitted timestamp.",
  );
  assert(
    Number.isInteger(row?.call_count),
    "MCP analytics timeline row omitted integer call_count.",
  );
}

function assertNoMCPConnectionSecretLeak(config, label) {
  for (const key of [
    "oauth_token_encrypted",
    "oauth_refresh_token_encrypted",
    "oauth_token_expires_at",
    "api_key",
    "api_key_id",
  ]) {
    assert(config?.[key] === undefined, `${label} exposed ${key}.`);
  }
  const text = JSON.stringify(config ?? {});
  assert(
    !/Bearer\s+[A-Za-z0-9._-]+/.test(text),
    `${label} appears to contain a bearer token.`,
  );
  assert(
    !/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/.test(text),
    `${label} appears to contain a JWT.`,
  );
}

function assertGroupSetsEqual(left, right, message) {
  const leftSet = new Set(left || []);
  const rightSet = new Set(right || []);
  assert(leftSet.size === rightSet.size, message);
  for (const item of leftSet) {
    assert(rightSet.has(item), message);
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

async function loadDeveloperSecretKeyDbAudit(keyId, organizationId) {
  const sql = `
SELECT json_build_object(
  'id', id::text,
  'name', name,
  'api_key', api_key,
  'secret_key', secret_key,
  'organization_id', organization_id::text,
  'user_id', user_id::text,
  'workspace_id', workspace_id::text,
  'type', type,
  'enabled', enabled,
  'deleted', deleted,
  'deleted_at_set', deleted_at IS NOT NULL
)
FROM accounts_orgapikey
WHERE id = ${sqlUuid(keyId)}
  AND organization_id = ${sqlUuid(organizationId)};
`;
  return runPostgresJson(sql);
}

async function loadSystemOrgKeyDbAudit({
  organizationId,
  keyId,
  apiKey,
  secretKey,
}) {
  const matchingKeyFilter = keyId
    ? `WHERE id = ${sqlUuid(keyId)}`
    : "WHERE false";
  const apiKeyMatchExpression = apiKey
    ? `api_key = ${sqlTextLiteral(apiKey)}`
    : "NULL::boolean";
  const secretKeyMatchExpression = secretKey
    ? `secret_key = ${sqlTextLiteral(secretKey)}`
    : "NULL::boolean";
  const sql = `
WITH system_keys AS (
  SELECT *
  FROM accounts_orgapikey
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND type = 'system'
    AND enabled = true
    AND deleted = false
),
matching_key AS (
  SELECT *
  FROM system_keys
  ${matchingKeyFilter}
)
SELECT json_build_object(
  'enabled_system_key_count', (SELECT count(*) FROM system_keys),
  'matching_key_count', (SELECT count(*) FROM matching_key),
  'matching_key_id', (SELECT id::text FROM matching_key LIMIT 1),
  'type', (SELECT type FROM matching_key LIMIT 1),
  'enabled', (SELECT enabled FROM matching_key LIMIT 1),
  'deleted', (SELECT deleted FROM matching_key LIMIT 1),
  'workspace_id', (SELECT workspace_id::text FROM matching_key LIMIT 1),
  'user_id', (SELECT user_id::text FROM matching_key LIMIT 1),
  'api_key_matches', (SELECT ${apiKeyMatchExpression} FROM matching_key LIMIT 1),
  'secret_key_matches', (SELECT ${secretKeyMatchExpression} FROM matching_key LIMIT 1)
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteDeveloperSecretKeyDb(keyId, organizationId) {
  const sql = `
WITH target_keys AS (
  SELECT id
  FROM accounts_orgapikey
  WHERE id = ${sqlUuid(keyId)}
    AND organization_id = ${sqlUuid(organizationId)}
),
deleted_keys AS (
  DELETE FROM accounts_orgapikey key
  USING target_keys target
  WHERE key.id = target.id
  RETURNING key.id
)
SELECT json_build_object(
  'deleted_key_count', (SELECT count(*) FROM deleted_keys),
  'remaining_key_count',
    (SELECT count(*) FROM target_keys) - (SELECT count(*) FROM deleted_keys)
);
`;
  return runPostgresJson(sql);
}

async function loadPrototypeProjectDbAudit({
  projectId,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id
)
SELECT COALESCE(
  (
    SELECT jsonb_build_object(
      'exists', true,
      'project_id', p.id::text,
      'name', p.name,
      'trace_type', p.trace_type,
      'model_type', p.model_type,
      'organization_id', p.organization_id::text,
      'workspace_id', p.workspace_id::text,
      'deleted', p.deleted,
      'deleted_at_set', p.deleted_at IS NOT NULL,
      'config_length', jsonb_array_length(COALESCE(p.config, '[]'::jsonb)),
      'session_config_length', jsonb_array_length(COALESCE(p.session_config, '[]'::jsonb)),
      'sampling_rate', tsc.sampling_rate
    )
    FROM requested
    JOIN tracer_project p
      ON p.id = requested.project_id
     AND p.organization_id = requested.organization_id
     AND p.workspace_id = requested.workspace_id
    LEFT JOIN tracer_trace_scan_config tsc
      ON tsc.project_id = p.id
  ),
  jsonb_build_object('exists', false)
)::text;
`;
  return runPostgresJson(sql);
}

async function loadProjectCrudDbAudit({
  projectId,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id
)
SELECT COALESCE(
  (
    SELECT jsonb_build_object(
      'exists', true,
      'project_id', p.id::text,
      'name', p.name,
      'trace_type', p.trace_type,
      'model_type', p.model_type,
      'organization_id', p.organization_id::text,
      'workspace_id', p.workspace_id::text,
      'metadata', COALESCE(p.metadata, '{}'::jsonb),
      'config', COALESCE(p.config, '[]'::jsonb),
      'session_config', COALESCE(p.session_config, '[]'::jsonb),
      'config_visibility', COALESCE((
        SELECT jsonb_object_agg(item->>'id', (item->>'is_visible')::boolean)
        FROM jsonb_array_elements(COALESCE(p.config, '[]'::jsonb)) item
        WHERE item ? 'id'
      ), '{}'::jsonb),
      'session_config_visibility', COALESCE((
        SELECT jsonb_object_agg(item->>'id', (item->>'is_visible')::boolean)
        FROM jsonb_array_elements(COALESCE(p.session_config, '[]'::jsonb)) item
        WHERE item ? 'id'
      ), '{}'::jsonb),
      'tags', COALESCE(p.tags, '[]'::jsonb),
      'deleted', p.deleted,
      'deleted_at_set', p.deleted_at IS NOT NULL
    )
    FROM requested
    JOIN tracer_project p
      ON p.id = requested.project_id
     AND p.organization_id = requested.organization_id
     AND p.workspace_id = requested.workspace_id
  ),
  jsonb_build_object('exists', false)
)::text;
`;
  return runPostgresJson(sql);
}

function assertProjectCrudDbAudit(
  audit,
  {
    projectId,
    organizationId,
    workspaceId,
    name,
    deleted,
    deletedAtSet,
    tags,
    configVisibility,
    sessionConfigVisibility,
  },
) {
  assert(
    audit?.exists === true,
    "Project CRUD DB audit did not find the project.",
  );
  assert(audit.project_id === projectId, "Project CRUD DB audit id mismatch.");
  assert(
    audit.organization_id === organizationId,
    "Project CRUD DB audit organization mismatch.",
  );
  assert(
    audit.workspace_id === workspaceId,
    "Project CRUD DB audit workspace mismatch.",
  );
  assert(audit.name === name, "Project CRUD DB audit name mismatch.");
  assert(
    audit.trace_type === "experiment",
    "Project CRUD DB audit trace_type mismatch.",
  );
  assert(
    audit.model_type === "GenerativeLLM",
    "Project CRUD DB audit model_type mismatch.",
  );
  assert(
    audit.deleted === deleted,
    "Project CRUD DB audit deleted state mismatch.",
  );
  if (deletedAtSet !== undefined) {
    assert(
      audit.deleted_at_set === deletedAtSet,
      "Project CRUD DB audit deleted_at mismatch.",
    );
  }
  for (const [key, expected] of Object.entries(configVisibility || {})) {
    assert(
      audit.config_visibility?.[key] === expected,
      `Project CRUD DB audit config visibility mismatch for ${key}.`,
    );
  }
  for (const [key, expected] of Object.entries(sessionConfigVisibility || {})) {
    assert(
      audit.session_config_visibility?.[key] === expected,
      `Project CRUD DB audit session config visibility mismatch for ${key}.`,
    );
  }
  if (tags) {
    assertGroupSetsEqual(
      audit.tags || [],
      tags,
      "Project CRUD DB audit tags mismatch.",
    );
  }
}

function assertPrototypeProjectDbAudit(
  audit,
  { projectId, name, organizationId, workspaceId, deleted, samplingRate },
) {
  assert(
    audit?.exists === true,
    "Prototype project DB audit did not find the project.",
  );
  assert(
    audit.project_id === projectId,
    "Prototype project DB audit id mismatch.",
  );
  assert(audit.name === name, "Prototype project DB audit name mismatch.");
  assert(
    audit.trace_type === "experiment",
    "Prototype project DB audit trace_type mismatch.",
  );
  assert(
    audit.model_type === "GenerativeLLM",
    "Prototype project DB audit model_type mismatch.",
  );
  assert(
    audit.organization_id === organizationId,
    "Prototype project DB audit organization mismatch.",
  );
  assert(
    audit.workspace_id === workspaceId,
    "Prototype project DB audit workspace mismatch.",
  );
  assert(
    audit.deleted === deleted,
    "Prototype project DB audit deleted state mismatch.",
  );
  assert(
    Number(audit.config_length || 0) > 0,
    "Prototype project DB audit config was empty.",
  );
  if (samplingRate !== undefined) {
    assert(
      Number(audit.sampling_rate) === samplingRate,
      "Prototype project DB audit sampling_rate mismatch.",
    );
  }
}

async function hardDeletePrototypeProject({
  projectId,
  projectName,
  updatedProjectName,
  extraNames = [],
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(projectId)} AS project_id,
    ${sqlTextArray([projectName, updatedProjectName, ...extraNames])} AS allowed_names
),
deleted_scan_config AS (
  DELETE FROM tracer_trace_scan_config
  WHERE project_id = (SELECT project_id FROM requested)
  RETURNING 1
),
deleted_project AS (
  DELETE FROM tracer_project
  WHERE id = (SELECT project_id FROM requested)
    AND name::text IN (SELECT unnest(allowed_names) FROM requested)
  RETURNING 1
)
SELECT jsonb_build_object(
  'project_count', CASE
    WHEN (SELECT count(*) FROM deleted_project) > 0 THEN 0
    ELSE (
      SELECT count(*)
      FROM tracer_project
      WHERE id = (SELECT project_id FROM requested)
    )
  END,
  'scan_config_count', CASE
    WHEN (SELECT count(*) FROM deleted_scan_config) > 0 THEN 0
    ELSE (
      SELECT count(*)
      FROM tracer_trace_scan_config
      WHERE project_id = (SELECT project_id FROM requested)
    )
  END,
  'deleted_project_count', (SELECT count(*) FROM deleted_project),
  'deleted_scan_config_count', (SELECT count(*) FROM deleted_scan_config)
)::text;
`;
  return runPostgresJson(sql);
}

async function createProjectVersionJourneyRun({
  client,
  projectId,
  name,
  metadata,
}) {
  const created = await client.post(apiPath("/tracer/project-version/"), {
    project: projectId,
    name,
    metadata,
  });
  const projectVersionId = created.project_version_id || created.id;
  assert(
    isUuid(projectVersionId),
    "Project-version create did not return a valid id.",
  );
  assert(
    String(created.version || "").trim(),
    "Project-version create did not return version.",
  );
  return { id: projectVersionId, version: created.version };
}

async function seedProjectVersionJourneyTraceAndSpan({
  client,
  projectId,
  projectVersionId,
  marker,
  label,
  latencyMs,
  cost,
}) {
  const trace = await client.post(apiPath("/tracer/trace/"), {
    project: projectId,
    project_version: projectVersionId,
    name: `api journey pv trace ${label} ${marker}`,
    input: { prompt: `api journey ${label} input` },
    output: { response: `api journey ${label} output` },
    metadata: { source: "api-journey", marker, label },
    tags: ["api-journey", label],
  });
  const traceId = trace.id || trace.trace_id || trace.trace?.id;
  assert(
    isUuid(traceId),
    "Project-version trace seed did not return a trace id.",
  );

  const spanId = `api_journey_pv_${label}_${marker}`;
  const startTime = new Date(Date.now() - latencyMs).toISOString();
  const endTime = new Date().toISOString();
  const span = await client.post(apiPath("/tracer/observation-span/"), {
    id: spanId,
    project: projectId,
    project_version: projectVersionId,
    trace: traceId,
    name: `api journey pv span ${label} ${marker}`,
    observation_type: "llm",
    start_time: startTime,
    end_time: endTime,
    input: { messages: [{ role: "user", content: `hello ${label}` }] },
    output: { choices: [{ message: { content: `hi ${label}` } }] },
    model: "api-journey-model",
    prompt_tokens: 2,
    completion_tokens: 3,
    total_tokens: 5,
    latency_ms: latencyMs,
    cost,
    status: "OK",
    tags: ["api-journey", label],
    metadata: { source: "api-journey", marker, label },
  });
  assert(
    (span.id || spanId) === spanId,
    "Project-version span seed returned the wrong id.",
  );

  return { traceId, spanId };
}

async function loadProjectVersionJourneyDbAudit({
  projectId,
  organizationId,
  workspaceId,
  projectVersionIds,
  traceIds,
  spanIds,
  winnerVersionId,
  annotationId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuidArray(projectVersionIds)} AS project_version_ids,
    ${sqlUuidArray(traceIds)} AS trace_ids,
    ${sqlTextArray(spanIds)} AS span_ids,
    ${sqlUuid(winnerVersionId)} AS winner_version_id,
    ${sqlUuid(annotationId)} AS annotation_id
)
SELECT jsonb_build_object(
  'project', (
    SELECT jsonb_build_object(
      'id', p.id::text,
      'name', p.name,
      'organization_id', p.organization_id::text,
      'workspace_id', p.workspace_id::text,
      'trace_type', p.trace_type,
      'deleted', p.deleted
    )
    FROM tracer_project p, requested
    WHERE p.id = requested.project_id
  ),
  'versions', COALESCE((
    SELECT jsonb_agg(
      jsonb_build_object(
        'id', pv.id::text,
        'project_id', pv.project_id::text,
        'name', pv.name,
        'version', pv.version,
        'deleted', pv.deleted,
        'deleted_at_set', pv.deleted_at IS NOT NULL,
        'avg_eval_score', pv.avg_eval_score,
        'annotations_id', pv.annotations_id::text,
        'config', COALESCE(pv.config, '[]'::jsonb)
      )
      ORDER BY pv.version
    )
    FROM tracer_project_version pv, requested
    WHERE pv.id = ANY(requested.project_version_ids)
  ), '[]'::jsonb),
  'trace_deleted', COALESCE((
    SELECT jsonb_object_agg(t.id::text, t.deleted)
    FROM tracer_trace t, requested
    WHERE t.id = ANY(requested.trace_ids)
  ), '{}'::jsonb),
  'span_deleted', COALESCE((
    SELECT jsonb_object_agg(s.id::text, s.deleted)
    FROM tracer_observation_span s, requested
    WHERE s.id = ANY(requested.span_ids)
  ), '{}'::jsonb),
  'winner', (
    SELECT jsonb_build_object(
      'id', w.id::text,
      'project_id', w.project_id::text,
      'winner_version_id', w.winner_version_id::text,
      'deleted', w.deleted,
      'eval_config', w.eval_config,
      'version_mapper', w.version_mapper
    )
    FROM tracer_project_version_winner w, requested
    WHERE w.winner_version_id = requested.winner_version_id
    ORDER BY w.created_at DESC
    LIMIT 1
  ),
  'annotation', (
    SELECT jsonb_build_object(
      'id', a.id::text,
      'name', a.name,
      'organization_id', a.organization_id::text,
      'workspace_id', a.workspace_id::text,
      'deleted', a.deleted
    )
    FROM model_hub_annotations a, requested
    WHERE a.id = requested.annotation_id
  )
)::text;
`;
  return runPostgresJson(sql);
}

function assertProjectVersionJourneyDbAudit(
  audit,
  {
    projectId,
    organizationId,
    workspaceId,
    alphaVersionId,
    betaVersionId,
    alphaName,
    betaName,
    alphaDeleted,
    betaDeleted,
    alphaTraceId,
    betaTraceId,
    alphaSpanId,
    betaSpanId,
    traceDeleted,
    spanDeleted,
    annotationId,
    winnerVersionId,
    latencyVisible,
  },
) {
  assert(
    audit?.project?.id === projectId,
    "Project-version DB audit project id mismatch.",
  );
  assert(
    audit.project.organization_id === organizationId,
    "Project-version DB audit organization mismatch.",
  );
  assert(
    audit.project.workspace_id === workspaceId,
    "Project-version DB audit workspace mismatch.",
  );
  assert(
    audit.project.trace_type === "experiment",
    "Project-version DB audit trace_type mismatch.",
  );

  const alpha = findAuditRow(audit.versions, alphaVersionId);
  const beta = findAuditRow(audit.versions, betaVersionId);
  assert(
    alpha?.name === alphaName,
    "Project-version DB audit alpha name mismatch.",
  );
  assert(
    beta?.name === betaName,
    "Project-version DB audit beta name mismatch.",
  );
  assert(
    alpha?.deleted === alphaDeleted,
    "Project-version DB audit alpha deleted mismatch.",
  );
  assert(
    beta?.deleted === betaDeleted,
    "Project-version DB audit beta deleted mismatch.",
  );
  assert(
    alpha?.annotations_id === annotationId,
    "Project-version DB audit annotation link mismatch.",
  );

  assert(
    audit.trace_deleted?.[alphaTraceId] === traceDeleted,
    "Alpha trace deleted state mismatch.",
  );
  assert(
    audit.trace_deleted?.[betaTraceId] === traceDeleted,
    "Beta trace deleted state mismatch.",
  );
  assert(
    audit.span_deleted?.[alphaSpanId] === spanDeleted,
    "Alpha span deleted state mismatch.",
  );
  assert(
    audit.span_deleted?.[betaSpanId] === spanDeleted,
    "Beta span deleted state mismatch.",
  );
  assert(
    audit.annotation?.id === annotationId,
    "Project-version DB audit annotation mismatch.",
  );
  assert(
    audit.winner?.winner_version_id === winnerVersionId,
    "Project-version DB audit winner mismatch.",
  );

  const latencyConfig = (alpha.config || []).find(
    (item) => item?.id === "latency",
  );
  assert(
    latencyConfig?.is_visible === latencyVisible,
    "Project-version config latency visibility mismatch.",
  );
}

async function hardDeleteProjectVersionJourneyArtifacts({
  projectId,
  projectName,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(projectId)} AS project_id,
    ${sqlTextLiteral(projectName)} AS project_name
),
annotation_ids AS (
  SELECT pv.annotations_id AS id
  FROM tracer_project_version pv, requested
  WHERE pv.project_id = requested.project_id
    AND pv.annotations_id IS NOT NULL
),
deleted_winners AS (
  DELETE FROM tracer_project_version_winner
  WHERE project_id = (SELECT project_id FROM requested)
     OR winner_version_id IN (
       SELECT id FROM tracer_project_version
       WHERE project_id = (SELECT project_id FROM requested)
     )
  RETURNING 1
),
deleted_spans AS (
  DELETE FROM tracer_observation_span
  WHERE project_id = (SELECT project_id FROM requested)
  RETURNING 1
),
deleted_traces AS (
  DELETE FROM tracer_trace
  WHERE project_id = (SELECT project_id FROM requested)
  RETURNING 1
),
deleted_versions AS (
  DELETE FROM tracer_project_version
  WHERE project_id = (SELECT project_id FROM requested)
  RETURNING 1
),
deleted_scan_config AS (
  DELETE FROM tracer_trace_scan_config
  WHERE project_id = (SELECT project_id FROM requested)
  RETURNING 1
),
deleted_project AS (
  DELETE FROM tracer_project
  WHERE id = (SELECT project_id FROM requested)
    AND name = (SELECT project_name FROM requested)
  RETURNING 1
),
deleted_annotations AS (
  DELETE FROM model_hub_annotations
  WHERE id IN (SELECT id FROM annotation_ids)
  RETURNING 1
)
SELECT jsonb_build_object(
  'deleted_winner_count', (SELECT count(*) FROM deleted_winners),
  'deleted_span_count', (SELECT count(*) FROM deleted_spans),
  'deleted_trace_count', (SELECT count(*) FROM deleted_traces),
  'deleted_project_version_count', (SELECT count(*) FROM deleted_versions),
  'deleted_scan_config_count', (SELECT count(*) FROM deleted_scan_config),
  'deleted_project_count', (SELECT count(*) FROM deleted_project),
  'deleted_annotation_count', (SELECT count(*) FROM deleted_annotations),
  'project_count', CASE
    WHEN (SELECT count(*) FROM deleted_project) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_project
      WHERE id = (SELECT project_id FROM requested)
    )
  END,
  'project_version_count', CASE
    WHEN (SELECT count(*) FROM deleted_versions) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_project_version
      WHERE project_id = (SELECT project_id FROM requested)
    )
  END,
  'trace_count', CASE
    WHEN (SELECT count(*) FROM deleted_traces) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_trace
      WHERE project_id = (SELECT project_id FROM requested)
    )
  END,
  'span_count', CASE
    WHEN (SELECT count(*) FROM deleted_spans) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_observation_span
      WHERE project_id = (SELECT project_id FROM requested)
    )
  END,
  'annotation_count', CASE
    WHEN (SELECT count(*) FROM deleted_annotations) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM model_hub_annotations
      WHERE id IN (SELECT id FROM annotation_ids)
    )
  END,
  'scan_config_count', CASE
    WHEN (SELECT count(*) FROM deleted_scan_config) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_trace_scan_config
      WHERE project_id = (SELECT project_id FROM requested)
    )
  END
)::text;
`;
  return runPostgresJson(sql);
}

function findAuditRow(rows, id) {
  return (rows || []).find((row) => row?.id === id);
}

async function loadAccountContextDbAudit({
  userId,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(userId)} AS user_id,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id
),
user_row AS (
  SELECT u.id, u.email, u.organization_id, u.organization_role, u.is_active
  FROM accounts_user u
  JOIN requested r ON u.id = r.user_id
),
workspace_row AS (
  SELECT w.id, w.organization_id, w.is_active, w.is_default
  FROM accounts_workspace w
  JOIN requested r ON w.id = r.workspace_id
  WHERE w.deleted = false
),
org_membership_counts AS (
  SELECT
    count(*) FILTER (WHERE om.deleted = false) AS membership_count,
    count(*) FILTER (WHERE om.deleted = false AND om.is_active = true) AS active_membership_count
  FROM accounts_organization_membership om
  JOIN requested r ON om.user_id = r.user_id AND om.organization_id = r.organization_id
),
workspace_membership_counts AS (
  SELECT
    count(*) FILTER (WHERE wm.deleted = false) AS membership_count,
    count(*) FILTER (WHERE wm.deleted = false AND wm.is_active = true) AS active_membership_count
  FROM accounts_workspacemembership wm
  JOIN requested r ON wm.user_id = r.user_id AND wm.workspace_id = r.workspace_id
)
SELECT json_build_object(
  'user_id', user_row.id::text,
  'email', user_row.email,
  'user_organization_id', user_row.organization_id::text,
  'user_organization_role', user_row.organization_role,
  'user_active', user_row.is_active,
  'organization_membership_count', org_membership_counts.membership_count,
  'active_org_membership_count', org_membership_counts.active_membership_count,
  'workspace_id', workspace_row.id::text,
  'workspace_organization_id', workspace_row.organization_id::text,
  'workspace_active', workspace_row.is_active,
  'workspace_is_default', workspace_row.is_default,
  'workspace_membership_count', workspace_membership_counts.membership_count,
  'active_workspace_membership_count', workspace_membership_counts.active_membership_count
)
FROM requested
LEFT JOIN user_row ON true
LEFT JOIN workspace_row ON true
CROSS JOIN org_membership_counts
CROSS JOIN workspace_membership_counts;
`;
  return runPostgresJson(sql);
}

async function loadLegacyTeamMemberAudit({
  email,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    lower(${sqlTextLiteral(email)}) AS email,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id
),
user_rows AS (
  SELECT u.id, u.email, u.organization_id, u.organization_role, u.is_active, u.config
  FROM accounts_user u
  JOIN requested r ON lower(u.email) = r.email
),
workspace_memberships AS (
  SELECT wm.id, wm.user_id, wm.role, wm.level, wm.is_active, wm.deleted
  FROM accounts_workspacemembership wm
  JOIN user_rows u ON wm.user_id = u.id
  JOIN requested r ON wm.workspace_id = r.workspace_id
),
org_memberships AS (
  SELECT om.id, om.user_id, om.role, om.level, om.is_active, om.deleted
  FROM accounts_organization_membership om
  JOIN user_rows u ON om.user_id = u.id
  JOIN requested r ON om.organization_id = r.organization_id
),
auth_tokens AS (
  SELECT token.id, token.auth_type, token.is_active
  FROM accounts_auth_token token
  JOIN user_rows u ON token.user_id = u.id
)
SELECT json_build_object(
  'email', (SELECT email FROM requested),
  'user_count', (SELECT count(*) FROM user_rows),
  'user_id', (SELECT id::text FROM user_rows LIMIT 1),
  'user_active', (SELECT is_active FROM user_rows LIMIT 1),
  'user_organization_id', (SELECT organization_id::text FROM user_rows LIMIT 1),
  'user_organization_role', (SELECT organization_role FROM user_rows LIMIT 1),
  'selected_organization_id', (SELECT config->>'selected_organization_id' FROM user_rows LIMIT 1),
  'workspace_membership_count', (SELECT count(*) FROM workspace_memberships),
  'active_workspace_membership_count', (
    SELECT count(*) FROM workspace_memberships WHERE is_active = true AND deleted = false
  ),
  'inactive_workspace_membership_count', (
    SELECT count(*) FROM workspace_memberships WHERE is_active = false AND deleted = false
  ),
  'org_membership_count', (SELECT count(*) FROM org_memberships),
  'active_org_membership_count', (
    SELECT count(*) FROM org_memberships WHERE is_active = true AND deleted = false
  ),
  'active_access_token_count', (
    SELECT count(*) FROM auth_tokens WHERE auth_type = 'access' AND is_active = true
  ),
  'active_refresh_token_count', (
    SELECT count(*) FROM auth_tokens WHERE auth_type = 'refresh' AND is_active = true
  )
);
`;
  return runPostgresJson(sql);
}

async function loadWorkspaceSettingsDbAudit({
  userId,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(userId)} AS user_id,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id
),
workspace_row AS (
  SELECT w.id, w.name, w.display_name, w.description, w.organization_id, w.is_active, w.is_default
  FROM accounts_workspace w
  JOIN requested r ON w.id = r.workspace_id
),
current_membership AS (
  SELECT wm.user_id, wm.workspace_id, wm.role, wm.level, wm.is_active, wm.deleted
  FROM accounts_workspacemembership wm
  JOIN requested r ON wm.user_id = r.user_id
   AND wm.workspace_id = r.workspace_id
),
active_members AS (
  SELECT count(*) AS active_member_count
  FROM accounts_workspacemembership wm
  JOIN requested r ON wm.workspace_id = r.workspace_id
  WHERE wm.is_active = true AND wm.deleted = false
),
current_org_membership AS (
  SELECT om.role, om.level, om.is_active, om.deleted
  FROM accounts_organization_membership om
  JOIN requested r ON om.user_id = r.user_id
   AND om.organization_id = r.organization_id
)
SELECT json_build_object(
  'workspace_id', workspace_row.id::text,
  'name', workspace_row.name,
  'display_name', workspace_row.display_name,
  'description', workspace_row.description,
  'workspace_organization_id', workspace_row.organization_id::text,
  'workspace_active', workspace_row.is_active,
  'workspace_default', workspace_row.is_default,
  'active_member_count', active_members.active_member_count,
  'current_user_ws_role', (SELECT role FROM current_membership LIMIT 1),
  'current_user_ws_level', (SELECT level FROM current_membership LIMIT 1),
  'current_user_ws_active', (SELECT is_active FROM current_membership LIMIT 1),
  'current_user_ws_deleted', (SELECT deleted FROM current_membership LIMIT 1),
  'current_user_org_role', (SELECT role FROM current_org_membership LIMIT 1),
  'current_user_org_level', (SELECT level FROM current_org_membership LIMIT 1),
  'current_user_org_active', (SELECT is_active FROM current_org_membership LIMIT 1),
  'current_user_org_deleted', (SELECT deleted FROM current_org_membership LIMIT 1)
)
FROM workspace_row
CROSS JOIN active_members;
`;
  return runPostgresJson(sql);
}

async function restoreWorkspaceDisplayNameDb({ workspaceId, displayName }) {
  const sql = `
WITH updated_workspace AS (
  UPDATE accounts_workspace
  SET display_name = ${sqlTextLiteral(displayName)}
  WHERE id = ${sqlUuid(workspaceId)}
  RETURNING id, display_name
)
SELECT json_build_object(
  'updated_count', (SELECT count(*) FROM updated_workspace),
  'display_name', (SELECT display_name FROM updated_workspace LIMIT 1)
);
`;
  return runPostgresJson(sql);
}

async function seedWorkspaceUsageBoundaryData({
  marker,
  organizationId,
  userId,
}) {
  assert(
    isUuid(organizationId),
    "Usage boundary seed organization id must be a UUID.",
  );
  assert(isUuid(userId), "Usage boundary seed user id must be a UUID.");
  const sourceIdPrefix = `api-journey-usage-${marker}`;
  const script = `
import json
from datetime import datetime, timezone
from decimal import Decimal
from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from ee.usage.models.usage import APICallLog, APICallStatusChoices, APICallType, APICallTypeChoices

organization = Organization.objects.get(id=${JSON.stringify(organizationId)})
user = User.objects.get(id=${JSON.stringify(userId)})
call_type, _ = APICallType.objects.get_or_create(
    name=APICallTypeChoices.DATASET_EVALUATION.value,
    defaults={"description": "Dataset Evaluation"},
)
workspace = Workspace.objects.create(
    name=${JSON.stringify(sourceIdPrefix)},
    display_name=${JSON.stringify(`API Journey Usage ${marker}`)},
    organization=organization,
    created_by=user,
    is_active=True,
    is_default=False,
)

def create_log(suffix, cost, created_at):
    log = APICallLog.objects.create(
        organization=organization,
        workspace=workspace,
        user=user,
        api_call_type=call_type,
        cost=Decimal(cost),
        deducted_cost=Decimal(cost),
        status=APICallStatusChoices.SUCCESS.value,
        input_token_count=100,
        source=APICallTypeChoices.DATASET_EVALUATION.value,
        source_id=f"${sourceIdPrefix}-{suffix}",
    )
    APICallLog.all_objects.filter(id=log.id).update(created_at=created_at)
    return str(log.id)

april_log_id = create_log("april", "0.50", datetime(2026, 4, 30, 23, 0, tzinfo=timezone.utc))
may_log_id = create_log("may", "1.25", datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc))
print(json.dumps({
    "workspace_id": str(workspace.id),
    "workspace_name": workspace.name,
    "source_id_prefix": ${JSON.stringify(sourceIdPrefix)},
    "target_month": 4,
    "target_year": 2026,
    "expected_month_cost": 0.5,
    "leaking_next_month_cost": 1.25,
    "april_log_id": april_log_id,
    "may_log_id": may_log_id,
}))
`;
  return runBackendShellJson(script);
}

async function loadWorkspaceUsageBoundaryDbAudit({
  workspaceId,
  sourceIdPrefix,
  targetMonth,
  targetYear,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlTextLiteral(`${sourceIdPrefix}%`)} AS source_id_like,
    MAKE_DATE(${Number(targetYear)}, ${Number(targetMonth)}, 1)::timestamp with time zone AS month_start,
    (MAKE_DATE(${Number(targetYear)}, ${Number(targetMonth)}, 1) + INTERVAL '1 month')::timestamp with time zone AS month_end,
    (MAKE_DATE(${Number(targetYear)}, ${Number(targetMonth)}, 1) + INTERVAL '2 month')::timestamp with time zone AS next_month_end
),
usage_rows AS (
  SELECT log.id, log.deducted_cost, log.created_at
  FROM usage_apicalllog log
  JOIN requested r ON log.workspace_id = r.workspace_id
   AND log.source_id LIKE r.source_id_like
),
workspace_rows AS (
  SELECT workspace.id
  FROM accounts_workspace workspace
  JOIN requested r ON workspace.id = r.workspace_id
)
SELECT json_build_object(
  'workspace_count', (SELECT count(*) FROM workspace_rows),
  'total_log_count', (SELECT count(*) FROM usage_rows),
  'month_log_count', (
    SELECT count(*) FROM usage_rows, requested r
    WHERE created_at >= r.month_start AND created_at < r.month_end
  ),
  'month_deducted_cost', COALESCE((
    SELECT sum(deducted_cost)::float FROM usage_rows, requested r
    WHERE created_at >= r.month_start AND created_at < r.month_end
  ), 0),
  'next_month_log_count', (
    SELECT count(*) FROM usage_rows, requested r
    WHERE created_at >= r.month_end AND created_at < r.next_month_end
  ),
  'next_month_deducted_cost', COALESCE((
    SELECT sum(deducted_cost)::float FROM usage_rows, requested r
    WHERE created_at >= r.month_end AND created_at < r.next_month_end
  ), 0)
);
`;
  return runPostgresJson(sql);
}

async function deleteWorkspaceUsageBoundaryData({
  workspaceId,
  sourceIdPrefix,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlTextLiteral(`${sourceIdPrefix}%`)} AS source_id_like
),
deleted_logs AS (
  DELETE FROM usage_apicalllog log
  USING requested r
  WHERE log.workspace_id = r.workspace_id OR log.source_id LIKE r.source_id_like
  RETURNING log.id
),
deleted_workspaces AS (
  DELETE FROM accounts_workspace workspace
  USING requested r
  WHERE workspace.id = r.workspace_id
  RETURNING workspace.id
)
SELECT json_build_object(
  'deleted_logs', (SELECT count(*) FROM deleted_logs),
  'deleted_workspaces', (SELECT count(*) FROM deleted_workspaces)
);
`;
  return runPostgresJson(sql);
}

async function seedIntegrationConnectionData({
  marker,
  organizationId,
  workspaceId,
  userId,
  status = "error",
  includeProject = false,
  lastSyncedSecondsAgo = 420,
  displayNamePrefix = "API Journey Langfuse",
}) {
  assert(
    isUuid(organizationId),
    "Integration seed organization id must be a UUID.",
  );
  assert(isUuid(workspaceId), "Integration seed workspace id must be a UUID.");
  assert(isUuid(userId), "Integration seed user id must be a UUID.");
  const displayName = `${displayNamePrefix} ${marker}`;
  const externalProjectName = `api-journey-${marker}`;
  const publicKey = `pk-lf-${marker.slice(0, 12)}pub1234`;
  const secretKey = `sk-lf-${marker.slice(0, 12)}sec5678`;
  const script = `
import json
from datetime import datetime, timedelta, timezone
from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from integrations.models import ConnectionStatus, IntegrationConnection, IntegrationPlatform, SyncLog, SyncStatus
from integrations.services.credentials import CredentialManager
from tracer.models.project import Project

organization = Organization.objects.get(id=${JSON.stringify(organizationId)})
workspace = Workspace.objects.get(id=${JSON.stringify(workspaceId)}, organization=organization)
user = User.objects.get(id=${JSON.stringify(userId)})
project = None
if ${includeProject ? "True" : "False"}:
    project = Project.objects.filter(
        organization=organization,
        workspace=workspace,
        deleted=False,
    ).first()
    if project is None:
        raise RuntimeError("No active project exists in the current workspace for integration seeding")
credentials = {
    "public_key": ${JSON.stringify(publicKey)},
    "secret_key": ${JSON.stringify(secretKey)},
}
now = datetime.now(timezone.utc)
connection = IntegrationConnection.no_workspace_objects.create(
    organization=organization,
    workspace=workspace,
    created_by=user,
    platform=IntegrationPlatform.LANGFUSE,
    display_name=${JSON.stringify(displayName)},
    host_url="https://langfuse.example.com",
    encrypted_credentials=CredentialManager.encrypt(credentials),
    project=project,
    external_project_name=${JSON.stringify(externalProjectName)},
    status=${JSON.stringify(status)},
    status_message="" if ${JSON.stringify(status)} == ConnectionStatus.ACTIVE else "API journey seeded sync failure without secret values",
    last_synced_at=now - timedelta(seconds=${Number(lastSyncedSecondsAgo)}),
    sync_interval_seconds=300,
    backfill_completed=True,
    total_traces_synced=7,
    total_spans_synced=11,
    total_scores_synced=3,
)
sync_log = SyncLog.objects.create(
    connection=connection,
    status=SyncStatus.FAILED,
    started_at=now - timedelta(minutes=8),
    completed_at=now - timedelta(minutes=7),
    traces_fetched=7,
    traces_created=5,
    traces_updated=2,
    spans_synced=11,
    scores_synced=3,
    error_message="API journey seeded sync failure without secret values",
    error_details={"type": "ApiJourneySeed"},
    sync_from=now - timedelta(hours=1),
    sync_to=now,
)
print(json.dumps({
    "connection_id": str(connection.id),
    "sync_log_id": str(sync_log.id),
    "project_id": str(project.id) if project else None,
    "display_name": connection.display_name,
    "host_url": connection.host_url,
    "external_project_name": connection.external_project_name,
    "plain_public_key": ${JSON.stringify(publicKey)},
    "plain_secret_key": ${JSON.stringify(secretKey)},
    "expected_public_key_display": "pk-lf-****1234",
    "expected_secret_key_display": "sk-lf-****5678",
    "total_traces_synced": connection.total_traces_synced,
}))
`;
  return runBackendShellJson(script);
}

async function loadIntegrationConnectionDbAudit({
  connectionId,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(connectionId)} AS connection_id,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id
),
connection_rows AS (
  SELECT
    connection.id,
    connection.organization_id,
    connection.workspace_id,
    connection.platform,
    connection.display_name,
    connection.status,
    connection.sync_interval_seconds,
    connection.deleted,
    connection.deleted_at IS NOT NULL AS deleted_at_set,
    octet_length(connection.encrypted_credentials) AS encrypted_credentials_bytes
  FROM integrations_connection connection
  JOIN requested r ON connection.id = r.connection_id
),
sync_log_rows AS (
  SELECT log.id, log.connection_id
  FROM integrations_sync_log log
  JOIN requested r ON log.connection_id = r.connection_id
)
SELECT json_build_object(
  'connection_count', (SELECT count(*) FROM connection_rows),
  'sync_log_count', (SELECT count(*) FROM sync_log_rows),
  'organization_id', (SELECT organization_id::text FROM connection_rows LIMIT 1),
  'workspace_id', (SELECT workspace_id::text FROM connection_rows LIMIT 1),
  'platform', (SELECT platform FROM connection_rows LIMIT 1),
  'display_name', (SELECT display_name FROM connection_rows LIMIT 1),
  'status', (SELECT status FROM connection_rows LIMIT 1),
  'sync_interval_seconds', (SELECT sync_interval_seconds FROM connection_rows LIMIT 1),
  'deleted', (SELECT deleted FROM connection_rows LIMIT 1),
  'deleted_at_set', (SELECT deleted_at_set FROM connection_rows LIMIT 1),
  'encrypted_credentials_bytes', COALESCE((SELECT encrypted_credentials_bytes FROM connection_rows LIMIT 1), 0)
);
`;
  return runPostgresJson(sql);
}

async function deleteIntegrationConnectionData({ connectionId }) {
  const sql = `
WITH requested AS (
  SELECT ${sqlUuid(connectionId)} AS connection_id
),
deleted_logs AS (
  DELETE FROM integrations_sync_log log
  USING requested r
  WHERE log.connection_id = r.connection_id
  RETURNING log.id
),
deleted_connections AS (
  DELETE FROM integrations_connection connection
  USING requested r
  WHERE connection.id = r.connection_id
  RETURNING connection.id
)
SELECT json_build_object(
  'deleted_logs', (SELECT count(*) FROM deleted_logs),
  'deleted_connections', (SELECT count(*) FROM deleted_connections)
);
`;
  return runPostgresJson(sql);
}

async function loadProviderKeyDbAudit({ organizationId, workspaceId }) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id
),
workspace_row AS (
  SELECT workspace.id, workspace.organization_id, workspace.is_default
  FROM accounts_workspace workspace
  JOIN requested r ON workspace.id = r.workspace_id
),
scoped_keys AS (
  SELECT key.id, key.provider, key.key, key.config_json, key.workspace_id
  FROM model_hub_apikey key
  JOIN requested r ON key.organization_id = r.organization_id
  JOIN workspace_row workspace ON workspace.organization_id = r.organization_id
  WHERE key.deleted = false
    AND (
      (
        workspace.is_default = true
        AND (
          key.workspace_id = workspace.id
          OR key.workspace_id IS NULL
          OR key.workspace_id IN (
            SELECT default_workspace.id
            FROM accounts_workspace default_workspace
            WHERE default_workspace.organization_id = r.organization_id
              AND default_workspace.is_default = true
          )
        )
      )
      OR (
        workspace.is_default = false
        AND key.workspace_id = workspace.id
      )
    )
),
provider_counts AS (
  SELECT provider, count(*) AS row_count
  FROM scoped_keys
  GROUP BY provider
)
SELECT json_build_object(
  'active_key_count', (SELECT count(*) FROM scoped_keys),
  'distinct_provider_count', (SELECT count(*) FROM provider_counts),
  'duplicate_provider_row_count', COALESCE((SELECT sum(row_count - 1) FROM provider_counts WHERE row_count > 1), 0),
  'key_field_count', (SELECT count(*) FROM scoped_keys WHERE key IS NOT NULL),
  'config_json_count', (SELECT count(*) FROM scoped_keys WHERE config_json IS NOT NULL),
  'provider_counts', COALESCE((SELECT jsonb_object_agg(provider, row_count) FROM provider_counts), '{}'::jsonb)
);
`;
  return runPostgresJson(sql);
}

async function loadBillingLicenseDbAudit({ organizationId, email }) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    lower(${sqlTextLiteral(email)}) AS email
),
subscription_rows AS (
  SELECT
    sub.id,
    sub.plan,
    sub.billing_interval,
    sub.status,
    sub.wallet_balance
  FROM usage_organizationsubscription sub
  JOIN requested r ON sub.organization_id = r.organization_id
  WHERE sub.deleted = false
),
invoice_rows AS (
  SELECT invoice.id, invoice.total
  FROM usage_invoice invoice
  JOIN requested r ON invoice.organization_id = r.organization_id
  WHERE invoice.deleted = false
),
budget_rows AS (
  SELECT budget.id, budget.is_active
  FROM usage_usagebudget budget
  JOIN requested r ON budget.organization_id = r.organization_id
  WHERE budget.deleted = false
),
license_rows AS (
  SELECT license.id, license.status
  FROM usage_eelicensegrant license
  JOIN requested r ON lower(license.customer_email) = r.email
  WHERE license.deleted = false
)
SELECT json_build_object(
  'subscription_count', (SELECT count(*) FROM subscription_rows),
  'subscription_plan', (SELECT plan FROM subscription_rows LIMIT 1),
  'subscription_billing_interval', (SELECT billing_interval FROM subscription_rows LIMIT 1),
  'subscription_status', (SELECT status FROM subscription_rows LIMIT 1),
  'invoice_count', (SELECT count(*) FROM invoice_rows),
  'invoice_total_sum', COALESCE((SELECT sum(total)::float FROM invoice_rows), 0),
  'budget_count', (SELECT count(*) FROM budget_rows),
  'active_budget_count', (SELECT count(*) FROM budget_rows WHERE is_active = true),
  'email_license_count', (SELECT count(*) FROM license_rows),
  'active_email_license_count', (SELECT count(*) FROM license_rows WHERE status = 'active'),
  'revoked_email_license_count', (SELECT count(*) FROM license_rows WHERE status = 'revoked')
);
`;
  return runPostgresJson(sql);
}

async function loadMCPSettingsDbAudit({ userId, organizationId, workspaceId }) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(userId)} AS user_id,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id
),
connection_rows AS (
  SELECT
    conn.id,
    conn.user_id,
    conn.organization_id,
    conn.workspace_id,
    conn.connection_mode,
    conn.is_active,
    conn.oauth_token_encrypted,
    conn.oauth_refresh_token_encrypted,
    conn.api_key_id
  FROM mcp_server_mcpconnection conn
  JOIN requested r
    ON conn.user_id = r.user_id
   AND conn.organization_id = r.organization_id
   AND conn.workspace_id = r.workspace_id
  WHERE conn.deleted = false
),
tool_config_rows AS (
  SELECT cfg.id, cfg.enabled_groups, cfg.disabled_tools
  FROM mcp_server_mcptoolgroupconfig cfg
  JOIN connection_rows conn ON cfg.connection_id = conn.id
  WHERE cfg.deleted = false
),
session_rows AS (
  SELECT session.id, session.status
  FROM mcp_server_mcpsession session
  JOIN requested r ON session.organization_id = r.organization_id
),
usage_rows AS (
  SELECT usage.id, usage.tool_name, usage.response_status
  FROM mcp_server_mcpusagerecord usage
  JOIN requested r ON usage.organization_id = r.organization_id
)
SELECT json_build_object(
  'connection_count', (SELECT count(*) FROM connection_rows),
  'connection_id', (SELECT id::text FROM connection_rows LIMIT 1),
  'connection_user_id', (SELECT user_id::text FROM connection_rows LIMIT 1),
  'connection_organization_id', (SELECT organization_id::text FROM connection_rows LIMIT 1),
  'connection_workspace_id', (SELECT workspace_id::text FROM connection_rows LIMIT 1),
  'connection_mode', (SELECT connection_mode FROM connection_rows LIMIT 1),
  'connection_is_active', (SELECT is_active FROM connection_rows LIMIT 1),
  'tool_config_count', (SELECT count(*) FROM tool_config_rows),
  'enabled_groups', COALESCE((SELECT enabled_groups FROM tool_config_rows LIMIT 1), '[]'::jsonb),
  'disabled_tools', COALESCE((SELECT disabled_tools FROM tool_config_rows LIMIT 1), '[]'::jsonb),
  'session_count', (SELECT count(*) FROM session_rows),
  'active_session_count', (SELECT count(*) FROM session_rows WHERE status = 'active'),
  'revoked_session_count', (SELECT count(*) FROM session_rows WHERE status = 'revoked'),
  'usage_count', (SELECT count(*) FROM usage_rows),
  'distinct_usage_tool_count', (SELECT count(DISTINCT tool_name) FROM usage_rows),
  'error_usage_count', (SELECT count(*) FROM usage_rows WHERE response_status = 'error'),
  'encrypted_token_count', (SELECT count(*) FROM connection_rows WHERE oauth_token_encrypted IS NOT NULL),
  'encrypted_refresh_token_count', (SELECT count(*) FROM connection_rows WHERE oauth_refresh_token_encrypted IS NOT NULL),
  'api_key_connection_count', (SELECT count(*) FROM connection_rows WHERE api_key_id IS NOT NULL)
);
`;
  return runPostgresJson(sql);
}

async function createDisposableMCPSessionDb({
  sessionId,
  connectionId,
  userId,
  organizationId,
  workspaceId,
  clientName,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(sessionId)} AS session_id,
    ${sqlUuid(connectionId)} AS connection_id,
    ${sqlUuid(userId)} AS user_id,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlTextLiteral(clientName)} AS client_name
),
inserted AS (
  INSERT INTO mcp_server_mcpsession (
    id,
    connection_id,
    user_id,
    organization_id,
    workspace_id,
    status,
    transport,
    client_name,
    client_version,
    client_os,
    started_at,
    last_activity_at,
    ended_at,
    tool_call_count,
    error_count
  )
  SELECT
    r.session_id,
    r.connection_id,
    r.user_id,
    r.organization_id,
    r.workspace_id,
    'active',
    'stdio',
    r.client_name,
    'api-journey',
    'local',
    now(),
    now(),
    NULL,
    0,
    0
  FROM requested r
  JOIN mcp_server_mcpconnection conn
    ON conn.id = r.connection_id
   AND conn.user_id = r.user_id
   AND conn.organization_id = r.organization_id
   AND conn.workspace_id = r.workspace_id
   AND conn.deleted = false
  RETURNING id, status, transport, client_name, connection_id, user_id, organization_id, workspace_id
)
SELECT json_build_object(
  'inserted_session_count', (SELECT count(*) FROM inserted),
  'session_id', (SELECT id::text FROM inserted LIMIT 1),
  'status', (SELECT status FROM inserted LIMIT 1),
  'transport', (SELECT transport FROM inserted LIMIT 1),
  'client_name', (SELECT client_name FROM inserted LIMIT 1),
  'connection_id', (SELECT connection_id::text FROM inserted LIMIT 1),
  'user_id', (SELECT user_id::text FROM inserted LIMIT 1),
  'organization_id', (SELECT organization_id::text FROM inserted LIMIT 1),
  'workspace_id', (SELECT workspace_id::text FROM inserted LIMIT 1)
);
`;
  return runPostgresJson(sql);
}

async function loadDisposableMCPSessionDbAudit({
  sessionId,
  connectionId,
  userId,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(sessionId)} AS session_id,
    ${sqlUuid(connectionId)} AS connection_id,
    ${sqlUuid(userId)} AS user_id,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id
),
session_rows AS (
  SELECT
    session.id,
    session.connection_id,
    session.user_id,
    session.organization_id,
    session.workspace_id,
    session.status,
    session.transport,
    session.client_name,
    session.ended_at IS NOT NULL AS ended_at_set,
    session.tool_call_count,
    session.error_count
  FROM mcp_server_mcpsession session
  JOIN requested r ON session.id = r.session_id
)
SELECT json_build_object(
  'session_count', (SELECT count(*) FROM session_rows),
  'session_id', (SELECT id::text FROM session_rows LIMIT 1),
  'connection_id', (SELECT connection_id::text FROM session_rows LIMIT 1),
  'user_id', (SELECT user_id::text FROM session_rows LIMIT 1),
  'organization_id', (SELECT organization_id::text FROM session_rows LIMIT 1),
  'workspace_id', (SELECT workspace_id::text FROM session_rows LIMIT 1),
  'matches_requested_context', COALESCE((
    SELECT connection_id = (SELECT connection_id FROM requested)
       AND user_id = (SELECT user_id FROM requested)
       AND organization_id = (SELECT organization_id FROM requested)
       AND workspace_id = (SELECT workspace_id FROM requested)
    FROM session_rows
    LIMIT 1
  ), false),
  'status', (SELECT status FROM session_rows LIMIT 1),
  'transport', (SELECT transport FROM session_rows LIMIT 1),
  'client_name', (SELECT client_name FROM session_rows LIMIT 1),
  'ended_at_set', COALESCE((SELECT ended_at_set FROM session_rows LIMIT 1), false),
  'tool_call_count', (SELECT tool_call_count FROM session_rows LIMIT 1),
  'error_count', (SELECT error_count FROM session_rows LIMIT 1)
);
`;
  return runPostgresJson(sql);
}

async function deleteDisposableMCPSessionDb({ sessionId }) {
  const deleted = await runPostgresJson(`
WITH requested AS (
  SELECT ${sqlUuid(sessionId)} AS session_id
),
deleted_usage AS (
  DELETE FROM mcp_server_mcpusagerecord usage
  USING requested r
  WHERE usage.session_id = r.session_id
  RETURNING usage.id
),
deleted_session AS (
  DELETE FROM mcp_server_mcpsession session
  USING requested r
  WHERE session.id = r.session_id
  RETURNING session.id
)
SELECT json_build_object(
  'deleted_usage_count', (SELECT count(*) FROM deleted_usage),
  'deleted_session_count', (SELECT count(*) FROM deleted_session)
);
`);
  const residue = await runPostgresJson(`
WITH requested AS (
  SELECT ${sqlUuid(sessionId)} AS session_id
)
SELECT json_build_object(
  'remaining_session_count', (
    SELECT count(*)
    FROM mcp_server_mcpsession session, requested r
    WHERE session.id = r.session_id
  ),
  'remaining_usage_count', (
    SELECT count(*)
    FROM mcp_server_mcpusagerecord usage, requested r
    WHERE usage.session_id = r.session_id
  )
);
`);
  return { ...deleted, ...residue };
}

async function loadFalconConnectorDbAudit({
  connectorId,
  organizationId,
  workspaceId,
  userId,
  rawSecret,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(connectorId)} AS connector_id,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(userId)} AS user_id,
    ${sqlTextLiteral(rawSecret)} AS raw_secret
),
connector_rows AS (
  SELECT
    connector.id,
    connector.name,
    connector.server_url,
    connector.transport,
    connector.auth_type,
    connector.auth_header_name,
    connector.auth_header_value,
    connector.organization_id,
    connector.workspace_id,
    connector.created_by_id,
    connector.is_active,
    connector.is_verified,
    connector.discovered_tools,
    connector.enabled_tool_names,
    connector.deleted,
    connector.deleted_at IS NOT NULL AS deleted_at_set
  FROM falcon_ai_mcpconnector connector
  JOIN requested r ON connector.id = r.connector_id
)
SELECT json_build_object(
  'connector_count', (SELECT count(*) FROM connector_rows),
  'id', (SELECT id::text FROM connector_rows LIMIT 1),
  'name', (SELECT name FROM connector_rows LIMIT 1),
  'server_url', (SELECT server_url FROM connector_rows LIMIT 1),
  'transport', (SELECT transport FROM connector_rows LIMIT 1),
  'auth_type', (SELECT auth_type FROM connector_rows LIMIT 1),
  'auth_header_name', (SELECT auth_header_name FROM connector_rows LIMIT 1),
  'organization_id', (SELECT organization_id::text FROM connector_rows LIMIT 1),
  'workspace_id', (SELECT workspace_id::text FROM connector_rows LIMIT 1),
  'created_by_id', (SELECT created_by_id::text FROM connector_rows LIMIT 1),
  'is_active', (SELECT is_active FROM connector_rows LIMIT 1),
  'is_verified', (SELECT is_verified FROM connector_rows LIMIT 1),
  'deleted', (SELECT deleted FROM connector_rows LIMIT 1),
  'deleted_at_set', (SELECT deleted_at_set FROM connector_rows LIMIT 1),
  'auth_value_is_encrypted',
    COALESCE((SELECT auth_header_value LIKE 'enc::%' FROM connector_rows LIMIT 1), false),
  'auth_value_hash',
    COALESCE((SELECT md5(auth_header_value) FROM connector_rows LIMIT 1), ''),
  'auth_value_contains_raw_secret',
    COALESCE((SELECT position((SELECT raw_secret FROM requested) in auth_header_value) > 0 FROM connector_rows LIMIT 1), false),
  'discovered_tool_count',
    COALESCE((SELECT jsonb_array_length(discovered_tools::jsonb) FROM connector_rows LIMIT 1), 0),
  'enabled_tool_count',
    COALESCE((SELECT jsonb_array_length(enabled_tool_names::jsonb) FROM connector_rows LIMIT 1), 0),
  'enabled_tool_names',
    COALESCE((SELECT enabled_tool_names FROM connector_rows LIMIT 1), '[]'::jsonb)
);
`;
  return runPostgresJson(sql);
}

async function seedFalconConnectorDb({
  name,
  serverUrl,
  transport,
  authType,
  authHeaderName,
  rawSecret,
  organizationId,
  workspaceId,
  userId,
}) {
  const connectorId = randomUUID();
  const encryptedSecret = await encryptFalconConnectorSecret(rawSecret);
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(connectorId)} AS connector_id,
    ${sqlTextLiteral(name)} AS name,
    ${sqlTextLiteral(serverUrl)} AS server_url,
    ${sqlTextLiteral(transport)} AS transport,
    ${sqlTextLiteral(authType)} AS auth_type,
    ${sqlTextLiteral(authHeaderName)} AS auth_header_name,
    ${sqlTextLiteral(encryptedSecret)} AS auth_header_value,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(userId)} AS user_id
),
deleted_existing AS (
  DELETE FROM falcon_ai_mcpconnector connector
  USING requested r
  WHERE connector.organization_id = r.organization_id
    AND connector.workspace_id = r.workspace_id
    AND connector.name = r.name
),
inserted AS (
  INSERT INTO falcon_ai_mcpconnector (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    server_url,
    transport,
    auth_type,
    auth_header_name,
    auth_header_value,
    is_active,
    is_verified,
    discovered_tools,
    enabled_tool_names,
    last_discovery_at,
    last_error,
    created_by_id,
    organization_id,
    workspace_id,
    oauth_client_id,
    oauth_client_secret,
    oauth_server_metadata,
    oauth_access_token,
    oauth_refresh_token,
    oauth_token_expires_at,
    oauth_code_verifier,
    oauth_state
  )
  SELECT
    NOW(),
    NOW(),
    false,
    NULL,
    connector_id,
    name,
    server_url,
    transport,
    auth_type,
    auth_header_name,
    auth_header_value,
    true,
    false,
    '[]'::jsonb,
    '[]'::jsonb,
    NULL,
    '',
    user_id,
    organization_id,
    workspace_id,
    '',
    '',
    '{}'::jsonb,
    '',
    '',
    NULL,
    '',
    ''
  FROM requested
  RETURNING
    id,
    name,
    server_url,
    transport,
    auth_type,
    auth_header_name,
    is_active,
    is_verified
)
SELECT json_build_object(
  'id', (SELECT id::text FROM inserted LIMIT 1),
  'name', (SELECT name FROM inserted LIMIT 1),
  'server_url', (SELECT server_url FROM inserted LIMIT 1),
  'transport', (SELECT transport FROM inserted LIMIT 1),
  'auth_type', (SELECT auth_type FROM inserted LIMIT 1),
  'auth_header_name', (SELECT auth_header_name FROM inserted LIMIT 1),
  'is_active', (SELECT is_active FROM inserted LIMIT 1),
  'is_verified', (SELECT is_verified FROM inserted LIMIT 1),
  'seeded', true
);
`;
  return runPostgresJson(sql);
}

async function encryptFalconConnectorSecret(rawSecret) {
  const script = `
import json
from agentcc.services.credential_manager import encrypt_token

print(json.dumps({
    "encrypted": encrypt_token(${JSON.stringify(rawSecret)}),
}))
`;
  const encrypted = await runBackendShellJson(script);
  assert(
    typeof encrypted?.encrypted === "string" &&
      encrypted.encrypted.startsWith("enc::"),
    "Falcon connector secret encryption helper did not return encrypted text.",
  );
  return encrypted.encrypted;
}

function assertFalconConnectorDbAudit(
  audit,
  {
    name,
    organizationId,
    workspaceId,
    userId,
    deleted,
    expectedEnabledToolCount,
  },
) {
  assert(
    audit.connector_count === 1,
    "Falcon connector DB audit found no row.",
  );
  assert(audit.name === name, "Falcon connector DB audit name mismatch.");
  assert(
    audit.organization_id === organizationId,
    "Falcon connector DB audit organization mismatch.",
  );
  assert(
    audit.workspace_id === workspaceId,
    "Falcon connector DB audit workspace mismatch.",
  );
  assert(
    audit.created_by_id === userId,
    "Falcon connector DB audit created_by mismatch.",
  );
  assert(
    audit.deleted === deleted,
    "Falcon connector DB audit deleted flag mismatch.",
  );
  assert(
    Number(audit.enabled_tool_count) === expectedEnabledToolCount,
    "Falcon connector DB audit enabled tool count mismatch.",
  );
}

async function seedFalconConnectorToolsDb({
  connectorId,
  organizationId,
  workspaceId,
  tools,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(connectorId)} AS connector_id,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlJson(tools)} AS tools
),
updated AS (
  UPDATE falcon_ai_mcpconnector connector
  SET
    discovered_tools = requested.tools,
    is_verified = true,
    last_discovery_at = NOW(),
    updated_at = NOW()
  FROM requested
  WHERE connector.id = requested.connector_id
    AND connector.organization_id = requested.organization_id
    AND connector.workspace_id = requested.workspace_id
    AND connector.deleted = false
  RETURNING connector.id, connector.discovered_tools
)
SELECT json_build_object(
  'updated_connector_count', (SELECT count(*) FROM updated),
  'discovered_tool_count',
    COALESCE((SELECT jsonb_array_length(discovered_tools::jsonb) FROM updated LIMIT 1), 0)
);
`;
  return runPostgresJson(sql);
}

async function seedOtherWorkspaceFalconConnectorDb({
  namePrefix,
  organizationId,
  userId,
}) {
  const workspaceId = randomUUID();
  const connectorId = randomUUID();
  const toolName = `${namePrefix}_other_workspace_tool`;
  const workspaceName = `${namePrefix}_other_workspace`;
  const connectorName = `${namePrefix}_other_workspace_connector`;
  const sql = `
WITH inserted_workspace AS (
  INSERT INTO accounts_workspace (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    display_name,
    description,
    is_active,
    is_default,
    created_by_id,
    organization_id
  )
  VALUES (
    NOW(),
    NOW(),
    false,
    NULL,
    ${sqlUuid(workspaceId)},
    ${sqlTextLiteral(workspaceName)},
    ${sqlTextLiteral(workspaceName)},
    ${sqlTextLiteral("Temporary workspace for Falcon connector API journey.")},
    true,
    false,
    ${sqlUuid(userId)},
    ${sqlUuid(organizationId)}
  )
  RETURNING id, name
),
inserted_connector AS (
  INSERT INTO falcon_ai_mcpconnector (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    server_url,
    transport,
    auth_type,
    auth_header_name,
    auth_header_value,
    is_active,
    is_verified,
    discovered_tools,
    enabled_tool_names,
    last_discovery_at,
    last_error,
    created_by_id,
    organization_id,
    workspace_id,
    oauth_client_id,
    oauth_client_secret,
    oauth_server_metadata,
    oauth_access_token,
    oauth_refresh_token,
    oauth_token_expires_at,
    oauth_code_verifier,
    oauth_state
  )
  SELECT
    NOW(),
    NOW(),
    false,
    NULL,
    ${sqlUuid(connectorId)},
    ${sqlTextLiteral(connectorName)},
    'https://example.com/futureagi-api-journey-other-workspace-mcp',
    'streamable_http',
    'none',
    'Authorization',
    '',
    true,
    true,
    ${sqlJson([
      {
        name: toolName,
        description: "Temporary other-workspace Falcon connector tool.",
      },
    ])},
    '[]'::jsonb,
    NOW(),
    '',
    ${sqlUuid(userId)},
    ${sqlUuid(organizationId)},
    id,
    '',
    '',
    '{}'::jsonb,
    '',
    '',
    NULL,
    '',
    ''
  FROM inserted_workspace
  RETURNING id, workspace_id, name
)
SELECT json_build_object(
  'workspace_id', (SELECT workspace_id::text FROM inserted_connector LIMIT 1),
  'workspace_name', (SELECT name FROM inserted_workspace LIMIT 1),
  'connector_id', (SELECT id::text FROM inserted_connector LIMIT 1),
  'connector_name', (SELECT name FROM inserted_connector LIMIT 1),
  'tool_name', ${sqlTextLiteral(toolName)}
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteFalconConnectorFixturesDb({
  namePrefix,
  organizationId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlTextLiteral(`${namePrefix}%`)} AS name_like,
    ${sqlUuid(organizationId)} AS organization_id
),
target_connectors AS (
  SELECT connector.id
  FROM falcon_ai_mcpconnector connector
  JOIN requested r
    ON connector.organization_id = r.organization_id
   AND connector.name LIKE r.name_like
),
deleted_connectors AS (
  DELETE FROM falcon_ai_mcpconnector connector
  USING target_connectors target
  WHERE connector.id = target.id
  RETURNING connector.id
),
target_workspaces AS (
  SELECT workspace.id
  FROM accounts_workspace workspace
  JOIN requested r
    ON workspace.organization_id = r.organization_id
   AND workspace.name LIKE ${sqlTextLiteral(`${namePrefix}%other_workspace%`)}
),
deleted_workspaces AS (
  DELETE FROM accounts_workspace workspace
  USING target_workspaces target
  WHERE workspace.id = target.id
  RETURNING workspace.id
)
SELECT json_build_object(
  'deleted_connector_count', (SELECT count(*) FROM deleted_connectors),
  'remaining_connector_count',
    (SELECT count(*) FROM target_connectors) - (SELECT count(*) FROM deleted_connectors),
  'deleted_workspace_count', (SELECT count(*) FROM deleted_workspaces),
  'remaining_workspace_count',
    (SELECT count(*) FROM target_workspaces) - (SELECT count(*) FROM deleted_workspaces)
);
`;
  return runPostgresJson(sql);
}

function assertFalconConversationDetail(
  detail,
  { conversationId, title, workspaceId, messageCount },
) {
  assert(
    detail?.id === conversationId &&
      detail.title === title &&
      detail.workspace === workspaceId,
    "Falcon conversation detail did not return the requested workspace row.",
  );
  assert(
    detail.metadata && typeof detail.metadata === "object",
    "Falcon conversation detail omitted metadata object.",
  );
  const messages = asArray(detail.messages);
  assert(
    messages.length === messageCount,
    `Falcon conversation detail expected ${messageCount} messages but got ${messages.length}.`,
  );
  for (const message of messages) {
    assert(isUuid(message?.id), "Falcon message omitted valid id.");
    assert(
      message.conversation === conversationId,
      "Falcon message did not point at the detail conversation.",
    );
    assert(
      ["user", "assistant", "system"].includes(message.role),
      `Falcon message returned invalid role ${message.role}.`,
    );
    assert(
      typeof message.content === "string",
      "Falcon message content was not a string.",
    );
    assert(
      Array.isArray(message.thoughts) && Array.isArray(message.tool_calls),
      "Falcon message omitted thoughts/tool_calls arrays.",
    );
    assert(Array.isArray(message.files), "Falcon message omitted files array.");
  }
}

async function seedFalconConversationMessagesDb({ conversationId, messages }) {
  const rows = messages.map((message, index) => {
    const messageId = randomUUID();
    return {
      id: messageId,
      sql: `(
    NOW() + (${index} * interval '1 millisecond'),
    NOW() + (${index} * interval '1 millisecond'),
    false,
    NULL,
    ${sqlUuid(messageId)},
    ${sqlTextLiteral(message.role)},
    ${sqlTextLiteral(message.content)},
    ${sqlJson(message.thoughts || [])},
    ${sqlJson(message.toolCalls || [])},
    ${message.completionCard == null ? "NULL" : sqlJson(message.completionCard)},
    ${sqlTextLiteral(message.feedback || "")},
    ${Number(message.tokenCount || 0)},
    ${sqlTextLiteral(message.modelUsed || "")},
    ${Number(message.latencyMs || 0)},
    ${sqlUuid(conversationId)},
    ${Number(message.inputTokens || 0)},
    ${Number(message.outputTokens || 0)},
    ${sqlJson(message.files || [])}
  )`,
    };
  });
  const sql = `
WITH inserted AS (
  INSERT INTO falcon_ai_message (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    role,
    content,
    thoughts,
    tool_calls,
    completion_card,
    feedback,
    token_count,
    model_used,
    latency_ms,
    conversation_id,
    input_tokens,
    output_tokens,
    files
  )
  VALUES
  ${rows.map((row) => row.sql).join(",\n")}
  RETURNING id, created_at
)
SELECT json_build_object(
  'inserted_message_count', (SELECT count(*) FROM inserted),
  'message_ids',
    COALESCE((SELECT json_agg(id::text ORDER BY created_at) FROM inserted), '[]'::json)
);
`;
  return runPostgresJson(sql);
}

async function seedHiddenFalconConversationDb({
  title,
  organizationId,
  workspaceId,
  userId,
}) {
  const conversationId = randomUUID();
  const sql = `
WITH inserted AS (
  INSERT INTO falcon_ai_conversation (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    title,
    context_page,
    metadata,
    organization_id,
    user_id,
    workspace_id,
    mode,
    active_skill_id,
    context_summary,
    total_tokens
  )
  VALUES (
    NOW(),
    NOW(),
    false,
    NULL,
    ${sqlUuid(conversationId)},
    ${sqlTextLiteral(title)},
    '/dashboard/falcon-ai',
    ${sqlJson({ hidden: true, source: "api-journey" })},
    ${sqlUuid(organizationId)},
    ${sqlUuid(userId)},
    ${sqlUuid(workspaceId)},
    '',
    NULL,
    '',
    0
  )
  RETURNING id, title
)
SELECT json_build_object(
  'id', (SELECT id::text FROM inserted LIMIT 1),
  'title', (SELECT title FROM inserted LIMIT 1),
  'hidden', true
);
`;
  return runPostgresJson(sql);
}

async function seedOtherWorkspaceFalconConversationDb({
  titlePrefix,
  organizationId,
  userId,
}) {
  const workspaceId = randomUUID();
  const conversationId = randomUUID();
  const messageId = randomUUID();
  const workspaceName = `${titlePrefix} other workspace`;
  const title = `${titlePrefix} other workspace conversation`;
  const sql = `
WITH inserted_workspace AS (
  INSERT INTO accounts_workspace (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    display_name,
    description,
    is_active,
    is_default,
    created_by_id,
    organization_id
  )
  VALUES (
    NOW(),
    NOW(),
    false,
    NULL,
    ${sqlUuid(workspaceId)},
    ${sqlTextLiteral(workspaceName)},
    ${sqlTextLiteral(workspaceName)},
    ${sqlTextLiteral("Temporary workspace for Falcon conversation API journey.")},
    true,
    false,
    ${sqlUuid(userId)},
    ${sqlUuid(organizationId)}
  )
  RETURNING id, name
),
inserted_conversation AS (
  INSERT INTO falcon_ai_conversation (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    title,
    context_page,
    metadata,
    organization_id,
    user_id,
    workspace_id,
    mode,
    active_skill_id,
    context_summary,
    total_tokens
  )
  VALUES (
    NOW(),
    NOW(),
    false,
    NULL,
    ${sqlUuid(conversationId)},
    ${sqlTextLiteral(title)},
    '/dashboard/falcon-ai',
    ${sqlJson({ source: "api-journey-other-workspace" })},
    ${sqlUuid(organizationId)},
    ${sqlUuid(userId)},
    ${sqlUuid(workspaceId)},
    '',
    NULL,
    '',
    0
  )
  RETURNING id, workspace_id, title
),
inserted_message AS (
  INSERT INTO falcon_ai_message (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    role,
    content,
    thoughts,
    tool_calls,
    completion_card,
    feedback,
    token_count,
    model_used,
    latency_ms,
    conversation_id,
    input_tokens,
    output_tokens,
    files
  )
  VALUES (
    NOW(),
    NOW(),
    false,
    NULL,
    ${sqlUuid(messageId)},
    'assistant',
    ${sqlTextLiteral("Other workspace Falcon response should not be visible.")},
    '[]'::jsonb,
    '[]'::jsonb,
    NULL,
    '',
    4,
    '',
    0,
    ${sqlUuid(conversationId)},
    1,
    3,
    '[]'::jsonb
  )
  RETURNING id
)
SELECT json_build_object(
  'workspace_id', (SELECT id::text FROM inserted_workspace LIMIT 1),
  'workspace_name', (SELECT name FROM inserted_workspace LIMIT 1),
  'conversation_id', (SELECT id::text FROM inserted_conversation LIMIT 1),
  'conversation_title', (SELECT title FROM inserted_conversation LIMIT 1),
  'message_id', (SELECT id::text FROM inserted_message LIMIT 1)
);
`;
  return runPostgresJson(sql);
}

async function loadFalconConversationDbAudit({
  conversationIds,
  messageIds,
  organizationId,
  workspaceId,
  userId,
}) {
  const sql = `
WITH requested_conversations AS (
  SELECT unnest(${sqlUuidArray(conversationIds)}) AS conversation_id
),
requested_messages AS (
  SELECT unnest(${sqlUuidArray(messageIds)}) AS message_id
),
conversation_rows AS (
  SELECT conversation.*
  FROM falcon_ai_conversation conversation
  JOIN requested_conversations requested
    ON conversation.id = requested.conversation_id
  WHERE conversation.organization_id = ${sqlUuid(organizationId)}
    AND conversation.user_id = ${sqlUuid(userId)}
),
message_rows AS (
  SELECT message.*
  FROM falcon_ai_message message
  JOIN requested_messages requested
    ON message.id = requested.message_id
)
SELECT json_build_object(
  'conversation_count', (SELECT count(*) FROM conversation_rows),
  'active_conversation_count',
    (SELECT count(*) FROM conversation_rows WHERE deleted = false),
  'deleted_conversation_count',
    (SELECT count(*) FROM conversation_rows WHERE deleted = true),
  'deleted_at_count',
    (SELECT count(*) FROM conversation_rows WHERE deleted = true AND deleted_at IS NOT NULL),
  'active_workspace_conversation_count',
    (SELECT count(*) FROM conversation_rows WHERE workspace_id = ${sqlUuid(workspaceId)}),
  'other_workspace_conversation_count',
    (SELECT count(*) FROM conversation_rows WHERE workspace_id <> ${sqlUuid(workspaceId)}),
  'hidden_conversation_count',
    (SELECT count(*) FROM conversation_rows WHERE metadata @> '{"hidden": true}'::jsonb),
  'message_count', (SELECT count(*) FROM message_rows),
  'message_conversation_count',
    (SELECT count(DISTINCT conversation_id) FROM message_rows),
  'feedback_thumbs_up_count',
    (SELECT count(*) FROM message_rows WHERE feedback = 'thumbs_up')
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteFalconConversationFixturesDb({
  titlePrefix,
  organizationId,
}) {
  const sql = `
WITH target_conversations AS (
  SELECT id
  FROM falcon_ai_conversation
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND title LIKE ${sqlTextLiteral(`${titlePrefix}%`)}
),
target_messages AS (
  SELECT message.id
  FROM falcon_ai_message message
  JOIN target_conversations conversation
    ON message.conversation_id = conversation.id
),
deleted_messages AS (
  DELETE FROM falcon_ai_message message
  USING target_messages target
  WHERE message.id = target.id
  RETURNING message.id
),
deleted_conversations AS (
  DELETE FROM falcon_ai_conversation conversation
  USING target_conversations target
  WHERE conversation.id = target.id
  RETURNING conversation.id
),
target_workspaces AS (
  SELECT id
  FROM accounts_workspace
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlTextLiteral(`${titlePrefix}% other workspace%`)}
),
deleted_workspaces AS (
  DELETE FROM accounts_workspace workspace
  USING target_workspaces target
  WHERE workspace.id = target.id
  RETURNING workspace.id
)
SELECT json_build_object(
  'deleted_conversation_count', (SELECT count(*) FROM deleted_conversations),
  'remaining_conversation_count',
    (SELECT count(*) FROM target_conversations) - (SELECT count(*) FROM deleted_conversations),
  'deleted_message_count', (SELECT count(*) FROM deleted_messages),
  'remaining_message_count',
    (SELECT count(*) FROM target_messages) - (SELECT count(*) FROM deleted_messages),
  'deleted_workspace_count', (SELECT count(*) FROM deleted_workspaces),
  'remaining_workspace_count',
    (SELECT count(*) FROM target_workspaces) - (SELECT count(*) FROM deleted_workspaces)
);
`;
  return runPostgresJson(sql);
}

async function seedFalconMemorySkillIsolationDb({
  marker,
  organizationId,
  userId,
}) {
  const workspaceId = randomUUID();
  const memoryId = randomUUID();
  const skillId = randomUUID();
  const builtinSkillId = randomUUID();
  const workspaceName = `${marker}-other-workspace`;
  const otherMemoryKey = `${marker}-other-memory`;
  const otherSkillSlug = `${marker}-other-skill`;
  const builtinSlug = `${marker}-builtin`;
  const sql = `
WITH inserted_workspace AS (
  INSERT INTO accounts_workspace (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    display_name,
    description,
    is_active,
    is_default,
    created_by_id,
    organization_id
  )
  VALUES (
    NOW(),
    NOW(),
    false,
    NULL,
    ${sqlUuid(workspaceId)},
    ${sqlTextLiteral(workspaceName)},
    ${sqlTextLiteral(workspaceName)},
    ${sqlTextLiteral("Temporary workspace for Falcon memory and skill API journey.")},
    true,
    false,
    ${sqlUuid(userId)},
    ${sqlUuid(organizationId)}
  )
  RETURNING id, name
),
inserted_memory AS (
  INSERT INTO falcon_ai_falconmemory (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    key,
    value,
    source,
    conversation_id,
    created_by_id,
    organization_id,
    workspace_id
  )
  VALUES (
    NOW(),
    NOW(),
    false,
    NULL,
    ${sqlUuid(memoryId)},
    ${sqlTextLiteral(otherMemoryKey)},
    ${sqlTextLiteral("Other workspace memory should not be visible.")},
    'user',
    NULL,
    ${sqlUuid(userId)},
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)}
  )
  RETURNING id, key
),
inserted_other_skill AS (
  INSERT INTO falcon_ai_skill (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    slug,
    description,
    icon,
    is_builtin,
    is_active,
    instructions,
    tool_names,
    example_trajectories,
    trigger_phrases,
    created_by_id,
    organization_id,
    workspace_id
  )
  VALUES (
    NOW(),
    NOW(),
    false,
    NULL,
    ${sqlUuid(skillId)},
    ${sqlTextLiteral(`${marker} other skill`)},
    ${sqlTextLiteral(otherSkillSlug)},
    ${sqlTextLiteral("Other workspace skill should not be visible.")},
    'mdi:test-tube',
    false,
    true,
    ${sqlTextLiteral("Do not expose from another workspace.")},
    ${sqlJson(["list_memories"])},
    '[]'::jsonb,
    ${sqlJson([`${marker} other trigger`])},
    ${sqlUuid(userId)},
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)}
  )
  RETURNING id, slug
),
inserted_builtin_skill AS (
  INSERT INTO falcon_ai_skill (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    slug,
    description,
    icon,
    is_builtin,
    is_active,
    instructions,
    tool_names,
    example_trajectories,
    trigger_phrases,
    created_by_id,
    organization_id,
    workspace_id
  )
  VALUES (
    NOW(),
    NOW(),
    false,
    NULL,
    ${sqlUuid(builtinSkillId)},
    ${sqlTextLiteral(`${marker} builtin skill`)},
    ${sqlTextLiteral(builtinSlug)},
    ${sqlTextLiteral("Disposable global builtin skill.")},
    'mdi:star',
    true,
    true,
    ${sqlTextLiteral("Global builtin skill for API journey.")},
    ${sqlJson(["list_memories"])},
    '[]'::jsonb,
    ${sqlJson([`${marker} builtin trigger`])},
    NULL,
    NULL,
    NULL
  )
  RETURNING id, slug
)
SELECT json_build_object(
  'workspace_id', (SELECT id::text FROM inserted_workspace LIMIT 1),
  'workspace_name', (SELECT name FROM inserted_workspace LIMIT 1),
  'other_memory_id', (SELECT id::text FROM inserted_memory LIMIT 1),
  'other_memory_key', (SELECT key FROM inserted_memory LIMIT 1),
  'other_skill_id', (SELECT id::text FROM inserted_other_skill LIMIT 1),
  'other_skill_slug', (SELECT slug FROM inserted_other_skill LIMIT 1),
  'builtin_skill_id', (SELECT id::text FROM inserted_builtin_skill LIMIT 1),
  'builtin_slug', (SELECT slug FROM inserted_builtin_skill LIMIT 1)
);
`;
  return runPostgresJson(sql);
}

async function loadFalconMemorySkillDbAudit({
  memoryIds,
  skillIds,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH requested_memories AS (
  SELECT unnest(${sqlUuidArray(memoryIds)}) AS memory_id
),
requested_skills AS (
  SELECT unnest(${sqlUuidArray(skillIds)}) AS skill_id
),
memory_rows AS (
  SELECT memory.*
  FROM falcon_ai_falconmemory memory
  JOIN requested_memories requested
    ON memory.id = requested.memory_id
  WHERE memory.organization_id = ${sqlUuid(organizationId)}
),
skill_rows AS (
  SELECT skill.*
  FROM falcon_ai_skill skill
  JOIN requested_skills requested
    ON skill.id = requested.skill_id
  WHERE skill.organization_id = ${sqlUuid(organizationId)}
     OR (skill.organization_id IS NULL AND skill.is_builtin = true)
)
SELECT json_build_object(
  'memory_count', (SELECT count(*) FROM memory_rows),
  'deleted_memory_count',
    (SELECT count(*) FROM memory_rows WHERE deleted = true),
  'memory_deleted_at_count',
    (SELECT count(*) FROM memory_rows WHERE deleted = true AND deleted_at IS NOT NULL),
  'other_workspace_memory_count',
    (SELECT count(*) FROM memory_rows WHERE workspace_id <> ${sqlUuid(workspaceId)}),
  'skill_count', (SELECT count(*) FROM skill_rows),
  'deleted_skill_count',
    (SELECT count(*) FROM skill_rows WHERE deleted = true),
  'skill_deleted_at_count',
    (SELECT count(*) FROM skill_rows WHERE deleted = true AND deleted_at IS NOT NULL),
  'builtin_skill_count',
    (SELECT count(*) FROM skill_rows WHERE is_builtin = true AND organization_id IS NULL),
  'other_workspace_skill_count',
    (SELECT count(*) FROM skill_rows WHERE workspace_id <> ${sqlUuid(workspaceId)})
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteFalconMemorySkillFixturesDb({
  marker,
  organizationId,
}) {
  const sql = `
WITH target_memories AS (
  SELECT id
  FROM falcon_ai_falconmemory
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND key LIKE ${sqlTextLiteral(`${marker}%`)}
),
deleted_memories AS (
  DELETE FROM falcon_ai_falconmemory memory
  USING target_memories target
  WHERE memory.id = target.id
  RETURNING memory.id
),
target_skills AS (
  SELECT id
  FROM falcon_ai_skill
  WHERE slug LIKE ${sqlTextLiteral(`${marker}%`)}
    AND (
      organization_id = ${sqlUuid(organizationId)}
      OR (organization_id IS NULL AND is_builtin = true)
    )
),
deleted_skills AS (
  DELETE FROM falcon_ai_skill skill
  USING target_skills target
  WHERE skill.id = target.id
  RETURNING skill.id
),
target_workspaces AS (
  SELECT id
  FROM accounts_workspace
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlTextLiteral(`${marker}%`)}
),
deleted_workspaces AS (
  DELETE FROM accounts_workspace workspace
  USING target_workspaces target
  WHERE workspace.id = target.id
  RETURNING workspace.id
)
SELECT json_build_object(
  'deleted_memory_count', (SELECT count(*) FROM deleted_memories),
  'remaining_memory_count',
    (SELECT count(*) FROM target_memories) - (SELECT count(*) FROM deleted_memories),
  'deleted_skill_count', (SELECT count(*) FROM deleted_skills),
  'remaining_skill_count',
    (SELECT count(*) FROM target_skills) - (SELECT count(*) FROM deleted_skills),
  'deleted_workspace_count', (SELECT count(*) FROM deleted_workspaces),
  'remaining_workspace_count',
    (SELECT count(*) FROM target_workspaces) - (SELECT count(*) FROM deleted_workspaces)
);
`;
  return runPostgresJson(sql);
}

async function loadFalconFileDbAudit({
  fileIds,
  organizationId,
  workspaceId,
  userId,
  marker,
}) {
  const sql = `
WITH requested_files AS (
  SELECT unnest(${sqlUuidArray(fileIds)}) AS file_id
),
file_rows AS (
  SELECT file.*
  FROM falcon_ai_falconfile file
  JOIN requested_files requested
    ON file.id = requested.file_id
  WHERE file.organization_id = ${sqlUuid(organizationId)}
    AND file.name LIKE ${sqlTextLiteral(`${marker}%`)}
)
SELECT json_build_object(
  'file_count', (SELECT count(*) FROM file_rows),
  'active_workspace_file_count',
    (SELECT count(*) FROM file_rows WHERE workspace_id = ${sqlUuid(workspaceId)}),
  'user_file_count',
    (SELECT count(*) FROM file_rows WHERE user_id = ${sqlUuid(userId)}),
  'text_content_match_count',
    (SELECT count(*) FROM file_rows WHERE text_content LIKE '%Falcon upload text%' OR text_content LIKE '%"ok":true%'),
  'sanitized_name_count',
    (SELECT count(*) FROM file_rows WHERE name LIKE ${sqlTextLiteral(`${marker}_notes.txt`)}),
  'json_content_type_count',
    (SELECT count(*) FROM file_rows WHERE content_type = 'application/json'),
  'storage_key_count',
    (SELECT count(*) FROM file_rows WHERE storage_key LIKE 'falcon-ai/%'),
  'storage_keys',
    COALESCE((SELECT json_agg(storage_key ORDER BY created_at) FROM file_rows), '[]'::json)
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteFalconFileFixturesDb({ marker, organizationId }) {
  const sql = `
WITH target_files AS (
  SELECT id, storage_key
  FROM falcon_ai_falconfile
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlTextLiteral(`${marker}%`)}
),
deleted_files AS (
  DELETE FROM falcon_ai_falconfile file
  USING target_files target
  WHERE file.id = target.id
  RETURNING file.id
)
SELECT json_build_object(
  'deleted_file_count', (SELECT count(*) FROM deleted_files),
  'remaining_file_count',
    (SELECT count(*) FROM target_files) - (SELECT count(*) FROM deleted_files),
  'storage_keys',
    COALESCE((SELECT json_agg(storage_key) FROM target_files), '[]'::json)
);
`;
  const audit = await runPostgresJson(sql);
  await removeFalconMinioObjects(audit.storage_keys || []);
  return audit;
}

async function multipartAppCoreRequest({
  apiBase,
  accessToken,
  organizationId,
  workspaceId,
  method,
  pathName,
  fields = {},
  files = [],
}) {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined || value === null) continue;
    form.append(key, String(value));
  }
  for (const file of files) {
    form.append(
      file.fieldName || "file",
      new Blob([file.content], { type: file.contentType || "text/plain" }),
      file.fileName,
    );
  }

  const response = await fetch(new URL(pathName, apiBase), {
    method,
    headers: {
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(organizationId ? { "X-Organization-Id": organizationId } : {}),
      ...(workspaceId ? { "X-Workspace-Id": workspaceId } : {}),
    },
    body: form,
  });
  const body = await parseAppCoreResponseBody(response);
  if (!response.ok) {
    const error = new Error(
      `${method} ${pathName} failed with HTTP ${response.status}: ${formatAppCoreBody(
        body,
      )}`,
    );
    error.status = response.status;
    error.body = body;
    throw error;
  }
  if (body && typeof body === "object" && body.status === false) {
    throw new Error(
      `${method} ${pathName} returned status:false: ${formatAppCoreBody(body)}`,
    );
  }
  return body?.result ?? body?.results ?? body;
}

async function parseAppCoreResponseBody(response) {
  const text = await response.text();
  if (!text) return null;
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function formatAppCoreBody(body) {
  if (typeof body === "string") return body.slice(0, 1000);
  return JSON.stringify(body).slice(0, 1000);
}

async function assertFalconMinioObjectsExist(storageKeys) {
  for (const storageKey of storageKeys || []) {
    await runFalconMinioCommand(["stat", falconMinioTarget(storageKey)]);
  }
}

async function assertFalconMinioObjectsAbsent(storageKeys) {
  for (const storageKey of storageKeys || []) {
    try {
      await runFalconMinioCommand(["stat", falconMinioTarget(storageKey)]);
    } catch {
      continue;
    }
    throw new Error(
      `Falcon MinIO object still exists after cleanup: ${storageKey}`,
    );
  }
}

async function removeFalconMinioObjects(storageKeys) {
  const targets = (storageKeys || []).map((storageKey) =>
    falconMinioTarget(storageKey),
  );
  if (!targets.length) return;
  await runFalconMinioCommand(["rm", "--force", ...targets]);
}

async function runFalconMinioCommand(args) {
  const container =
    process.env.API_JOURNEY_MINIO_CONTAINER || "futureagi-ws2-minio-1";
  const command = [
    'mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null',
    `mc ${args.map((arg) => shellQuote(arg)).join(" ")}`,
  ].join(" && ");
  await execFileAsync("docker", ["exec", container, "sh", "-lc", command], {
    maxBuffer: 5 * 1024 * 1024,
  });
}

function falconMinioTarget(storageKey) {
  const bucket = process.env.API_JOURNEY_MINIO_BUCKET || "fi-content";
  return `local/${bucket}/${storageKey}`;
}

async function loadGetStartedFirstChecksDbAudit({
  userId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(userId), "userId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH ctx AS (
  SELECT
    ${sqlUuid(userId)} AS user_id,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    COALESCE((SELECT is_default FROM accounts_workspace WHERE id = ${sqlUuid(workspaceId)}), false) AS is_default
),
default_workspaces AS (
  SELECT id
  FROM accounts_workspace, ctx
  WHERE accounts_workspace.organization_id = ctx.organization_id
    AND accounts_workspace.is_default = true
),
key_rows AS (
  SELECT a.id
  FROM model_hub_apikey a, ctx
  WHERE a.user_id = ctx.user_id
    AND a.organization_id = ctx.organization_id
    AND a.deleted = false
    AND (
      (ctx.is_default = false AND a.workspace_id = ctx.workspace_id)
      OR (
        ctx.is_default = true
        AND (
          a.workspace_id = ctx.workspace_id
          OR a.workspace_id IS NULL
          OR a.workspace_id IN (SELECT id FROM default_workspaces)
        )
      )
    )
),
dataset_rows AS (
  SELECT d.id
  FROM model_hub_dataset d, ctx
  WHERE d.user_id = ctx.user_id
    AND d.organization_id = ctx.organization_id
    AND d.deleted = false
    AND (
      (ctx.is_default = false AND d.workspace_id = ctx.workspace_id)
      OR (
        ctx.is_default = true
        AND (
          d.workspace_id = ctx.workspace_id
          OR d.workspace_id IS NULL
          OR d.workspace_id IN (SELECT id FROM default_workspaces)
        )
      )
    )
),
evaluation_rows AS (
  SELECT m.id
  FROM model_hub_userevalmetric m, ctx
  WHERE m.user_id = ctx.user_id
    AND m.organization_id = ctx.organization_id
    AND m.deleted = false
    AND (
      (ctx.is_default = false AND m.workspace_id = ctx.workspace_id)
      OR (
        ctx.is_default = true
        AND (
          m.workspace_id = ctx.workspace_id
          OR m.workspace_id IS NULL
          OR m.workspace_id IN (SELECT id FROM default_workspaces)
        )
      )
    )
),
experiment_rows AS (
  SELECT e.id
  FROM model_hub_experimentstable e
  JOIN model_hub_dataset d ON d.id = e.dataset_id
  CROSS JOIN ctx
  WHERE e.user_id = ctx.user_id
    AND e.deleted = false
    AND d.organization_id = ctx.organization_id
    AND d.deleted = false
    AND (
      (ctx.is_default = false AND d.workspace_id = ctx.workspace_id)
      OR (
        ctx.is_default = true
        AND (
          d.workspace_id = ctx.workspace_id
          OR d.workspace_id IS NULL
          OR d.workspace_id IN (SELECT id FROM default_workspaces)
        )
      )
    )
),
observe_rows AS (
  SELECT p.id
  FROM tracer_project p, ctx
  WHERE p.user_id = ctx.user_id
    AND p.organization_id = ctx.organization_id
    AND p.deleted = false
    AND p.trace_type = 'observe'
    AND (
      (ctx.is_default = false AND p.workspace_id = ctx.workspace_id)
      OR (
        ctx.is_default = true
        AND (
          p.workspace_id = ctx.workspace_id
          OR p.workspace_id IS NULL
          OR p.workspace_id IN (SELECT id FROM default_workspaces)
        )
      )
    )
),
invite_rows AS (
  SELECT wm.id
  FROM accounts_workspacemembership wm
  JOIN accounts_user invited_user ON invited_user.id = wm.user_id
  CROSS JOIN ctx
  WHERE wm.workspace_id = ctx.workspace_id
    AND wm.deleted = false
    AND wm.is_active = true
    AND (wm.invited_by_id = ctx.user_id OR invited_user.invited_by_id = ctx.user_id)
)
SELECT json_build_object(
  'key_count', (SELECT count(*) FROM key_rows)::int,
  'dataset_count', (SELECT count(*) FROM dataset_rows)::int,
  'evaluation_count', (SELECT count(*) FROM evaluation_rows)::int,
  'experiment_count', (SELECT count(*) FROM experiment_rows)::int,
  'observe_count', (SELECT count(*) FROM observe_rows)::int,
  'invite_count', (SELECT count(*) FROM invite_rows)::int
);
`;
  return runPostgresJson(sql);
}

async function loadProfileSecurityDbAudit({ userId, organizationId }) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(userId)} AS user_id,
    ${sqlUuid(organizationId)} AS organization_id
),
user_row AS (
  SELECT u.id, u.email, u.name, u.last_timezone, u.organization_id, u.is_active
  FROM accounts_user u
  JOIN requested r ON u.id = r.user_id
),
org_row AS (
  SELECT o.id, o.require_2fa, o.require_2fa_grace_period_days, o.require_2fa_enforced_at
  FROM accounts_organization o
  JOIN requested r ON o.id = r.organization_id
),
totp_counts AS (
  SELECT
    count(*) FILTER (WHERE device.deleted = false) AS totp_count,
    count(*) FILTER (WHERE device.deleted = false AND device.confirmed = true) AS confirmed_totp_count
  FROM accounts_user_totp_device device
  JOIN requested r ON device.user_id = r.user_id
),
passkey_counts AS (
  SELECT count(*) FILTER (WHERE credential.deleted = false) AS passkey_count
  FROM accounts_webauthn_credential credential
  JOIN requested r ON credential.user_id = r.user_id
)
SELECT json_build_object(
  'user_id', user_row.id::text,
  'email', user_row.email,
  'name', user_row.name,
  'last_timezone', user_row.last_timezone,
  'user_organization_id', user_row.organization_id::text,
  'user_active', user_row.is_active,
  'totp_count', totp_counts.totp_count,
  'confirmed_totp_count', totp_counts.confirmed_totp_count,
  'passkey_count', passkey_counts.passkey_count,
  'require_2fa', org_row.require_2fa,
  'require_2fa_grace_period_days', org_row.require_2fa_grace_period_days,
  'require_2fa_enforced_at', org_row.require_2fa_enforced_at
)
FROM requested
LEFT JOIN user_row ON true
LEFT JOIN org_row ON true
CROSS JOIN totp_counts
CROSS JOIN passkey_counts;
`;
  return runPostgresJson(sql);
}

async function restoreUserTimezoneDb({ userId, timezone }) {
  const sql = `
WITH updated_user AS (
  UPDATE accounts_user
  SET last_timezone = ${timezone ? sqlTextLiteral(timezone) : "NULL"}
  WHERE id = ${sqlUuid(userId)}
  RETURNING id, last_timezone
)
SELECT json_build_object(
  'updated_count', (SELECT count(*) FROM updated_user),
  'last_timezone', (SELECT last_timezone FROM updated_user LIMIT 1)
);
`;
  return runPostgresJson(sql);
}

async function loadRbacMemberLifecycleAudit({
  email,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    lower(${sqlTextLiteral(email)}) AS email,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id
),
user_rows AS (
  SELECT u.id, u.email, u.organization_id, u.organization_role, u.is_active
  FROM accounts_user u
  JOIN requested r ON lower(u.email) = r.email
),
invite_rows AS (
  SELECT oi.id, oi.status, oi.level
  FROM accounts_organization_invite oi
  JOIN requested r ON lower(oi.target_email) = r.email
   AND oi.organization_id = r.organization_id
  WHERE oi.deleted = false
),
org_memberships AS (
  SELECT om.id, om.user_id, om.role, om.level, om.is_active, om.deleted
  FROM accounts_organization_membership om
  JOIN user_rows u ON om.user_id = u.id
  JOIN requested r ON om.organization_id = r.organization_id
),
workspace_memberships AS (
  SELECT wm.id, wm.user_id, wm.role, wm.level, wm.is_active, wm.deleted
  FROM accounts_workspacemembership wm
  JOIN user_rows u ON wm.user_id = u.id
  JOIN requested r ON wm.workspace_id = r.workspace_id
)
SELECT json_build_object(
  'email', (SELECT email FROM requested),
  'user_count', (SELECT count(*) FROM user_rows),
  'user_id', (SELECT id::text FROM user_rows LIMIT 1),
  'user_active', (SELECT is_active FROM user_rows LIMIT 1),
  'user_organization_id', (SELECT organization_id::text FROM user_rows LIMIT 1),
  'invite_count', (SELECT count(*) FROM invite_rows),
  'pending_invite_count', (SELECT count(*) FROM invite_rows WHERE status = 'Pending'),
  'accepted_invite_count', (SELECT count(*) FROM invite_rows WHERE status = 'Accepted'),
  'cancelled_invite_count', (SELECT count(*) FROM invite_rows WHERE status = 'Cancelled'),
  'invite_statuses', COALESCE((SELECT json_agg(status ORDER BY status) FROM invite_rows), '[]'::json),
  'invite_levels', COALESCE((SELECT json_agg(level ORDER BY level) FROM invite_rows), '[]'::json),
  'org_membership_count', (SELECT count(*) FROM org_memberships),
  'active_org_membership_count', (SELECT count(*) FROM org_memberships WHERE is_active = true AND deleted = false),
  'inactive_org_membership_count', (SELECT count(*) FROM org_memberships WHERE is_active = false AND deleted = false),
  'deleted_org_membership_count', (SELECT count(*) FROM org_memberships WHERE deleted = true),
  'org_membership_level', (SELECT level FROM org_memberships WHERE deleted = false LIMIT 1),
  'org_membership_role', (SELECT role FROM org_memberships WHERE deleted = false LIMIT 1),
  'workspace_membership_count', (SELECT count(*) FROM workspace_memberships),
  'active_workspace_membership_count', (SELECT count(*) FROM workspace_memberships WHERE is_active = true AND deleted = false),
  'inactive_workspace_membership_count', (SELECT count(*) FROM workspace_memberships WHERE is_active = false AND deleted = false),
  'deleted_workspace_membership_count', (SELECT count(*) FROM workspace_memberships WHERE deleted = true),
  'workspace_membership_level', (SELECT level FROM workspace_memberships WHERE deleted = false LIMIT 1),
  'workspace_membership_role', (SELECT role FROM workspace_memberships WHERE deleted = false LIMIT 1)
);
`;
  return runPostgresJson(sql);
}

async function deleteDisposableRbacUserArtifacts(email) {
  const sql = `
WITH requested AS (
  SELECT lower(${sqlTextLiteral(email)}) AS email
),
user_rows AS (
  SELECT u.id
  FROM accounts_user u
  JOIN requested r ON lower(u.email) = r.email
),
deleted_auth_tokens AS (
  DELETE FROM accounts_auth_token token
  USING user_rows u
  WHERE token.user_id = u.id
  RETURNING token.id
),
deleted_recovery_codes AS (
  DELETE FROM accounts_recovery_code code
  USING user_rows u
  WHERE code.user_id = u.id
  RETURNING code.id
),
deleted_totp_devices AS (
  DELETE FROM accounts_user_totp_device device
  USING user_rows u
  WHERE device.user_id = u.id
  RETURNING device.id
),
deleted_webauthn_credentials AS (
  DELETE FROM accounts_webauthn_credential credential
  USING user_rows u
  WHERE credential.user_id = u.id
  RETURNING credential.id
),
deleted_user_groups AS (
  DELETE FROM accounts_user_groups user_group
  USING user_rows u
  WHERE user_group.user_id = u.id
  RETURNING user_group.id
),
deleted_user_permissions AS (
  DELETE FROM accounts_user_user_permissions user_permission
  USING user_rows u
  WHERE user_permission.user_id = u.id
  RETURNING user_permission.id
),
deleted_workspace_memberships AS (
  DELETE FROM accounts_workspacemembership membership
  USING user_rows u
  WHERE membership.user_id = u.id
  RETURNING membership.id
),
deleted_org_memberships AS (
  DELETE FROM accounts_organization_membership membership
  USING user_rows u
  WHERE membership.user_id = u.id
  RETURNING membership.id
),
deleted_invites AS (
  DELETE FROM accounts_organization_invite oi
  USING requested r
  WHERE lower(oi.target_email) = r.email
  RETURNING oi.id
),
deleted_users AS (
  DELETE FROM accounts_user u
  USING requested r
  WHERE lower(u.email) = r.email
  RETURNING u.id
)
SELECT json_build_object(
  'deleted_invites', (SELECT count(*) FROM deleted_invites),
  'deleted_auth_tokens', (SELECT count(*) FROM deleted_auth_tokens),
  'deleted_recovery_codes', (SELECT count(*) FROM deleted_recovery_codes),
  'deleted_totp_devices', (SELECT count(*) FROM deleted_totp_devices),
  'deleted_webauthn_credentials', (SELECT count(*) FROM deleted_webauthn_credentials),
  'deleted_user_groups', (SELECT count(*) FROM deleted_user_groups),
  'deleted_user_permissions', (SELECT count(*) FROM deleted_user_permissions),
  'deleted_workspace_memberships', (SELECT count(*) FROM deleted_workspace_memberships),
  'deleted_org_memberships', (SELECT count(*) FROM deleted_org_memberships),
  'deleted_users', (SELECT count(*) FROM deleted_users),
  'remaining_invites', (
    SELECT count(*) FROM accounts_organization_invite oi, requested r
    WHERE lower(oi.target_email) = r.email
  ),
  'remaining_users', (
    SELECT count(*) FROM accounts_user u, requested r
    WHERE lower(u.email) = r.email
  )
);
`;
  return runPostgresJson(sql);
}

async function resolveInviteAcceptanceToken(email) {
  const script = `
import json
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from accounts.models import User
user = User.objects.get(email=${JSON.stringify(email)})
print(json.dumps({
    "user_id": str(user.id),
    "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
    "token": default_token_generator.make_token(user),
}))
`;
  return runBackendShellJson(script);
}

async function runBackendShellJson(script) {
  let stdout;
  const container = process.env.API_JOURNEY_BACKEND_CONTAINER;
  if (container) {
    const command = [
      "cd /app/backend",
      `python manage.py shell -c ${shellQuote(script)}`,
    ].join(" && ");
    ({ stdout } = await execFileAsync(
      "docker",
      ["exec", container, "sh", "-lc", command],
      { maxBuffer: 20 * 1024 * 1024 },
    ));
  } else {
    const backendDir = process.env.API_JOURNEY_BACKEND_DIR || "futureagi";
    ({ stdout } = await execFileAsync(
      "uv",
      ["run", "python", "manage.py", "shell", "-c", script],
      {
        cwd: backendDir,
        env: {
          ...process.env,
          EE_LICENSE_KEY: process.env.EE_LICENSE_KEY || "test-license-key",
          PGBOUNCER_HOST: process.env.PGBOUNCER_HOST || "127.0.0.1",
          PGBOUNCER_PORT: process.env.PGBOUNCER_PORT || "5436",
          REDIS_URL: process.env.REDIS_URL || "redis://127.0.0.1:6382/0",
          REDIS_CACHE_URL:
            process.env.REDIS_CACHE_URL || "redis://127.0.0.1:6382/0",
          UV_PROJECT_ENVIRONMENT:
            process.env.UV_PROJECT_ENVIRONMENT || ".venv-th5064-py311",
        },
        maxBuffer: 20 * 1024 * 1024,
      },
    ));
  }
  const jsonLine = stdout
    .trim()
    .split(/\r?\n/)
    .reverse()
    .find((line) => line.trim().startsWith("{"));
  assert(jsonLine, "Backend shell command did not emit a JSON object.");
  return JSON.parse(jsonLine);
}

async function unauthenticatedApiRequest(apiBase, method, pathName, body) {
  const response = await fetch(new URL(pathName, apiBase), {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!response.ok) {
    throw new Error(
      `${method} ${pathName} failed with HTTP ${response.status}: ${text.slice(0, 1000)}`,
    );
  }
  if (payload && typeof payload === "object" && payload.status === false) {
    throw new Error(
      `${method} ${pathName} returned status:false: ${JSON.stringify(payload).slice(0, 1000)}`,
    );
  }
  return payload?.result ?? payload;
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
  const rows = values || [];
  assert(rows.length > 0, "SQL UUID array cannot be empty.");
  return `ARRAY[${rows.map((value) => sqlUuid(value)).join(", ")}]::uuid[]`;
}

function sqlTextLiteral(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function sqlTextArray(values) {
  const rows = values || [];
  assert(rows.length > 0, "SQL text array cannot be empty.");
  return `ARRAY[${rows.map((value) => sqlTextLiteral(value)).join(", ")}]::text[]`;
}

function sqlJson(value) {
  return `${sqlTextLiteral(JSON.stringify(value ?? null))}::jsonb`;
}

function shellQuote(value) {
  return `'${String(value).replaceAll("'", "'\"'\"'")}'`;
}

async function ignoreNotFound(fn) {
  try {
    return await fn();
  } catch (error) {
    const message = String(error?.message || "").toLowerCase();
    if (
      error?.status === 404 ||
      message.includes("not found") ||
      message.includes("does not exist") ||
      message.includes("no keys are associated") ||
      (message.includes("no ") && message.includes(" matches "))
    ) {
      return null;
    }
    throw error;
  }
}
