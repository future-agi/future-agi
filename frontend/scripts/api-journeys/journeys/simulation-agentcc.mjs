import { execFile } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  createApiClient,
  currentUserId,
  envFlag,
  isUuid,
  requireMutations,
  skip,
} from "../lib/api-client.mjs";

const execFileAsync = promisify(execFile);

export const simulationAgentccJourneys = [
  {
    id: "SIM-API-001",
    title:
      "Simulation persona create, duplicate, search, update, retrieve, and delete lifecycle",
    tags: ["simulation", "personas", "mutating", "data-roundtrip"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
      user,
    }) {
      requireMutations();
      const name = `api journey persona ${runId}`;
      let hardCleaned = false;
      cleanup.defer("hard-delete API journey persona rows", () =>
        hardCleaned
          ? null
          : deleteSimulationPersonaFixturesDb({
              namePrefix: name,
              organizationId,
            }),
      );

      const fieldOptions = await client.get(
        apiPath("/simulate/api/personas/field-options/"),
      );
      assert(
        asArray(fieldOptions.gender_choices).some(
          (choice) => choice.value === "male",
        ) &&
          asArray(fieldOptions.language_choices).some(
            (choice) => choice.value === "English",
          ),
        "Persona field-options did not expose expected gender/language choices.",
      );

      const invalidListSimulationType = await expectApiError(
        () =>
          client.get(apiPath("/simulate/api/personas/"), {
            query: { simulation_type: "fax" },
          }),
        [400],
        "Persona list accepted an invalid simulation_type filter.",
      );
      assert(
        errorText(invalidListSimulationType).includes("simulation_type"),
        "Persona invalid list filter error did not mention simulation_type.",
      );

      const invalidGender = await expectApiError(
        () =>
          client.post(apiPath("/simulate/api/personas/"), {
            name: `${name} invalid gender`,
            description: "Invalid persona should be rejected.",
            gender: ["robot"],
            language: ["English"],
            simulation_type: "voice",
          }),
        [400],
        "Persona create accepted an invalid gender choice.",
      );
      const invalidNullName = await expectApiError(
        () =>
          client.post(apiPath("/simulate/api/personas/"), {
            name: null,
            description: "Invalid persona should be rejected.",
            gender: ["male"],
            language: ["English"],
            simulation_type: "voice",
          }),
        [400],
        "Persona create accepted a null name.",
      );
      const invalidSimulationType = await expectApiError(
        () =>
          client.post(apiPath("/simulate/api/personas/"), {
            name: `${name} invalid simulation`,
            description: "Invalid persona should be rejected.",
            gender: ["male"],
            language: ["English"],
            simulation_type: "fax",
          }),
        [400],
        "Persona create accepted an invalid simulation_type.",
      );
      const invalidMultilingual = await expectApiError(
        () =>
          client.post(apiPath("/simulate/api/personas/"), {
            name: `${name} invalid multilingual`,
            description: "Invalid persona should be rejected.",
            gender: ["male"],
            multilingual: true,
            language: [],
            simulation_type: "voice",
          }),
        [400],
        "Persona create accepted multilingual=true without languages.",
      );
      const invalidCustomProperties = await expectApiError(
        () =>
          client.post(apiPath("/simulate/api/personas/"), {
            name: `${name} invalid custom properties`,
            description: "Invalid persona should be rejected.",
            gender: ["male"],
            language: ["English"],
            custom_properties: { "": "missing key" },
            simulation_type: "voice",
          }),
        [400],
        "Persona create accepted an empty custom property key.",
      );

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
          client.delete(
            apiPath("/simulate/api/personas/{id}/", { id: created.id }),
          ),
        ),
      );
      assert(
        created.name === name &&
          created.simulation_type === "voice" &&
          asArray(created.occupation).includes("Engineer") &&
          asArray(created.languages).includes("English") &&
          created.metadata?.source === "api-journey",
        "Persona create response did not preserve canonical fields.",
      );

      let dbAudit = await loadSimulationPersonaDbAudit({
        personaIds: [created.id],
        organizationId,
      });
      const createdAudit = collectionRows(dbAudit.personas).find(
        (persona) => persona.id === created.id,
      );
      assert(
        createdAudit?.organization_id === organizationId &&
          createdAudit?.workspace_id === workspaceId &&
          createdAudit?.deleted === false &&
          createdAudit?.deleted_at_set === false &&
          createdAudit?.metadata?.source === "api-journey",
        "Persona DB audit did not show active workspace-owned row.",
      );

      const searched = asArray(
        await client.get(apiPath("/simulate/api/personas/"), {
          query: {
            search: name,
            page_size: 10,
            type: "custom",
            simulation_type: "voice",
          },
        }),
      );
      assert(
        searched.some((persona) => persona.id === created.id),
        "Created persona was not visible through custom voice list/search.",
      );

      const workspacePersonas = asArray(
        await client.get(apiPath("/simulate/api/personas/workspace/")),
      );
      assert(
        workspacePersonas.some((persona) => persona.id === created.id),
        "Created persona was not visible through workspace persona list.",
      );

      const duplicateCreate = await expectApiError(
        () =>
          client.post(apiPath("/simulate/api/personas/"), {
            name,
            description: "Duplicate persona should be rejected.",
            gender: ["male"],
            language: ["English"],
            simulation_type: "voice",
          }),
        [400],
        "Persona create accepted a duplicate active workspace name.",
      );
      assert(
        errorText(duplicateCreate).toLowerCase().includes("exists"),
        "Persona duplicate create error did not mention existing name.",
      );

      const unknownDuplicateField = await expectApiError(
        () =>
          client.post(
            apiPath("/simulate/api/personas/duplicate/{persona_id}/", {
              persona_id: created.id,
            }),
            { name: `${name} unknown field copy`, legacy_extra: "reject me" },
          ),
        [400],
        "Persona duplicate accepted an unknown request field.",
      );
      assert(
        errorText(unknownDuplicateField).includes("legacy_extra"),
        "Persona duplicate unknown-field error did not mention legacy_extra.",
      );

      const duplicateName = `${name} duplicate`;
      const duplicated = await client.post(
        apiPath("/simulate/api/personas/duplicate/{persona_id}/", {
          persona_id: created.id,
        }),
        { name: duplicateName },
      );
      assert(
        duplicated?.id &&
          duplicated.id !== created.id &&
          duplicated.name === duplicateName &&
          duplicated.metadata?.source === "api-journey",
        "Persona custom duplicate route did not create a copied persona.",
      );
      cleanup.defer("delete API journey duplicated persona", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/simulate/api/personas/{id}/", { id: duplicated.id }),
          ),
        ),
      );

      const duplicateDuplicateName = await expectApiError(
        () =>
          client.post(
            apiPath("/simulate/api/personas/duplicate/{persona_id}/", {
              persona_id: created.id,
            }),
            { name: duplicateName },
          ),
        [400],
        "Persona duplicate accepted a duplicate target name.",
      );
      assert(
        errorText(duplicateDuplicateName).toLowerCase().includes("exists"),
        "Persona duplicate-name error did not mention existing name.",
      );

      const viewsetDuplicateName = `${name} viewset duplicate`;
      const viewsetDuplicated = await client.post(
        apiPath("/simulate/api/personas/{id}/duplicate/", { id: created.id }),
        { name: viewsetDuplicateName },
      );
      assert(
        viewsetDuplicated?.id &&
          viewsetDuplicated.id !== created.id &&
          viewsetDuplicated.name === viewsetDuplicateName,
        "Persona viewset duplicate route did not create a copied persona.",
      );
      cleanup.defer("delete API journey viewset duplicated persona", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/simulate/api/personas/{id}/", {
              id: viewsetDuplicated.id,
            }),
          ),
        ),
      );

      const duplicatePatch = await expectApiError(
        () =>
          client.patch(
            apiPath("/simulate/api/personas/{id}/", { id: created.id }),
            { name: viewsetDuplicateName },
          ),
        [400],
        "Persona update accepted a duplicate active workspace name.",
      );
      assert(
        errorText(duplicatePatch).toLowerCase().includes("exists"),
        "Persona duplicate update error did not mention existing name.",
      );

      const userId = currentUserId(user);
      assert(
        userId,
        "Persona journey requires current user id for DB fixture.",
      );
      const otherWorkspaceFixture =
        await insertOtherWorkspaceSimulationPersonaFixtureDb({
          namePrefix: name,
          organizationId,
          userId,
        });
      const crossWorkspaceDuplicate = await expectApiError(
        () =>
          client.post(
            apiPath("/simulate/api/personas/duplicate/{persona_id}/", {
              persona_id: otherWorkspaceFixture.persona_id,
            }),
            { name: `${name} cross workspace copy` },
          ),
        [400, 404],
        "Persona custom duplicate route copied an inaccessible workspace persona.",
      );
      assert(
        errorText(crossWorkspaceDuplicate).toLowerCase().includes("not found"),
        "Persona cross-workspace duplicate error did not fail closed as not found.",
      );

      const updated = await client.patch(
        apiPath("/simulate/api/personas/{id}/", { id: created.id }),
        {
          description: "Updated temporary persona for API journey regression.",
          keywords: ["api-journey", "updated"],
          language: ["English", "Hindi"],
          custom_properties: { source: "api-journey", updated: "true" },
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
      assert(
        asArray(updated.languages).includes("Hindi") &&
          updated.metadata?.updated === "true",
        "Persona update did not persist language/custom_properties aliases.",
      );

      const detail = await client.get(
        apiPath("/simulate/api/personas/{id}/", { id: created.id }),
      );
      assert(detail?.id === created.id, "Persona detail returned wrong id.");
      assert(
        detail.metadata?.updated === "true" &&
          asArray(detail.languages).includes("Hindi"),
        "Persona detail did not read back updated metadata/languages.",
      );

      await client.delete(
        apiPath("/simulate/api/personas/{id}/", { id: created.id }),
      );
      await client.delete(
        apiPath("/simulate/api/personas/{id}/", { id: duplicated.id }),
      );
      await client.delete(
        apiPath("/simulate/api/personas/{id}/", { id: viewsetDuplicated.id }),
      );
      const afterDelete = asArray(
        await client.get(apiPath("/simulate/api/personas/"), {
          query: { search: name, page_size: 10 },
        }),
      );
      assert(
        !afterDelete.some((persona) => persona.id === created.id),
        "Deleted persona was still visible through list/search.",
      );

      dbAudit = await loadSimulationPersonaDbAudit({
        personaIds: [created.id, duplicated.id, viewsetDuplicated.id],
        organizationId,
      });
      assert(
        Number(dbAudit.active_count) === 0 &&
          Number(dbAudit.deleted_at_count) === 3,
        "Persona DB audit did not show deleted_at on all disposable personas.",
      );

      const hardCleanup = await deleteSimulationPersonaFixturesDb({
        namePrefix: name,
        organizationId,
      });
      hardCleaned = true;
      assert(
        Number(hardCleanup.remaining_persona_count) === 0 &&
          Number(hardCleanup.remaining_workspace_count) === 0,
        "Persona hard cleanup left disposable rows behind.",
      );

      evidence.push({
        persona_id: created.id,
        persona_name: name,
        duplicate_persona_id: duplicated.id,
        viewset_duplicate_persona_id: viewsetDuplicated.id,
        invalid_gender_status: invalidGender.status,
        invalid_null_name_status: invalidNullName.status,
        invalid_simulation_type_status: invalidSimulationType.status,
        invalid_list_simulation_type_status: invalidListSimulationType.status,
        invalid_multilingual_status: invalidMultilingual.status,
        invalid_custom_properties_status: invalidCustomProperties.status,
        duplicate_create_status: duplicateCreate.status,
        duplicate_unknown_field_status: unknownDuplicateField.status,
        duplicate_name_status: duplicateDuplicateName.status,
        duplicate_patch_status: duplicatePatch.status,
        cross_workspace_duplicate_status: crossWorkspaceDuplicate.status,
        deleted_at_count: Number(dbAudit.deleted_at_count),
        hard_cleanup_deleted_persona_count: Number(
          hardCleanup.deleted_persona_count,
        ),
        hard_cleanup_remaining_persona_count: Number(
          hardCleanup.remaining_persona_count,
        ),
      });
    },
  },
  {
    id: "SIM-API-002",
    title:
      "Simulation agent definition operations create, update, retrieve, and delete lifecycle",
    tags: ["simulation", "agents", "mutating", "data-roundtrip"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const name = `api journey agent ${runId}`;
      const rawApiKey = `sk-agent-definition-${runId}-secret`;
      let hardCleaned = false;
      cleanup.defer("hard-delete API journey agent definition rows", () =>
        hardCleaned
          ? null
          : deleteSimulationAgentDefinitionFixturesDb({
              namePrefix: name,
              organizationId,
            }),
      );

      const invalidLegacyList = await expectApiError(
        () =>
          client.get(apiPath("/simulate/agent-definitions/"), {
            query: { legacyFilter: "voice" },
          }),
        [400],
        "Agent definition legacy list accepted an unknown query parameter.",
      );
      const invalidLegacyCreate = await expectApiError(
        () =>
          client.post(apiPath("/simulate/agent-definitions/create/"), {
            agent_name: `${name} invalid`,
            agent_type: "text",
            commit_message: "Initial",
            legacy_extra: "reject me",
          }),
        [400],
        "Agent definition legacy create accepted an unknown field.",
      );
      const invalidBlankName = await expectApiError(
        () =>
          client.post(apiPath("/simulate/agent-definitions/create/"), {
            agent_name: "   ",
            agent_type: "text",
            commit_message: "Initial",
          }),
        [400],
        "Agent definition legacy create accepted a blank name.",
      );
      const invalidVoiceProvider = await expectApiError(
        () =>
          client.post(apiPath("/simulate/agent-definitions/create/"), {
            agent_name: `${name} invalid voice`,
            agent_type: "voice",
            contact_number: "+12345678901",
            commit_message: "Initial",
            inbound: true,
          }),
        [400],
        "Agent definition legacy create accepted a voice agent without provider.",
      );
      const invalidOperationsCreate = await expectApiError(
        () =>
          client.post(apiPath("/simulate/api/agent-definition-operations/"), {
            agent_name: `${name} invalid operations`,
            agent_type: "text",
            inbound: true,
            languages: ["en"],
            description: "Invalid operations agent.",
            legacy_extra: "reject me",
          }),
        [400],
        "Agent definition operations create accepted an unknown field.",
      );
      const invalidFetchAssistant = await expectApiError(
        () =>
          client.post(
            apiPath(
              "/simulate/api/agent-definition-operations/fetch_assistant_from_provider/",
            ),
            {
              assistant_id: "asst_invalid",
              api_key: "sk-invalid",
              provider: "others",
              legacy_extra: "reject me",
            },
          ),
        [400],
        "Agent definition fetch_assistant accepted an unknown field.",
      );

      const createdEnvelope = await client.post(
        apiPath("/simulate/agent-definitions/create/"),
        {
          agent_name: name,
          agent_type: "text",
          inbound: true,
          description: "Temporary text agent definition for API journey.",
          language: "en",
          languages: ["en", "es"],
          commit_message: "Initial API journey version",
          model: "gpt-4o-mini",
          model_details: { source: "api-journey", version: 1 },
        },
      );
      assertNoRawSecret(
        createdEnvelope,
        rawApiKey,
        "Agent definition legacy create response leaked the raw provider API key.",
      );
      const created = createdEnvelope?.agent;
      assert(
        created?.id,
        "Agent definition legacy create did not return agent.id.",
      );
      cleanup.defer("delete legacy API journey agent definition", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/simulate/agent-definitions/{agent_id}/delete/", {
              agent_id: created.id,
            }),
          ),
        ),
      );
      assert(
        created.agent_name === name &&
          created.agent_type === "text" &&
          created.model === "gpt-4o-mini" &&
          created.model_details?.version === 1,
        "Agent definition legacy create did not preserve expected text-agent fields.",
      );

      let dbAudit = await loadSimulationAgentDefinitionDbAudit({
        agentIds: [created.id],
        organizationId,
        rawApiKey,
        maskedApiKey: created.api_key,
      });
      let createdAudit = collectionRows(dbAudit.agents).find(
        (agent) => agent.id === created.id,
      );
      assert(
        createdAudit?.organization_id === organizationId &&
          createdAudit?.workspace_id === workspaceId &&
          createdAudit?.deleted === false &&
          Number(dbAudit.version_count) === 1 &&
          Number(dbAudit.active_version_count) === 1 &&
          Number(dbAudit.credential_count) === 0,
        "Agent definition DB audit did not show an active workspace-owned text agent.",
      );

      const detail = await client.get(
        apiPath("/simulate/agent-definitions/{agent_id}/", {
          agent_id: created.id,
        }),
      );
      assertNoRawSecret(
        detail,
        rawApiKey,
        "Agent definition detail response leaked the raw provider API key.",
      );
      const initialVersionId = detail.active_version?.id;
      assert(
        detail.id === created.id &&
          detail.version_count === 1 &&
          isUuid(initialVersionId) &&
          asArray(detail.versions).some(
            (version) => version.id === initialVersionId,
          ),
        "Agent definition detail did not include active version history.",
      );

      const searchedLegacyAgents = collectionRows(
        await client.get(apiPath("/simulate/agent-definitions/"), {
          query: {
            search: name,
            agent_type: "text",
            agent_definition_id: created.id,
            limit: 10,
          },
        }),
      );
      assert(
        searchedLegacyAgents[0]?.id === created.id,
        "Agent definition legacy list did not pin/search the created agent.",
      );

      const initialVersionDetail = await client.get(
        apiPath(
          "/simulate/agent-definitions/{agent_id}/versions/{version_id}/",
          {
            agent_id: created.id,
            version_id: initialVersionId,
          },
        ),
      );
      assertNoRawSecret(
        initialVersionDetail,
        rawApiKey,
        "Agent definition initial version detail leaked the raw provider API key.",
      );
      assert(
        !initialVersionDetail.configuration_snapshot?.api_key &&
          initialVersionDetail.configuration_snapshot?.model_details
            ?.version === 1,
        "Agent definition version detail did not return the expected text-agent snapshot.",
      );

      const invalidLegacyEdit = await expectApiError(
        () =>
          client.put(
            apiPath("/simulate/agent-definitions/{agent_id}/edit/", {
              agent_id: created.id,
            }),
            {
              description: "Invalid edit should be rejected.",
              legacy_extra: "reject me",
            },
          ),
        [400],
        "Agent definition legacy edit accepted an unknown field.",
      );

      const editedEnvelope = await client.put(
        apiPath("/simulate/agent-definitions/{agent_id}/edit/", {
          agent_id: created.id,
        }),
        {
          description: "Updated temporary text agent definition.",
          model_details: { source: "api-journey", edited: true },
        },
      );
      assertNoRawSecret(
        editedEnvelope,
        rawApiKey,
        "Agent definition edit response leaked the raw provider API key.",
      );
      assert(
        editedEnvelope.agent?.description ===
          "Updated temporary text agent definition." &&
          editedEnvelope.agent?.model_details?.edited === true,
        "Agent definition legacy edit did not persist description/model_details.",
      );

      dbAudit = await loadSimulationAgentDefinitionDbAudit({
        agentIds: [created.id],
        organizationId,
        rawApiKey,
        maskedApiKey: created.api_key,
      });
      createdAudit = collectionRows(dbAudit.agents).find(
        (agent) => agent.id === created.id,
      );
      assert(
        createdAudit?.model_details?.edited === true &&
          dbAudit.raw_key_present_in_credential_ciphertext === false,
        "Agent definition edit DB audit did not preserve updated model_details.",
      );

      const invalidCreateVersion = await expectApiError(
        () =>
          client.post(
            apiPath("/simulate/agent-definitions/{agent_id}/versions/create/", {
              agent_id: created.id,
            }),
            {
              commit_message: "Invalid version should be rejected.",
              legacy_extra: "reject me",
            },
          ),
        [400],
        "Agent definition version create accepted an unknown field.",
      );

      const secondVersionEnvelope = await client.post(
        apiPath("/simulate/agent-definitions/{agent_id}/versions/create/", {
          agent_id: created.id,
        }),
        {
          agent_name: `${name} renamed`,
          description: "Second API journey version.",
          commit_message: "Create second API journey version",
          model_details: { source: "api-journey", version: 2 },
        },
      );
      assertNoRawSecret(
        secondVersionEnvelope,
        rawApiKey,
        "Agent definition version create response leaked the raw provider API key.",
      );
      const secondVersion = secondVersionEnvelope.version;
      assert(
        secondVersion?.version_number === 2 &&
          secondVersion.status === "active" &&
          secondVersion.configuration_snapshot?.model_details?.version === 2,
        "Agent definition version create did not return an active v2 snapshot.",
      );

      let versions = collectionRows(
        await client.get(
          apiPath("/simulate/agent-definitions/{agent_id}/versions/", {
            agent_id: created.id,
          }),
          { query: { limit: 10 } },
        ),
      );
      assert(
        versions.length === 2 &&
          versions.filter((version) => version.status === "active").length ===
            1,
        "Agent definition version list did not contain exactly one active version.",
      );

      const invalidActivateBody = await expectApiError(
        () =>
          client.post(
            apiPath(
              "/simulate/agent-definitions/{agent_id}/versions/{version_id}/activate/",
              {
                agent_id: created.id,
                version_id: initialVersionId,
              },
            ),
            { legacy_extra: "reject me" },
          ),
        [400],
        "Agent definition version activate accepted an unknown body field.",
      );
      const activatedInitial = await client.post(
        apiPath(
          "/simulate/agent-definitions/{agent_id}/versions/{version_id}/activate/",
          {
            agent_id: created.id,
            version_id: initialVersionId,
          },
        ),
        {},
      );
      assert(
        activatedInitial.version?.id === initialVersionId &&
          activatedInitial.version?.status === "active",
        "Agent definition activate did not activate the requested version.",
      );

      const invalidRestoreBody = await expectApiError(
        () =>
          client.post(
            apiPath(
              "/simulate/agent-definitions/{agent_id}/versions/{version_id}/restore/",
              {
                agent_id: created.id,
                version_id: secondVersion.id,
              },
            ),
            { legacy_extra: "reject me" },
          ),
        [400],
        "Agent definition version restore accepted an unknown body field.",
      );
      const restoredSecond = await client.post(
        apiPath(
          "/simulate/agent-definitions/{agent_id}/versions/{version_id}/restore/",
          {
            agent_id: created.id,
            version_id: secondVersion.id,
          },
        ),
        {},
      );
      assertNoRawSecret(
        restoredSecond,
        rawApiKey,
        "Agent definition restore response leaked the raw provider API key.",
      );
      assert(
        restoredSecond.agent?.agent_name === `${name} renamed`,
        "Agent definition restore did not restore the v2 snapshot.",
      );

      const evalSummary = await client.get(
        apiPath(
          "/simulate/agent-definitions/{agent_id}/versions/{version_id}/eval-summary/",
          {
            agent_id: created.id,
            version_id: secondVersion.id,
          },
        ),
      );
      assert(
        Array.isArray(evalSummary),
        "Agent definition version eval-summary did not return a list payload.",
      );
      const callExecutions = await client.get(
        apiPath(
          "/simulate/agent-definitions/{agent_id}/versions/{version_id}/call-executions/",
          {
            agent_id: created.id,
            version_id: secondVersion.id,
          },
        ),
        { query: { limit: 5 } },
      );
      assert(
        Number(callExecutions.count || 0) === 0,
        "Fresh agent definition version unexpectedly returned call executions.",
      );

      await client.delete(
        apiPath(
          "/simulate/agent-definitions/{agent_id}/versions/{version_id}/delete/",
          {
            agent_id: created.id,
            version_id: secondVersion.id,
          },
        ),
      );
      versions = collectionRows(
        await client.get(
          apiPath("/simulate/agent-definitions/{agent_id}/versions/", {
            agent_id: created.id,
          }),
          { query: { limit: 10 } },
        ),
      );
      assert(
        versions.every((version) => version.id !== secondVersion.id),
        "Deleted agent definition version was still visible in version list.",
      );

      const singleDeleteEnvelope = await client.post(
        apiPath("/simulate/agent-definitions/create/"),
        {
          agent_name: `${name} single delete`,
          agent_type: "text",
          inbound: true,
          description: "Temporary text agent for single-delete cascade.",
          languages: ["en"],
          commit_message: "Initial text version",
          model: "gpt-4o-mini",
          model_details: { source: "api-journey", singleDelete: true },
        },
      );
      const singleDeleteAgent = singleDeleteEnvelope.agent;
      cleanup.defer("delete single-delete API journey agent definition", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/simulate/agent-definitions/{agent_id}/delete/", {
              agent_id: singleDeleteAgent.id,
            }),
          ),
        ),
      );
      await client.delete(
        apiPath("/simulate/agent-definitions/{agent_id}/delete/", {
          agent_id: singleDeleteAgent.id,
        }),
      );
      const singleDeleteAudit = await loadSimulationAgentDefinitionDbAudit({
        agentIds: [singleDeleteAgent.id],
        organizationId,
        rawApiKey,
        maskedApiKey: created.api_key,
      });
      assert(
        Number(singleDeleteAudit.deleted_agent_count) === 1 &&
          Number(singleDeleteAudit.deleted_version_count) === 1,
        "Agent definition single delete did not soft-delete the agent and first version.",
      );

      const operationsCreated = await client.post(
        apiPath("/simulate/api/agent-definition-operations/"),
        {
          agent_name: `${name} operations`,
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
      assert(
        operationsCreated?.id,
        "Agent definition operations create did not return id.",
      );
      cleanup.defer("delete operations API journey agent definition", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/simulate/api/agent-definition-operations/{id}/", {
              id: operationsCreated.id,
            }),
          ),
        ),
      );

      const operationsUpdated = await client.patch(
        apiPath("/simulate/api/agent-definition-operations/{id}/", {
          id: operationsCreated.id,
        }),
        {
          description:
            "Updated temporary agent definition for API journey regression.",
          model_details: { source: "api-journey", updated: true },
        },
      );
      assert(
        operationsUpdated.description.includes("Updated temporary agent"),
        "Agent definition update did not persist description.",
      );
      assert(
        operationsUpdated.model_details?.updated === true,
        "Agent definition update did not persist model_details.",
      );

      const operationsDetail = await client.get(
        apiPath("/simulate/api/agent-definition-operations/{id}/", {
          id: operationsCreated.id,
        }),
      );
      assert(
        operationsDetail?.id === operationsCreated.id,
        "Agent definition detail returned wrong id.",
      );
      assert(
        operationsDetail?.agent_name === `${name} operations`,
        "Agent definition detail returned wrong name.",
      );

      await client.delete(
        apiPath("/simulate/api/agent-definition-operations/{id}/", {
          id: operationsCreated.id,
        }),
      );
      const listed = asArray(
        await client.get(
          apiPath("/simulate/api/agent-definition-operations/"),
          {
            query: { search: `${name} operations`, limit: 10 },
          },
        ),
      );
      assert(
        !listed.some((agent) => agent.id === operationsCreated.id),
        "Deleted agent definition was still visible through list/search.",
      );

      await client.delete(apiPath("/simulate/agent-definitions/"), {
        body: { agent_ids: [created.id] },
      });
      dbAudit = await loadSimulationAgentDefinitionDbAudit({
        agentIds: [created.id, singleDeleteAgent.id, operationsCreated.id],
        organizationId,
        rawApiKey,
        maskedApiKey: created.api_key,
      });
      assert(
        Number(dbAudit.deleted_agent_count) === 3 &&
          Number(dbAudit.deleted_version_count) >= 2 &&
          Number(dbAudit.active_version_count) === 0,
        "Agent definition cleanup audit did not show deleted agent/version rows.",
      );

      const hardCleanup = await deleteSimulationAgentDefinitionFixturesDb({
        namePrefix: name,
        organizationId,
      });
      hardCleaned = true;
      const hardCleanupSucceeded =
        Number(hardCleanup.remaining_agent_count) === 0 &&
        Number(hardCleanup.remaining_version_count) === 0 &&
        Number(hardCleanup.remaining_credential_count) === 0;
      assert(
        hardCleanupSucceeded,
        `Agent definition hard cleanup left disposable DB rows behind: ${JSON.stringify(
          hardCleanup,
        )}`,
      );

      evidence.push({
        agent_definition_id: created.id,
        agent_name: name,
        initial_version_id: initialVersionId,
        second_version_id: secondVersion.id,
        operations_agent_definition_id: operationsCreated.id,
        invalid_legacy_list_status: invalidLegacyList.status,
        invalid_legacy_create_status: invalidLegacyCreate.status,
        invalid_blank_name_status: invalidBlankName.status,
        invalid_voice_provider_status: invalidVoiceProvider.status,
        invalid_operations_create_status: invalidOperationsCreate.status,
        invalid_fetch_assistant_status: invalidFetchAssistant.status,
        invalid_legacy_edit_status: invalidLegacyEdit.status,
        invalid_create_version_status: invalidCreateVersion.status,
        invalid_activate_body_status: invalidActivateBody.status,
        invalid_restore_body_status: invalidRestoreBody.status,
        deleted_agent_count: Number(dbAudit.deleted_agent_count),
        deleted_version_count: Number(dbAudit.deleted_version_count),
        hard_cleanup_deleted_agent_count: Number(
          hardCleanup.deleted_agent_count,
        ),
        hard_cleanup_remaining_agent_count: Number(
          hardCleanup.remaining_agent_count,
        ),
      });
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
      assert(
        Array.isArray(runTests),
        "Simulation run-test list was not array-like.",
      );
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
      if (!runTest)
        skip("No simulation run tests found for read-surface coverage.");

      const runTestId = runTest.id;
      assert(
        isUuid(runTestId),
        "Selected simulation run test id was not a UUID.",
      );
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
      assert(
        detail?.id === runTestId,
        "Run-test detail returned the wrong id.",
      );
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
      assert(
        Array.isArray(runCalls),
        "Run-test call executions were not array-like.",
      );
      assert(
        Array.isArray(executions),
        "Run-test executions were not array-like.",
      );
      assert(
        Array.isArray(scenarios),
        "Run-test scenarios were not array-like.",
      );
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
        sdkPayload.run_test_id === runTestId &&
          sdkPayload.run_test_name === runTestName,
        "Run-test SDK payload returned the wrong run id/name.",
      );

      const testExecutionId =
        firstUuid(statusPayload.execution_id) ||
        firstUuid(executions.find((row) => isUuid(row?.id))?.id) ||
        firstUuid(
          apiTestExecutions.find((row) => row?.run_test === runTestId)?.id,
        );
      assert(
        !testExecutionId ||
          dbAudit.test_execution_ids.includes(testExecutionId),
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
            apiPath(
              "/simulate/test-executions/{test_execution_id}/analytics/",
              {
                test_execution_id: testExecutionId,
              },
            ),
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
            apiPath(
              "/simulate/test-executions/{test_execution_id}/transcripts/",
              {
                test_execution_id: testExecutionId,
              },
            ),
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

      const [
        callDetail,
        callTranscripts,
        callLogs,
        errorTasks,
        branchAnalysis,
      ] = await Promise.all([
        client.get(
          apiPath("/simulate/call-executions/{call_execution_id}/", {
            call_execution_id: callExecutionId,
          }),
        ),
        client.get(
          apiPath(
            "/simulate/call-executions/{call_execution_id}/transcripts/",
            {
              call_execution_id: callExecutionId,
            },
          ),
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
          apiPath(
            "/simulate/call-executions/{call_execution_id}/branch-analysis/",
            {
              call_execution_id: callExecutionId,
            },
          ),
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
    id: "SIM-API-008",
    title:
      "Simulation scenario create, list, detail, edit, add data, and delete lifecycle",
    tags: ["simulation", "scenarios", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
      user,
    }) {
      requireMutations();
      const userId = currentUserId(user);
      assert(
        userId,
        "Scenario journey requires current user id for DB fixture.",
      );
      const name = `api journey scenario ${runId}`;
      let hardCleaned = false;
      await deleteSimulationScenarioFixturesDb({
        namePrefix: name,
        organizationId,
      });
      cleanup.defer("hard-delete API journey scenario rows", () =>
        hardCleaned
          ? null
          : deleteSimulationScenarioFixturesDb({
              namePrefix: name,
              organizationId,
            }),
      );

      const fixture = await seedSimulationScenarioFixturesDb({
        namePrefix: name,
        organizationId,
        workspaceId,
        userId,
      });
      const {
        agent_definition_id: agentDefinitionId,
        dataset_id: datasetId,
        scenario_id: scenarioId,
        no_dataset_scenario_id: noDatasetScenarioId,
        no_simulator_scenario_id: noSimulatorScenarioId,
      } = fixture;
      assert(
        isUuid(agentDefinitionId) &&
          isUuid(datasetId) &&
          isUuid(scenarioId) &&
          isUuid(noDatasetScenarioId) &&
          isUuid(noSimulatorScenarioId),
        "Scenario DB fixture did not return expected UUIDs.",
      );

      const invalidListParam = await expectApiError(
        () =>
          client.get(apiPath("/simulate/scenarios/"), {
            query: { legacyFilter: "reject" },
          }),
        [400],
        "Scenario list accepted an unknown query parameter.",
      );
      const invalidListAgentId = await expectApiError(
        () =>
          client.get(apiPath("/simulate/scenarios/"), {
            query: { agent_definition_id: "not-a-uuid" },
          }),
        [400],
        "Scenario list accepted an invalid agent_definition_id.",
      );
      const invalidColumnsJson = await expectApiError(
        () =>
          client.get(apiPath("/simulate/scenarios/get-columns/"), {
            query: { scenarios: "not-json" },
          }),
        [400],
        "Scenario get-columns accepted a non-JSON scenarios value.",
      );
      const invalidCreateUnknown = await expectApiError(
        () =>
          client.post(apiPath("/simulate/scenarios/create/"), {
            name: `${name} invalid create`,
            dataset_id: datasetId,
            kind: "dataset",
            agent_definition_id: agentDefinitionId,
            legacy_extra: true,
          }),
        [400],
        "Scenario create accepted an unknown body field.",
      );
      const invalidCreateMissingAgent = await expectApiError(
        () =>
          client.post(apiPath("/simulate/scenarios/create/"), {
            name: `${name} missing agent`,
            dataset_id: datasetId,
            kind: "dataset",
          }),
        [400],
        "Scenario create accepted dataset source without agent_definition_id.",
      );
      const invalidCreateDuplicateColumns = await expectApiError(
        () =>
          client.post(apiPath("/simulate/scenarios/create/"), {
            name: `${name} duplicate columns`,
            kind: "script",
            script_url: "https://example.com/scenario-script.txt",
            agent_definition_id: agentDefinitionId,
            custom_columns: [
              { name: "same", data_type: "text", description: "First" },
              { name: "same", data_type: "text", description: "Second" },
            ],
          }),
        [400],
        "Scenario create accepted duplicate custom column names.",
      );

      const listPayload = await client.get(apiPath("/simulate/scenarios/"), {
        query: { search: name, agent_type: "text", limit: 20 },
      });
      const listedScenarios = collectionRows(listPayload);
      assert(
        listedScenarios.some((scenario) => scenario.id === scenarioId),
        "Seeded scenario was not visible through list/search.",
      );

      const columnsPayload = await client.get(
        apiPath("/simulate/scenarios/get-columns/"),
        { query: { scenarios: JSON.stringify([scenarioId]) } },
      );
      assert(
        asArray(columnsPayload.column_configs).some(
          (config) => config.id === scenarioId,
        ),
        "Scenario get-columns did not include the selected scenario.",
      );

      let dbAudit = await loadSimulationScenarioDbAudit({
        scenarioIds: [scenarioId, noDatasetScenarioId, noSimulatorScenarioId],
        datasetId,
        organizationId,
      });
      const scenarioAudit = collectionRows(dbAudit.scenarios).find(
        (scenario) => scenario.id === scenarioId,
      );
      assert(
        scenarioAudit?.organization_id === organizationId &&
          scenarioAudit?.workspace_id === workspaceId &&
          scenarioAudit?.deleted === false &&
          Number(dbAudit.dataset_row_count) === 2 &&
          Number(dbAudit.dataset_column_count) === 2 &&
          Number(dbAudit.active_graph_count) === 1,
        "Scenario DB audit did not show active workspace-owned scenario data.",
      );

      const detail = await client.get(
        apiPath("/simulate/scenarios/{scenario_id}/", {
          scenario_id: scenarioId,
        }),
      );
      assert(
        detail?.id === scenarioId &&
          detail.dataset_id === datasetId &&
          Number(detail.dataset_rows) === 2 &&
          detail.agent_type === "text",
        "Scenario detail did not return the expected dataset-backed scenario.",
      );
      assert(
        Array.isArray(detail.prompts) && detail.prompts.length === 1,
        "Scenario detail did not include simulator prompts.",
      );

      const blankEdit = await client.put(
        apiPath("/simulate/scenarios/{scenario_id}/edit/", {
          scenario_id: scenarioId,
        }),
        { description: "", prompt: "" },
      );
      assert(
        blankEdit?.scenario?.description === "",
        "Scenario edit did not persist a blank description.",
      );

      const graphEdit = await client.put(
        apiPath("/simulate/scenarios/{scenario_id}/edit/", {
          scenario_id: scenarioId,
        }),
        { graph: { nodes: [], edges: [] } },
      );
      assert(
        graphEdit?.scenario?.id === scenarioId,
        "Scenario graph edit did not return the selected scenario.",
      );

      const promptEdit = await client.put(
        apiPath("/simulate/scenarios/{scenario_id}/prompts/", {
          scenario_id: scenarioId,
        }),
        { prompts: `${name} updated simulator prompt` },
      );
      assert(
        promptEdit?.prompts === `${name} updated simulator prompt`,
        "Scenario prompts route did not persist the updated prompt.",
      );

      const noSimulatorEdit = await expectApiError(
        () =>
          client.put(
            apiPath("/simulate/scenarios/{scenario_id}/edit/", {
              scenario_id: noSimulatorScenarioId,
            }),
            { prompt: "should fail closed" },
          ),
        [400],
        "Scenario edit prompt accepted a scenario without simulator agent.",
      );
      const noSimulatorPrompts = await expectApiError(
        () =>
          client.put(
            apiPath("/simulate/scenarios/{scenario_id}/prompts/", {
              scenario_id: noSimulatorScenarioId,
            }),
            { prompts: "should fail closed" },
          ),
        [400],
        "Scenario prompts route accepted a scenario without simulator agent.",
      );

      const addRowsNoDataset = await expectApiError(
        () =>
          client.post(
            apiPath("/simulate/scenarios/{scenario_id}/add-rows/", {
              scenario_id: noDatasetScenarioId,
            }),
            { num_rows: 10 },
          ),
        [400, 402, 403],
        "Scenario add-rows accepted a scenario without dataset.",
      );
      let addRowsEntitlementDenied = isFeatureDeniedError(addRowsNoDataset);
      let addRowsInvalidCount = null;
      let addRowsSucceeded = false;
      let addRowsTemporalUnavailable = false;
      if (!addRowsEntitlementDenied) {
        addRowsInvalidCount = await expectApiError(
          () =>
            client.post(
              apiPath("/simulate/scenarios/{scenario_id}/add-rows/", {
                scenario_id: scenarioId,
              }),
              { num_rows: 9 },
            ),
          [400, 402, 403],
          "Scenario add-rows accepted num_rows below the documented minimum.",
        );
        if (isFeatureDeniedError(addRowsInvalidCount)) {
          addRowsEntitlementDenied = true;
        }
      }

      const addColumnsNoDataset = await expectApiError(
        () =>
          client.post(
            apiPath("/simulate/scenarios/{scenario_id}/add-columns/", {
              scenario_id: noDatasetScenarioId,
            }),
            {
              columns: [
                {
                  name: "not_created",
                  data_type: "text",
                  description: "Should fail",
                },
              ],
            },
          ),
        [400, 402, 403],
        "Scenario add-columns accepted a scenario without dataset.",
      );
      let addColumnsEntitlementDenied =
        isFeatureDeniedError(addColumnsNoDataset);
      let addColumnsDuplicate = null;
      let addColumnsExisting = null;
      let addColumnsSucceeded = false;
      let addColumnsTemporalUnavailable = false;
      if (!addColumnsEntitlementDenied) {
        addColumnsDuplicate = await expectApiError(
          () =>
            client.post(
              apiPath("/simulate/scenarios/{scenario_id}/add-columns/", {
                scenario_id: scenarioId,
              }),
              {
                columns: [
                  { name: "dupe", data_type: "text", description: "First" },
                  { name: "dupe", data_type: "text", description: "Second" },
                ],
              },
            ),
          [400, 402, 403],
          "Scenario add-columns accepted duplicate request column names.",
        );
        if (isFeatureDeniedError(addColumnsDuplicate)) {
          addColumnsEntitlementDenied = true;
        }
      }

      if (!addColumnsEntitlementDenied) {
        addColumnsExisting = await expectApiError(
          () =>
            client.post(
              apiPath("/simulate/scenarios/{scenario_id}/add-columns/", {
                scenario_id: scenarioId,
              }),
              {
                columns: [
                  {
                    name: "input",
                    data_type: "text",
                    description: "Existing column",
                  },
                ],
              },
            ),
          [400, 402, 403],
          "Scenario add-columns accepted a column name already present in the dataset.",
        );
        if (isFeatureDeniedError(addColumnsExisting)) {
          addColumnsEntitlementDenied = true;
        }
      }

      if (!addColumnsEntitlementDenied) {
        try {
          const addColumns = await client.post(
            apiPath("/simulate/scenarios/{scenario_id}/add-columns/", {
              scenario_id: scenarioId,
            }),
            {
              columns: [
                {
                  name: "api_journey_extra",
                  data_type: "text",
                  description: "Generated by scenario API journey.",
                },
              ],
            },
          );
          assert(
            asArray(addColumns.columns).includes("api_journey_extra"),
            "Scenario add-columns success response did not include the requested column.",
          );
          addColumnsSucceeded = true;
        } catch (error) {
          if (!isTemporalUnavailableError(error)) throw error;
          addColumnsTemporalUnavailable = true;
        }
      }

      if (!addRowsEntitlementDenied) {
        try {
          const addRows = await client.post(
            apiPath("/simulate/scenarios/{scenario_id}/add-rows/", {
              scenario_id: scenarioId,
            }),
            { num_rows: 10, description: "Generated by scenario API journey." },
          );
          assert(
            Number(addRows.num_rows) === 10 && addRows.dataset_id === datasetId,
            "Scenario add-rows success response did not report the generated rows.",
          );
          addRowsSucceeded = true;
        } catch (error) {
          if (!isTemporalUnavailableError(error)) throw error;
          addRowsTemporalUnavailable = true;
        }
      }

      dbAudit = await loadSimulationScenarioDbAudit({
        scenarioIds: [scenarioId, noDatasetScenarioId, noSimulatorScenarioId],
        datasetId,
        organizationId,
      });
      const expectedRows = addRowsSucceeded ? 12 : 2;
      const expectedColumns = addColumnsSucceeded ? 3 : 2;
      const minimumCells =
        4 +
        (addColumnsSucceeded ? 2 : 0) +
        (addRowsSucceeded ? 10 * expectedColumns : 0);
      const datasetRowCount = Number(dbAudit.dataset_row_count);
      const datasetColumnCount = Number(dbAudit.dataset_column_count);
      const datasetCellCount = Number(dbAudit.dataset_cell_count);
      const rowsMatch = addRowsTemporalUnavailable
        ? datasetRowCount >= 2
        : datasetRowCount === expectedRows;
      const columnsMatch = addColumnsTemporalUnavailable
        ? datasetColumnCount >= 2
        : datasetColumnCount === expectedColumns;
      assert(
        rowsMatch && columnsMatch && datasetCellCount >= minimumCells,
        `Scenario add-rows/add-columns DB audit did not show generated rows/cells. rows=${datasetRowCount} expected=${expectedRows} columns=${datasetColumnCount} expected=${expectedColumns} cells=${datasetCellCount} minimum=${minimumCells}`,
      );

      let createdScenarioId = null;
      let createEntitlementDenied = false;
      let createTemporalUnavailable = false;
      try {
        const createdViaApi = await client.post(
          apiPath("/simulate/scenarios/create/"),
          {
            name: `${name} created via API`,
            description: "Temporary workflow-start scenario for API journey.",
            dataset_id: datasetId,
            kind: "dataset",
            agent_definition_id: agentDefinitionId,
            no_of_rows: 10,
          },
        );
        createdScenarioId = createdViaApi?.scenario?.id;
        assert(
          isUuid(createdScenarioId) && createdViaApi.status === "processing",
          "Scenario create did not return a processing scenario envelope.",
        );
      } catch (error) {
        if (isFeatureDeniedError(error)) {
          createEntitlementDenied = true;
        } else if (isTemporalUnavailableError(error)) {
          createTemporalUnavailable = true;
        } else {
          throw error;
        }
      }

      await client.delete(
        apiPath("/simulate/scenarios/{scenario_id}/delete/", {
          scenario_id: scenarioId,
        }),
      );
      const afterDelete = collectionRows(
        await client.get(apiPath("/simulate/scenarios/"), {
          query: { search: name, limit: 20 },
        }),
      );
      assert(
        !afterDelete.some((scenario) => scenario.id === scenarioId),
        "Deleted scenario was still visible through list/search.",
      );

      dbAudit = await loadSimulationScenarioDbAudit({
        scenarioIds: [scenarioId, createdScenarioId].filter(Boolean),
        datasetId,
        organizationId,
      });
      assert(
        Number(dbAudit.deleted_scenario_count) >= 1 &&
          Number(dbAudit.deleted_graph_count) >= 1,
        "Scenario delete DB audit did not show deleted_at on scenario and graph rows.",
      );

      const hardCleanup = await deleteSimulationScenarioFixturesDb({
        namePrefix: name,
        organizationId,
      });
      hardCleaned = true;
      const cleanupSucceeded =
        Number(hardCleanup.remaining_scenario_count) === 0 &&
        Number(hardCleanup.remaining_dataset_count) === 0 &&
        Number(hardCleanup.remaining_agent_count) === 0 &&
        Number(hardCleanup.remaining_simulator_agent_count) === 0;
      assert(
        cleanupSucceeded,
        `Scenario hard cleanup left disposable DB rows behind: ${JSON.stringify(
          hardCleanup,
        )}`,
      );

      evidence.push({
        scenario_id: scenarioId,
        created_via_api_scenario_id: createdScenarioId,
        dataset_id: datasetId,
        agent_definition_id: agentDefinitionId,
        invalid_list_param_status: invalidListParam.status,
        invalid_list_agent_id_status: invalidListAgentId.status,
        invalid_columns_json_status: invalidColumnsJson.status,
        invalid_create_unknown_status: invalidCreateUnknown.status,
        invalid_create_missing_agent_status: invalidCreateMissingAgent.status,
        invalid_create_duplicate_columns_status:
          invalidCreateDuplicateColumns.status,
        no_simulator_edit_status: noSimulatorEdit.status,
        no_simulator_prompts_status: noSimulatorPrompts.status,
        add_rows_no_dataset_status: addRowsNoDataset.status,
        add_rows_invalid_count_status: addRowsInvalidCount?.status || null,
        add_rows_entitlement_denied: addRowsEntitlementDenied,
        add_columns_no_dataset_status: addColumnsNoDataset.status,
        add_columns_duplicate_status: addColumnsDuplicate?.status || null,
        add_columns_existing_status: addColumnsExisting?.status || null,
        add_columns_entitlement_denied: addColumnsEntitlementDenied,
        add_rows_succeeded: addRowsSucceeded,
        add_columns_succeeded: addColumnsSucceeded,
        add_rows_temporal_unavailable: addRowsTemporalUnavailable,
        add_columns_temporal_unavailable: addColumnsTemporalUnavailable,
        create_entitlement_denied: createEntitlementDenied,
        create_temporal_unavailable: createTemporalUnavailable,
        dataset_row_count: Number(dbAudit.dataset_row_count),
        dataset_column_count: Number(dbAudit.dataset_column_count),
        deleted_scenario_count: Number(dbAudit.deleted_scenario_count),
        deleted_graph_count: Number(dbAudit.deleted_graph_count),
        hard_cleanup_deleted_scenario_count: Number(
          hardCleanup.deleted_scenario_count,
        ),
        hard_cleanup_remaining_scenario_count: Number(
          hardCleanup.remaining_scenario_count,
        ),
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
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
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

      const created = await client.post(
        apiPath("/simulate/run-tests/create/"),
        {
          name: runName,
          description: "Temporary run test for API journey lifecycle coverage.",
          agent_definition_id: seed.agentDefinitionId,
          agent_version: seed.agentVersionId,
          scenario_ids: [seed.scenarioId],
          enable_tool_evaluation: false,
        },
      );
      assert(isUuid(created?.id), "Run-test create did not return a UUID id.");
      createdRunIds.push(created.id);
      assert(
        asArray(created.scenarios).map(String).includes(seed.scenarioId) ||
          asArray(created.scenarios_detail).some(
            (scenario) => scenario.id === seed.scenarioId,
          ),
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
        apiPath("/simulate/run-tests/{run_test_id}/", {
          run_test_id: created.id,
        }),
        {
          description:
            "Updated temporary run test for API journey lifecycle coverage.",
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

      const activeTests = await client.get(
        apiPath("/simulate/run-tests/active/"),
      );
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
      assert(
        testExecutionId,
        "Chat execute did not return a test execution id.",
      );
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
      assert(
        failedCall.status === "failed",
        "Call-execution PATCH did not set failed.",
      );
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
            apiPath(
              "/simulate/call-executions/{call_execution_id}/session-comparison/",
              {
                call_execution_id: callExecutionId,
              },
            ),
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
      assert(
        cancelled.success === true,
        "Test-execution cancel did not return success.",
      );
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

      const detailDeleted = await client.post(
        apiPath("/simulate/run-tests/create/"),
        {
          name: `${runName} detail delete`,
          description: "Temporary run test for direct detail DELETE coverage.",
          agent_definition_id: seed.agentDefinitionId,
          agent_version: seed.agentVersionId,
          scenario_ids: [seed.scenarioId],
          enable_tool_evaluation: false,
        },
      );
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
        collectionRows(dbAudit.run_tests).map((runTest) => [
          runTest.id,
          runTest,
        ]),
      );
      for (const runTestId of [created.id, detailDeleted.id]) {
        const row = deletedRuns.get(runTestId);
        assert(
          row?.deleted === true,
          "Disposable run test was not soft-deleted.",
        );
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
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
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

      cleanup.defer(
        "delete disposable simulation eval-config run tests",
        async () => {
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
        },
      );

      const created = await client.post(
        apiPath("/simulate/run-tests/create/"),
        {
          name: runName,
          description:
            "Temporary run test for eval-config API journey coverage.",
          agent_definition_id: seed.agentDefinitionId,
          agent_version: seed.agentVersionId,
          scenario_ids: [seed.scenarioId],
          enable_tool_evaluation: false,
        },
      );
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
      const primaryConfig = createdConfigs.find(
        (config) => config.name === primaryName,
      );
      const secondaryConfig = createdConfigs.find(
        (config) => config.name === secondaryName,
      );
      assert(
        isUuid(primaryConfig?.id),
        "Primary eval config did not return a UUID.",
      );
      assert(
        isUuid(secondaryConfig?.id),
        "Secondary eval config did not return a UUID.",
      );
      assertSimulationEvalMapping(primaryConfig.mapping, mapping);
      assertSimulationEvalParams(
        primaryConfig.config?.params,
        initialParams.expected,
      );

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
      assert(
        structureEval?.id === primaryConfig.id,
        "Eval structure returned wrong id.",
      );
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
      const updatedStructureEval =
        updatedStructure.eval || updatedStructure.result?.eval;
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
      assertSimulationEvalParams(
        updatedStructureEval.params,
        updatedParams.expected,
      );

      await client.delete(
        apiPath(
          "/simulate/run-tests/{run_test_id}/eval-configs/{eval_config_id}/",
          {
            run_test_id: created.id,
            eval_config_id: secondaryConfig.id,
          },
        ),
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
      assert(
        dbAudit.run_test_deleted === true,
        "Disposable run test was not deleted.",
      );
      assert(
        Number(dbAudit.active_eval_config_count) === 0,
        "Active eval configs remained after run-test cleanup.",
      );
      for (const config of collectionRows(dbAudit.eval_configs)) {
        assert(
          config.deleted === true,
          "Disposable eval config was not soft-deleted.",
        );
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
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
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

      cleanup.defer(
        "delete disposable simulation call-action run tests",
        async () => {
          for (const runTestId of createdRunIds) {
            await ignoreNotFound(() =>
              client.delete(
                apiPath("/simulate/run-tests/{run_test_id}/delete/", {
                  run_test_id: runTestId,
                }),
              ),
            );
          }
        },
      );
      cleanup.defer(
        "delete disposable simulation call-action test execution",
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
      cleanup.defer(
        "delete disposable simulation call-action call execution",
        async () => {
          if (!callExecutionId) return;
          await ignoreNotFound(() =>
            client.delete(
              apiPath("/simulate/call-executions/{call_execution_id}/delete/", {
                call_execution_id: callExecutionId,
              }),
            ),
          );
        },
      );

      const created = await client.post(
        apiPath("/simulate/run-tests/create/"),
        {
          name: runName,
          description: "Temporary run test for chat/branch action coverage.",
          agent_definition_id: seed.agentDefinitionId,
          agent_version: seed.agentVersionId,
          scenario_ids: [seed.scenarioId],
          enable_tool_evaluation: false,
        },
      );
      assert(isUuid(created?.id), "Run-test create did not return a UUID id.");
      createdRunIds.push(created.id);

      const chatExecution = await client.post(
        apiPath("/simulate/run-tests/{run_test_id}/chat-execute/", {
          run_test_id: created.id,
        }),
        {},
      );
      testExecutionId = firstUuid(chatExecution.execution_id);
      assert(
        testExecutionId,
        "Chat execute did not return a test execution id.",
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
        "Chat call-execution batch did not create a call execution.",
      );
      callExecutionId = callExecutionIds[0];

      const chatGuardError = await expectApiError(
        () =>
          client.post(
            apiPath(
              "/simulate/call-executions/{call_execution_id}/chat/send-message/",
              {
                call_execution_id: callExecutionId,
              },
            ),
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
        apiPath(
          "/simulate/call-executions/{call_execution_id}/branch-analysis/",
          {
            call_execution_id: callExecutionId,
          },
        ),
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
            apiPath(
              "/simulate/call-executions/{call_execution_id}/branch-analysis/",
              {
                call_execution_id: callExecutionId,
              },
            ),
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
        apiPath(
          "/simulate/call-executions/{call_execution_id}/branch-analysis/",
          {
            call_execution_id: callExecutionId,
          },
        ),
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
        active_test_execution_count: Number(
          dbAudit.active_test_execution_count,
        ),
        active_call_execution_count: Number(
          dbAudit.active_call_execution_count,
        ),
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
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
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

      cleanup.defer(
        "delete disposable simulation execution-guard run tests",
        async () => {
          for (const runTestId of createdRunIds) {
            await ignoreNotFound(() =>
              client.delete(
                apiPath("/simulate/run-tests/{run_test_id}/delete/", {
                  run_test_id: runTestId,
                }),
              ),
            );
          }
        },
      );
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
      cleanup.defer(
        "delete disposable simulation execution-guard call execution",
        async () => {
          if (!callExecutionId) return;
          await ignoreNotFound(() =>
            client.delete(
              apiPath("/simulate/call-executions/{call_execution_id}/delete/", {
                call_execution_id: callExecutionId,
              }),
            ),
          );
        },
      );

      const created = await client.post(
        apiPath("/simulate/run-tests/create/"),
        {
          name: runName,
          description:
            "Temporary run test for execution action guard coverage.",
          agent_definition_id: seed.agentDefinitionId,
          agent_version: seed.agentVersionId,
          scenario_ids: [seed.scenarioId],
          enable_tool_evaluation: false,
        },
      );
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
      assert(
        testExecutionId,
        "Chat execute did not return a test execution id.",
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
            apiPath(
              "/simulate/test-executions/{test_execution_id}/rerun-calls/",
              {
                test_execution_id: testExecutionId,
              },
            ),
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
            apiPath(
              "/simulate/run-tests/{run_test_id}/rerun-test-executions/",
              {
                run_test_id: created.id,
              },
            ),
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
        test_execution_rerun_text_guard_status:
          testExecutionRerunTextGuard.status,
        run_new_evals_empty_configs_status: runNewEvalsEmptyConfigs.status,
        active_test_execution_count: Number(
          dbAudit.active_test_execution_count,
        ),
        active_call_execution_count: Number(
          dbAudit.active_call_execution_count,
        ),
      });
    },
  },
  {
    id: "SIM-API-009",
    title:
      "Simulation execution successful chat actions, comparison, refresh, and rerun dispatch",
    tags: [
      "simulation",
      "run-tests",
      "test-executions",
      "call-executions",
      "chat",
      "rerun",
      "refresh",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
      user,
    }) {
      requireMutations();
      const userId = currentUserId(user);
      assert(userId, "Simulation action journey requires current user id.");

      const seed = await selectSimulationRunTestSeed(client);
      if (!seed) {
        skip(
          "No completed text simulation seed with an agent definition and version was available.",
        );
      }

      const template = await findEvalTemplateDetailByName(
        client,
        "word_count_in_range",
      );
      if (!template) {
        skip("System eval word_count_in_range was not available.");
      }

      const name = `api journey sim actions ${runId}`;
      let hardCleanedRuns = false;
      let hardCleanedScenarios = false;
      cleanup.defer("hard-delete API journey action scenario rows", () =>
        hardCleanedScenarios
          ? null
          : deleteSimulationScenarioFixturesDb({
              namePrefix: name,
              organizationId,
            }),
      );
      cleanup.defer("hard-delete API journey action run rows", () =>
        hardCleanedRuns
          ? null
          : hardDeleteSimulationRunActionFixturesDb({
              namePrefix: name,
              organizationId,
            }),
      );

      await hardDeleteSimulationRunActionFixturesDb({
        namePrefix: name,
        organizationId,
      });
      await deleteSimulationScenarioFixturesDb({
        namePrefix: name,
        organizationId,
      });

      const scenarioFixture = await seedSimulationScenarioFixturesDb({
        namePrefix: name,
        organizationId,
        workspaceId,
        userId,
      });
      const scenarioId = scenarioFixture.scenario_id;
      assert(
        isUuid(scenarioId),
        "Scenario DB fixture did not return a scenario id.",
      );

      const runName = `${name} run`;
      const created = await client.post(
        apiPath("/simulate/run-tests/create/"),
        {
          name: runName,
          description:
            "Temporary run test for successful execution action coverage.",
          agent_definition_id: seed.agentDefinitionId,
          agent_version: seed.agentVersionId,
          scenario_ids: [scenarioId],
          enable_tool_evaluation: false,
        },
      );
      assert(isUuid(created?.id), "Run-test create did not return a UUID id.");

      const chatExecution = await client.post(
        apiPath("/simulate/run-tests/{run_test_id}/chat-execute/", {
          run_test_id: created.id,
        }),
        {},
      );
      const testExecutionId = firstUuid(chatExecution.execution_id);
      assert(
        testExecutionId,
        "Chat execute did not return a test execution id.",
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
      const callExecutionId = callExecutionIds[0];

      const actionFixture = await seedSimulationRunActionStateDb({
        namePrefix: name,
        runTestId: created.id,
        testExecutionId,
        callExecutionId,
        organizationId,
        workspaceId,
      });
      assert(
        actionFixture.call_row_id,
        "Action DB fixture did not attach a dataset row to the call execution.",
      );

      const realChatProviderEnabled = envFlag(
        "API_JOURNEY_REAL_SIMULATION_CHAT",
      );
      let chatResponse = {
        chat_ended: true,
        output_message: [
          {
            role: "assistant",
            content:
              "Maximum conversation turns reached by seeded API journey fixture.",
          },
        ],
      };
      let chatDispatchMode = "db_seeded_without_provider";

      if (realChatProviderEnabled) {
        chatResponse = await client.post(
          apiPath(
            "/simulate/call-executions/{call_execution_id}/chat/send-message/",
            {
              call_execution_id: callExecutionId,
            },
          ),
          {
            messages: [
              {
                role: "user",
                content: "final message from successful action journey",
              },
            ],
          },
        );
        chatDispatchMode = "api_send_message";
        assert(
          chatResponse.chat_ended === true,
          "Successful chat send-message did not end through the max-turn path.",
        );
        assert(
          asArray(chatResponse.output_message).some((message) =>
            String(message?.content || "").includes(
              "Maximum conversation turns",
            ),
          ),
          "Successful chat send-message did not return the max-turn output message.",
        );
      }

      const completionSeed = await markSimulationRunActionFixtureCompletedDb({
        testExecutionId,
        callExecutionId,
        callExecutionIds,
        organizationId,
        workspaceId,
      });
      assert(
        Number(completionSeed.updated_call_count) === callExecutionIds.length &&
          Number(completionSeed.updated_execution_count) === 1,
        `Action completion DB seed failed: ${JSON.stringify(completionSeed)}`,
      );

      const sessionComparison = await client.get(
        apiPath(
          "/simulate/call-executions/{call_execution_id}/session-comparison/",
          {
            call_execution_id: callExecutionId,
          },
        ),
      );
      assert(
        asArray(sessionComparison.comparison_metrics).some(
          (item) => item.metric === "tokens",
        ),
        "Session comparison did not return token comparison metrics.",
      );
      assert(
        asArray(
          sessionComparison.comparison_transcripts?.base_session_transcripts,
        ).length >= 2,
        "Session comparison did not return base session transcripts.",
      );
      assert(
        asArray(
          sessionComparison.comparison_transcripts?.comparison_call_transcripts,
        ).length >= 2,
        "Session comparison did not return comparison call transcripts.",
      );

      const evalRefresh = await client.post(
        apiPath(
          "/simulate/test-executions/{test_execution_id}/eval-explanation-summary/refresh/",
          { test_execution_id: testExecutionId },
        ),
        {},
      );
      assert(
        /initiated|marked pending/i.test(String(evalRefresh.message || "")),
        "Eval explanation refresh did not acknowledge the refresh request.",
      );

      const optimiserRefresh = await client.post(
        apiPath(
          "/simulate/test-executions/{test_execution_id}/optimiser-analysis/refresh/",
          { test_execution_id: testExecutionId },
        ),
        {},
      );
      assert(
        String(optimiserRefresh.message || "").includes("initiated") &&
          optimiserRefresh.status,
        "Optimiser refresh did not create an optimiser run.",
      );

      const requiredKeys = simulationEvalRequiredKeys(template);
      const mapping = buildSimulationEvalConfigMapping(requiredKeys);
      const params = simulationEvalParamsForTemplate(template, {
        min_words: "1",
        max_words: "1000",
        k: "3",
      });
      const evalName = `sim_actions_eval_${runId.replace(/[^a-z0-9]/gi, "_")}`;
      const addEvalConfig = await client.post(
        apiPath("/simulate/run-tests/{run_test_id}/eval-configs/", {
          run_test_id: created.id,
        }),
        {
          evaluations_config: [
            {
              template_id: template.id,
              name: evalName,
              mapping,
              config: {
                params: params.submitted,
                run_config: { pass_threshold: 0.5 },
              },
              filters: [],
              error_localizer: false,
              model: "turing_small",
            },
          ],
        },
      );
      const evalConfig = asArray(addEvalConfig.created_eval_configs)[0];
      assert(
        isUuid(evalConfig?.id),
        "Eval-config create did not return a UUID.",
      );

      const runNewEvals = await client.post(
        apiPath("/simulate/run-tests/{run_test_id}/run-new-evals/", {
          run_test_id: created.id,
        }),
        {
          test_execution_ids: [testExecutionId],
          eval_config_ids: [evalConfig.id],
          enable_tool_evaluation: false,
        },
      );
      assert(
        runNewEvals.call_execution_count === callExecutionIds.length,
        "Run-new-evals did not dispatch all call executions.",
      );

      const evalOutputSeed = await markSimulationEvalOutputsCompletedDb({
        testExecutionId,
        callExecutionIds,
        evalConfigId: evalConfig.id,
        evalName,
        organizationId,
        workspaceId,
      });
      assert(
        Number(evalOutputSeed.updated_call_count) === callExecutionIds.length &&
          Number(evalOutputSeed.updated_execution_count) === 1,
        `Eval-output DB seed failed: ${JSON.stringify(evalOutputSeed)}`,
      );

      const evalSummary = await client.get(
        apiPath("/simulate/run-tests/{run_test_id}/eval-summary/", {
          run_test_id: created.id,
        }),
        { query: { execution_id: testExecutionId } },
      );
      const evalSummaryRow = assertSimulationEvalSummary(evalSummary, {
        template,
        evalConfig,
        expectedCalls: callExecutionIds.length,
      });

      const evalComparison = await client.get(
        apiPath("/simulate/run-tests/{run_test_id}/eval-summary-comparison/", {
          run_test_id: created.id,
        }),
        { query: { execution_ids: JSON.stringify([testExecutionId]) } },
      );
      const comparisonSummary = evalComparison?.[testExecutionId];
      assert(
        comparisonSummary,
        "Eval-summary comparison did not include the completed test execution.",
      );
      assertSimulationEvalSummary(comparisonSummary, {
        template,
        evalConfig,
        expectedCalls: callExecutionIds.length,
      });

      const callDetailAfterEval = await client.get(
        apiPath("/simulate/call-executions/{call_execution_id}/", {
          call_execution_id: callExecutionId,
        }),
      );
      const structuredEval = callDetailAfterEval?.eval_outputs?.[evalConfig.id];
      assert(
        structuredEval?.name === evalName &&
          structuredEval.status === "completed" &&
          structuredEval.type === "Pass/Fail" &&
          structuredEval.value === true,
        "Call detail did not expose the completed eval output in the UI shape.",
      );

      const csvExport = await client.get(
        apiPath("/simulate/export/{item_id}/", { item_id: testExecutionId }),
        { query: { type: "testexecution", status: "completed" } },
      );
      assert(
        String(csvExport).includes(evalName) &&
          String(csvExport).includes("SIM eval journey reason") &&
          String(csvExport).includes("True"),
        "Simulation CSV export did not include the completed eval output columns.",
      );

      const callRerun = await client.post(
        apiPath("/simulate/test-executions/{test_execution_id}/rerun-calls/", {
          test_execution_id: testExecutionId,
        }),
        {
          rerun_type: "eval_only",
          call_execution_ids: [callExecutionId],
        },
      );
      assert(
        callRerun.success_count === 1 &&
          asArray(callRerun.successful_reruns).includes(callExecutionId),
        "Call rerun eval_only did not accept the completed call execution.",
      );

      await markSimulationRunActionFixtureCompletedDb({
        testExecutionId,
        callExecutionId,
        callExecutionIds,
        organizationId,
        workspaceId,
      });

      const testExecutionRerunEnvelope = await client.post(
        apiPath("/simulate/run-tests/{run_test_id}/rerun-test-executions/", {
          run_test_id: created.id,
        }),
        {
          rerun_type: "eval_only",
          test_execution_ids: [testExecutionId],
        },
        { unwrap: false },
      );
      const testExecutionRerun =
        testExecutionRerunEnvelope?.result || testExecutionRerunEnvelope;
      assert(
        testExecutionRerun.overall_success_count === callExecutionIds.length,
        `Test execution rerun eval_only did not accept all completed call executions: ${JSON.stringify(
          testExecutionRerun,
        )}`,
      );

      const dbAudit = await loadSimulationRunActionDbAudit({
        namePrefix: name,
        runTestId: created.id,
        testExecutionId,
        callExecutionId,
        evalConfigId: evalConfig.id,
        organizationId,
      });
      assert(
        Number(dbAudit.chat_session_count) === 1 &&
          Number(dbAudit.trace_count) === 1 &&
          Number(dbAudit.optimiser_run_count) >= 1 &&
          Number(dbAudit.call_snapshot_count) >= 2,
        `Action DB audit did not find expected side effects: ${JSON.stringify(
          dbAudit,
        )}`,
      );

      await ignoreNotFound(() =>
        client.delete(
          apiPath("/simulate/call-executions/{call_execution_id}/delete/", {
            call_execution_id: callExecutionId,
          }),
        ),
      );
      await ignoreNotFound(() =>
        client.delete(
          apiPath("/simulate/test-executions/{test_execution_id}/delete/", {
            test_execution_id: testExecutionId,
          }),
        ),
      );
      await ignoreNotFound(() =>
        client.delete(
          apiPath("/simulate/run-tests/{run_test_id}/delete/", {
            run_test_id: created.id,
          }),
        ),
      );

      const hardCleanup = await hardDeleteSimulationRunActionFixturesDb({
        namePrefix: name,
        organizationId,
      });
      hardCleanedRuns = true;
      const scenarioCleanup = await deleteSimulationScenarioFixturesDb({
        namePrefix: name,
        organizationId,
      });
      hardCleanedScenarios = true;
      assert(
        Number(hardCleanup.remaining_run_test_count) === 0 &&
          Number(hardCleanup.remaining_test_execution_count) === 0 &&
          Number(hardCleanup.remaining_call_execution_count) === 0 &&
          Number(hardCleanup.remaining_trace_project_count) === 0,
        `Action hard cleanup left run artifacts: ${JSON.stringify(
          hardCleanup,
        )}`,
      );
      assert(
        Number(scenarioCleanup.remaining_scenario_count) === 0,
        `Action hard cleanup left scenario artifacts: ${JSON.stringify(
          scenarioCleanup,
        )}`,
      );

      evidence.push({
        seed_run_test_id: seed.runTestId,
        seed_agent_definition_id: seed.agentDefinitionId,
        run_test_id: created.id,
        scenario_id: scenarioId,
        test_execution_id: testExecutionId,
        call_execution_id: callExecutionId,
        call_execution_count: callExecutionIds.length,
        eval_config_id: evalConfig.id,
        eval_summary_template_id: evalSummaryRow.id,
        eval_summary_total_pass_rate: evalSummaryRow.total_pass_rate,
        eval_output_seed: evalOutputSeed,
        chat_ended: chatResponse.chat_ended,
        chat_dispatch_mode: chatDispatchMode,
        real_chat_provider_enabled: realChatProviderEnabled,
        session_comparison_metric_count: asArray(
          sessionComparison.comparison_metrics,
        ).length,
        optimiser_refresh_status: optimiserRefresh.status,
        run_new_evals_call_count: runNewEvals.call_execution_count,
        call_rerun_success_count: callRerun.success_count,
        test_execution_rerun_success_count:
          testExecutionRerun.overall_success_count,
        db_audit: dbAudit,
        hard_cleanup: hardCleanup,
        scenario_cleanup: scenarioCleanup,
      });
    },
  },
  {
    id: "SIM-API-010",
    title: "Simulation cancellation status readback and cleanup",
    tags: [
      "simulation",
      "run-tests",
      "test-executions",
      "call-executions",
      "cancellation",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
      user,
    }) {
      requireMutations();
      const userId = currentUserId(user);
      assert(
        userId,
        "Simulation cancellation journey requires current user id.",
      );

      const seed = await selectSimulationRunTestSeed(client);
      if (!seed) {
        skip(
          "No completed text simulation seed with an agent definition and version was available.",
        );
      }

      const name = `api journey sim cancel ${runId}`;
      let hardCleanedRuns = false;
      let hardCleanedScenarios = false;
      cleanup.defer("hard-delete API journey cancellation scenario rows", () =>
        hardCleanedScenarios
          ? null
          : deleteSimulationScenarioFixturesDb({
              namePrefix: name,
              organizationId,
            }),
      );
      cleanup.defer("hard-delete API journey cancellation run rows", () =>
        hardCleanedRuns
          ? null
          : hardDeleteSimulationRunActionFixturesDb({
              namePrefix: name,
              organizationId,
            }),
      );

      await hardDeleteSimulationRunActionFixturesDb({
        namePrefix: name,
        organizationId,
      });
      await deleteSimulationScenarioFixturesDb({
        namePrefix: name,
        organizationId,
      });

      const scenarioFixture = await seedSimulationScenarioFixturesDb({
        namePrefix: name,
        organizationId,
        workspaceId,
        userId,
      });
      const scenarioId = scenarioFixture.scenario_id;
      assert(
        isUuid(scenarioId),
        "Cancellation scenario DB fixture did not return a scenario id.",
      );

      const created = await client.post(
        apiPath("/simulate/run-tests/create/"),
        {
          name: `${name} run`,
          description:
            "Temporary run test for cancellation status readback coverage.",
          agent_definition_id: seed.agentDefinitionId,
          agent_version: seed.agentVersionId,
          scenario_ids: [scenarioId],
          enable_tool_evaluation: false,
        },
      );
      assert(isUuid(created?.id), "Run-test create did not return a UUID id.");

      const chatExecution = await client.post(
        apiPath("/simulate/run-tests/{run_test_id}/chat-execute/", {
          run_test_id: created.id,
        }),
        {},
      );
      const testExecutionId = firstUuid(chatExecution.execution_id);
      assert(
        testExecutionId,
        "Chat execute did not return a test execution id.",
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

      const statusBeforeCancel = await client.get(
        apiPath("/simulate/run-tests/{run_test_id}/status/", {
          run_test_id: created.id,
        }),
      );
      assert(
        statusBeforeCancel.execution_id === testExecutionId &&
          statusBeforeCancel.status === "pending",
        "Run-test status before cancel did not reflect the pending execution.",
      );
      assert(
        statusBeforeCancel.total_calls === callExecutionIds.length,
        "Run-test status before cancel did not count the disposable calls.",
      );

      const pendingCalls = await client.get(
        apiPath("/simulate/run-tests/{run_test_id}/call-executions/", {
          run_test_id: created.id,
        }),
        { query: { status: "pending", limit: 20, page: 1 } },
      );
      const pendingCallIds = collectionRows(pendingCalls)
        .map((row) => row?.id)
        .filter(isUuid);
      assert(
        callExecutionIds.every((id) => pendingCallIds.includes(id)),
        "Run-test call-execution readback did not include all pending calls.",
      );

      const cancelled = await client.post(
        apiPath("/simulate/test-executions/{test_execution_id}/cancel/", {
          test_execution_id: testExecutionId,
        }),
        {},
      );
      assert(
        cancelled.success === true &&
          cancelled.test_execution_id === testExecutionId,
        "Test-execution cancel did not return success for the disposable execution.",
      );

      const statusAfterCancel = await client.get(
        apiPath("/simulate/run-tests/{run_test_id}/status/", {
          run_test_id: created.id,
        }),
      );
      assert(
        statusAfterCancel.execution_id === testExecutionId &&
          statusAfterCancel.status === "cancelled",
        "Run-test status after cancel did not reflect the cancelled execution.",
      );

      const cancelledCalls = await client.get(
        apiPath("/simulate/run-tests/{run_test_id}/call-executions/", {
          run_test_id: created.id,
        }),
        { query: { status: "cancelled", limit: 20, page: 1 } },
      );
      const cancelledCallIds = collectionRows(cancelledCalls)
        .map((row) => row?.id)
        .filter(isUuid);
      assert(
        callExecutionIds.every((id) => cancelledCallIds.includes(id)),
        "Run-test call-execution readback did not include all cancelled calls.",
      );

      const callDetail = await client.get(
        apiPath("/simulate/call-executions/{call_execution_id}/", {
          call_execution_id: callExecutionIds[0],
        }),
      );
      assert(
        callDetail?.id === callExecutionIds[0] &&
          callDetail.status === "cancelled",
        "Call detail after cancel did not return the cancelled call.",
      );
      assert(
        callDetail.ended_reason === "Cancelled by user",
        "Call detail after cancel did not preserve the cancellation reason.",
      );

      const dbAudit = await loadSimulationCancellationDbAudit({
        runTestId: created.id,
        testExecutionId,
        callExecutionIds,
        organizationId,
        workspaceId,
      });
      assert(
        dbAudit.workspace_matches === true &&
          dbAudit.test_execution_status === "cancelled" &&
          dbAudit.test_execution_completed_at_set === true &&
          dbAudit.test_execution_picked_up === false &&
          Number(dbAudit.target_call_count) === callExecutionIds.length &&
          Number(dbAudit.cancelled_call_count) === callExecutionIds.length &&
          Number(dbAudit.active_call_count) === 0 &&
          Number(dbAudit.active_create_call_count) === 0,
        `Cancellation DB audit did not match expected terminal state: ${JSON.stringify(
          dbAudit,
        )}`,
      );

      for (const callExecutionId of callExecutionIds) {
        await ignoreNotFound(() =>
          client.delete(
            apiPath("/simulate/call-executions/{call_execution_id}/delete/", {
              call_execution_id: callExecutionId,
            }),
          ),
        );
      }
      await ignoreNotFound(() =>
        client.delete(
          apiPath("/simulate/test-executions/{test_execution_id}/delete/", {
            test_execution_id: testExecutionId,
          }),
        ),
      );
      await ignoreNotFound(() =>
        client.delete(
          apiPath("/simulate/run-tests/{run_test_id}/delete/", {
            run_test_id: created.id,
          }),
        ),
      );

      const hardCleanup = await hardDeleteSimulationRunActionFixturesDb({
        namePrefix: name,
        organizationId,
      });
      hardCleanedRuns = true;
      const scenarioCleanup = await deleteSimulationScenarioFixturesDb({
        namePrefix: name,
        organizationId,
      });
      hardCleanedScenarios = true;
      assert(
        Number(hardCleanup.remaining_run_test_count) === 0 &&
          Number(hardCleanup.remaining_test_execution_count) === 0 &&
          Number(hardCleanup.remaining_call_execution_count) === 0 &&
          Number(scenarioCleanup.remaining_scenario_count) === 0,
        `Cancellation cleanup left disposable artifacts: ${JSON.stringify({
          hardCleanup,
          scenarioCleanup,
        })}`,
      );

      evidence.push({
        seed_run_test_id: seed.runTestId,
        seed_agent_definition_id: seed.agentDefinitionId,
        run_test_id: created.id,
        scenario_id: scenarioId,
        test_execution_id: testExecutionId,
        call_execution_count: callExecutionIds.length,
        status_before_cancel: statusBeforeCancel.status,
        status_after_cancel: statusAfterCancel.status,
        cancelled_call_count: Number(dbAudit.cancelled_call_count),
        active_call_count: Number(dbAudit.active_call_count),
        db_audit: dbAudit,
        hard_cleanup: hardCleanup,
        scenario_cleanup: scenarioCleanup,
      });
    },
  },
  {
    id: "SIM-API-011",
    title: "Voice simulation completed call output readback and cleanup",
    tags: [
      "simulation",
      "voice",
      "run-tests",
      "test-executions",
      "call-executions",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
      user,
    }) {
      requireMutations();
      assert(
        currentUserId(user),
        "Voice simulation journey requires current user id.",
      );

      const name = `api journey voice sim ${runId}`;
      let hardCleaned = false;
      cleanup.defer("hard-delete API journey voice simulation rows", () =>
        hardCleaned
          ? null
          : hardDeleteVoiceSimulationCallOutputFixturesDb({
              namePrefix: name,
              organizationId,
            }),
      );

      await hardDeleteVoiceSimulationCallOutputFixturesDb({
        namePrefix: name,
        organizationId,
      });

      const fixture = await seedVoiceSimulationCallOutputFixtureDb({
        namePrefix: name,
        organizationId,
        workspaceId,
      });
      assert(
        isUuid(fixture.run_test_id) &&
          isUuid(fixture.test_execution_id) &&
          isUuid(fixture.call_execution_id),
        `Voice simulation seed did not return run/test/call ids: ${JSON.stringify(
          fixture,
        )}`,
      );

      const runDetail = await client.get(
        apiPath("/simulate/run-tests/{run_test_id}/", {
          run_test_id: fixture.run_test_id,
        }),
      );
      assert(
        runDetail?.id === fixture.run_test_id,
        "Voice run-test detail returned the wrong id.",
      );
      assert(
        asArray(runDetail.scenarios)
          .map(String)
          .includes(fixture.scenario_id) ||
          asArray(runDetail.scenarios_detail).some(
            (scenario) => scenario.id === fixture.scenario_id,
          ),
        "Voice run-test detail did not include the seeded scenario.",
      );

      const statusPayload = await client.get(
        apiPath("/simulate/run-tests/{run_test_id}/status/", {
          run_test_id: fixture.run_test_id,
        }),
      );
      assert(
        statusPayload.execution_id === fixture.test_execution_id &&
          statusPayload.status === "completed" &&
          Number(statusPayload.completed_calls) === 1,
        `Voice run-test status did not reflect the completed seeded call: ${JSON.stringify(
          statusPayload,
        )}`,
      );

      const callsPayload = await client.get(
        apiPath("/simulate/run-tests/{run_test_id}/call-executions/", {
          run_test_id: fixture.run_test_id,
        }),
        { query: { status: "completed", limit: 10, page: 1 } },
      );
      const callRows = collectionRows(callsPayload);
      const callListRow = callRows.find(
        (row) => row?.id === fixture.call_execution_id,
      );
      assert(
        callListRow?.status === "completed" &&
          callListRow.simulation_call_type === "voice" &&
          callListRow.audio_url === fixture.recording_url,
        "Voice call list did not expose the completed voice call output row.",
      );

      const [
        callDetail,
        callTranscripts,
        testTranscripts,
        kpis,
        performance,
        analytics,
      ] = await Promise.all([
        client.get(
          apiPath("/simulate/call-executions/{call_execution_id}/", {
            call_execution_id: fixture.call_execution_id,
          }),
        ),
        client.get(
          apiPath(
            "/simulate/call-executions/{call_execution_id}/transcripts/",
            { call_execution_id: fixture.call_execution_id },
          ),
        ),
        client.get(
          apiPath(
            "/simulate/test-executions/{test_execution_id}/transcripts/",
            { test_execution_id: fixture.test_execution_id },
          ),
        ),
        client.get(
          apiPath("/simulate/test-executions/{test_execution_id}/kpis/", {
            test_execution_id: fixture.test_execution_id,
          }),
        ),
        client.get(
          apiPath(
            "/simulate/test-executions/{test_execution_id}/performance-summary/",
            { test_execution_id: fixture.test_execution_id },
          ),
        ),
        client.get(
          apiPath("/simulate/test-executions/{test_execution_id}/analytics/", {
            test_execution_id: fixture.test_execution_id,
          }),
        ),
      ]);

      const detailTranscripts = asArray(callDetail.transcript);
      assert(
        callDetail?.id === fixture.call_execution_id &&
          callDetail.status === "completed" &&
          callDetail.simulation_call_type === "voice" &&
          callDetail.provider === "vapi" &&
          callDetail.audio_url === fixture.recording_url &&
          callDetail.recordings?.mono?.combined === fixture.recording_url &&
          callDetail.duration_seconds === 64 &&
          callDetail.turn_count === 1 &&
          callDetail.agent_talk_percentage === 60,
        `Voice call detail did not expose expected output fields: ${JSON.stringify(
          callDetail,
        )}`,
      );
      assert(
        detailTranscripts.length === 2 &&
          detailTranscripts[0].speaker_role === "user" &&
          detailTranscripts[1].speaker_role === "assistant",
        "Voice call detail transcript did not preserve ordered user/assistant turns.",
      );
      assert(
        callDetail.attributes?.raw_log ||
          callDetail.attributes?.["vapi.call_id"] === fixture.customer_call_id,
        "Voice call detail did not expose provider attributes/raw log.",
      );

      const transcriptRows = asArray(callTranscripts.transcripts);
      assert(
        callTranscripts.call_execution_id === fixture.call_execution_id &&
          Number(callTranscripts.total_transcripts) === 2 &&
          transcriptRows[0]?.speaker_role === "user" &&
          transcriptRows[1]?.speaker_role === "assistant",
        "Call transcript endpoint did not return the seeded voice transcript turns.",
      );
      assert(
        testTranscripts.test_execution_id === fixture.test_execution_id &&
          Number(testTranscripts.total_calls) === 1 &&
          Number(testTranscripts.total_transcripts) === 2,
        "Test-execution transcript endpoint did not aggregate the seeded voice call.",
      );
      assert(
        Number(kpis.total_calls) === 1 &&
          performance?.test_run_performance_metrics &&
          analytics?.metadata,
        "Voice test-execution KPI/performance/analytics reads did not return expected shapes.",
      );

      const dbAudit = await loadVoiceSimulationCallOutputDbAudit({
        runTestId: fixture.run_test_id,
        testExecutionId: fixture.test_execution_id,
        callExecutionId: fixture.call_execution_id,
        organizationId,
        workspaceId,
      });
      assert(
        dbAudit.workspace_matches === true &&
          dbAudit.agent_type === "voice" &&
          dbAudit.call_status === "completed" &&
          Number(dbAudit.transcript_count) === 2 &&
          Number(dbAudit.create_call_count) === 1 &&
          dbAudit.recording_available === true &&
          dbAudit.provider_keys?.includes("vapi"),
        `Voice simulation DB audit did not match expected output state: ${JSON.stringify(
          dbAudit,
        )}`,
      );

      const hardCleanup = await hardDeleteVoiceSimulationCallOutputFixturesDb({
        namePrefix: name,
        organizationId,
      });
      hardCleaned = true;
      assert(
        Number(hardCleanup.remaining_run_test_count) === 0 &&
          Number(hardCleanup.remaining_test_execution_count) === 0 &&
          Number(hardCleanup.remaining_call_execution_count) === 0 &&
          Number(hardCleanup.remaining_scenario_count) === 0 &&
          Number(hardCleanup.remaining_agent_count) === 0 &&
          Number(hardCleanup.remaining_simulator_agent_count) === 0,
        `Voice simulation cleanup left disposable artifacts: ${JSON.stringify(
          hardCleanup,
        )}`,
      );

      evidence.push({
        run_test_id: fixture.run_test_id,
        test_execution_id: fixture.test_execution_id,
        call_execution_id: fixture.call_execution_id,
        scenario_id: fixture.scenario_id,
        agent_definition_id: fixture.agent_definition_id,
        simulator_agent_id: fixture.simulator_agent_id,
        transcript_count: Number(dbAudit.transcript_count),
        turn_count: callDetail.turn_count,
        agent_talk_percentage: callDetail.agent_talk_percentage,
        provider: callDetail.provider,
        recording_url: fixture.recording_url,
        db_audit: dbAudit,
        hard_cleanup: hardCleanup,
      });
    },
  },
  {
    id: "SIM-API-012",
    title:
      "Agent prompt optimiser generated read routes enforce workspace scope",
    tags: [
      "simulation",
      "agent-prompt-optimiser",
      "generated-api",
      "workspace-scope",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
      user,
    }) {
      requireMutations();
      const userId = currentUserId(user);
      assert(
        userId,
        "Agent prompt optimiser journey requires current user id.",
      );

      const name = `api journey agent prompt optimiser ${runId}`;
      let hardCleaned = false;
      cleanup.defer(
        "hard-delete API journey agent prompt optimiser rows",
        () =>
          hardCleaned
            ? null
            : hardDeleteAgentPromptOptimiserFixturesDb({
                namePrefix: name,
                organizationId,
              }),
      );

      await hardDeleteAgentPromptOptimiserFixturesDb({
        namePrefix: name,
        organizationId,
      });

      const fixture = await seedAgentPromptOptimiserFixtureDb({
        namePrefix: name,
        organizationId,
        workspaceId,
        userId,
      });
      assert(
        isUuid(fixture.active_run_id) &&
          isUuid(fixture.hidden_run_id) &&
          isUuid(fixture.active_test_execution_id) &&
          isUuid(fixture.hidden_test_execution_id) &&
          isUuid(fixture.active_trial_id),
        `Agent prompt optimiser seed did not return expected ids: ${JSON.stringify(
          fixture,
        )}`,
      );

      const visibleList = await client.get(
        apiPath("/simulate/api/agent-prompt-optimiser/"),
        { query: { test_execution_id: fixture.active_test_execution_id } },
      );
      assert(
        Number(visibleList.metadata?.total_rows) === 1 &&
          asArray(visibleList.table).map((row) => row.id)[0] ===
            fixture.active_run_id,
        "Agent prompt optimiser list did not return the active workspace run.",
      );

      const hiddenList = await client.get(
        apiPath("/simulate/api/agent-prompt-optimiser/"),
        { query: { test_execution_id: fixture.hidden_test_execution_id } },
      );
      assert(
        Number(hiddenList.metadata?.total_rows) === 0 &&
          asArray(hiddenList.table).length === 0,
        "Agent prompt optimiser list leaked the other-workspace run.",
      );

      const detail = await client.get(
        apiPath("/simulate/api/agent-prompt-optimiser/{id}/", {
          id: fixture.active_run_id,
        }),
      );
      assert(
        detail.optimiser_name === `${name} active prompt run` &&
          detail.model === "gpt-4o-mini" &&
          asArray(detail.table).some(
            (row) => row.id === fixture.active_trial_id,
          ),
        "Agent prompt optimiser detail did not return active run trial data.",
      );

      const steps = await client.get(
        apiPath("/simulate/api/agent-prompt-optimiser/{id}/steps/", {
          id: fixture.active_run_id,
        }),
      );
      assert(
        asArray(steps)
          .map((row) => row.step_number)
          .join(",") === "1,2",
        "Agent prompt optimiser steps did not return ordered seeded steps.",
      );

      const graph = await client.get(
        apiPath("/simulate/api/agent-prompt-optimiser/{id}/graph/", {
          id: fixture.active_run_id,
        }),
      );
      const graphEval = graph[fixture.active_eval_config_id];
      assert(
        asArray(graphEval?.evaluations)
          .map((row) => row.trial_number)
          .join(",") === "0,1",
        "Agent prompt optimiser graph did not return baseline and trial scores.",
      );

      const prompt = await client.get(
        apiPath(
          "/simulate/api/agent-prompt-optimiser/{id}/trial/{trial_id}/prompt/",
          { id: fixture.active_run_id, trial_id: fixture.active_trial_id },
        ),
      );
      assert(
        prompt.trial_prompt === "Improved support prompt" &&
          prompt.base_prompt === "Base support prompt",
        "Agent prompt optimiser trial prompt did not return baseline and trial prompts.",
      );

      const evaluations = await client.get(
        apiPath(
          "/simulate/api/agent-prompt-optimiser/{id}/trial/{trial_id}/evaluations/",
          { id: fixture.active_run_id, trial_id: fixture.active_trial_id },
        ),
      );
      const evalRow = asArray(evaluations.table)[0];
      assert(
        evalRow?.id === fixture.active_eval_config_id &&
          Number(evalRow.score) === 0.8,
        "Agent prompt optimiser trial evaluations did not return seeded eval score.",
      );

      const scenarios = await client.get(
        apiPath(
          "/simulate/api/agent-prompt-optimiser/{id}/trial/{trial_id}/scenarios/",
          { id: fixture.active_run_id, trial_id: fixture.active_trial_id },
        ),
      );
      const scenarioRow = asArray(scenarios.table)[0];
      assert(
        scenarioRow?.id === fixture.active_trial_item_id &&
          scenarioRow.output_text === "I can help start the refund.",
        "Agent prompt optimiser trial scenarios did not return seeded item output.",
      );

      const hiddenPaths = [
        apiPath("/simulate/api/agent-prompt-optimiser/{id}/", {
          id: fixture.hidden_run_id,
        }),
        apiPath("/simulate/api/agent-prompt-optimiser/{id}/steps/", {
          id: fixture.hidden_run_id,
        }),
        apiPath("/simulate/api/agent-prompt-optimiser/{id}/graph/", {
          id: fixture.hidden_run_id,
        }),
        apiPath(
          "/simulate/api/agent-prompt-optimiser/{id}/trial/{trial_id}/prompt/",
          { id: fixture.hidden_run_id, trial_id: fixture.hidden_trial_id },
        ),
        apiPath(
          "/simulate/api/agent-prompt-optimiser/{id}/trial/{trial_id}/evaluations/",
          { id: fixture.hidden_run_id, trial_id: fixture.hidden_trial_id },
        ),
        apiPath(
          "/simulate/api/agent-prompt-optimiser/{id}/trial/{trial_id}/scenarios/",
          { id: fixture.hidden_run_id, trial_id: fixture.hidden_trial_id },
        ),
      ];
      for (const path of hiddenPaths) {
        await expectApiError(
          () => client.get(path),
          [404],
          `Agent prompt optimiser route leaked hidden run at ${path}.`,
        );
      }
      const hiddenDetailPath = apiPath(
        "/simulate/api/agent-prompt-optimiser/{id}/",
        { id: fixture.hidden_run_id },
      );
      await expectApiError(
        () => client.put(hiddenDetailPath, {}),
        [404],
        "Agent prompt optimiser PUT accepted hidden run id.",
      );
      await expectApiError(
        () => client.patch(hiddenDetailPath, { status: "failed" }),
        [404],
        "Agent prompt optimiser PATCH accepted hidden run id.",
      );
      await expectApiError(
        () => client.delete(hiddenDetailPath),
        [404],
        "Agent prompt optimiser DELETE accepted hidden run id.",
      );

      const beforeCreateAudit = await loadAgentPromptOptimiserDbAudit({
        namePrefix: name,
        organizationId,
        activeRunId: fixture.active_run_id,
        hiddenRunId: fixture.hidden_run_id,
      });
      await expectApiError(
        () =>
          client.post(apiPath("/simulate/api/agent-prompt-optimiser/"), {
            name: `${name} hidden create should fail`,
            test_execution_id: fixture.hidden_test_execution_id,
            optimiser_type: "protegi",
            model: "gpt-4o-mini",
            configuration: {
              beam_size: 2,
              num_gradients: 1,
              errors_per_gradient: 1,
              prompts_per_gradient: 1,
              num_rounds: 1,
            },
          }),
        [400],
        "Agent prompt optimiser create accepted hidden test_execution_id.",
      );
      const afterCreateAudit = await loadAgentPromptOptimiserDbAudit({
        namePrefix: name,
        organizationId,
        activeRunId: fixture.active_run_id,
        hiddenRunId: fixture.hidden_run_id,
      });
      assert(
        Number(afterCreateAudit.prompt_run_count) ===
          Number(beforeCreateAudit.prompt_run_count) &&
          Number(afterCreateAudit.hidden_prompt_run_count) ===
            Number(beforeCreateAudit.hidden_prompt_run_count),
        `Hidden create changed prompt optimiser rows: ${JSON.stringify({
          beforeCreateAudit,
          afterCreateAudit,
        })}`,
      );

      const hardCleanup = await hardDeleteAgentPromptOptimiserFixturesDb({
        namePrefix: name,
        organizationId,
      });
      hardCleaned = true;
      assert(
        Number(hardCleanup.remaining_run_test_count) === 0 &&
          Number(hardCleanup.remaining_test_execution_count) === 0 &&
          Number(hardCleanup.remaining_prompt_run_count) === 0 &&
          Number(hardCleanup.remaining_hidden_workspace_count) === 0,
        `Agent prompt optimiser cleanup left disposable artifacts: ${JSON.stringify(
          hardCleanup,
        )}`,
      );

      evidence.push({
        active_run_id: fixture.active_run_id,
        hidden_run_id: fixture.hidden_run_id,
        active_test_execution_id: fixture.active_test_execution_id,
        hidden_test_execution_id: fixture.hidden_test_execution_id,
        active_eval_config_id: fixture.active_eval_config_id,
        visible_list_rows: Number(visibleList.metadata?.total_rows),
        hidden_list_rows: Number(hiddenList.metadata?.total_rows),
        prompt_run_count: Number(afterCreateAudit.prompt_run_count),
        db_audit: afterCreateAudit,
        hard_cleanup: hardCleanup,
      });
    },
  },
  {
    id: "SIM-API-013",
    title:
      "Simulator agent lifecycle enforces workspace scope and soft-delete audit",
    tags: [
      "simulation",
      "simulator-agents",
      "mutating",
      "data-roundtrip",
      "workspace-scope",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
      user,
    }) {
      requireMutations();
      const userId = currentUserId(user);
      assert(userId, "Simulator agent journey requires current user id.");

      const name = `api journey simulator agent ${runId}`;
      let hardCleaned = false;
      cleanup.defer("hard-delete API journey simulator agent rows", () =>
        hardCleaned
          ? null
          : deleteSimulatorAgentFixturesDb({
              namePrefix: name,
              organizationId,
            }),
      );

      await deleteSimulatorAgentFixturesDb({
        namePrefix: name,
        organizationId,
      });

      const hidden = await seedOtherWorkspaceSimulatorAgentFixtureDb({
        namePrefix: name,
        organizationId,
        userId,
      });
      assert(
        isUuid(hidden.workspace_id) && isUuid(hidden.simulator_agent_id),
        `Simulator agent hidden seed did not return ids: ${JSON.stringify(
          hidden,
        )}`,
      );

      const unknownField = await expectApiError(
        () =>
          client.post(apiPath("/simulate/simulator-agents/create/"), {
            name: `${name} invalid`,
            prompt: "This request should be rejected.",
            voice_provider: "elevenlabs",
            voice_name: "marissa",
            model: "gpt-4o-mini",
            legacy_extra: true,
          }),
        [400],
        "Simulator agent create accepted an unknown field.",
      );
      assert(
        errorText(unknownField).includes("legacy_extra"),
        "Simulator agent unknown-field error did not mention legacy_extra.",
      );

      const created = await client.post(
        apiPath("/simulate/simulator-agents/create/"),
        {
          name,
          prompt: "You are a temporary simulator persona for API coverage.",
          voice_provider: "elevenlabs",
          voice_name: "marissa",
          model: "gpt-4o-mini",
          interrupt_sensitivity: 0.4,
          conversation_speed: 1.1,
          finished_speaking_sensitivity: 0.6,
          llm_temperature: 0.3,
          max_call_duration_in_minutes: 12,
          initial_message_delay: 2,
          initial_message: "Hello from the API journey.",
        },
      );
      assert(
        isUuid(created.id) &&
          created.name === name &&
          created.initial_message === "Hello from the API journey.",
        `Simulator agent create returned unexpected payload: ${JSON.stringify(
          created,
        )}`,
      );

      const list = await client.get(apiPath("/simulate/simulator-agents/"), {
        query: { search: "api journey simulator agent", limit: 50 },
      });
      const listIds = collectionRows(list).map((row) => row.id);
      assert(
        listIds.includes(created.id) &&
          !listIds.includes(hidden.simulator_agent_id),
        `Simulator agent list did not isolate workspace rows: ${JSON.stringify(
          list,
        )}`,
      );

      const detail = await client.get(
        apiPath("/simulate/simulator-agents/{agent_id}/", {
          agent_id: created.id,
        }),
      );
      assert(
        detail.id === created.id &&
          detail.name === name &&
          Number(detail.llm_temperature) === 0.3,
        "Simulator agent detail did not return created values.",
      );

      const updated = await client.put(
        apiPath("/simulate/simulator-agents/{agent_id}/edit/", {
          agent_id: created.id,
        }),
        {
          name: `${name} updated`,
          prompt: "Updated simulator prompt.",
          llm_temperature: 0.8,
          initial_message: "Updated hello.",
        },
      );
      assert(
        updated.id === created.id &&
          updated.name === `${name} updated` &&
          updated.prompt === "Updated simulator prompt." &&
          Number(updated.llm_temperature) === 0.8,
        "Simulator agent edit did not persist partial values.",
      );

      await expectApiError(
        () =>
          client.get(
            apiPath("/simulate/simulator-agents/{agent_id}/", {
              agent_id: hidden.simulator_agent_id,
            }),
          ),
        [404],
        "Simulator agent detail leaked a same-org other-workspace row.",
      );
      await expectApiError(
        () =>
          client.put(
            apiPath("/simulate/simulator-agents/{agent_id}/edit/", {
              agent_id: hidden.simulator_agent_id,
            }),
            { name: `${name} hidden edit leaked` },
          ),
        [404],
        "Simulator agent edit accepted a same-org other-workspace id.",
      );
      await expectApiError(
        () =>
          client.delete(
            apiPath("/simulate/simulator-agents/{agent_id}/delete/", {
              agent_id: hidden.simulator_agent_id,
            }),
          ),
        [404],
        "Simulator agent delete accepted a same-org other-workspace id.",
      );

      const beforeDeleteAudit = await loadSimulatorAgentDbAudit({
        agentIds: [created.id, hidden.simulator_agent_id],
        organizationId,
      });
      const createdAudit = asArray(beforeDeleteAudit.agents).find(
        (row) => row.id === created.id,
      );
      const hiddenAudit = asArray(beforeDeleteAudit.agents).find(
        (row) => row.id === hidden.simulator_agent_id,
      );
      assert(
        createdAudit?.workspace_id === workspaceId &&
          createdAudit?.deleted === false &&
          createdAudit?.name === `${name} updated` &&
          hiddenAudit?.workspace_id === hidden.workspace_id &&
          hiddenAudit?.deleted === false &&
          hiddenAudit?.name === hidden.simulator_agent_name,
        `Simulator agent DB audit before delete failed: ${JSON.stringify(
          beforeDeleteAudit,
        )}`,
      );

      const deleted = await client.delete(
        apiPath("/simulate/simulator-agents/{agent_id}/delete/", {
          agent_id: created.id,
        }),
      );
      assert(
        deleted.message === "Simulator agent deleted successfully",
        "Simulator agent delete returned unexpected response.",
      );

      const afterDeleteAudit = await loadSimulatorAgentDbAudit({
        agentIds: [created.id, hidden.simulator_agent_id],
        organizationId,
      });
      const deletedAudit = asArray(afterDeleteAudit.agents).find(
        (row) => row.id === created.id,
      );
      const hiddenAfterDelete = asArray(afterDeleteAudit.agents).find(
        (row) => row.id === hidden.simulator_agent_id,
      );
      assert(
        deletedAudit?.deleted === true &&
          deletedAudit?.deleted_at_set === true &&
          hiddenAfterDelete?.deleted === false &&
          hiddenAfterDelete?.deleted_at_set === false,
        `Simulator agent delete audit failed: ${JSON.stringify(
          afterDeleteAudit,
        )}`,
      );

      const hardCleanup = await deleteSimulatorAgentFixturesDb({
        namePrefix: name,
        organizationId,
      });
      hardCleaned = true;
      assert(
        Number(hardCleanup.remaining_simulator_agent_count) === 0 &&
          Number(hardCleanup.remaining_workspace_count) === 0,
        `Simulator agent cleanup left disposable artifacts: ${JSON.stringify(
          hardCleanup,
        )}`,
      );

      evidence.push({
        simulator_agent_id: created.id,
        hidden_simulator_agent_id: hidden.simulator_agent_id,
        hidden_workspace_id: hidden.workspace_id,
        list_count: collectionRows(list).length,
        before_delete_audit: beforeDeleteAudit,
        after_delete_audit: afterDeleteAudit,
        hard_cleanup: hardCleanup,
      });
    },
  },
  {
    id: "AGENTCC-API-001",
    title:
      "Gateway blocklist create, add words, remove words, update, and delete lifecycle",
    tags: [
      "gateway",
      "agentcc",
      "blocklists",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({ client, cleanup, runId, evidence, organizationId }) {
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
          client.delete(
            apiPath("/agentcc/blocklists/{id}/", { id: created.id }),
          ),
        ),
      );

      let dbAudit = await loadAgentccBlocklistDbAudit({
        blocklistId: created.id,
        organizationId,
      });
      assert(
        dbAudit.id === created.id &&
          dbAudit.name === name &&
          dbAudit.organization_id === organizationId &&
          dbAudit.deleted === false,
        "Blocklist DB audit did not find the created org-scoped active row.",
      );

      const duplicateCreate = await expectApiError(
        () =>
          client.post(apiPath("/agentcc/blocklists/"), {
            name,
            description: "Duplicate blocklist should be rejected.",
            words: ["blocked-alpha"],
          }),
        [400],
        "Blocklist create accepted a duplicate active name.",
      );
      assert(
        errorText(duplicateCreate).toLowerCase().includes("unique") ||
          errorText(duplicateCreate).toLowerCase().includes("duplicate"),
        "Duplicate blocklist create did not return a uniqueness error.",
      );

      const invalidCreate = await expectApiError(
        () =>
          client.post(apiPath("/agentcc/blocklists/"), {
            name: `${name}_invalid`,
            words: "blocked-alpha",
          }),
        [400],
        "Blocklist create accepted a non-array words payload.",
      );
      assert(
        errorText(invalidCreate).toLowerCase().includes("words"),
        "Invalid blocklist create did not explain the words validation failure.",
      );

      const detail = await client.get(
        apiPath("/agentcc/blocklists/{id}/", { id: created.id }),
      );
      assert(
        detail?.id === created.id &&
          detail.name === name &&
          asArray(detail.words).includes("blocked-alpha"),
        "Blocklist detail did not return the created row.",
      );

      const listed = asArray(await client.get(apiPath("/agentcc/blocklists/")));
      assert(
        listed.some((blocklist) => blocklist.id === created.id),
        "Created blocklist was not visible through list.",
      );

      const invalidAdd = await expectApiError(
        () =>
          client.post(
            apiPath("/agentcc/blocklists/{id}/add-words/", { id: created.id }),
            { words: ["blocked-gamma", 123] },
          ),
        [400],
        "Blocklist add-words accepted a non-string word.",
      );
      assert(
        errorText(invalidAdd).includes("Word at index 1 must be a string"),
        "Blocklist add-words did not identify the invalid word position.",
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

      const invalidRemove = await expectApiError(
        () =>
          client.post(
            apiPath("/agentcc/blocklists/{id}/remove-words/", {
              id: created.id,
            }),
            { words: "blocked-beta" },
          ),
        [400],
        "Blocklist remove-words accepted a non-array words payload.",
      );
      assert(
        errorText(invalidRemove).toLowerCase().includes("words must be a list"),
        "Blocklist remove-words did not explain the words-list validation failure.",
      );

      const updated = await client.patch(
        apiPath("/agentcc/blocklists/{id}/", { id: created.id }),
        {
          description:
            "Updated temporary blocklist for API journey regression.",
          is_active: false,
        },
      );
      assert(
        updated.is_active === false,
        "Blocklist update did not persist is_active.",
      );

      await client.delete(
        apiPath("/agentcc/blocklists/{id}/", { id: created.id }),
      );
      const afterDeleteList = asArray(
        await client.get(apiPath("/agentcc/blocklists/")),
      );
      assert(
        !afterDeleteList.some((blocklist) => blocklist.id === created.id),
        "Deleted blocklist was still visible in list.",
      );

      dbAudit = await loadAgentccBlocklistDbAudit({
        blocklistId: created.id,
        organizationId,
      });
      assert(
        dbAudit.deleted === true && dbAudit.deleted_at_set === true,
        "Blocklist DB audit did not show soft-delete state.",
      );

      evidence.push({
        blocklist_id: created.id,
        blocklist_name: name,
        duplicate_create_status: duplicateCreate.status,
        invalid_create_status: invalidCreate.status,
        invalid_add_status: invalidAdd.status,
        invalid_remove_status: invalidRemove.status,
        deleted_at_set: dbAudit.deleted_at_set,
      });
    },
  },
  {
    id: "AGENTCC-API-002",
    title:
      "Gateway custom property schema create, validate, update, and delete lifecycle",
    tags: [
      "gateway",
      "agentcc",
      "custom-properties",
      "mutating",
      "data-roundtrip",
    ],
    async run({ client, cleanup, runId, evidence, organizationId }) {
      requireMutations();
      const name = `api_journey_property_${runId.replace(/[^a-z0-9]/gi, "_")}`;

      const created = await client.post(
        apiPath("/agentcc/custom-properties/"),
        {
          name,
          description: "Temporary custom property for API journey regression.",
          property_type: "enum",
          required: true,
          allowed_values: ["alpha", "beta"],
          default_value: "alpha",
        },
      );
      assert(created?.id, "Custom property schema create did not return id.");
      cleanup.defer("hard-delete API journey custom property rows", () =>
        deleteAgentccCustomPropertyRowsDb({ name, organizationId }),
      );
      cleanup.defer("delete API journey custom property", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/agentcc/custom-properties/{id}/", { id: created.id }),
          ),
        ),
      );
      assert(
        created.property_type === "enum" &&
          created.required === true &&
          created.default_value === "alpha" &&
          asArray(created.allowed_values).includes("beta"),
        "Custom property create did not persist enum schema fields.",
      );

      let dbAudit = await loadAgentccCustomPropertyDbAudit({
        propertyId: created.id,
        organizationId,
      });
      assert(
        dbAudit.organization_id === organizationId &&
          dbAudit.deleted === false &&
          dbAudit.deleted_at_set === false,
        "Custom property DB audit did not show active org-owned row.",
      );

      const duplicateCreate = await expectApiError(
        () =>
          client.post(apiPath("/agentcc/custom-properties/"), {
            name,
            description: "Duplicate property should be rejected.",
            property_type: "string",
          }),
        [400],
        "Custom property duplicate active name was accepted.",
      );
      assert(
        errorText(duplicateCreate).toLowerCase().includes("unique") ||
          errorText(duplicateCreate).toLowerCase().includes("duplicate"),
        "Custom property duplicate error did not mention uniqueness.",
      );

      const invalidType = await expectApiError(
        () =>
          client.post(apiPath("/agentcc/custom-properties/"), {
            name: `${name}_bad_type`,
            property_type: "object",
          }),
        [400],
        "Custom property invalid property_type was accepted.",
      );
      assert(
        errorText(invalidType).toLowerCase().includes("property_type"),
        "Custom property invalid type error did not mention property_type.",
      );

      const invalidEnum = await expectApiError(
        () =>
          client.post(apiPath("/agentcc/custom-properties/"), {
            name: `${name}_bad_enum`,
            property_type: "enum",
            allowed_values: [],
          }),
        [400],
        "Custom property enum without allowed values was accepted.",
      );
      assert(
        errorText(invalidEnum).toLowerCase().includes("allowed_values"),
        "Custom property invalid enum error did not mention allowed_values.",
      );

      const invalidDefault = await expectApiError(
        () =>
          client.post(apiPath("/agentcc/custom-properties/"), {
            name: `${name}_bad_default`,
            property_type: "enum",
            allowed_values: ["alpha", "beta"],
            default_value: "gamma",
          }),
        [400],
        "Custom property enum default outside allowed values was accepted.",
      );
      assert(
        errorText(invalidDefault).toLowerCase().includes("default_value"),
        "Custom property invalid default error did not mention default_value.",
      );

      const invalidNumberDefault = await expectApiError(
        () =>
          client.post(apiPath("/agentcc/custom-properties/"), {
            name: `${name}_bad_number_default`,
            property_type: "number",
            default_value: true,
          }),
        [400],
        "Custom property boolean number default was accepted.",
      );
      assert(
        errorText(invalidNumberDefault).toLowerCase().includes("default_value"),
        "Custom property invalid number default error did not mention default_value.",
      );

      const invalidValidatePayload = await expectApiError(
        () =>
          client.post(apiPath("/agentcc/custom-properties/validate/"), {
            properties: [],
          }),
        [400],
        "Custom property validate accepted a non-object properties payload.",
      );
      assert(
        errorText(invalidValidatePayload).includes(
          "properties must be a JSON object",
        ),
        "Custom property validate payload error did not mention object requirement.",
      );

      const valid = await client.post(
        apiPath("/agentcc/custom-properties/validate/"),
        {
          properties: { [name]: "beta", unknown_property: "allowed" },
        },
      );
      assert(
        valid.valid === true,
        "Custom property validate rejected a valid value or unknown property.",
      );

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
      assert(
        updated.name === name && updated.default_value === "alpha",
        "Custom property PATCH did not preserve name/default_value.",
      );

      const putUpdated = await client.put(
        apiPath("/agentcc/custom-properties/{id}/", { id: created.id }),
        {
          name,
          description:
            "PUT-updated custom property for API journey regression.",
          property_type: "enum",
          required: false,
          allowed_values: ["alpha", "beta", "gamma"],
          default_value: "gamma",
        },
      );
      assert(
        putUpdated.description.includes("PUT-updated") &&
          putUpdated.default_value === "gamma",
        "Custom property PUT did not persist full schema payload.",
      );

      const validAfterUpdate = await client.post(
        apiPath("/agentcc/custom-properties/validate/"),
        {
          properties: { [name]: "gamma" },
        },
      );
      assert(
        validAfterUpdate.valid === true,
        "Custom property validate did not honor updated allowed_values.",
      );

      await client.delete(
        apiPath("/agentcc/custom-properties/{id}/", { id: created.id }),
      );
      const listed = asArray(
        await client.get(apiPath("/agentcc/custom-properties/")),
      );
      assert(
        !listed.some((schema) => schema.id === created.id),
        "Deleted custom property schema was still visible in list.",
      );

      dbAudit = await loadAgentccCustomPropertyDbAudit({
        propertyId: created.id,
        organizationId,
      });
      assert(
        dbAudit.deleted === true && dbAudit.deleted_at_set === true,
        "Custom property DB audit did not show soft-delete timestamp.",
      );

      const hardCleanup = await deleteAgentccCustomPropertyRowsDb({
        name,
        organizationId,
      });
      assert(
        Number(hardCleanup.remaining_count) === 0,
        "Custom property hard cleanup left disposable rows behind.",
      );

      evidence.push({
        custom_property_id: created.id,
        custom_property_name: name,
        duplicate_create_status: duplicateCreate.status,
        invalid_type_status: invalidType.status,
        invalid_enum_status: invalidEnum.status,
        invalid_default_status: invalidDefault.status,
        invalid_number_default_status: invalidNumberDefault.status,
        invalid_validate_payload_status: invalidValidatePayload.status,
        deleted_at_set: dbAudit.deleted_at_set,
        hard_cleanup_deleted_count: Number(hardCleanup.deleted_count),
        hard_cleanup_remaining_count: Number(hardCleanup.remaining_count),
      });
    },
  },
  {
    id: "AGENTCC-API-003",
    title: "Gateway API key create, update, revoke, and delete lifecycle",
    tags: [
      "gateway",
      "agentcc",
      "api-keys",
      "mutating",
      "data-roundtrip",
      "db-audit",
      "authz",
    ],
    async run({ client, cleanup, runId, evidence, organizationId, user }) {
      requireMutations();
      const name = `api_journey_key_${runId.replace(/[^a-z0-9]/gi, "_")}`;
      const userId = currentUserId(user);
      assert(
        organizationId,
        "Gateway API key journey requires organization id.",
      );
      let hardCleaned = false;
      cleanup.defer("hard-delete API journey gateway API key rows", () =>
        hardCleaned
          ? null
          : deleteAgentccApiKeyFixtureDb({ namePrefix: name, organizationId }),
      );
      cleanup.defer("hard-delete hidden API key project fixture", () =>
        deleteAgentccApiKeyProjectFixtureDb({
          namePrefix: name,
          organizationId,
        }),
      );

      const hiddenProject = await seedOtherWorkspaceAgentccProjectFixtureDb({
        namePrefix: name,
        organizationId,
        userId,
      });
      const hiddenProjectCreate = await expectApiError(
        () =>
          client.post(apiPath("/agentcc/api-keys/"), {
            name: `${name}_hidden_project`,
            project_id: hiddenProject.project_id,
          }),
        [404],
        "Gateway API key create accepted a same-org other-workspace project_id.",
      );
      assert(
        errorText(hiddenProjectCreate).toLowerCase().includes("project"),
        "Hidden project guard did not mention project lookup.",
      );

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

      const putUpdated = await client.put(
        apiPath("/agentcc/api-keys/{id}/", { id: created.id }),
        {
          name: `${name}_updated_put`,
          owner: "api-journey-put",
          allowed_providers: ["openai", "anthropic"],
          metadata: { source: "api-journey", putUpdated: true },
        },
      );
      assert(
        putUpdated.name === `${name}_updated_put` &&
          asArray(putUpdated.allowed_providers).includes("anthropic"),
        "Gateway API key PUT did not route through the validated update path.",
      );

      const detail = await client.get(
        apiPath("/agentcc/api-keys/{id}/", { id: created.id }),
      );
      assert(
        detail?.id === created.id,
        "Gateway API key detail returned wrong id.",
      );
      assert(
        !Object.prototype.hasOwnProperty.call(detail, "key"),
        "Gateway API key detail leaked the raw key after creation.",
      );

      const revoked = await client.post(
        apiPath("/agentcc/api-keys/{id}/revoke/", { id: created.id }),
        {},
      );
      assert(
        revoked.status === "revoked",
        "Gateway API key revoke did not persist.",
      );

      await client.delete(
        apiPath("/agentcc/api-keys/{id}/", { id: created.id }),
      );
      const listed = asArray(await client.get(apiPath("/agentcc/api-keys/")));
      assert(
        !listed.some((key) => key.id === created.id),
        "Deleted gateway API key was still visible in list.",
      );

      const expectedKeyHash = createHash("sha256")
        .update(created.key)
        .digest("hex");
      const dbAudit = await loadAgentccApiKeyDbAudit({
        keyId: created.id,
        organizationId,
        expectedKeyHash,
        expectedName: `${name}_updated_put`,
      });
      assert(dbAudit.row_count === 1, "Gateway API key DB audit found no row.");
      assert(
        dbAudit.key_hash_matches_expected === true,
        "Gateway API key DB audit did not match the raw key hash.",
      );
      assert(
        dbAudit.deleted === true && dbAudit.deleted_at_set === true,
        "Gateway API key delete did not stamp deleted/deleted_at.",
      );
      assert(
        dbAudit.status === "revoked",
        "Gateway API key DB audit did not preserve revoked status before delete.",
      );

      const hardCleanup = await deleteAgentccApiKeyFixtureDb({
        namePrefix: name,
        organizationId,
      });
      hardCleaned = true;
      assert(
        Number(hardCleanup.remaining_count) === 0,
        "Gateway API key hard cleanup left disposable rows behind.",
      );

      evidence.push({
        api_key_id: created.id,
        key_name: name,
        hidden_project_create_status: hiddenProjectCreate.status,
        deleted_at_set: dbAudit.deleted_at_set,
        key_hash_matches_expected: dbAudit.key_hash_matches_expected,
        hard_cleanup_deleted_count: Number(hardCleanup.deleted_count),
        hard_cleanup_remaining_count: Number(hardCleanup.remaining_count),
      });
    },
  },
  {
    id: "AGENTCC-API-014",
    title: "Gateway startup API key bulk sync admin-token contract",
    tags: [
      "gateway",
      "agentcc",
      "api-keys",
      "admin-token",
      "startup-sync",
      "mutating",
      "db-audit",
    ],
    async run({
      cleanup,
      runId,
      evidence,
      apiBase,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const adminToken = process.env.AGENTCC_ADMIN_TOKEN;
      if (!adminToken) {
        skip(
          "Set AGENTCC_ADMIN_TOKEN to run the gateway API-key bulk sync journey.",
        );
      }
      assert(
        organizationId && workspaceId,
        "Gateway API-key bulk journey requires organization and workspace ids.",
      );

      const marker = `api_journey_key_bulk_${runId.replace(/[^a-z0-9]/gi, "_")}`;
      let hardCleaned = false;
      cleanup.defer("hard-delete API-key bulk sync fixtures", () =>
        hardCleaned
          ? null
          : deleteAgentccApiKeyBulkFixtureDb({ marker, organizationId }),
      );

      const fixture = await seedAgentccApiKeyBulkFixtureDb({
        marker,
        organizationId,
        workspaceId,
      });
      const adminClient = createApiClient({
        apiBase,
        accessToken: adminToken,
      });
      const wrongAdminClient = createApiClient({
        apiBase,
        accessToken: `${adminToken}-wrong`,
      });

      const wrongToken = await expectApiError(
        () => wrongAdminClient.get(apiPath("/agentcc/api-keys/bulk/")),
        [403],
        "Gateway API-key bulk endpoint accepted a wrong admin token.",
      );

      const rows = asArray(
        await adminClient.get(apiPath("/agentcc/api-keys/bulk/")),
      );
      const activeRow = rows.find(
        (row) => row.id === fixture.active_gateway_key_id,
      );
      assert(
        activeRow,
        "Gateway API-key bulk response did not include the active hashed key fixture.",
      );
      assert(
        activeRow.key_hash === fixture.active_key_hash,
        "Gateway API-key bulk response did not preserve key_hash.",
      );
      assert(
        asArray(activeRow.models).includes("gpt-4o") &&
          asArray(activeRow.providers).includes("openai"),
        "Gateway API-key bulk response did not preserve model/provider ACLs.",
      );
      assert(
        activeRow.metadata?.org_id === organizationId &&
          activeRow.metadata?.enabled === "true" &&
          activeRow.metadata?.limits === '{"rpm":10}' &&
          activeRow.metadata?.tags === '["startup","sync"]' &&
          !Object.prototype.hasOwnProperty.call(activeRow.metadata, "none"),
        "Gateway API-key bulk metadata was not normalized to map[string]string with org_id.",
      );
      assert(
        !Object.prototype.hasOwnProperty.call(activeRow, "key") &&
          !JSON.stringify(activeRow).includes(fixture.active_raw_key),
        "Gateway API-key bulk response leaked raw key material.",
      );

      const returnedIds = new Set(rows.map((row) => row.id));
      assert(
        !returnedIds.has(fixture.no_hash_gateway_key_id) &&
          !returnedIds.has(fixture.revoked_gateway_key_id) &&
          !returnedIds.has(fixture.deleted_gateway_key_id),
        "Gateway API-key bulk response included no-hash, revoked, or deleted key fixtures.",
      );

      const hardCleanup = await deleteAgentccApiKeyBulkFixtureDb({
        marker,
        organizationId,
      });
      hardCleaned = true;
      assert(
        Number(hardCleanup.remaining_count) === 0,
        "Gateway API-key bulk fixture cleanup left rows behind.",
      );

      evidence.push({
        wrong_token_status: wrongToken.status,
        active_gateway_key_id: fixture.active_gateway_key_id,
        metadata_org_id: activeRow.metadata.org_id,
        metadata_enabled: activeRow.metadata.enabled,
        key_hash_matches: activeRow.key_hash === fixture.active_key_hash,
        no_hash_omitted: !returnedIds.has(fixture.no_hash_gateway_key_id),
        revoked_omitted: !returnedIds.has(fixture.revoked_gateway_key_id),
        deleted_omitted: !returnedIds.has(fixture.deleted_gateway_key_id),
        hard_cleanup_deleted_count: Number(hardCleanup.deleted_count),
        hard_cleanup_remaining_count: Number(hardCleanup.remaining_count),
      });
    },
  },
  {
    id: "AGENTCC-API-004",
    title: "Gateway webhook create, update, event retry, and delete lifecycle",
    tags: [
      "gateway",
      "agentcc",
      "webhooks",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({ client, cleanup, runId, evidence, organizationId }) {
      requireMutations();
      const name = `api_journey_webhook_${runId.replace(/[^a-z0-9]/gi, "_")}`;
      const rawSecret = `secret-${runId}`;

      let created;
      let createMode = "api";
      let createEntitlementStatus = null;
      try {
        created = await client.post(apiPath("/agentcc/webhooks/"), {
          name,
          url: "https://example.com/futureagi-api-journey-webhook",
          secret: rawSecret,
          events: ["request.completed", "error.occurred"],
          is_active: true,
          headers: { "X-API-Journey": runId },
          description: "Temporary webhook for API journey regression.",
        });
      } catch (error) {
        if (!isEntitlementDeniedError(error)) throw error;
        createMode = "db_seeded_after_create_entitlement";
        createEntitlementStatus = error.status;
        created = await seedAgentccWebhookDb({
          webhookId: randomUUID(),
          organizationId,
          name,
          rawSecret,
          runId,
        });
      }
      assert(created?.id, "Gateway webhook create did not return id.");
      cleanup.defer("hard delete API journey gateway webhook fixture", () =>
        deleteAgentccWebhookFixtureDb({
          webhookId: created.id,
          organizationId,
        }),
      );

      let dbAudit = await loadAgentccWebhookDbAudit({
        webhookId: created.id,
        organizationId,
        eventIds: [],
        rawSecret,
      });
      assert(
        dbAudit.id === created.id &&
          dbAudit.name === name &&
          dbAudit.organization_id === organizationId &&
          dbAudit.raw_secret_present === true &&
          dbAudit.deleted === false,
        "Gateway webhook DB audit did not find the created org-scoped active row.",
      );

      let duplicateStatus = null;
      if (createMode === "api") {
        const duplicateCreate = await expectApiError(
          () =>
            client.post(apiPath("/agentcc/webhooks/"), {
              name,
              url: "https://example.com/futureagi-api-journey-webhook-duplicate",
              events: ["request.completed"],
            }),
          [400],
          "Gateway webhook create accepted a duplicate active name.",
        );
        duplicateStatus = duplicateCreate.status;
        assert(
          errorText(duplicateCreate).toLowerCase().includes("unique") ||
            errorText(duplicateCreate).toLowerCase().includes("duplicate"),
          "Duplicate webhook create did not return a uniqueness error.",
        );
      }

      const privateUrlError = await expectApiError(
        () =>
          client.patch(apiPath("/agentcc/webhooks/{id}/", { id: created.id }), {
            url: "https://127.0.0.1/futureagi-api-journey-webhook",
          }),
        [400],
        "Gateway webhook update accepted a private loopback URL.",
      );
      assert(
        errorText(privateUrlError).toLowerCase().includes("private") ||
          errorText(privateUrlError).toLowerCase().includes("internal"),
        "Private webhook URL guard did not explain the URL safety failure.",
      );

      const invalidEventsPayload = await expectApiError(
        () =>
          client.patch(apiPath("/agentcc/webhooks/{id}/", { id: created.id }), {
            events: "request.completed",
          }),
        [400],
        "Gateway webhook update accepted non-array events.",
      );
      assert(
        errorText(invalidEventsPayload).toLowerCase().includes("events"),
        "Invalid webhook events payload did not explain the events validation failure.",
      );

      const invalidEventName = await expectApiError(
        () =>
          client.patch(apiPath("/agentcc/webhooks/{id}/", { id: created.id }), {
            events: ["request.unknown"],
          }),
        [400],
        "Gateway webhook update accepted an unsupported event type.",
      );
      assert(
        errorText(invalidEventName).toLowerCase().includes("invalid event"),
        "Invalid webhook event type did not return an event validation error.",
      );

      const updated = await client.patch(
        apiPath("/agentcc/webhooks/{id}/", { id: created.id }),
        {
          description: "Updated temporary webhook for API journey regression.",
          events: ["request.completed"],
          is_active: false,
          headers: { "X-API-Journey": `${runId}-updated` },
        },
      );
      assert(
        updated.is_active === false &&
          asArray(updated.events).length === 1 &&
          asArray(updated.events).includes("request.completed") &&
          updated.headers?.["X-API-Journey"] === `${runId}-updated`,
        "Gateway webhook update did not persist active/events/header fields.",
      );

      const detail = await client.get(
        apiPath("/agentcc/webhooks/{id}/", { id: created.id }),
      );
      assert(
        detail?.id === created.id,
        "Gateway webhook detail returned wrong id.",
      );
      assert(
        !Object.prototype.hasOwnProperty.call(detail, "secret"),
        "Gateway webhook detail leaked the write-only secret.",
      );

      const eventIds = [randomUUID(), randomUUID()];
      const seedAudit = await seedAgentccWebhookEventsDb({
        organizationId,
        webhookId: created.id,
        eventIds,
        marker: name,
      });
      assert(
        Number(seedAudit.inserted_count) === 2,
        "Gateway webhook event DB seed did not insert the expected rows.",
      );

      const events = asArray(
        await client.get(apiPath("/agentcc/webhook-events/"), {
          query: { webhook_id: created.id },
        }),
      );
      assert(
        events.length === 2 &&
          events.every((event) => event.webhook === created.id) &&
          events.every((event) => event.webhook_name === name),
        "Gateway webhook events list did not filter by webhook_id.",
      );

      const failedEvents = asArray(
        await client.get(apiPath("/agentcc/webhook-events/"), {
          query: {
            webhook_id: created.id,
            status: "failed",
            event_type: "error.occurred",
          },
        }),
      );
      assert(
        failedEvents.length === 1 &&
          failedEvents[0]?.id === eventIds[0] &&
          failedEvents[0]?.last_response_code === 503 &&
          failedEvents[0]?.last_error?.includes(name),
        "Gateway webhook event status/event_type filters did not return the seeded failed event.",
      );

      const failedDetail = await client.get(
        apiPath("/agentcc/webhook-events/{id}/", { id: eventIds[0] }),
      );
      assert(
        failedDetail?.id === eventIds[0] &&
          failedDetail.webhook === created.id &&
          failedDetail.max_attempts === 5,
        "Gateway webhook event detail did not return the seeded failed event.",
      );

      const retried = await client.post(
        apiPath("/agentcc/webhook-events/{id}/retry/", { id: eventIds[0] }),
        {},
      );
      assert(
        retried.status === "pending" &&
          retried.attempts === 0 &&
          !retried.last_error &&
          !retried.next_retry_at,
        "Gateway webhook event retry did not reset failed event state.",
      );

      const deliveredRetryGuard = await expectApiError(
        () =>
          client.post(
            apiPath("/agentcc/webhook-events/{id}/retry/", {
              id: eventIds[1],
            }),
            {},
          ),
        [400],
        "Gateway webhook event retry accepted an already delivered event.",
      );
      assert(
        errorText(deliveredRetryGuard).toLowerCase().includes("delivered"),
        "Delivered webhook retry guard did not explain the delivered-state rejection.",
      );

      await client.delete(
        apiPath("/agentcc/webhooks/{id}/", { id: created.id }),
      );
      const listed = asArray(await client.get(apiPath("/agentcc/webhooks/")));
      assert(
        !listed.some((webhook) => webhook.id === created.id),
        "Deleted gateway webhook was still visible in list.",
      );

      dbAudit = await loadAgentccWebhookDbAudit({
        webhookId: created.id,
        organizationId,
        eventIds,
        rawSecret,
      });
      assert(
        dbAudit.deleted === true &&
          dbAudit.deleted_at_set === true &&
          dbAudit.event_count === 2 &&
          dbAudit.pending_event_count === 1 &&
          dbAudit.delivered_event_count === 1,
        "Gateway webhook DB audit did not confirm soft-delete and retried event state.",
      );

      const cleanupAudit = await deleteAgentccWebhookFixtureDb({
        webhookId: created.id,
        organizationId,
      });
      assert(
        Number(cleanupAudit.remaining_webhook_count) === 0 &&
          Number(cleanupAudit.remaining_event_count) === 0,
        `Gateway webhook fixture hard cleanup left disposable rows behind: ${JSON.stringify(cleanupAudit)}`,
      );

      evidence.push({
        webhook_id: created.id,
        webhook_name: name,
        private_url_status: privateUrlError.status,
        invalid_events_status: invalidEventsPayload.status,
        invalid_event_name_status: invalidEventName.status,
        duplicate_status: duplicateStatus,
        create_mode: createMode,
        create_entitlement_status: createEntitlementStatus,
        event_count: dbAudit.event_count,
        retry_status: retried.status,
        delivered_retry_guard_status: deliveredRetryGuard.status,
        deleted_at_set: dbAudit.deleted_at_set,
        cleanup_remaining_webhook_count: cleanupAudit.remaining_webhook_count,
        cleanup_remaining_event_count: cleanupAudit.remaining_event_count,
      });
    },
  },
  {
    id: "AGENTCC-API-005",
    title:
      "Gateway routing policy create, activate, list, and delete lifecycle",
    tags: [
      "gateway",
      "agentcc",
      "routing-policies",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({ client, cleanup, runId, evidence, organizationId }) {
      requireMutations();
      const name = `api_journey_routing_${runId.replace(/[^a-z0-9]/gi, "_")}`;

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
      cleanup.defer(
        "restore AgentCC routing-policy org config versions",
        restoreOrgConfig,
      );

      const invalidCreate = await expectApiError(
        () =>
          client.post(apiPath("/agentcc/routing-policies/"), {
            name: `${name}_invalid`,
            config: ["not", "an", "object"],
          }),
        [400],
        "Gateway routing policy create accepted a non-object config.",
      );
      assert(
        errorText(invalidCreate).toLowerCase().includes("config"),
        "Routing policy invalid config guard did not mention config.",
      );

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
      assert(
        created.version >= 1,
        "Gateway routing policy did not get a version.",
      );
      assert(
        created.created_by,
        "Gateway routing policy create did not return created_by.",
      );

      let dbAudit = await loadAgentccRoutingPolicyDbAudit({
        policyIds: [created.id],
        organizationId,
        policyName: name,
        createdConfigIds: [],
      });
      assert(
        dbAudit.policy_count === 1 &&
          dbAudit.active_policy_count === 1 &&
          dbAudit.policy_versions.includes(created.version),
        "Routing policy DB audit did not find the created active policy.",
      );

      const detail = await client.get(
        apiPath("/agentcc/routing-policies/{id}/", { id: created.id }),
      );
      assert(
        detail?.id === created.id &&
          detail.name === name &&
          detail.config?.rules?.[0]?.route_to?.model === "gpt-4o-mini",
        "Gateway routing policy detail did not return the created policy/config.",
      );

      const updateGuard = await expectApiError(
        () =>
          client.patch(
            apiPath("/agentcc/routing-policies/{id}/", { id: created.id }),
            { description: "patch should be rejected" },
          ),
        [400],
        "Gateway routing policy PATCH unexpectedly mutated a versioned policy.",
      );
      assert(
        errorText(updateGuard).toLowerCase().includes("versioned"),
        "Routing policy PATCH guard did not explain versioned policy behavior.",
      );

      const secondVersion = await client.post(
        apiPath("/agentcc/routing-policies/"),
        {
          name,
          description: "Second temporary routing policy version.",
          config: {
            rules: [
              {
                when: { provider: "openai" },
                route_to: { provider: "openai", model: "gpt-4.1-mini" },
              },
            ],
          },
          is_active: true,
        },
      );
      assert(
        secondVersion?.id &&
          secondVersion.id !== created.id &&
          secondVersion.version === created.version + 1 &&
          secondVersion.is_active === true,
        "Gateway routing policy create did not create the next active version.",
      );
      cleanup.defer("delete API journey routing policy v2", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/agentcc/routing-policies/{id}/", {
              id: secondVersion.id,
            }),
          ),
        ),
      );

      const afterVersionList = asArray(
        await client.get(apiPath("/agentcc/routing-policies/")),
      );
      const firstVersionListRow = afterVersionList.find(
        (policy) => policy.id === created.id,
      );
      assert(
        firstVersionListRow?.is_active === false &&
          afterVersionList.some(
            (policy) => policy.id === secondVersion.id && policy.is_active,
          ),
        "Gateway routing policy version create did not deactivate the older version.",
      );

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
      assert(
        !activePolicies.some((policy) => policy.id === secondVersion.id),
        "Gateway routing policy active-only list still included the deactivated version.",
      );

      const syncResult = await client.post(
        apiPath("/agentcc/routing-policies/sync/"),
        {},
      );
      assert(
        syncResult?.synced === true &&
          typeof syncResult.gateway_synced === "boolean",
        "Gateway routing policy manual sync did not return sync flags.",
      );

      await client.delete(
        apiPath("/agentcc/routing-policies/{id}/", { id: created.id }),
      );
      await client.delete(
        apiPath("/agentcc/routing-policies/{id}/", { id: secondVersion.id }),
      );
      const listed = asArray(
        await client.get(apiPath("/agentcc/routing-policies/")),
      );
      assert(
        !listed.some(
          (policy) =>
            policy.id === created.id || policy.id === secondVersion.id,
        ),
        "Deleted gateway routing policy was still visible in list.",
      );

      const restoreEvidence = await restoreOrgConfig();
      dbAudit = await loadAgentccRoutingPolicyDbAudit({
        policyIds: [created.id, secondVersion.id],
        organizationId,
        policyName: name,
        createdConfigIds: restoreEvidence.deleted_config_ids,
      });
      assert(
        dbAudit.policy_count === 2 &&
          dbAudit.deleted_policy_count === 2 &&
          dbAudit.deleted_at_count === 2 &&
          dbAudit.active_policy_count === 0,
        "Routing policy DB audit did not show both versions soft-deleted.",
      );
      assert(
        dbAudit.active_config_policy_present === false &&
          dbAudit.created_config_deleted_count ===
            restoreEvidence.deleted_config_ids.length,
        "Routing policy OrgConfig audit did not show restored active config and deleted disposable versions.",
      );

      evidence.push({
        routing_policy_id: created.id,
        routing_policy_v2_id: secondVersion.id,
        routing_policy_name: name,
        invalid_create_status: invalidCreate.status,
        update_guard_status: updateGuard.status,
        sync_gateway_synced: syncResult.gateway_synced,
        deleted_at_count: dbAudit.deleted_at_count,
        restored_org_config_id: restoreEvidence.original_config_id,
        deleted_config_versions: restoreEvidence.deleted_config_versions,
      });
    },
  },
  {
    id: "AGENTCC-API-006",
    title:
      "Gateway session create, search, stats, requests, close, and delete lifecycle",
    tags: [
      "gateway",
      "agentcc",
      "sessions",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const sessionId = `api_journey_session_${runId.replace(/[^a-z0-9]/gi, "_")}`;
      const marker = `api_journey_session_log_${runId.replace(/[^a-z0-9]/gi, "_")}`;
      const logIds = [randomUUID(), randomUUID(), randomUUID()];

      const created = await client.post(apiPath("/agentcc/sessions/"), {
        session_id: sessionId,
        name: "API journey session",
        status: "active",
        metadata: { source: "api-journey", runId },
      });
      assert(created?.id, "Gateway session create did not return id.");
      cleanup.defer("hard delete API journey gateway session fixture", () =>
        deleteAgentccSessionFixtureDb({
          sessionUuid: created.id,
          organizationId,
          logIds,
        }),
      );

      let dbAudit = await loadAgentccSessionDbAudit({
        sessionUuid: created.id,
        organizationId,
        workspaceId,
        sessionId,
      });
      assert(
        dbAudit.id === created.id &&
          dbAudit.organization_id === organizationId &&
          dbAudit.workspace_id === workspaceId &&
          dbAudit.deleted === false,
        "Gateway session DB audit did not find the created workspace-scoped active row.",
      );

      const duplicateCreate = await expectApiError(
        () =>
          client.post(apiPath("/agentcc/sessions/"), {
            session_id: sessionId,
            name: "Duplicate API journey session",
          }),
        [400],
        "Gateway session create accepted a duplicate active session_id.",
      );
      assert(
        errorText(duplicateCreate).toLowerCase().includes("unique") ||
          errorText(duplicateCreate).toLowerCase().includes("duplicate"),
        "Duplicate gateway session create did not return a uniqueness error.",
      );

      const invalidStatus = await expectApiError(
        () =>
          client.post(apiPath("/agentcc/sessions/"), {
            session_id: `${sessionId}_invalid`,
            status: "paused",
          }),
        [400],
        "Gateway session create accepted an unsupported status.",
      );
      assert(
        errorText(invalidStatus).toLowerCase().includes("status"),
        "Invalid gateway session status did not return a status validation error.",
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

      const searchedByName = asArray(
        await client.get(apiPath("/agentcc/sessions/"), {
          query: { search: "API journey session", limit: 100 },
        }),
      );
      assert(
        searchedByName.some((session) => session.id === created.id),
        "Created gateway session was not visible through name search.",
      );

      const searchedById = asArray(
        await client.get(apiPath("/agentcc/sessions/"), {
          query: { search: sessionId.slice(-12), limit: 100 },
        }),
      );
      assert(
        searchedById.some((session) => session.id === created.id),
        "Created gateway session was not visible through session_id search.",
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

      await seedAgentccRequestLogsDb({
        organizationId,
        workspaceId,
        logIds,
        apiKeyId: `api_journey_session_key_${runId}`,
        marker,
        sharedSessionId: sessionId,
        soloSessionId: `${sessionId}_other`,
      });

      const detail = await client.get(
        apiPath("/agentcc/sessions/{id}/", { id: created.id }),
      );
      assert(
        detail?.id === created.id,
        "Gateway session detail returned wrong id.",
      );
      assert(
        detail?.stats && typeof detail.stats.request_count === "number",
        "Gateway session detail did not include request stats.",
      );
      assert(
        detail.stats.request_count === 2 &&
          detail.stats.total_tokens === 180 &&
          Number(detail.stats.total_cost).toFixed(6) === "0.003700" &&
          Number(detail.stats.avg_latency_ms).toFixed(2) === "185.00",
        "Gateway session detail stats did not match seeded request logs.",
      );

      const requests = asArray(
        await client.get(
          apiPath("/agentcc/sessions/{id}/requests/", { id: created.id }),
        ),
      );
      assert(
        requests.length === 2 &&
          requests.every((request) => request.session_id === sessionId) &&
          requests.some(
            (request) => request.request_id === `${marker}_error`,
          ) &&
          requests.some(
            (request) => request.request_id === `${marker}_success`,
          ),
        "Gateway session requests did not return the seeded request chain.",
      );

      const closed = await client.post(
        apiPath("/agentcc/sessions/{id}/close/", { id: created.id }),
        {},
      );
      assert(
        closed.status === "closed",
        "Gateway session close did not persist.",
      );

      const closedSessions = asArray(
        await client.get(apiPath("/agentcc/sessions/"), {
          query: { status: "closed", limit: 100 },
        }),
      );
      assert(
        closedSessions.some((session) => session.id === created.id),
        "Closed gateway session was not visible through status filter.",
      );

      await client.delete(
        apiPath("/agentcc/sessions/{id}/", { id: created.id }),
      );
      const listed = asArray(
        await client.get(apiPath("/agentcc/sessions/"), {
          query: { search: sessionId, limit: 100 },
        }),
      );
      assert(
        !listed.some((session) => session.id === created.id),
        "Deleted gateway session was still visible in list.",
      );

      dbAudit = await loadAgentccSessionDbAudit({
        sessionUuid: created.id,
        organizationId,
        workspaceId,
        sessionId,
      });
      assert(
        dbAudit.deleted === true &&
          dbAudit.deleted_at_set === true &&
          dbAudit.request_log_count === 2 &&
          dbAudit.other_session_log_count === 1,
        "Gateway session DB audit did not confirm soft-delete and seeded log state.",
      );

      const cleanupAudit = await deleteAgentccSessionFixtureDb({
        sessionUuid: created.id,
        organizationId,
        logIds,
      });
      assert(
        Number(cleanupAudit.remaining_session_count) === 0 &&
          Number(cleanupAudit.remaining_log_count) === 0,
        `Gateway session fixture hard cleanup left disposable rows behind: ${JSON.stringify(cleanupAudit)}`,
      );

      evidence.push({
        session_uuid: created.id,
        session_id: sessionId,
        duplicate_status: duplicateCreate.status,
        invalid_status_status: invalidStatus.status,
        request_count: detail.stats.request_count,
        total_tokens: detail.stats.total_tokens,
        total_cost: Number(detail.stats.total_cost).toFixed(6),
        avg_latency_ms: Number(detail.stats.avg_latency_ms).toFixed(2),
        request_rows: requests.length,
        deleted_at_set: dbAudit.deleted_at_set,
        cleanup_remaining_session_count: cleanupAudit.remaining_session_count,
        cleanup_remaining_log_count: cleanupAudit.remaining_log_count,
      });
    },
  },
  {
    id: "AGENTCC-API-007",
    title:
      "Gateway request logs, sessions aggregate, analytics, filters, and detail read consistency",
    tags: [
      "gateway",
      "agentcc",
      "request-logs",
      "analytics",
      "mutating",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      assert(
        organizationId,
        "Gateway request-log journey requires organization id.",
      );
      assert(workspaceId, "Gateway request-log journey requires workspace id.");

      const marker = `api_journey_log_${runId.replace(/[^a-z0-9]/gi, "_")}`;
      const apiKeyId = `${marker}_key`;
      const sharedSessionId = `${marker}_session_shared`;
      const soloSessionId = `${marker}_session_solo`;
      const logIds = [randomUUID(), randomUUID(), randomUUID()];

      await seedAgentccRequestLogsDb({
        organizationId,
        workspaceId,
        logIds,
        apiKeyId,
        marker,
        sharedSessionId,
        soloSessionId,
      });
      cleanup.defer("hard-delete disposable gateway request logs", () =>
        deleteAgentccRequestLogsDb({ logIds, organizationId }),
      );

      const seededLogs = collectionRows(
        await client.get(apiPath("/agentcc/request-logs/"), {
          query: { api_key_id: apiKeyId, limit: 10, ordering: "started_at" },
        }),
      );
      assert(
        seededLogs.length === 3,
        `Gateway request log list expected 3 seeded rows, saw ${seededLogs.length}.`,
      );

      const analyticsOverview = await client.get(
        apiPath("/agentcc/analytics/overview/"),
      );
      assert(
        analyticsOverview?.total_requests &&
          typeof analyticsOverview.total_requests.value !== "undefined",
        "Gateway analytics overview did not include total_requests KPI.",
      );

      const usage = await client.get(
        apiPath("/agentcc/analytics/usage-timeseries/"),
        {
          query: { granularity: "day" },
        },
      );
      assert(
        Array.isArray(usage?.series),
        "Gateway usage analytics missing series.",
      );

      const cost = await client.get(
        apiPath("/agentcc/analytics/cost-breakdown/"),
        {
          query: { group_by: "model", top_n: 5 },
        },
      );
      assert(
        Array.isArray(cost?.breakdown),
        "Gateway cost analytics missing breakdown.",
      );

      const latency = await client.get(
        apiPath("/agentcc/analytics/latency-stats/"),
      );
      assert(
        latency?.summary && Array.isArray(latency?.timeseries),
        "Gateway latency analytics missing summary/timeseries.",
      );

      const errors = await client.get(
        apiPath("/agentcc/analytics/error-breakdown/"),
        {
          query: { group_by: "status_code" },
        },
      );
      assert(
        Array.isArray(errors?.breakdown) &&
          Array.isArray(errors?.error_timeseries),
        "Gateway error analytics missing breakdown/timeseries.",
      );

      const models = await client.get(
        apiPath("/agentcc/analytics/model-comparison/"),
      );
      assert(
        Array.isArray(models?.models),
        "Gateway model comparison missing models array.",
      );

      const sessionAggregates = asArray(
        await client.get(apiPath("/agentcc/request-logs/sessions/"), {
          query: { api_key_id: apiKeyId, limit: 5 },
        }),
      );
      assert(
        sessionAggregates.some(
          (session) =>
            session.session_id === sharedSessionId &&
            Number(session.request_count) === 2,
        ),
        "Gateway request-log session aggregate did not include the seeded shared session.",
      );

      const success = seededLogs.find((row) =>
        row.request_id.endsWith("_success"),
      );
      const error = seededLogs.find((row) => row.request_id.endsWith("_error"));
      const stream = seededLogs.find((row) =>
        row.request_id.endsWith("_stream"),
      );
      assert(
        success && error && stream,
        "Seeded request-log rows were incomplete.",
      );

      const fixtureIds = new Set(logIds);
      const detail = await client.get(
        apiPath("/agentcc/request-logs/{id}/", { id: error.id }),
      );
      assert(
        detail?.id === error.id &&
          detail?.request_body?.messages?.[0]?.content?.includes(marker) &&
          detail?.response_body?.error?.code === "provider_unavailable" &&
          detail?.request_headers?.["x-api-journey"] === marker &&
          detail?.response_headers?.["x-request-outcome"] === "error" &&
          detail?.guardrail_results?.checks?.[0]?.name === "toxicity",
        "Gateway request-log detail did not expose seeded request/response/guardrail bodies.",
      );

      const byRequestId = asArray(
        await client.get(apiPath("/agentcc/request-logs/"), {
          query: { request_id: success.request_id, limit: 10 },
        }),
      );
      assert(
        byRequestId.length === 1 && byRequestId[0].id === success.id,
        "Gateway request-log request_id filter did not return the sample row.",
      );

      const byProvider = asArray(
        await client.get(apiPath("/agentcc/request-logs/"), {
          query: { api_key_id: apiKeyId, provider: "openai", limit: 10 },
        }),
      );
      assert(
        byProvider.length === 2 &&
          byProvider.every((row) => row.provider === "openai"),
        "Gateway request-log provider filter did not isolate seeded OpenAI rows.",
      );

      const byModelCsv = asArray(
        await client.get(apiPath("/agentcc/request-logs/"), {
          query: {
            api_key_id: apiKeyId,
            model: "gpt-4o-mini,claude-3-haiku",
            limit: 10,
          },
        }),
      );
      assert(
        byModelCsv.length === 2 &&
          byModelCsv.some((row) => row.id === success.id) &&
          byModelCsv.some((row) => row.id === stream.id),
        "Gateway request-log model CSV filter did not return the expected seeded rows.",
      );

      const byStatusCode = asArray(
        await client.get(apiPath("/agentcc/request-logs/"), {
          query: { api_key_id: apiKeyId, status_code: "200,503", limit: 10 },
        }),
      );
      assert(
        byStatusCode.length === 2 &&
          byStatusCode.some((row) => row.id === success.id) &&
          byStatusCode.some((row) => row.id === error.id),
        "Gateway request-log status_code CSV filter did not return expected rows.",
      );

      const byStatusRange = asArray(
        await client.get(apiPath("/agentcc/request-logs/"), {
          query: {
            api_key_id: apiKeyId,
            min_status_code: 500,
            max_status_code: 599,
            limit: 10,
          },
        }),
      );
      assert(
        byStatusRange.length === 1 && byStatusRange[0].id === error.id,
        "Gateway request-log status-code range filter did not isolate the error row.",
      );

      const booleanFilters = [
        ["is_error", true, error.id],
        ["cache_hit", true, success.id],
        ["fallback_used", true, stream.id],
        ["guardrail_triggered", true, error.id],
        ["is_stream", true, stream.id],
      ];
      for (const [filterName, value, expectedId] of booleanFilters) {
        const rows = asArray(
          await client.get(apiPath("/agentcc/request-logs/"), {
            query: { api_key_id: apiKeyId, [filterName]: value, limit: 10 },
          }),
        );
        assert(
          rows.length === 1 && rows[0].id === expectedId,
          `Gateway request-log ${filterName} filter did not isolate expected row.`,
        );
      }

      const byUser = asArray(
        await client.get(apiPath("/agentcc/request-logs/"), {
          query: { api_key_id: apiKeyId, user_id: `${marker}_user_beta` },
        }),
      );
      assert(
        byUser.length === 1 && byUser[0].id === error.id,
        "Gateway request-log user_id filter did not isolate seeded user.",
      );

      const byLatencyCostTokens = asArray(
        await client.get(apiPath("/agentcc/request-logs/"), {
          query: {
            api_key_id: apiKeyId,
            min_latency: 300,
            max_latency: 500,
            min_cost: "0.005",
            max_cost: "0.006",
            min_tokens: 600,
            max_tokens: 800,
            limit: 10,
          },
        }),
      );
      assert(
        byLatencyCostTokens.length === 1 &&
          byLatencyCostTokens[0].id === stream.id,
        "Gateway request-log numeric range filters did not isolate the streamed fallback row.",
      );

      const afterAll = new Date(
        Math.min(...seededLogs.map((row) => Date.parse(row.started_at))) - 1000,
      ).toISOString();
      const beforeAll = new Date(
        Math.max(...seededLogs.map((row) => Date.parse(row.started_at))) + 1000,
      ).toISOString();
      const byDateRange = asArray(
        await client.get(apiPath("/agentcc/request-logs/"), {
          query: {
            api_key_id: apiKeyId,
            started_after: afterAll,
            started_before: beforeAll,
            limit: 10,
          },
        }),
      );
      assert(
        byDateRange.length === 3,
        "Gateway request-log started_at range filters did not include all seeded rows.",
      );

      const searched = asArray(
        await client.get(apiPath("/agentcc/request-logs/search/"), {
          query: { api_key_id: apiKeyId, q: marker, limit: 10 },
        }),
      );
      assert(
        searched.length === 3 &&
          searched.every((row) => fixtureIds.has(row.id)),
        "Gateway request-log search did not return the seeded rows.",
      );

      const shortSearchError = await expectApiError(
        () =>
          client.get(apiPath("/agentcc/request-logs/search/"), {
            query: { api_key_id: apiKeyId, q: "a" },
          }),
        [400],
        "Gateway request-log short search query unexpectedly succeeded.",
      );
      assert(
        errorText(shortSearchError).includes("at least 2 characters"),
        "Gateway request-log short search guard returned an unexpected error.",
      );

      const sessionDetail = asArray(
        await client.get(
          apiPath("/agentcc/request-logs/sessions/{session_id}/", {
            session_id: sharedSessionId,
          }),
          { query: { api_key_id: apiKeyId, limit: 10 } },
        ),
      );
      assert(
        sessionDetail.length === 2 &&
          sessionDetail.some((row) => row.id === success.id) &&
          sessionDetail.some((row) => row.id === error.id),
        "Gateway request-log session detail did not return the shared session rows.",
      );

      const sessionsByRequests = asArray(
        await client.get(apiPath("/agentcc/request-logs/sessions/"), {
          query: {
            api_key_id: apiKeyId,
            ordering: "-request_count",
            limit: 10,
          },
        }),
      );
      for (let i = 1; i < sessionsByRequests.length; i += 1) {
        assert(
          Number(sessionsByRequests[i - 1].request_count) >=
            Number(sessionsByRequests[i].request_count),
          "Gateway request-log sessions were not sorted by request_count descending.",
        );
      }

      const orderedByTokens = asArray(
        await client.get(apiPath("/agentcc/request-logs/"), {
          query: { api_key_id: apiKeyId, ordering: "-total_tokens", limit: 10 },
        }),
      );
      assert(
        orderedByTokens[0]?.id === stream.id,
        "Gateway request-log ordering=-total_tokens did not place the largest row first.",
      );

      const csvExport = await client.get(
        apiPath("/agentcc/request-logs/export/"),
        {
          query: { api_key_id: apiKeyId, export_format: "csv" },
        },
      );
      assert(
        typeof csvExport === "string" &&
          csvExport.includes("request_id,model,provider") &&
          csvExport.includes(success.request_id) &&
          csvExport.includes(error.request_id) &&
          csvExport.includes(stream.request_id),
        "Gateway request-log CSV export did not include all seeded rows.",
      );

      const jsonExport = await client.get(
        apiPath("/agentcc/request-logs/export/"),
        {
          query: { api_key_id: apiKeyId, export_format: "json" },
        },
      );
      const jsonRows = String(jsonExport)
        .trim()
        .split("\n")
        .filter(Boolean)
        .map((line) => JSON.parse(line));
      assert(
        jsonRows.length === 3 &&
          jsonRows.some((row) => row.request_id === success.request_id) &&
          jsonRows.some((row) => row.request_id === error.request_id) &&
          jsonRows.some((row) => row.request_id === stream.request_id),
        "Gateway request-log NDJSON export did not include all seeded rows.",
      );

      const emptyExportError = await expectApiError(
        () =>
          client.get(apiPath("/agentcc/request-logs/export/"), {
            query: {
              api_key_id: `${apiKeyId}_missing`,
              export_format: "csv",
            },
          }),
        [400],
        "Gateway request-log empty export unexpectedly succeeded.",
      );
      assert(
        errorText(emptyExportError).includes("No data to export"),
        "Gateway request-log empty export guard returned an unexpected error.",
      );

      const cleanupAudit = await deleteAgentccRequestLogsDb({
        logIds,
        organizationId,
      });
      assert(
        Number(cleanupAudit.remaining_count) === 0,
        "Disposable gateway request logs remained after cleanup.",
      );

      evidence.push({
        seeded_request_log_count: seededLogs.length,
        shared_session_id: sharedSessionId,
        shared_session_request_count: Number(
          sessionsByRequests.find(
            (session) => session.session_id === sharedSessionId,
          )?.request_count || 0,
        ),
        csv_export_bytes: csvExport.length,
        ndjson_export_rows: jsonRows.length,
        cleanup_remaining_count: cleanupAudit.remaining_count,
      });
    },
  },
  {
    id: "AGENTCC-API-012",
    title:
      "Gateway analytics deterministic aggregate, grouping, and filter coverage",
    tags: [
      "gateway",
      "agentcc",
      "analytics",
      "request-logs",
      "mutating",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      assert(
        organizationId,
        "Gateway analytics journey requires organization id.",
      );
      assert(workspaceId, "Gateway analytics journey requires workspace id.");

      const marker = `api_journey_analytics_${runId.replace(/[^a-z0-9]/gi, "_")}`;
      const apiKeyId = `${marker}_key`;
      const sharedSessionId = `${marker}_session_shared`;
      const soloSessionId = `${marker}_session_solo`;
      const logIds = [randomUUID(), randomUUID(), randomUUID()];
      const start = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
      const end = new Date(Date.now() + 10 * 60 * 1000).toISOString();
      const baseQuery = { api_key_id: apiKeyId, start, end };

      await seedAgentccRequestLogsDb({
        organizationId,
        workspaceId,
        logIds,
        apiKeyId,
        marker,
        sharedSessionId,
        soloSessionId,
      });
      cleanup.defer("hard-delete disposable gateway analytics logs", () =>
        deleteAgentccRequestLogsDb({ logIds, organizationId }),
      );

      const overview = await client.get(
        apiPath("/agentcc/analytics/overview/"),
        {
          query: baseQuery,
        },
      );
      assert(
        overview.total_requests?.value === 3,
        "Analytics overview total requests mismatch.",
      );
      assert(
        overview.total_tokens?.value === 880,
        "Analytics overview total tokens mismatch.",
      );
      assertApprox(
        overview.total_cost?.value,
        0.0092,
        "Analytics overview total cost mismatch.",
      );
      assertApprox(
        overview.avg_latency_ms?.value,
        263.33,
        "Analytics overview average latency mismatch.",
      );
      assertApprox(
        overview.error_rate?.value,
        33.33,
        "Analytics overview error rate mismatch.",
      );
      assertApprox(
        overview.cache_hit_rate?.value,
        33.33,
        "Analytics overview cache hit rate mismatch.",
      );
      assert(
        overview.active_models?.value === 3,
        "Analytics overview active model count mismatch.",
      );
      assert(
        overview.p95_latency_ms?.value === 420,
        "Analytics overview p95 latency mismatch.",
      );

      const usageDaily = await client.get(
        apiPath("/agentcc/analytics/usage-timeseries/"),
        {
          query: { ...baseQuery, granularity: "day" },
        },
      );
      assert(
        usageDaily.granularity === "day",
        "Analytics usage did not preserve valid daily granularity.",
      );
      assert(
        sumMetric(usageDaily.series, "request_count") === 3 &&
          sumMetric(usageDaily.series, "total_tokens") === 880 &&
          sumMetric(usageDaily.series, "input_tokens") === 390 &&
          sumMetric(usageDaily.series, "output_tokens") === 490 &&
          sumMetric(usageDaily.series, "error_count") === 1,
        "Analytics usage timeseries totals did not match seeded rows.",
      );
      assertApprox(
        sumMetric(usageDaily.series, "total_cost"),
        0.0092,
        "Analytics usage cost total mismatch.",
      );

      const usageByProvider = await client.get(
        apiPath("/agentcc/analytics/usage-timeseries/"),
        {
          query: { ...baseQuery, granularity: "day", group_by: "provider" },
        },
      );
      assert(
        usageByProvider.group_by === "provider" &&
          sumMetric(usageByProvider.groups?.openai, "request_count") === 2 &&
          sumMetric(usageByProvider.groups?.anthropic, "request_count") === 1,
        "Analytics usage provider grouping mismatch.",
      );

      const invalidGranularity = await client.get(
        apiPath("/agentcc/analytics/usage-timeseries/"),
        {
          query: {
            ...baseQuery,
            granularity: "invalid",
            group_by: "unsupported",
          },
        },
      );
      assert(
        invalidGranularity.granularity === "hour" &&
          !invalidGranularity.group_by &&
          sumMetric(invalidGranularity.series, "request_count") === 3,
        "Analytics usage did not fall back for invalid granularity/group_by.",
      );

      const costByProvider = await client.get(
        apiPath("/agentcc/analytics/cost-breakdown/"),
        {
          query: { ...baseQuery, group_by: "provider", top_n: 10 },
        },
      );
      assert(
        costByProvider.group_by === "provider",
        "Analytics cost group_by provider mismatch.",
      );
      assertApprox(
        costByProvider.total_cost,
        0.0092,
        "Analytics cost total mismatch.",
      );
      const openaiCost = findBreakdown(costByProvider.breakdown, "openai");
      const anthropicCost = findBreakdown(
        costByProvider.breakdown,
        "anthropic",
      );
      assert(
        openaiCost?.request_count === 2,
        "Analytics cost OpenAI request count mismatch.",
      );
      assert(
        anthropicCost?.request_count === 1,
        "Analytics cost Anthropic request count mismatch.",
      );
      assertApprox(
        openaiCost.total_cost,
        0.0037,
        "Analytics OpenAI cost mismatch.",
      );
      assertApprox(
        anthropicCost.total_cost,
        0.0055,
        "Analytics Anthropic cost mismatch.",
      );

      const costTopTwo = await client.get(
        apiPath("/agentcc/analytics/cost-breakdown/"),
        {
          query: { ...baseQuery, group_by: "model", top_n: 2 },
        },
      );
      assert(
        costTopTwo.breakdown.length === 3 &&
          findBreakdown(costTopTwo.breakdown, "Other")?.request_count === 1,
        "Analytics cost top_n did not include expected Other bucket.",
      );

      const costInvalidGroup = await client.get(
        apiPath("/agentcc/analytics/cost-breakdown/"),
        {
          query: {
            ...baseQuery,
            group_by: "not_a_group",
            top_n: "not_a_number",
          },
        },
      );
      assert(
        costInvalidGroup.group_by === "model" &&
          costInvalidGroup.breakdown.length === 3,
        "Analytics cost did not fall back for invalid group/top_n.",
      );

      const latency = await client.get(
        apiPath("/agentcc/analytics/latency-stats/"),
        {
          query: { ...baseQuery, granularity: "day" },
        },
      );
      assert(
        latency.summary?.total_requests === 3,
        "Analytics latency total requests mismatch.",
      );
      assert(latency.summary.min_ms === 120, "Analytics latency min mismatch.");
      assert(latency.summary.max_ms === 420, "Analytics latency max mismatch.");
      assertApprox(
        latency.summary.avg_ms,
        263.33,
        "Analytics latency average mismatch.",
      );
      assert(latency.summary.p50_ms === 250, "Analytics latency p50 mismatch.");
      assert(latency.summary.p95_ms === 420, "Analytics latency p95 mismatch.");
      assert(
        sumMetric(latency.timeseries, "request_count") === 3,
        "Analytics latency timeseries count mismatch.",
      );

      const errorByStatus = await client.get(
        apiPath("/agentcc/analytics/error-breakdown/"),
        {
          query: { ...baseQuery, granularity: "day", group_by: "status_code" },
        },
      );
      assert(
        errorByStatus.total_requests === 3,
        "Analytics error total requests mismatch.",
      );
      assert(
        errorByStatus.total_errors === 1,
        "Analytics error total errors mismatch.",
      );
      assertApprox(
        errorByStatus.overall_error_rate,
        33.33,
        "Analytics error rate mismatch.",
      );
      const status503 = findBreakdown(errorByStatus.breakdown, "503");
      assert(
        status503?.error_count === 1,
        "Analytics error status breakdown mismatch.",
      );
      assert(
        sumMetric(errorByStatus.error_timeseries, "error_count") === 1 &&
          sumMetric(errorByStatus.error_timeseries, "total_count") === 3,
        "Analytics error timeseries totals mismatch.",
      );

      const errorByMessage = await client.get(
        apiPath("/agentcc/analytics/error-breakdown/"),
        {
          query: {
            ...baseQuery,
            granularity: "day",
            group_by: "error_message",
          },
        },
      );
      assert(
        errorByMessage.breakdown.some((row) => row.name.includes(marker)),
        "Analytics error_message breakdown did not include seeded error text.",
      );

      const models = await client.get(
        apiPath("/agentcc/analytics/model-comparison/"),
        {
          query: {
            ...baseQuery,
            models: "gpt-4o-mini,gpt-4o,claude-3-haiku",
          },
        },
      );
      assert(
        models.models?.length === 3,
        "Analytics model comparison row count mismatch.",
      );
      const miniModel = models.models.find(
        (row) => row.model === "gpt-4o-mini",
      );
      const errorModel = models.models.find((row) => row.model === "gpt-4o");
      const streamModel = models.models.find(
        (row) => row.model === "claude-3-haiku",
      );
      assert(
        miniModel?.cache_hit_rate === 100,
        "Analytics model cache-hit rate mismatch.",
      );
      assert(
        errorModel?.error_rate === 100,
        "Analytics model error rate mismatch.",
      );
      assert(
        streamModel?.provider === "anthropic",
        "Analytics model provider mismatch.",
      );
      assertApprox(
        streamModel.total_cost,
        0.0055,
        "Analytics model total cost mismatch.",
      );

      const guardrailOverview = await client.get(
        apiPath("/agentcc/analytics/guardrail-overview/"),
        {
          query: baseQuery,
        },
      );
      assert(
        guardrailOverview.total_requests === 3 &&
          guardrailOverview.guardrail_triggered === 1 &&
          guardrailOverview.top_triggered_rule === "toxicity",
        "Analytics guardrail overview mismatch.",
      );

      const guardrailRules = await client.get(
        apiPath("/agentcc/analytics/guardrail-rules/"),
        {
          query: baseQuery,
        },
      );
      assert(
        guardrailRules.rules?.[0]?.rule === "toxicity" &&
          guardrailRules.rules?.[0]?.trigger_count === 1,
        "Analytics guardrail rules mismatch.",
      );

      const guardrailTrends = await client.get(
        apiPath("/agentcc/analytics/guardrail-trends/"),
        {
          query: { ...baseQuery, granularity: "day" },
        },
      );
      assert(
        sumMetric(guardrailTrends.series, "trigger_count") === 1,
        "Analytics guardrail trends mismatch.",
      );

      const cleanupAudit = await deleteAgentccRequestLogsDb({
        logIds,
        organizationId,
      });
      assert(
        Number(cleanupAudit.remaining_count) === 0,
        "Disposable gateway analytics logs remained after cleanup.",
      );

      evidence.push({
        seeded_analytics_log_count: 3,
        total_requests: overview.total_requests.value,
        total_cost: overview.total_cost.value,
        usage_total_tokens: sumMetric(usageDaily.series, "total_tokens"),
        error_rate: errorByStatus.overall_error_rate,
        model_rows: models.models.length,
        guardrail_triggered: guardrailOverview.guardrail_triggered,
        cleanup_remaining_count: cleanupAudit.remaining_count,
      });
    },
  },
  {
    id: "AGENTCC-API-008",
    title:
      "Gateway provider credential create, mask, rotate, update, and delete lifecycle",
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

      const created = await client.post(
        apiPath("/agentcc/provider-credentials/"),
        {
          provider_name: providerName,
          display_name: "API journey provider",
          credentials: { api_key: initialKey },
          api_format: "openai",
          models_list: ["gpt-4o-mini"],
          default_timeout_seconds: 30,
          max_concurrent: 4,
          conn_pool_size: 8,
          extra_config: { source: "api-journey", runId },
        },
      );
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
      assert(
        detail?.id === created.id,
        "Provider credential detail returned wrong id.",
      );
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
        apiPath("/agentcc/provider-credentials/{id}/rotate/", {
          id: created.id,
        }),
        { credentials: { api_key: rotatedKey } },
      );
      assert(
        rotated.last_rotated_at,
        "Provider credential rotate did not set last_rotated_at.",
      );
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

      await client.delete(
        apiPath("/agentcc/provider-credentials/{id}/", { id: created.id }),
      );
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
        encrypted_credentials_bytes: Number(
          dbAudit.encrypted_credentials_bytes,
        ),
      });
    },
  },
  {
    id: "AGENTCC-API-009",
    title:
      "Gateway guardrail policy create, encrypted secret preservation, sync, and delete lifecycle",
    tags: [
      "gateway",
      "agentcc",
      "guardrails",
      "mutating",
      "data-roundtrip",
      "db-audit",
      "security",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.replace(/[^a-z0-9]/gi, "_");
      const name = `api_journey_guardrail_${suffix}`;
      const checkName = `api_journey_check_${suffix}`;
      const checkSecret = `gr-api-journey-${suffix}-secret-value`;
      assert(
        organizationId && workspaceId,
        "Guardrail policy journey requires organization and workspace ids.",
      );
      let applyKeyHardCleaned = false;
      cleanup.defer("hard-delete API journey guardrail apply key", () =>
        applyKeyHardCleaned
          ? null
          : deleteAgentccApiKeyFixtureDb({ namePrefix: name, organizationId }),
      );

      const topics = await client.get(
        apiPath("/agentcc/guardrail-configs/topics/"),
      );
      assert(
        Array.isArray(asArray(topics)),
        "Guardrail topics catalog was not array-like.",
      );
      const piiEntities = await client.get(
        apiPath("/agentcc/guardrail-configs/pii-entities/"),
      );
      assert(
        Array.isArray(asArray(piiEntities)),
        "Guardrail PII entities catalog was not array-like.",
      );

      const created = await client.post(
        apiPath("/agentcc/guardrail-policies/"),
        {
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
        },
      );
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
      assert(
        detail?.id === created.id,
        "Guardrail policy detail returned wrong id.",
      );
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

      const putUpdated = await client.put(
        apiPath("/agentcc/guardrail-policies/{id}/", { id: created.id }),
        {
          name,
          description:
            "Full PUT guardrail policy update for API journey regression.",
          scope: "global",
          mode: "enforce",
          is_active: true,
          priority: 995,
          applied_keys: [],
          applied_projects: [],
          checks: [
            {
              name: checkName,
              type: "regex",
              enabled: true,
              config: {
                pattern: "api-journey-put",
                action: "flag",
                api_key: "__encrypted__",
              },
            },
          ],
        },
      );
      assert(
        putUpdated.priority === 995 &&
          putUpdated.mode === "enforce" &&
          putUpdated.is_active === true,
        "Guardrail policy PUT did not persist full update payload.",
      );
      assertGuardrailSecretSanitized(putUpdated, checkName, checkSecret);

      const applyKey = await seedAgentccGuardrailApplyKeyFixtureDb({
        name,
        organizationId,
        workspaceId,
      });
      const applied = await client.post(
        apiPath("/agentcc/guardrail-policies/{id}/apply/", {
          id: created.id,
        }),
        { key_ids: [applyKey.id] },
      );
      assert(
        applied.scope === "key" &&
          asArray(applied.applied_keys).includes(applyKey.id),
        "Guardrail policy apply did not persist the seeded API key target.",
      );
      assertGuardrailSecretSanitized(applied, checkName, checkSecret);

      const syncResult = await client.post(
        apiPath("/agentcc/guardrail-policies/sync/"),
        {},
      );
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
          dbAudit.scope === "key" &&
          asArray(dbAudit.applied_keys).includes(applyKey.id) &&
          dbAudit.check_pattern === "api-journey-put" &&
          dbAudit.encrypted_check_configs_present === true,
        "Guardrail policy DB audit did not preserve sanitized secret after PUT/apply.",
      );

      await client.delete(
        apiPath("/agentcc/guardrail-policies/{id}/", { id: created.id }),
      );
      const listed = asArray(
        await client.get(apiPath("/agentcc/guardrail-policies/")),
      );
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

      const applyKeyCleanup = await deleteAgentccApiKeyFixtureDb({
        namePrefix: name,
        organizationId,
      });
      applyKeyHardCleaned = true;
      assert(
        Number(applyKeyCleanup.remaining_count) === 0,
        "Guardrail policy apply API-key fixture cleanup left rows behind.",
      );

      evidence.push({
        guardrail_policy_id: created.id,
        guardrail_policy_name: name,
        applied_key_id: applyKey.id,
        put_gateway_synced: putUpdated.gateway_synced,
        apply_gateway_synced: applied.gateway_synced,
        sync_gateway_synced: syncResult.gateway_synced,
        encrypted_check_configs_present:
          dbAudit.encrypted_check_configs_present,
        apply_key_cleanup_deleted_count: Number(applyKeyCleanup.deleted_count),
      });
    },
  },
  {
    id: "AGENTCC-API-010",
    title:
      "Gateway overview, budget, alerting, fallback, MCP, and org-config restore lifecycle",
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
      cleanup.defer(
        "restore AgentCC gateway org config versions",
        restoreOrgConfig,
      );

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
        health?.status === "healthy" &&
          typeof health.provider_count === "number",
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
          gatewayConfig.alerting?.channels?.[alertChannelName]?.type ===
            "webhook",
        "Gateway config did not expose the disposable alert rule/channel.",
      );
      assert(
        gatewayConfig.routing?.fallback_enabled === true &&
          asArray(
            gatewayConfig.routing?.model_fallbacks?.[primaryModel],
          ).includes(fallbackModel),
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
        apiPath("/agentcc/gateways/{id}/update-mcp-guardrails/", {
          id: gatewayId,
        }),
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
        asArray(gatewayConfig.mcp?.guardrails?.server_ids).includes(
          mcpServerId,
        ),
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
        await client.get(
          apiPath("/agentcc/gateways/{id}/mcp-tools/", { id: gatewayId }),
        ),
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
        removedBudget?.action === "removed" &&
          removedBudget.budget === budgetLevel,
        "Gateway budget remove response did not echo the budget level/action.",
      );
      gatewayConfig = await client.get(
        apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
      );
      assert(
        !Object.prototype.hasOwnProperty.call(
          gatewayConfig.budgets || {},
          budgetLevel,
        ),
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
        created_config_deleted_count: Number(
          dbAudit.created_config_deleted_count,
        ),
      });
    },
  },
  {
    id: "AGENTCC-API-013",
    title:
      "Gateway generated provider, guardrail, batch, reload, and MCP action guards",
    tags: [
      "gateway",
      "agentcc",
      "provider-actions",
      "guardrails",
      "batch",
      "mcp",
      "mutating",
      "db-audit",
    ],
    async run({ client, cleanup, runId, evidence, organizationId }) {
      requireMutations();
      const suffix = runId.replace(/[^a-z0-9]/gi, "_").toLowerCase();
      const gatewayId = "default";
      const providerName = `api_journey_gateway_action_provider_${suffix}`;
      const guardrailName = `api_journey_gateway_action_guardrail_${suffix}`;
      const rawProviderKey = `sk-gateway-action-${suffix}-secret-value`;

      cleanup.defer("hard delete gateway action provider rows", () =>
        hardDeleteAgentccProviderCredentialByName({
          organizationId,
          providerName,
        }),
      );

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
      cleanup.defer(
        "restore AgentCC gateway action org config versions",
        restoreOrgConfig,
      );

      const providerUpdate = await client.post(
        apiPath("/agentcc/gateways/{id}/update-provider/", { id: gatewayId }),
        {
          name: providerName,
          config: {
            api_key: rawProviderKey,
            display_name: "API journey gateway action provider",
            api_format: "openai",
            models: ["gpt-4o-mini"],
            base_url: "https://api.example.com/v1",
            default_timeout: 19,
            max_concurrent: 3,
            conn_pool_size: 5,
          },
        },
      );
      assert(
        providerUpdate?.provider === providerName &&
          providerUpdate.action === "updated",
        "Gateway update-provider did not echo provider/action.",
      );

      let gatewayConfig = await client.get(
        apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
      );
      const providerConfig = gatewayConfig.providers?.[providerName];
      assert(
        providerConfig?.display_name ===
          "API journey gateway action provider" &&
          asArray(providerConfig.models).includes("gpt-4o-mini"),
        "Gateway config did not expose the provider created through update-provider.",
      );

      const providerAudit = await loadAgentccProviderCredentialDbAudit({
        credentialId: providerConfig.id,
        organizationId,
        rawKey: rawProviderKey,
      });
      assert(
        providerAudit.provider_name === providerName &&
          providerAudit.deleted === false &&
          providerAudit.raw_key_present_in_ciphertext === false,
        "Gateway update-provider DB audit did not find encrypted active provider row.",
      );

      const providerRemove = await client.post(
        apiPath("/agentcc/gateways/{id}/remove-provider/", { id: gatewayId }),
        { name: providerName },
      );
      assert(
        providerRemove?.provider === providerName &&
          providerRemove.action === "removed",
        "Gateway remove-provider did not echo provider/action.",
      );

      const guardrailUpdate = await client.post(
        apiPath("/agentcc/gateways/{id}/update-guardrail/", { id: gatewayId }),
        {
          name: guardrailName,
          config: {
            enabled: true,
            action: "flag",
            threshold: 0.42,
            stage: "pre",
            mode: "sync",
            config: { source: "api-journey" },
          },
        },
      );
      assert(
        guardrailUpdate?.guardrail === guardrailName &&
          guardrailUpdate.action === "updated",
        "Gateway update-guardrail did not echo guardrail/action.",
      );

      const guardrailToggle = await client.post(
        apiPath("/agentcc/gateways/{id}/toggle-guardrail/", { id: gatewayId }),
        {
          name: guardrailName,
          enabled: false,
        },
      );
      assert(
        guardrailToggle?.guardrail === guardrailName &&
          guardrailToggle.enabled === false,
        "Gateway toggle-guardrail did not echo guardrail/enabled state.",
      );

      gatewayConfig = await client.get(
        apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
      );
      const guardrailRule = asArray(gatewayConfig.guardrails?.rules).find(
        (rule) => rule?.name === guardrailName,
      );
      assert(
        guardrailRule?.enabled === false && guardrailRule.threshold === 0.42,
        "Gateway config did not expose the updated and toggled guardrail rule.",
      );

      const reload = await client.post(
        apiPath("/agentcc/gateways/{id}/reload/", { id: gatewayId }),
        {},
      );
      assert(
        reload?.status === "ok" &&
          Object.prototype.hasOwnProperty.call(reload, "gateway_synced"),
        "Gateway reload did not return status/gateway_synced.",
      );

      const resetMcp = await client.post(
        apiPath("/agentcc/gateways/{id}/update-config/", { id: gatewayId }),
        {
          mcp: {
            servers: {},
            guardrails: {},
          },
        },
      );
      assert(
        Number(resetMcp?.version) > Number(guardrailToggle.version),
        "Gateway update-config did not create a newer MCP reset version.",
      );

      const emptyBatchSubmit = await expectApiError(
        () =>
          client.post(
            apiPath("/agentcc/gateways/{id}/submit-batch/", {
              id: gatewayId,
            }),
            { requests: [] },
          ),
        [400],
        "Gateway submit-batch unexpectedly accepted an empty request list.",
      );
      const missingBatchId = await expectApiError(
        () =>
          client.get(
            apiPath("/agentcc/gateways/{id}/get-batch/", { id: gatewayId }),
          ),
        [400],
        "Gateway get-batch unexpectedly accepted a missing batch_id.",
      );
      const unknownBatchId = `unknown-${suffix}`;
      const unknownBatchRead = await expectApiError(
        () =>
          client.get(
            apiPath("/agentcc/gateways/{id}/get-batch/", { id: gatewayId }),
            { query: { batch_id: unknownBatchId } },
          ),
        [404],
        "Gateway get-batch unexpectedly accepted an unowned batch id.",
      );
      const unknownBatchCancel = await expectApiError(
        () =>
          client.post(
            apiPath("/agentcc/gateways/{id}/cancel-batch/", {
              id: gatewayId,
            }),
            { batch_id: unknownBatchId },
          ),
        [404],
        "Gateway cancel-batch unexpectedly accepted an unowned batch id.",
      );
      const mcpToolNoServers = await expectApiError(
        () =>
          client.post(
            apiPath("/agentcc/gateways/{id}/test-mcp-tool/", {
              id: gatewayId,
            }),
            {
              name: "echo",
              arguments: { message: "hello" },
            },
          ),
        [400],
        "Gateway test-mcp-tool unexpectedly dispatched without configured MCP servers.",
      );

      const restoreEvidence = await restoreOrgConfig();
      const dbAudit = await loadAgentccGatewayActionDbAudit({
        organizationId,
        originalConfigId: originalActiveConfig.id,
        createdConfigIds: restoreEvidence.deleted_config_ids,
        providerName,
        guardrailName,
      });
      assert(
        dbAudit.active_config_is_original === true &&
          dbAudit.active_guardrail_present === false &&
          dbAudit.provider_deleted === true &&
          Number(dbAudit.created_config_deleted_count) ===
            restoreEvidence.deleted_config_ids.length,
        "Gateway generated-action DB audit did not show provider removal and config restore.",
      );

      evidence.push({
        gateway_id: gatewayId,
        provider_name: providerName,
        provider_id: providerConfig.id,
        guardrail_name: guardrailName,
        update_provider_synced: providerUpdate.gateway_synced,
        remove_provider_synced: providerRemove.gateway_synced,
        guardrail_update_synced: guardrailUpdate.gateway_synced,
        guardrail_toggle_synced: guardrailToggle.gateway_synced,
        reload_synced: reload.gateway_synced,
        empty_batch_status: emptyBatchSubmit.status,
        missing_batch_id_status: missingBatchId.status,
        unknown_batch_read_status: unknownBatchRead.status,
        unknown_batch_cancel_status: unknownBatchCancel.status,
        mcp_tool_no_servers_status: mcpToolNoServers.status,
        restored_config_count: restoreEvidence.deleted_config_ids.length,
      });
    },
  },
  {
    id: "AGENTCC-API-011",
    title:
      "Gateway email alert create, mask, patch, validation-only test, and delete lifecycle",
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
          client.delete(
            apiPath("/agentcc/email-alerts/{id}/", { id: created.id }),
          ),
        ),
      );
      assertEmailAlertProviderConfigMasked(created, [
        initialApiKey,
        initialPassword,
      ]);

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

      const listed = asArray(
        await client.get(apiPath("/agentcc/email-alerts/")),
      );
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
      assert(
        detail?.id === created.id,
        "Email alert detail returned wrong id.",
      );
      assertEmailAlertProviderConfigMasked(detail, [
        initialApiKey,
        initialPassword,
      ]);

      const invalidTest = await expectApiError(
        () =>
          client.post(
            apiPath("/agentcc/email-alerts/{id}/test/", { id: created.id }),
            {
              recipient_override: "not-an-email",
            },
          ),
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
          asArray(updated.recipients).includes(
            "api-journey-updated@example.com",
          ),
        "Email alert patch did not persist active/cooldown/events/recipients.",
      );
      assertEmailAlertProviderConfigMasked(updated, [
        rotatedApiKey,
        rotatedPassword,
      ]);

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

      await client.delete(
        apiPath("/agentcc/email-alerts/{id}/", { id: created.id }),
      );
      const afterDelete = asArray(
        await client.get(apiPath("/agentcc/email-alerts/")),
      );
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

async function seedOtherWorkspaceAgentccProjectFixtureDb({
  namePrefix,
  organizationId,
  userId,
}) {
  const workspaceId = randomUUID();
  const projectId = randomUUID();
  const workspaceName = `${namePrefix} hidden api-key workspace`;
  const projectName = `${namePrefix} hidden api-key project`;
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
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(workspaceId)},
    ${sqlString(workspaceName)},
    ${sqlString(workspaceName)},
    ${sqlString("Temporary workspace for AgentCC API key project guard journey.")},
    true,
    false,
    ${sqlUuid(userId)},
    ${sqlUuid(organizationId)}
  )
  RETURNING id, name
),
inserted_project AS (
  INSERT INTO agentcc_project (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    organization_id,
    workspace_id,
    tracer_project_id,
    name,
    description,
    config
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(projectId)},
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    NULL,
    ${sqlString(projectName)},
    ${sqlString("Hidden project for AgentCC API key project guard journey.")},
    ${sqlJson({ source: "api-journey-hidden-project" })}
  )
  RETURNING id, name, workspace_id
)
SELECT json_build_object(
  'workspace_id', (SELECT id::text FROM inserted_workspace),
  'workspace_name', (SELECT name FROM inserted_workspace),
  'project_id', (SELECT id::text FROM inserted_project),
  'project_name', (SELECT name FROM inserted_project)
);
`;
  return runPostgresJson(sql);
}

async function loadAgentccApiKeyDbAudit({
  keyId,
  organizationId,
  expectedKeyHash,
  expectedName,
}) {
  const sql = `
WITH selected_keys AS (
  SELECT *
  FROM agentcc_api_key
  WHERE id = ${sqlUuid(keyId)}
    AND organization_id = ${sqlUuid(organizationId)}
)
SELECT json_build_object(
  'row_count', (SELECT count(*) FROM selected_keys),
  'name', (SELECT name FROM selected_keys LIMIT 1),
  'status', (SELECT status FROM selected_keys LIMIT 1),
  'deleted', (SELECT deleted FROM selected_keys LIMIT 1),
  'deleted_at_set', (SELECT deleted_at IS NOT NULL FROM selected_keys LIMIT 1),
  'key_hash_matches_expected', (
    SELECT key_hash = ${sqlString(expectedKeyHash)} FROM selected_keys LIMIT 1
  ),
  'name_matches_expected', (
    SELECT name = ${sqlString(expectedName)} FROM selected_keys LIMIT 1
  ),
  'workspace_id', (SELECT workspace_id::text FROM selected_keys LIMIT 1),
  'project_id', (SELECT project_id::text FROM selected_keys LIMIT 1),
  'metadata', (SELECT metadata FROM selected_keys LIMIT 1)
);
`;
  return runPostgresJson(sql);
}

async function deleteAgentccApiKeyFixtureDb({ namePrefix, organizationId }) {
  const sql = `
WITH target_keys AS (
  SELECT id
  FROM agentcc_api_key
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
deleted_keys AS (
  DELETE FROM agentcc_api_key k
  USING target_keys target
  WHERE k.id = target.id
  RETURNING k.id
)
SELECT json_build_object(
  'deleted_count', (SELECT count(*) FROM deleted_keys),
  'remaining_count', (
    SELECT count(*)
    FROM target_keys
    WHERE id NOT IN (SELECT id FROM deleted_keys)
  )
);
`;
  return runPostgresJson(sql);
}

async function seedAgentccGuardrailApplyKeyFixtureDb({
  name,
  organizationId,
  workspaceId,
}) {
  const keyId = randomUUID();
  const gatewayKeyId = `${name}_apply_key`;
  const rawKey = `${gatewayKeyId}_raw_secret`;
  const keyHash = createHash("sha256").update(rawKey).digest("hex");
  const sql = `
WITH inserted_key AS (
  INSERT INTO agentcc_api_key (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    organization_id,
    workspace_id,
    project_id,
    user_id,
    gateway_key_id,
    key_prefix,
    key_hash,
    name,
    owner,
    status,
    allowed_models,
    allowed_providers,
    metadata,
    last_used_at,
    expires_at
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(keyId)},
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    NULL,
    NULL,
    ${sqlString(gatewayKeyId)},
    'pk-gr',
    ${sqlString(keyHash)},
    ${sqlString(`${name}_apply_key`)},
    'api-journey',
    'active',
    ${sqlJson(["gpt-4o-mini"])},
    ${sqlJson(["openai"])},
    ${sqlJson({ source: "api-journey", purpose: "guardrail-policy-apply" })},
    NULL,
    NULL
  )
  RETURNING id, gateway_key_id
)
SELECT json_build_object(
  'id', (SELECT id::text FROM inserted_key),
  'gateway_key_id', (SELECT gateway_key_id FROM inserted_key)
);
`;
  const fixture = await runPostgresJson(sql);
  assert(
    fixture.id === keyId,
    `Failed to seed guardrail apply API-key fixture: ${JSON.stringify(fixture)}`,
  );
  return fixture;
}

async function deleteAgentccApiKeyProjectFixtureDb({
  namePrefix,
  organizationId,
}) {
  const sql = `
WITH target_projects AS (
  SELECT id, workspace_id
  FROM agentcc_project
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix} hidden api-key project%`)}
),
deleted_projects AS (
  DELETE FROM agentcc_project p
  USING target_projects target
  WHERE p.id = target.id
  RETURNING p.id
),
target_workspaces AS (
  SELECT id
  FROM accounts_workspace
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix} hidden api-key workspace%`)}
),
deleted_workspaces AS (
  DELETE FROM accounts_workspace w
  USING target_workspaces target
  WHERE w.id = target.id
  RETURNING w.id
)
SELECT json_build_object(
  'deleted_project_count', (SELECT count(*) FROM deleted_projects),
  'remaining_project_count', (
    SELECT count(*)
    FROM target_projects
    WHERE id NOT IN (SELECT id FROM deleted_projects)
  ),
  'deleted_workspace_count', (SELECT count(*) FROM deleted_workspaces),
  'remaining_workspace_count', (
    SELECT count(*)
    FROM target_workspaces
    WHERE id NOT IN (SELECT id FROM deleted_workspaces)
  )
);
`;
  return runPostgresJson(sql);
}

async function seedAgentccApiKeyBulkFixtureDb({
  marker,
  organizationId,
  workspaceId,
}) {
  const activeId = randomUUID();
  const noHashId = randomUUID();
  const revokedId = randomUUID();
  const deletedId = randomUUID();
  const activeGatewayKeyId = `${marker}_active`;
  const noHashGatewayKeyId = `${marker}_no_hash`;
  const revokedGatewayKeyId = `${marker}_revoked`;
  const deletedGatewayKeyId = `${marker}_deleted`;
  const activeRawKey = `${marker}_raw_secret_value`;
  const activeKeyHash = createHash("sha256").update(activeRawKey).digest("hex");
  const sql = `
WITH inserted_rows AS (
  INSERT INTO agentcc_api_key (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    organization_id,
    workspace_id,
    project_id,
    user_id,
    gateway_key_id,
    key_prefix,
    key_hash,
    name,
    owner,
    status,
    allowed_models,
    allowed_providers,
    metadata,
    last_used_at,
    expires_at
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(activeId)},
      ${sqlUuid(organizationId)},
      ${sqlUuid(workspaceId)},
      NULL,
      NULL,
      ${sqlString(activeGatewayKeyId)},
      'pk-bulk',
      ${sqlString(activeKeyHash)},
      ${sqlString(`${marker} active`)},
      'api-journey',
      'active',
      ${sqlJson(["gpt-4o"])},
      ${sqlJson(["openai"])},
      ${sqlJson({
        source: "api-journey",
        marker,
        enabled: true,
        limits: { rpm: 10 },
        tags: ["startup", "sync"],
        none: null,
      })},
      NULL,
      NULL
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(noHashId)},
      ${sqlUuid(organizationId)},
      ${sqlUuid(workspaceId)},
      NULL,
      NULL,
      ${sqlString(noHashGatewayKeyId)},
      'pk-nohash',
      '',
      ${sqlString(`${marker} no hash`)},
      'api-journey',
      'active',
      ${sqlJson([])},
      ${sqlJson([])},
      ${sqlJson({ source: "api-journey", marker, org_id: organizationId })},
      NULL,
      NULL
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(revokedId)},
      ${sqlUuid(organizationId)},
      ${sqlUuid(workspaceId)},
      NULL,
      NULL,
      ${sqlString(revokedGatewayKeyId)},
      'pk-revoked',
      ${sqlString(createHash("sha256").update(`${marker}_revoked`).digest("hex"))},
      ${sqlString(`${marker} revoked`)},
      'api-journey',
      'revoked',
      ${sqlJson([])},
      ${sqlJson([])},
      ${sqlJson({ source: "api-journey", marker, org_id: organizationId })},
      NULL,
      NULL
    ),
    (
      now(),
      now(),
      true,
      now(),
      ${sqlUuid(deletedId)},
      ${sqlUuid(organizationId)},
      ${sqlUuid(workspaceId)},
      NULL,
      NULL,
      ${sqlString(deletedGatewayKeyId)},
      'pk-deleted',
      ${sqlString(createHash("sha256").update(`${marker}_deleted`).digest("hex"))},
      ${sqlString(`${marker} deleted`)},
      'api-journey',
      'active',
      ${sqlJson([])},
      ${sqlJson([])},
      ${sqlJson({ source: "api-journey", marker, org_id: organizationId })},
      NULL,
      NULL
    )
  RETURNING id
)
SELECT json_build_object(
  'inserted_count', (SELECT count(*) FROM inserted_rows)
);
`;
  const result = await runPostgresJson(sql);
  assert(
    Number(result.inserted_count) === 4,
    `Failed to seed API-key bulk fixtures: ${JSON.stringify(result)}`,
  );
  return {
    ...result,
    active_id: activeId,
    active_gateway_key_id: activeGatewayKeyId,
    active_raw_key: activeRawKey,
    active_key_hash: activeKeyHash,
    no_hash_gateway_key_id: noHashGatewayKeyId,
    revoked_gateway_key_id: revokedGatewayKeyId,
    deleted_gateway_key_id: deletedGatewayKeyId,
  };
}

async function deleteAgentccApiKeyBulkFixtureDb({ marker, organizationId }) {
  const sql = `
WITH target AS (
  SELECT id
  FROM agentcc_api_key
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND gateway_key_id LIKE ${sqlString(`${marker}%`)}
),
deleted_rows AS (
  DELETE FROM agentcc_api_key k
  USING target
  WHERE k.id = target.id
  RETURNING k.id
)
SELECT json_build_object(
  'deleted_count', (SELECT count(*) FROM deleted_rows),
  'remaining_count', (
    SELECT count(*)
    FROM agentcc_api_key
    WHERE organization_id = ${sqlUuid(organizationId)}
      AND gateway_key_id LIKE ${sqlString(`${marker}%`)}
  )
);
`;
  return runPostgresJson(sql);
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

function isTemporalUnavailableError(error) {
  return /Failed client connect|tonic::transport|failed to lookup address information/i.test(
    errorText(error),
  );
}

function isInvalidSimulationPageError(error) {
  return (
    [404, 500].includes(error?.status) && /Invalid page/i.test(errorText(error))
  );
}

function assertNoRawSecret(payload, rawSecret, message) {
  assert(!JSON.stringify(payload || {}).includes(rawSecret), message);
}

function isEntitlementDeniedError(error) {
  const text = errorText(error).toLowerCase();
  return (
    error?.status === 402 &&
    (text.includes("entitlement") || text.includes("upgrade"))
  );
}

function isFeatureDeniedError(error) {
  const text = errorText(error).toLowerCase();
  return (
    [402, 403].includes(error?.status) &&
    (text.includes("entitlement") ||
      text.includes("upgrade") ||
      text.includes("not available") ||
      text.includes("agentic_eval") ||
      text.includes("has_agentic_eval") ||
      text.includes("synthetic_data"))
  );
}

function assertApprox(actual, expected, message, tolerance = 0.0001) {
  const actualNumber = Number(actual);
  assert(
    Number.isFinite(actualNumber) &&
      Math.abs(actualNumber - expected) <= tolerance,
    `${message} Expected ${expected}, received ${actual}.`,
  );
}

function sumMetric(rows, key) {
  return asArray(rows).reduce((sum, row) => sum + Number(row?.[key] || 0), 0);
}

function findBreakdown(rows, name) {
  return asArray(rows).find((row) => String(row?.name) === String(name));
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
      try {
        const payload = await client.get(source, {
          query: { limit: 25, page },
        });
        candidates.push(...collectionRows(payload));
      } catch (error) {
        if (!isInvalidSimulationPageError(error)) throw error;
      }
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
  const payload = await client.post(
    apiPath("/model-hub/eval-templates/list/"),
    {
      page: 0,
      page_size: 10,
      owner_filter: "all",
      search: name,
      sort_by: "updated_at",
      sort_order: "desc",
    },
  );
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

function assertSimulationEvalSummary(
  summary,
  { template, evalConfig, expectedCalls },
) {
  const rows = asArray(summary);
  const row = rows.find((item) => {
    const isTemplateRow =
      item?.id === template.id || item?.name === template.name;
    const hasEvalConfig = asArray(item?.result).some(
      (config) => config?.id === evalConfig.id,
    );
    return isTemplateRow && hasEvalConfig;
  });
  assert(
    row,
    `Eval summary did not include template ${template.name}: ${JSON.stringify(
      summary,
    )}`,
  );

  const configRow = asArray(row.result).find(
    (config) => config?.id === evalConfig.id,
  );
  assert(
    configRow?.name === evalConfig.name &&
      Number(configRow.total_cells) === expectedCalls,
    `Eval summary did not include the completed eval config cells: ${JSON.stringify(
      row,
    )}`,
  );
  assert(
    Number(configRow.output?.pass_count) === expectedCalls &&
      Number(configRow.output?.fail_count || 0) === 0 &&
      Number(row.total_pass_rate) === 100,
    `Eval summary pass/fail rollup was incorrect: ${JSON.stringify(row)}`,
  );
  return row;
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
  const assignmentPattern =
    /\b(FI_API_KEY|FI_SECRET_KEY)\s*=\s*["']([^"']+)["']/g;
  for (const match of sdkCode.matchAll(assignmentPattern)) {
    const [, name, value] = match;
    if (value.startsWith("<") || value.includes("YOUR_")) continue;
    if (/^[A-Za-z0-9_-]{16,}$/.test(value)) {
      findings.push({ name, length: value.length });
    }
  }
  return findings;
}

async function seedSimulationScenarioFixturesDb({
  namePrefix,
  organizationId,
  workspaceId,
  userId,
}) {
  const agentDefinitionId = randomUUID();
  const simulatorAgentId = randomUUID();
  const datasetId = randomUUID();
  const inputColumnId = randomUUID();
  const expectedColumnId = randomUUID();
  const rowOneId = randomUUID();
  const rowTwoId = randomUUID();
  const scenarioId = randomUUID();
  const noDatasetScenarioId = randomUUID();
  const noSimulatorScenarioId = randomUUID();
  const graphId = randomUUID();
  const cellIds = [randomUUID(), randomUUID(), randomUUID(), randomUUID()];
  const columnConfig = {
    [inputColumnId]: {
      name: "input",
      type: "text",
      description: "User input",
    },
    [expectedColumnId]: {
      name: "expected",
      type: "text",
      description: "Expected output",
    },
  };
  const sql = `
WITH inserted_agent AS (
  INSERT INTO simulate_agent_definition (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    agent_name,
    contact_number,
    inbound,
    description,
    assistant_id,
    language,
    websocket_url,
    websocket_headers,
    organization_id,
    provider,
    workspace_id,
    agent_type,
    api_key,
    authentication_method,
    languages,
    model,
    model_details
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(agentDefinitionId)},
    ${sqlString(`${namePrefix} agent`)},
    NULL,
    true,
    ${sqlString("Temporary text agent definition for scenario API journey.")},
    NULL,
    'en',
    NULL,
    '{}'::jsonb,
    ${sqlUuid(organizationId)},
    NULL,
    ${sqlUuid(workspaceId)},
    'text',
    NULL,
    'api_key',
    ARRAY['en']::varchar[],
    'gpt-4o-mini',
    ${sqlJson({ source: "api-journey", fixture: "scenario" })}
  )
  RETURNING id
),
inserted_simulator_agent AS (
  INSERT INTO simulator_agents (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    prompt,
    voice_provider,
    voice_name,
    interrupt_sensitivity,
    conversation_speed,
    finished_speaking_sensitivity,
    model,
    llm_temperature,
    max_call_duration_in_minutes,
    initial_message_delay,
    initial_message,
    organization_id,
    workspace_id
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(simulatorAgentId)},
    ${sqlString(`${namePrefix} simulator`)},
    ${sqlString("You are a temporary simulator agent for API journey coverage.")},
    'elevenlabs',
    'marissa',
    0.5,
    1.0,
    0.5,
    'gpt-4o-mini',
    0.7,
    30,
    0,
    '',
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)}
  )
  RETURNING id
),
inserted_dataset AS (
  INSERT INTO model_hub_dataset (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    column_order,
    model_type,
    organization_id,
    column_config,
    source,
    dataset_config,
    user_id,
    synthetic_dataset_config,
    workspace_id,
    eval_reasons,
    eval_reason_status
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(datasetId)},
    ${sqlString(`${namePrefix} source dataset`)},
    ARRAY[${sqlString(inputColumnId)}, ${sqlString(expectedColumnId)}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${sqlJson(columnConfig)},
    'scenario',
    '{}'::jsonb,
    ${sqlUuid(userId)},
    '{}'::jsonb,
    ${sqlUuid(workspaceId)},
    '[]'::jsonb,
    'pending'
  )
  RETURNING id
),
inserted_columns AS (
  INSERT INTO model_hub_column (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(inputColumnId)},
      'input',
      'text',
      'OTHERS',
      NULL,
      ${sqlUuid(datasetId)},
      ${sqlJson({ description: "User input" })},
      'Completed'
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(expectedColumnId)},
      'expected',
      'text',
      'OTHERS',
      NULL,
      ${sqlUuid(datasetId)},
      ${sqlJson({ description: "Expected output" })},
      'Completed'
    )
  RETURNING id
),
inserted_rows AS (
  INSERT INTO model_hub_row (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    "order",
    dataset_id,
    metadata
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(rowOneId)},
      0,
      ${sqlUuid(datasetId)},
      ${sqlJson({ source: "api-journey", row: 1 })}
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(rowTwoId)},
      1,
      ${sqlUuid(datasetId)},
      ${sqlJson({ source: "api-journey", row: 2 })}
    )
  RETURNING id
),
inserted_cells AS (
  INSERT INTO model_hub_cell (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    value,
    column_id,
    dataset_id,
    row_id,
    status,
    value_infos,
    feedback_info,
    column_metadata
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(cellIds[0])},
      ${sqlString(`${namePrefix} input one`)},
      ${sqlUuid(inputColumnId)},
      ${sqlUuid(datasetId)},
      ${sqlUuid(rowOneId)},
      'pass',
      '[]'::jsonb,
      '{}'::jsonb,
      '{}'::jsonb
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(cellIds[1])},
      ${sqlString(`${namePrefix} expected one`)},
      ${sqlUuid(expectedColumnId)},
      ${sqlUuid(datasetId)},
      ${sqlUuid(rowOneId)},
      'pass',
      '[]'::jsonb,
      '{}'::jsonb,
      '{}'::jsonb
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(cellIds[2])},
      ${sqlString(`${namePrefix} input two`)},
      ${sqlUuid(inputColumnId)},
      ${sqlUuid(datasetId)},
      ${sqlUuid(rowTwoId)},
      'pass',
      '[]'::jsonb,
      '{}'::jsonb,
      '{}'::jsonb
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(cellIds[3])},
      ${sqlString(`${namePrefix} expected two`)},
      ${sqlUuid(expectedColumnId)},
      ${sqlUuid(datasetId)},
      ${sqlUuid(rowTwoId)},
      'pass',
      '[]'::jsonb,
      '{}'::jsonb,
      '{}'::jsonb
    )
  RETURNING id
),
inserted_scenarios AS (
  INSERT INTO simulate_scenarios (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    source,
    scenario_type,
    organization_id,
    dataset_id,
    description,
    workspace_id,
    metadata,
    simulator_agent_id,
    status,
    agent_definition_id,
    source_type,
    prompt_template_id,
    prompt_version_id
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(scenarioId)},
      ${sqlString(`${namePrefix} completed`)},
      ${sqlString(`${namePrefix} source`)},
      'dataset',
      ${sqlUuid(organizationId)},
      ${sqlUuid(datasetId)},
      ${sqlString("Temporary completed scenario for API journey coverage.")},
      ${sqlUuid(workspaceId)},
      ${sqlJson({ source: "api-journey", fixture: "scenario" })},
      ${sqlUuid(simulatorAgentId)},
      'Completed',
      ${sqlUuid(agentDefinitionId)},
      'agent_definition',
      NULL,
      NULL
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(noDatasetScenarioId)},
      ${sqlString(`${namePrefix} no dataset`)},
      ${sqlString(`${namePrefix} no dataset source`)},
      'graph',
      ${sqlUuid(organizationId)},
      NULL,
      ${sqlString("Temporary no-dataset scenario for validation coverage.")},
      ${sqlUuid(workspaceId)},
      ${sqlJson({ source: "api-journey", fixture: "no-dataset" })},
      ${sqlUuid(simulatorAgentId)},
      'Completed',
      ${sqlUuid(agentDefinitionId)},
      'agent_definition',
      NULL,
      NULL
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(noSimulatorScenarioId)},
      ${sqlString(`${namePrefix} no simulator`)},
      ${sqlString(`${namePrefix} no simulator source`)},
      'dataset',
      ${sqlUuid(organizationId)},
      ${sqlUuid(datasetId)},
      ${sqlString("Temporary no-simulator scenario for validation coverage.")},
      ${sqlUuid(workspaceId)},
      ${sqlJson({ source: "api-journey", fixture: "no-simulator" })},
      NULL,
      'Completed',
      ${sqlUuid(agentDefinitionId)},
      'agent_definition',
      NULL,
      NULL
    )
  RETURNING id
),
inserted_graph AS (
  INSERT INTO simulate_scenario_graph (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    description,
    version,
    is_active,
    graph_config,
    organization_id,
    scenario_id
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(graphId)},
    ${sqlString(`${namePrefix} graph`)},
    ${sqlString("Temporary scenario graph for API journey coverage.")},
    1,
    true,
    ${sqlJson({
      graph_data: {
        nodes: [{ id: "start", type: "start" }],
        edges: [],
      },
      source: "api-journey",
    })},
    ${sqlUuid(organizationId)},
    ${sqlUuid(scenarioId)}
  )
  RETURNING id
)
SELECT json_build_object(
  'agent_definition_id', ${sqlString(agentDefinitionId)},
  'simulator_agent_id', ${sqlString(simulatorAgentId)},
  'dataset_id', ${sqlString(datasetId)},
  'scenario_id', ${sqlString(scenarioId)},
  'no_dataset_scenario_id', ${sqlString(noDatasetScenarioId)},
  'no_simulator_scenario_id', ${sqlString(noSimulatorScenarioId)},
  'graph_id', ${sqlString(graphId)},
  'inserted_agent_count', (SELECT count(*) FROM inserted_agent),
  'inserted_dataset_count', (SELECT count(*) FROM inserted_dataset),
  'inserted_column_count', (SELECT count(*) FROM inserted_columns),
  'inserted_row_count', (SELECT count(*) FROM inserted_rows),
  'inserted_cell_count', (SELECT count(*) FROM inserted_cells),
  'inserted_scenario_count', (SELECT count(*) FROM inserted_scenarios),
  'inserted_graph_count', (SELECT count(*) FROM inserted_graph)
);
`;
  const result = await runPostgresJson(sql);
  assert(
    Number(result.inserted_scenario_count) === 3 &&
      Number(result.inserted_row_count) === 2 &&
      Number(result.inserted_cell_count) === 4,
    `Failed to seed disposable scenario fixture rows: ${JSON.stringify(result)}`,
  );
  return result;
}

async function seedVoiceSimulationCallOutputFixtureDb({
  namePrefix,
  organizationId,
  workspaceId,
}) {
  const agentDefinitionId = randomUUID();
  const agentVersionId = randomUUID();
  const simulatorAgentId = randomUUID();
  const scenarioId = randomUUID();
  const scenarioGraphId = randomUUID();
  const runTestId = randomUUID();
  const testExecutionId = randomUUID();
  const callExecutionId = randomUUID();
  const transcriptIds = [randomUUID(), randomUUID()];
  const customerCallId = `${namePrefix}-provider-call`;
  const recordingUrl = "https://example.com/api-journey/voice-call.mp3";
  const stereoRecordingUrl =
    "https://example.com/api-journey/voice-call-stereo.mp3";
  const providerPayload = {
    id: customerCallId,
    type: "outboundPhoneCall",
    status: "ended",
    endedReason: "customer-ended-call",
    recordingUrl,
    stereoRecordingUrl,
    recording: {
      mono: { combined: recordingUrl },
      stereo: stereoRecordingUrl,
    },
    artifact: {
      recordingUrl,
      stereoRecordingUrl,
      messages: [
        { role: "user", message: "I need help checking my order." },
        {
          role: "assistant",
          message: "I can help with that. What is the order number?",
        },
      ],
    },
    analysis: {
      summary: "The customer asked for order status and the agent helped.",
      successEvaluation: true,
    },
    cost: 0.42,
  };
  const voiceSnapshot = {
    agent_name: `${namePrefix} voice agent`,
    agent_type: "voice",
    provider: "vapi",
    assistant_id: `${namePrefix}-assistant`,
    contact_number: "+15555550111",
    inbound: false,
    language: "en",
    languages: ["en"],
    authentication_method: "api_key",
    model: "gpt-4o-mini",
    model_details: { source: "api-journey", fixture: "voice-output" },
  };

  const sql = `
WITH inserted_agent AS (
  INSERT INTO simulate_agent_definition (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    agent_name,
    agent_type,
    contact_number,
    inbound,
    description,
    assistant_id,
    provider,
    language,
    languages,
    websocket_url,
    websocket_headers,
    organization_id,
    workspace_id,
    api_key,
    authentication_method,
    model,
    model_details
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(agentDefinitionId)},
    ${sqlString(`${namePrefix} voice agent`)},
    'voice',
    '+15555550111',
    false,
    ${sqlString("Temporary voice agent for API journey output readback.")},
    ${sqlString(`${namePrefix}-assistant`)},
    'vapi',
    'en',
    ARRAY['en']::varchar[],
    NULL,
    '{}'::jsonb,
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    NULL,
    'api_key',
    'gpt-4o-mini',
    ${sqlJson({ source: "api-journey", fixture: "voice-output" })}
  )
  RETURNING id
),
inserted_version AS (
  INSERT INTO simulate_agent_version (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    version_number,
    version_name,
    status,
    score,
    test_count,
    pass_rate,
    description,
    commit_message,
    release_notes,
    agent_definition_id,
    organization_id,
    workspace_id,
    configuration_snapshot
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(agentVersionId)},
    1,
    'v1',
    'active',
    9.0,
    1,
    100.00,
    ${sqlString("Temporary voice version for API journey output readback.")},
    ${sqlString("Seed voice simulation output fixture.")},
    NULL,
    ${sqlUuid(agentDefinitionId)},
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    ${sqlJson(voiceSnapshot)}
  )
  RETURNING id
),
inserted_simulator_agent AS (
  INSERT INTO simulator_agents (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    prompt,
    voice_provider,
    voice_name,
    interrupt_sensitivity,
    conversation_speed,
    finished_speaking_sensitivity,
    model,
    llm_temperature,
    max_call_duration_in_minutes,
    initial_message_delay,
    initial_message,
    organization_id,
    workspace_id
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(simulatorAgentId)},
    ${sqlString(`${namePrefix} simulator`)},
    ${sqlString("You are a temporary voice simulator for API journey coverage.")},
    'elevenlabs',
    'marissa',
    0.5,
    1.0,
    0.5,
    'gpt-4o-mini',
    0.7,
    30,
    0,
    'Hello, how can I help?',
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)}
  )
  RETURNING id
),
inserted_scenario AS (
  INSERT INTO simulate_scenarios (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    source,
    scenario_type,
    organization_id,
    dataset_id,
    description,
    workspace_id,
    metadata,
    simulator_agent_id,
    status,
    agent_definition_id,
    source_type,
    prompt_template_id,
    prompt_version_id
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(scenarioId)},
    ${sqlString(`${namePrefix} voice scenario`)},
    ${sqlString(`${namePrefix} voice source`)},
    'graph',
    ${sqlUuid(organizationId)},
    NULL,
    ${sqlString("Temporary voice scenario for completed call output coverage.")},
    ${sqlUuid(workspaceId)},
    ${sqlJson({ source: "api-journey", fixture: "voice-output" })},
    ${sqlUuid(simulatorAgentId)},
    'Completed',
    ${sqlUuid(agentDefinitionId)},
    'agent_definition',
    NULL,
    NULL
  )
  RETURNING id
),
inserted_graph AS (
  INSERT INTO simulate_scenario_graph (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    description,
    version,
    is_active,
    graph_config,
    organization_id,
    scenario_id
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(scenarioGraphId)},
    ${sqlString(`${namePrefix} voice graph`)},
    ${sqlString("Temporary voice graph for API journey output coverage.")},
    1,
    true,
    ${sqlJson({
      graph_data: {
        nodes: [
          { id: "start", type: "start", label: "Start" },
          { id: "order", type: "intent", label: "Order lookup" },
        ],
        edges: [{ id: "start-order", source: "start", target: "order" }],
      },
      source: "api-journey",
    })},
    ${sqlUuid(organizationId)},
    ${sqlUuid(scenarioId)}
  )
  RETURNING id
),
inserted_run AS (
  INSERT INTO simulate_run_test (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    description,
    agent_definition_id,
    agent_version_id,
    source_type,
    prompt_template_id,
    prompt_version_id,
    dataset_row_ids,
    simulator_agent_id,
    organization_id,
    workspace_id,
    enable_tool_evaluation
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(runTestId)},
    ${sqlString(`${namePrefix} run`)},
    ${sqlString("Temporary voice run for completed call output coverage.")},
    ${sqlUuid(agentDefinitionId)},
    ${sqlUuid(agentVersionId)},
    'agent_definition',
    NULL,
    NULL,
    ARRAY[]::varchar[],
    ${sqlUuid(simulatorAgentId)},
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    false
  )
  RETURNING id
),
inserted_run_scenario AS (
  INSERT INTO simulate_run_test_scenarios (
    runtest_id,
    scenarios_id
  )
  VALUES (
    ${sqlUuid(runTestId)},
    ${sqlUuid(scenarioId)}
  )
  RETURNING runtest_id
),
inserted_execution AS (
  INSERT INTO simulate_test_execution (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    run_test_id,
    status,
    started_at,
    completed_at,
    total_scenarios,
    scenario_ids,
    total_calls,
    completed_calls,
    failed_calls,
    execution_metadata,
    picked_up_by_executor,
    simulator_agent_id,
    agent_definition_id,
    agent_version_id,
    eval_explanation_summary,
    eval_explanation_summary_last_updated,
    eval_explanation_summary_status,
    agent_optimiser_id,
    error_reason
  )
  VALUES (
    now() - interval '2 minutes',
    now(),
    false,
    NULL,
    ${sqlUuid(testExecutionId)},
    ${sqlUuid(runTestId)},
    'completed',
    now() - interval '2 minutes',
    now() - interval '1 minute',
    1,
    ${sqlJson([scenarioId])},
    1,
    1,
    0,
    ${sqlJson({ source: "api-journey", fixture: "voice-output" })},
    true,
    ${sqlUuid(simulatorAgentId)},
    ${sqlUuid(agentDefinitionId)},
    ${sqlUuid(agentVersionId)},
    '{}'::jsonb,
    now(),
    'completed',
    NULL,
    NULL
  )
  RETURNING id
),
inserted_call AS (
  INSERT INTO simulate_call_execution (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    test_execution_id,
    simulation_call_type,
    scenario_id,
    phone_number,
    service_provider_call_id,
    status,
    started_at,
    completed_at,
    duration_seconds,
    recording_url,
    cost_cents,
    call_metadata,
    error_message,
    provider_call_data,
    monitor_call_data,
    logs_ingested_at,
    logs_summary,
    customer_logs_summary,
    stereo_recording_url,
    customer_log_url,
    call_summary,
    ended_reason,
    stt_cost_cents,
    llm_cost_cents,
    tts_cost_cents,
    storage_cost_cents,
    vapi_cost_cents,
    overall_score,
    response_time_ms,
    assistant_id,
    customer_number,
    call_type,
    ended_at,
    analysis_data,
    evaluation_data,
    message_count,
    transcript_available,
    recording_available,
    row_id,
    eval_outputs,
    tool_outputs,
    agent_version_id,
    customer_call_id,
    customer_cost_cents,
    customer_cost_breakdown,
    customer_latency_metrics,
    avg_agent_latency_ms,
    user_interruption_count,
    user_interruption_rate,
    user_wpm,
    bot_wpm,
    talk_ratio,
    ai_interruption_count,
    ai_interruption_rate,
    avg_stop_time_after_interruption_ms,
    conversation_metrics_data
  )
  VALUES (
    now() - interval '2 minutes',
    now(),
    false,
    NULL,
    ${sqlUuid(callExecutionId)},
    ${sqlUuid(testExecutionId)},
    'voice',
    ${sqlUuid(scenarioId)},
    '+15555550123',
    ${sqlString(customerCallId)},
    'completed',
    now() - interval '2 minutes',
    now() - interval '56 seconds',
    64,
    ${sqlString(recordingUrl)},
    42,
    ${sqlJson({
      source: "api-journey",
      fixture: "voice-output",
      call_direction: "outbound",
      recording_offset_ms: 250,
    })},
    NULL,
    ${sqlJson({ vapi: providerPayload })},
    ${sqlJson({ source: "api-journey", status: "completed" })},
    now(),
    ${sqlJson({ info: 1, error: 0 })},
    ${sqlJson({ info: 1 })},
    ${sqlString(stereoRecordingUrl)},
    'https://example.com/api-journey/customer-log.json',
    'Customer asked about an order and the agent collected the order number.',
    'customer-ended-call',
    5,
    12,
    20,
    0.05,
    42,
    9.4,
    1200,
    ${sqlString(`${namePrefix}-assistant`)},
    '+15555550123',
    'outboundPhoneCall',
    now() - interval '56 seconds',
    ${sqlJson({
      summary: "Customer asked about an order.",
      successEvaluation: true,
    })},
    ${sqlJson({ success: true, score: 0.94 })},
    2,
    true,
    true,
    NULL,
    '{}'::jsonb,
    '{}'::jsonb,
    ${sqlUuid(agentVersionId)},
    ${sqlString(customerCallId)},
    42,
    ${sqlJson({ total: 42, stt: 5, llm: 12, tts: 20, storage: 0.05 })},
    ${sqlJson({ avg_agent_latency_ms: 1200 })},
    1200,
    0,
    0,
    142.5,
    115.0,
    1.5,
    0,
    0,
    NULL,
    ${sqlJson({
      turn_count: 1,
      bot_message_count: 1,
      user_message_count: 1,
      message_count: 2,
      avg_latency_ms: 1200,
      csat_score: 5,
    })}
  )
  RETURNING id
),
inserted_create_call AS (
  INSERT INTO simulate_createcallexecution (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    phone_number_id,
    to_number,
    system_prompt,
    metadata,
    voice_settings,
    call_execution_id,
    status
  )
  VALUES (
    now() - interval '2 minutes',
    now(),
    false,
    NULL,
    ${sqlString(`${namePrefix}-phone-number`)},
    '+15555550123',
    'Temporary voice simulation call for API journey output coverage.',
    ${sqlJson({ source: "api-journey", fixture: "voice-output" })},
    ${sqlJson({ provider: "vapi", voice: "marissa" })},
    ${sqlUuid(callExecutionId)},
    'completed'
  )
  RETURNING id
),
inserted_transcripts AS (
  INSERT INTO simulate_call_transcript (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    call_execution_id,
    speaker_role,
    content,
    start_time_ms,
    end_time_ms,
    confidence_score
  )
  VALUES
    (
      now() - interval '2 minutes',
      now(),
      false,
      NULL,
      ${sqlUuid(transcriptIds[0])},
      ${sqlUuid(callExecutionId)},
      'user',
      'I need help checking my order.',
      0,
      1800,
      0.99
    ),
    (
      now() - interval '2 minutes',
      now(),
      false,
      NULL,
      ${sqlUuid(transcriptIds[1])},
      ${sqlUuid(callExecutionId)},
      'assistant',
      'I can help with that. What is the order number?',
      2200,
      5200,
      0.98
    )
  RETURNING id
)
SELECT json_build_object(
  'agent_definition_id', ${sqlString(agentDefinitionId)},
  'agent_version_id', ${sqlString(agentVersionId)},
  'simulator_agent_id', ${sqlString(simulatorAgentId)},
  'scenario_id', ${sqlString(scenarioId)},
  'scenario_graph_id', ${sqlString(scenarioGraphId)},
  'run_test_id', ${sqlString(runTestId)},
  'test_execution_id', ${sqlString(testExecutionId)},
  'call_execution_id', ${sqlString(callExecutionId)},
  'customer_call_id', ${sqlString(customerCallId)},
  'recording_url', ${sqlString(recordingUrl)},
  'inserted_agent_count', (SELECT count(*) FROM inserted_agent),
  'inserted_version_count', (SELECT count(*) FROM inserted_version),
  'inserted_simulator_agent_count', (SELECT count(*) FROM inserted_simulator_agent),
  'inserted_scenario_count', (SELECT count(*) FROM inserted_scenario),
  'inserted_graph_count', (SELECT count(*) FROM inserted_graph),
  'inserted_run_count', (SELECT count(*) FROM inserted_run),
  'inserted_run_scenario_count', (SELECT count(*) FROM inserted_run_scenario),
  'inserted_execution_count', (SELECT count(*) FROM inserted_execution),
  'inserted_call_count', (SELECT count(*) FROM inserted_call),
  'inserted_create_call_count', (SELECT count(*) FROM inserted_create_call),
  'inserted_transcript_count', (SELECT count(*) FROM inserted_transcripts)
);
`;
  const result = await runPostgresJson(sql);
  assert(
    Number(result.inserted_agent_count) === 1 &&
      Number(result.inserted_version_count) === 1 &&
      Number(result.inserted_run_count) === 1 &&
      Number(result.inserted_execution_count) === 1 &&
      Number(result.inserted_call_count) === 1 &&
      Number(result.inserted_transcript_count) === 2,
    `Failed to seed voice simulation output fixture: ${JSON.stringify(result)}`,
  );
  return result;
}

async function seedAgentPromptOptimiserFixtureDb({
  namePrefix,
  organizationId,
  workspaceId,
  userId,
}) {
  const hiddenWorkspaceId = randomUUID();
  const active = {
    agentOptimiserId: randomUUID(),
    agentOptimiserRunId: randomUUID(),
    runTestId: randomUUID(),
    testExecutionId: randomUUID(),
    promptRunId: randomUUID(),
    stepIds: [randomUUID(), randomUUID()],
    scenarioId: randomUUID(),
    callExecutionId: randomUUID(),
    evalTemplateId: randomUUID(),
    evalConfigId: randomUUID(),
    baselineTrialId: randomUUID(),
    trialId: randomUUID(),
    baselineItemId: randomUUID(),
    trialItemId: randomUUID(),
    componentEvalIds: [randomUUID(), randomUUID()],
  };
  const hidden = {
    agentOptimiserId: randomUUID(),
    agentOptimiserRunId: randomUUID(),
    runTestId: randomUUID(),
    testExecutionId: randomUUID(),
    promptRunId: randomUUID(),
    stepIds: [randomUUID(), randomUUID()],
    scenarioId: randomUUID(),
    callExecutionId: randomUUID(),
    evalTemplateId: randomUUID(),
    evalConfigId: randomUUID(),
    baselineTrialId: randomUUID(),
    trialId: randomUUID(),
    baselineItemId: randomUUID(),
    trialItemId: randomUUID(),
    componentEvalIds: [randomUUID(), randomUUID()],
  };

  const sql = `
WITH inserted_hidden_workspace AS (
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
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(hiddenWorkspaceId)},
    ${sqlString(`${namePrefix} hidden workspace`)},
    ${sqlString(`${namePrefix} hidden workspace`)},
    ${sqlString("Temporary hidden workspace for API journey coverage.")},
    true,
    false,
    ${sqlUuid(userId)},
    ${sqlUuid(organizationId)}
  )
  RETURNING id
),
inserted_agent_optimisers AS (
  INSERT INTO agent_optimiser (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    description,
    configuration
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.agentOptimiserId)},
      ${sqlString(`${namePrefix} active optimiser`)},
      ${sqlString("Temporary active optimizer for API journey coverage.")},
      ${sqlJson({ source: "api-journey", workspace: "active" })}
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.agentOptimiserId)},
      ${sqlString(`${namePrefix} hidden optimiser`)},
      ${sqlString("Temporary hidden optimizer for API journey coverage.")},
      ${sqlJson({ source: "api-journey", workspace: "hidden" })}
    )
  RETURNING id
),
inserted_agent_optimiser_runs AS (
  INSERT INTO agent_optimiser_run (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    agent_optimiser_id,
    status,
    input_data,
    result,
    metadata
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.agentOptimiserRunId)},
      ${sqlUuid(active.agentOptimiserId)},
      'completed',
      ${sqlJson({ prompt: "old" })},
      ${sqlJson({ prompt: "new" })},
      ${sqlJson({ source: "api-journey", workspace: "active" })}
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.agentOptimiserRunId)},
      ${sqlUuid(hidden.agentOptimiserId)},
      'completed',
      ${sqlJson({ prompt: "old" })},
      ${sqlJson({ prompt: "new" })},
      ${sqlJson({ source: "api-journey", workspace: "hidden" })}
    )
  RETURNING id
),
inserted_run_tests AS (
  INSERT INTO simulate_run_test (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    description,
    source_type,
    dataset_row_ids,
    organization_id,
    workspace_id,
    enable_tool_evaluation
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.runTestId)},
      ${sqlString(`${namePrefix} active run test`)},
      ${sqlString("Temporary active run test for prompt optimiser journey.")},
      'agent_definition',
      ARRAY[]::varchar[],
      ${sqlUuid(organizationId)},
      ${sqlUuid(workspaceId)},
      false
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.runTestId)},
      ${sqlString(`${namePrefix} hidden run test`)},
      ${sqlString("Temporary hidden run test for prompt optimiser journey.")},
      'agent_definition',
      ARRAY[]::varchar[],
      ${sqlUuid(organizationId)},
      ${sqlUuid(hiddenWorkspaceId)},
      false
    )
  RETURNING id
),
inserted_test_executions AS (
  INSERT INTO simulate_test_execution (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    run_test_id,
    status,
    started_at,
    completed_at,
    total_scenarios,
    scenario_ids,
    total_calls,
    completed_calls,
    failed_calls,
    execution_metadata,
    picked_up_by_executor,
    eval_explanation_summary,
    eval_explanation_summary_last_updated,
    eval_explanation_summary_status,
    agent_optimiser_id,
    error_reason
  )
  VALUES
    (
      now() - interval '2 minutes',
      now(),
      false,
      NULL,
      ${sqlUuid(active.testExecutionId)},
      ${sqlUuid(active.runTestId)},
      'completed',
      now() - interval '2 minutes',
      now() - interval '1 minute',
      1,
      ${sqlJson([active.scenarioId])},
      1,
      1,
      0,
      ${sqlJson({ source: "api-journey", workspace: "active" })},
      true,
      '{}'::jsonb,
      now(),
      'completed',
      ${sqlUuid(active.agentOptimiserId)},
      NULL
    ),
    (
      now() - interval '2 minutes',
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.testExecutionId)},
      ${sqlUuid(hidden.runTestId)},
      'completed',
      now() - interval '2 minutes',
      now() - interval '1 minute',
      1,
      ${sqlJson([hidden.scenarioId])},
      1,
      1,
      0,
      ${sqlJson({ source: "api-journey", workspace: "hidden" })},
      true,
      '{}'::jsonb,
      now(),
      'completed',
      ${sqlUuid(hidden.agentOptimiserId)},
      NULL
    )
  RETURNING id
),
inserted_prompt_runs AS (
  INSERT INTO agent_prompt_optimiser_run (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    agent_optimiser_id,
    agent_optimiser_run_id,
    test_execution_id,
    optimiser_type,
    model,
    status,
    result,
    error_message,
    configuration
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.promptRunId)},
      ${sqlString(`${namePrefix} active prompt run`)},
      ${sqlUuid(active.agentOptimiserId)},
      ${sqlUuid(active.agentOptimiserRunId)},
      ${sqlUuid(active.testExecutionId)},
      'protegi',
      'gpt-4o-mini',
      'completed',
      ${sqlJson({ history: [{ trial: 1 }] })},
      NULL,
      ${sqlJson({
        beam_size: 2,
        num_gradients: 1,
        errors_per_gradient: 1,
        prompts_per_gradient: 1,
        num_rounds: 1,
      })}
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.promptRunId)},
      ${sqlString(`${namePrefix} hidden prompt run`)},
      ${sqlUuid(hidden.agentOptimiserId)},
      ${sqlUuid(hidden.agentOptimiserRunId)},
      ${sqlUuid(hidden.testExecutionId)},
      'protegi',
      'gpt-4o-mini',
      'completed',
      ${sqlJson({ history: [{ trial: 1 }] })},
      NULL,
      ${sqlJson({
        beam_size: 2,
        num_gradients: 1,
        errors_per_gradient: 1,
        prompts_per_gradient: 1,
        num_rounds: 1,
      })}
    )
  RETURNING id
),
inserted_steps AS (
  INSERT INTO agent_prompt_optimiser_run_step (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    agent_prompt_optimiser_run_id,
    step_number,
    name,
    description,
    status,
    metadata
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.stepIds[0])},
      ${sqlUuid(active.promptRunId)},
      1,
      'Collect calls',
      'Collect call outputs',
      'completed',
      ${sqlJson({ source: "api-journey" })}
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.stepIds[1])},
      ${sqlUuid(active.promptRunId)},
      2,
      'Generate prompt',
      'Generate candidate prompt',
      'completed',
      ${sqlJson({ source: "api-journey" })}
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.stepIds[0])},
      ${sqlUuid(hidden.promptRunId)},
      1,
      'Collect calls',
      'Collect call outputs',
      'completed',
      ${sqlJson({ source: "api-journey" })}
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.stepIds[1])},
      ${sqlUuid(hidden.promptRunId)},
      2,
      'Generate prompt',
      'Generate candidate prompt',
      'completed',
      ${sqlJson({ source: "api-journey" })}
    )
  RETURNING id
),
inserted_scenarios AS (
  INSERT INTO simulate_scenarios (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    description,
    source,
    scenario_type,
    organization_id,
    workspace_id,
    metadata,
    status,
    source_type
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.scenarioId)},
      ${sqlString(`${namePrefix} active scenario`)},
      'Handle a refund request',
      'Customer asks for a refund.',
      'dataset',
      ${sqlUuid(organizationId)},
      ${sqlUuid(workspaceId)},
      ${sqlJson({ source: "api-journey", workspace: "active" })},
      'Completed',
      'agent_definition'
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.scenarioId)},
      ${sqlString(`${namePrefix} hidden scenario`)},
      'Handle a hidden refund request',
      'Customer asks for a refund in another workspace.',
      'dataset',
      ${sqlUuid(organizationId)},
      ${sqlUuid(hiddenWorkspaceId)},
      ${sqlJson({ source: "api-journey", workspace: "hidden" })},
      'Completed',
      'agent_definition'
    )
  RETURNING id
),
inserted_run_scenarios AS (
  INSERT INTO simulate_run_test_scenarios (
    runtest_id,
    scenarios_id
  )
  VALUES
    (${sqlUuid(active.runTestId)}, ${sqlUuid(active.scenarioId)}),
    (${sqlUuid(hidden.runTestId)}, ${sqlUuid(hidden.scenarioId)})
  RETURNING runtest_id
),
inserted_calls AS (
  INSERT INTO simulate_call_execution (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    test_execution_id,
    simulation_call_type,
    scenario_id,
    phone_number,
    status,
    started_at,
    completed_at,
    duration_seconds,
    call_metadata,
    eval_outputs,
    tool_outputs,
    transcript_available,
    recording_available,
    overall_score
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.callExecutionId)},
      ${sqlUuid(active.testExecutionId)},
      'text',
      ${sqlUuid(active.scenarioId)},
      '+15555550000',
      'completed',
      now() - interval '90 seconds',
      now() - interval '60 seconds',
      30,
      ${sqlJson({ source: "api-journey", workspace: "active" })},
      '{}'::jsonb,
      '{}'::jsonb,
      false,
      false,
      8.0
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.callExecutionId)},
      ${sqlUuid(hidden.testExecutionId)},
      'text',
      ${sqlUuid(hidden.scenarioId)},
      '+15555550001',
      'completed',
      now() - interval '90 seconds',
      now() - interval '60 seconds',
      30,
      ${sqlJson({ source: "api-journey", workspace: "hidden" })},
      '{}'::jsonb,
      '{}'::jsonb,
      false,
      false,
      8.0
    )
  RETURNING id
),
inserted_eval_templates AS (
  INSERT INTO model_hub_evaltemplate (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    description,
    organization_id,
    workspace_id,
    owner,
    eval_tags,
    config,
    eval_id,
    criteria,
    choices,
    multi_choice,
    visible_ui,
    proxy_agi,
    template_type,
    eval_type,
    allow_edit,
    allow_copy,
    output_type_normalized,
    pass_threshold,
    choice_scores,
    error_localizer_enabled,
    aggregation_enabled,
    aggregation_function,
    composite_child_axis
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.evalTemplateId)},
      ${sqlString(`${namePrefix.replaceAll(" ", "_")}_active_eval`)},
      'Scores whether the answer is helpful.',
      ${sqlUuid(organizationId)},
      ${sqlUuid(workspaceId)},
      'user',
      ARRAY['api-journey']::varchar[],
      ${sqlJson({ required_keys: ["answer"] })},
      0,
      '',
      '[]'::jsonb,
      false,
      true,
      true,
      'single',
      'code',
      true,
      true,
      'percentage',
      0.5,
      NULL,
      false,
      true,
      'weighted_avg',
      ''
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.evalTemplateId)},
      ${sqlString(`${namePrefix.replaceAll(" ", "_")}_hidden_eval`)},
      'Scores whether the answer is helpful.',
      ${sqlUuid(organizationId)},
      ${sqlUuid(hiddenWorkspaceId)},
      'user',
      ARRAY['api-journey']::varchar[],
      ${sqlJson({ required_keys: ["answer"] })},
      0,
      '',
      '[]'::jsonb,
      false,
      true,
      true,
      'single',
      'code',
      true,
      true,
      'percentage',
      0.5,
      NULL,
      false,
      true,
      'weighted_avg',
      ''
    )
  RETURNING id
),
inserted_eval_configs AS (
  INSERT INTO simulate_eval_config (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    eval_template_id,
    name,
    config,
    mapping,
    run_test_id,
    filters,
    error_localizer,
    status
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.evalConfigId)},
      ${sqlUuid(active.evalTemplateId)},
      ${sqlString(`${namePrefix} active quality`)},
      '{}'::jsonb,
      ${sqlJson({ answer: "output" })},
      ${sqlUuid(active.runTestId)},
      '{}'::jsonb,
      false,
      'Completed'
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.evalConfigId)},
      ${sqlUuid(hidden.evalTemplateId)},
      ${sqlString(`${namePrefix} hidden quality`)},
      '{}'::jsonb,
      ${sqlJson({ answer: "output" })},
      ${sqlUuid(hidden.runTestId)},
      '{}'::jsonb,
      false,
      'Completed'
    )
  RETURNING id
),
inserted_trials AS (
  INSERT INTO prompt_trial (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    agent_prompt_optimiser_run_id,
    trial_number,
    is_baseline,
    prompt,
    average_score,
    metadata
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.baselineTrialId)},
      ${sqlUuid(active.promptRunId)},
      0,
      true,
      'Base support prompt',
      0.4,
      ${sqlJson({ kind: "baseline" })}
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.trialId)},
      ${sqlUuid(active.promptRunId)},
      1,
      false,
      'Improved support prompt',
      0.8,
      ${sqlJson({ kind: "candidate" })}
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.baselineTrialId)},
      ${sqlUuid(hidden.promptRunId)},
      0,
      true,
      'Hidden base support prompt',
      0.4,
      ${sqlJson({ kind: "baseline" })}
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.trialId)},
      ${sqlUuid(hidden.promptRunId)},
      1,
      false,
      'Hidden improved support prompt',
      0.8,
      ${sqlJson({ kind: "candidate" })}
    )
  RETURNING id
),
inserted_trial_items AS (
  INSERT INTO trial_item_result (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    prompt_trial_id,
    call_execution_id,
    score,
    reason,
    input_text,
    output_text,
    metadata
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.baselineItemId)},
      ${sqlUuid(active.baselineTrialId)},
      ${sqlUuid(active.callExecutionId)},
      0.4,
      'Baseline answer was incomplete.',
      'Can I get a refund?',
      'Maybe.',
      ${sqlJson({ trial: 0 })}
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.trialItemId)},
      ${sqlUuid(active.trialId)},
      ${sqlUuid(active.callExecutionId)},
      0.8,
      'Candidate answer included next steps.',
      'Can I get a refund?',
      'I can help start the refund.',
      ${sqlJson({ trial: 1 })}
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.baselineItemId)},
      ${sqlUuid(hidden.baselineTrialId)},
      ${sqlUuid(hidden.callExecutionId)},
      0.4,
      'Hidden baseline answer was incomplete.',
      'Can I get a refund?',
      'Maybe.',
      ${sqlJson({ trial: 0 })}
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.trialItemId)},
      ${sqlUuid(hidden.trialId)},
      ${sqlUuid(hidden.callExecutionId)},
      0.8,
      'Hidden candidate answer included next steps.',
      'Can I get a refund?',
      'I can help start the refund.',
      ${sqlJson({ trial: 1 })}
    )
  RETURNING id
),
inserted_component_evaluations AS (
  INSERT INTO component_evaluation (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    trial_item_result_id,
    eval_config_id,
    score,
    reason
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.componentEvalIds[0])},
      ${sqlUuid(active.baselineItemId)},
      ${sqlUuid(active.evalConfigId)},
      0.4,
      'Sparse answer.'
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(active.componentEvalIds[1])},
      ${sqlUuid(active.trialItemId)},
      ${sqlUuid(active.evalConfigId)},
      0.8,
      'Helpful answer.'
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.componentEvalIds[0])},
      ${sqlUuid(hidden.baselineItemId)},
      ${sqlUuid(hidden.evalConfigId)},
      0.4,
      'Hidden sparse answer.'
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(hidden.componentEvalIds[1])},
      ${sqlUuid(hidden.trialItemId)},
      ${sqlUuid(hidden.evalConfigId)},
      0.8,
      'Hidden helpful answer.'
    )
  RETURNING id
)
SELECT json_build_object(
  'active_run_id', ${sqlString(active.promptRunId)},
  'hidden_run_id', ${sqlString(hidden.promptRunId)},
  'active_test_execution_id', ${sqlString(active.testExecutionId)},
  'hidden_test_execution_id', ${sqlString(hidden.testExecutionId)},
  'active_trial_id', ${sqlString(active.trialId)},
  'hidden_trial_id', ${sqlString(hidden.trialId)},
  'active_eval_config_id', ${sqlString(active.evalConfigId)},
  'hidden_eval_config_id', ${sqlString(hidden.evalConfigId)},
  'active_trial_item_id', ${sqlString(active.trialItemId)},
  'hidden_workspace_id', ${sqlString(hiddenWorkspaceId)},
  'inserted_hidden_workspace_count', (SELECT count(*) FROM inserted_hidden_workspace),
  'inserted_run_test_count', (SELECT count(*) FROM inserted_run_tests),
  'inserted_test_execution_count', (SELECT count(*) FROM inserted_test_executions),
  'inserted_prompt_run_count', (SELECT count(*) FROM inserted_prompt_runs),
  'inserted_trial_count', (SELECT count(*) FROM inserted_trials),
  'inserted_trial_item_count', (SELECT count(*) FROM inserted_trial_items),
  'inserted_component_evaluation_count', (SELECT count(*) FROM inserted_component_evaluations)
);
`;
  const result = await runPostgresJson(sql);
  assert(
    Number(result.inserted_hidden_workspace_count) === 1 &&
      Number(result.inserted_run_test_count) === 2 &&
      Number(result.inserted_test_execution_count) === 2 &&
      Number(result.inserted_prompt_run_count) === 2 &&
      Number(result.inserted_trial_count) === 4 &&
      Number(result.inserted_trial_item_count) === 4 &&
      Number(result.inserted_component_evaluation_count) === 4,
    `Failed to seed agent prompt optimiser fixture: ${JSON.stringify(result)}`,
  );
  return result;
}

async function loadAgentPromptOptimiserDbAudit({
  namePrefix,
  organizationId,
  activeRunId,
  hiddenRunId,
}) {
  const sql = `
WITH target_runs AS (
  SELECT id
  FROM simulate_run_test
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
target_executions AS (
  SELECT id
  FROM simulate_test_execution
  WHERE run_test_id IN (SELECT id FROM target_runs)
),
target_prompt_runs AS (
  SELECT apr.*
  FROM agent_prompt_optimiser_run apr
  WHERE apr.test_execution_id IN (SELECT id FROM target_executions)
     OR apr.id IN (${sqlUuid(activeRunId)}, ${sqlUuid(hiddenRunId)})
),
hidden_prompt_runs AS (
  SELECT apr.*
  FROM target_prompt_runs apr
  JOIN simulate_test_execution te ON te.id = apr.test_execution_id
  JOIN simulate_run_test rt ON rt.id = te.run_test_id
  JOIN accounts_workspace workspace ON workspace.id = rt.workspace_id
  WHERE workspace.name = ${sqlString(`${namePrefix} hidden workspace`)}
)
SELECT json_build_object(
  'run_test_count', (SELECT count(*) FROM target_runs),
  'test_execution_count', (SELECT count(*) FROM target_executions),
  'prompt_run_count', (SELECT count(*) FROM target_prompt_runs),
  'hidden_prompt_run_count', (SELECT count(*) FROM hidden_prompt_runs),
  'active_run_workspace_id', (
    SELECT rt.workspace_id::text
    FROM target_prompt_runs apr
    JOIN simulate_test_execution te ON te.id = apr.test_execution_id
    JOIN simulate_run_test rt ON rt.id = te.run_test_id
    WHERE apr.id = ${sqlUuid(activeRunId)}
  ),
  'hidden_run_workspace_name', (
    SELECT workspace.name
    FROM target_prompt_runs apr
    JOIN simulate_test_execution te ON te.id = apr.test_execution_id
    JOIN simulate_run_test rt ON rt.id = te.run_test_id
    JOIN accounts_workspace workspace ON workspace.id = rt.workspace_id
    WHERE apr.id = ${sqlUuid(hiddenRunId)}
  )
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteAgentPromptOptimiserFixturesDb({
  namePrefix,
  organizationId,
}) {
  const sql = `
WITH target_runs AS (
  SELECT id
  FROM simulate_run_test
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
target_executions AS (
  SELECT id, agent_optimiser_id
  FROM simulate_test_execution
  WHERE run_test_id IN (SELECT id FROM target_runs)
),
target_prompt_runs AS (
  SELECT id, agent_optimiser_id, agent_optimiser_run_id
  FROM agent_prompt_optimiser_run
  WHERE test_execution_id IN (SELECT id FROM target_executions)
     OR name LIKE ${sqlString(`${namePrefix}%`)}
),
target_trials AS (
  SELECT id
  FROM prompt_trial
  WHERE agent_prompt_optimiser_run_id IN (SELECT id FROM target_prompt_runs)
),
target_trial_items AS (
  SELECT id
  FROM trial_item_result
  WHERE prompt_trial_id IN (SELECT id FROM target_trials)
),
target_calls AS (
  SELECT id
  FROM simulate_call_execution
  WHERE test_execution_id IN (SELECT id FROM target_executions)
),
target_eval_configs AS (
  SELECT id, eval_template_id
  FROM simulate_eval_config
  WHERE run_test_id IN (SELECT id FROM target_runs)
),
target_eval_templates AS (
  SELECT id
  FROM model_hub_evaltemplate
  WHERE id IN (SELECT eval_template_id FROM target_eval_configs)
     OR name LIKE ${sqlString(`${namePrefix.replaceAll(" ", "_")}%`)}
),
target_optimisers AS (
  SELECT DISTINCT id
  FROM (
    SELECT agent_optimiser_id AS id
    FROM target_executions
    WHERE agent_optimiser_id IS NOT NULL
    UNION
    SELECT agent_optimiser_id AS id
    FROM target_prompt_runs
    WHERE agent_optimiser_id IS NOT NULL
  ) optimiser_ids
),
target_optimiser_runs AS (
  SELECT id
  FROM agent_optimiser_run
  WHERE agent_optimiser_id IN (SELECT id FROM target_optimisers)
     OR id IN (SELECT agent_optimiser_run_id FROM target_prompt_runs)
),
target_scenarios AS (
  SELECT id
  FROM simulate_scenarios
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
target_hidden_workspaces AS (
  SELECT id
  FROM accounts_workspace
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name = ${sqlString(`${namePrefix} hidden workspace`)}
),
deleted_component_evaluations AS (
  DELETE FROM component_evaluation
  WHERE trial_item_result_id IN (SELECT id FROM target_trial_items)
     OR eval_config_id IN (SELECT id FROM target_eval_configs)
  RETURNING id
),
deleted_trial_items AS (
  DELETE FROM trial_item_result
  WHERE id IN (SELECT id FROM target_trial_items)
     OR call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_trials AS (
  DELETE FROM prompt_trial
  WHERE id IN (SELECT id FROM target_trials)
  RETURNING id
),
deleted_steps AS (
  DELETE FROM agent_prompt_optimiser_run_step
  WHERE agent_prompt_optimiser_run_id IN (SELECT id FROM target_prompt_runs)
  RETURNING id
),
deleted_prompt_runs AS (
  DELETE FROM agent_prompt_optimiser_run
  WHERE id IN (SELECT id FROM target_prompt_runs)
  RETURNING id
),
deleted_calls AS (
  DELETE FROM simulate_call_execution
  WHERE id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_eval_configs AS (
  DELETE FROM simulate_eval_config
  WHERE id IN (SELECT id FROM target_eval_configs)
  RETURNING id
),
deleted_test_executions AS (
  DELETE FROM simulate_test_execution
  WHERE id IN (SELECT id FROM target_executions)
  RETURNING id
),
deleted_run_scenarios AS (
  DELETE FROM simulate_run_test_scenarios
  WHERE runtest_id IN (SELECT id FROM target_runs)
     OR scenarios_id IN (SELECT id FROM target_scenarios)
  RETURNING runtest_id
),
deleted_run_tests AS (
  DELETE FROM simulate_run_test
  WHERE id IN (SELECT id FROM target_runs)
  RETURNING id
),
deleted_scenarios AS (
  DELETE FROM simulate_scenarios
  WHERE id IN (SELECT id FROM target_scenarios)
  RETURNING id
),
deleted_eval_templates AS (
  DELETE FROM model_hub_evaltemplate
  WHERE id IN (SELECT id FROM target_eval_templates)
  RETURNING id
),
deleted_optimiser_runs AS (
  DELETE FROM agent_optimiser_run
  WHERE id IN (SELECT id FROM target_optimiser_runs)
  RETURNING id
),
deleted_optimisers AS (
  DELETE FROM agent_optimiser
  WHERE id IN (SELECT id FROM target_optimisers)
  RETURNING id
),
deleted_hidden_workspaces AS (
  DELETE FROM accounts_workspace
  WHERE id IN (SELECT id FROM target_hidden_workspaces)
  RETURNING id
)
SELECT json_build_object(
  'deleted_component_evaluation_count', (SELECT count(*) FROM deleted_component_evaluations),
  'deleted_trial_item_count', (SELECT count(*) FROM deleted_trial_items),
  'deleted_trial_count', (SELECT count(*) FROM deleted_trials),
  'deleted_step_count', (SELECT count(*) FROM deleted_steps),
  'deleted_prompt_run_count', (SELECT count(*) FROM deleted_prompt_runs),
  'deleted_call_execution_count', (SELECT count(*) FROM deleted_calls),
  'deleted_eval_config_count', (SELECT count(*) FROM deleted_eval_configs),
  'deleted_test_execution_count', (SELECT count(*) FROM deleted_test_executions),
  'deleted_run_test_count', (SELECT count(*) FROM deleted_run_tests),
  'deleted_scenario_count', (SELECT count(*) FROM deleted_scenarios),
  'deleted_eval_template_count', (SELECT count(*) FROM deleted_eval_templates),
  'deleted_optimiser_run_count', (SELECT count(*) FROM deleted_optimiser_runs),
  'deleted_optimiser_count', (SELECT count(*) FROM deleted_optimisers),
  'deleted_hidden_workspace_count', (SELECT count(*) FROM deleted_hidden_workspaces)
);
`;
  const cleanup = await runPostgresJson(sql);
  const remainingSql = `
WITH target_runs AS (
  SELECT id
  FROM simulate_run_test
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
target_executions AS (
  SELECT id
  FROM simulate_test_execution
  WHERE run_test_id IN (SELECT id FROM target_runs)
),
target_prompt_runs AS (
  SELECT id
  FROM agent_prompt_optimiser_run
  WHERE test_execution_id IN (SELECT id FROM target_executions)
     OR name LIKE ${sqlString(`${namePrefix}%`)}
),
target_hidden_workspaces AS (
  SELECT id
  FROM accounts_workspace
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name = ${sqlString(`${namePrefix} hidden workspace`)}
)
SELECT json_build_object(
  'remaining_run_test_count', (SELECT count(*) FROM target_runs),
  'remaining_test_execution_count', (SELECT count(*) FROM target_executions),
  'remaining_prompt_run_count', (SELECT count(*) FROM target_prompt_runs),
  'remaining_hidden_workspace_count', (SELECT count(*) FROM target_hidden_workspaces)
);
`;
  return {
    ...cleanup,
    ...(await runPostgresJson(remainingSql)),
  };
}

async function loadSimulationScenarioDbAudit({
  scenarioIds,
  datasetId,
  organizationId,
}) {
  const sql = `
WITH selected_ids AS (
  SELECT unnest(${sqlUuidArray(scenarioIds)}) AS id
),
selected_scenarios AS (
  SELECT s.*
  FROM simulate_scenarios s
  JOIN selected_ids ids ON ids.id = s.id
  WHERE s.organization_id = ${sqlUuid(organizationId)}
),
selected_graphs AS (
  SELECT g.*
  FROM simulate_scenario_graph g
  JOIN selected_scenarios s ON s.id = g.scenario_id
),
selected_dataset AS (
  SELECT d.*
  FROM model_hub_dataset d
  WHERE d.id = ${sqlUuid(datasetId)}
    AND d.organization_id = ${sqlUuid(organizationId)}
),
selected_columns AS (
  SELECT c.*
  FROM model_hub_column c
  JOIN selected_dataset d ON d.id = c.dataset_id
  WHERE c.deleted = false
),
selected_rows AS (
  SELECT r.*
  FROM model_hub_row r
  JOIN selected_dataset d ON d.id = r.dataset_id
  WHERE r.deleted = false
),
selected_cells AS (
  SELECT c.*
  FROM model_hub_cell c
  JOIN selected_dataset d ON d.id = c.dataset_id
  WHERE c.deleted = false
)
SELECT json_build_object(
  'scenarios', (
    SELECT COALESCE(json_agg(json_build_object(
      'id', id::text,
      'name', name,
      'scenario_type', scenario_type,
      'source_type', source_type,
      'organization_id', organization_id::text,
      'workspace_id', workspace_id::text,
      'dataset_id', dataset_id::text,
      'agent_definition_id', agent_definition_id::text,
      'simulator_agent_id', simulator_agent_id::text,
      'status', status,
      'deleted', deleted,
      'deleted_at_set', deleted_at IS NOT NULL,
      'metadata', metadata
    ) ORDER BY created_at), '[]'::json)
    FROM selected_scenarios
  ),
  'scenario_count', (SELECT count(*) FROM selected_scenarios),
  'active_scenario_count', (
    SELECT count(*) FROM selected_scenarios WHERE deleted = false
  ),
  'deleted_scenario_count', (
    SELECT count(*) FROM selected_scenarios WHERE deleted = true AND deleted_at IS NOT NULL
  ),
  'graph_count', (SELECT count(*) FROM selected_graphs),
  'active_graph_count', (
    SELECT count(*) FROM selected_graphs WHERE deleted = false
  ),
  'deleted_graph_count', (
    SELECT count(*) FROM selected_graphs WHERE deleted = true AND deleted_at IS NOT NULL
  ),
  'dataset_row_count', (SELECT count(*) FROM selected_rows),
  'dataset_column_count', (SELECT count(*) FROM selected_columns),
  'dataset_cell_count', (SELECT count(*) FROM selected_cells)
);
`;
  return runPostgresJson(sql);
}

async function deleteSimulationScenarioFixturesDb({
  namePrefix,
  organizationId,
}) {
  const sql = `
WITH target_scenarios AS (
  SELECT id, dataset_id, simulator_agent_id, agent_definition_id
  FROM simulate_scenarios
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
target_datasets AS (
  SELECT DISTINCT dataset_id AS id
  FROM target_scenarios
  WHERE dataset_id IS NOT NULL
),
target_simulator_agents AS (
  SELECT DISTINCT simulator_agent_id AS id
  FROM target_scenarios
  WHERE simulator_agent_id IS NOT NULL
),
target_agents AS (
  SELECT DISTINCT agent_definition_id AS id
  FROM target_scenarios
  WHERE agent_definition_id IS NOT NULL
),
target_graphs AS (
  SELECT id
  FROM simulate_scenario_graph
  WHERE scenario_id IN (SELECT id FROM target_scenarios)
),
target_cells AS (
  SELECT id
  FROM model_hub_cell
  WHERE dataset_id IN (SELECT id FROM target_datasets)
),
target_rows AS (
  SELECT id
  FROM model_hub_row
  WHERE dataset_id IN (SELECT id FROM target_datasets)
),
target_columns AS (
  SELECT id
  FROM model_hub_column
  WHERE dataset_id IN (SELECT id FROM target_datasets)
),
deleted_cells AS (
  DELETE FROM model_hub_cell c
  USING target_cells target
  WHERE c.id = target.id
  RETURNING c.id
),
deleted_rows AS (
  DELETE FROM model_hub_row r
  USING target_rows target
  WHERE r.id = target.id
  RETURNING r.id
),
deleted_columns AS (
  DELETE FROM model_hub_column c
  USING target_columns target
  WHERE c.id = target.id
  RETURNING c.id
),
deleted_graphs AS (
  DELETE FROM simulate_scenario_graph g
  USING target_graphs target
  WHERE g.id = target.id
  RETURNING g.id
),
deleted_scenarios AS (
  DELETE FROM simulate_scenarios s
  USING target_scenarios target
  WHERE s.id = target.id
  RETURNING s.id
),
deleted_simulator_agents AS (
  DELETE FROM simulator_agents a
  USING target_simulator_agents target
  WHERE a.id = target.id
  RETURNING a.id
),
deleted_agents AS (
  DELETE FROM simulate_agent_definition a
  USING target_agents target
  WHERE a.id = target.id
  RETURNING a.id
),
deleted_datasets AS (
  DELETE FROM model_hub_dataset d
  USING target_datasets target
  WHERE d.id = target.id
  RETURNING d.id
)
SELECT json_build_object(
  'deleted_scenario_count', (SELECT count(*) FROM deleted_scenarios),
  'deleted_graph_count', (SELECT count(*) FROM deleted_graphs),
  'deleted_dataset_count', (SELECT count(*) FROM deleted_datasets),
  'deleted_column_count', (SELECT count(*) FROM deleted_columns),
  'deleted_row_count', (SELECT count(*) FROM deleted_rows),
  'deleted_cell_count', (SELECT count(*) FROM deleted_cells),
  'deleted_agent_count', (SELECT count(*) FROM deleted_agents),
  'deleted_simulator_agent_count', (SELECT count(*) FROM deleted_simulator_agents),
  'remaining_scenario_count',
    (SELECT count(*) FROM target_scenarios) - (SELECT count(*) FROM deleted_scenarios),
  'remaining_graph_count',
    (SELECT count(*) FROM target_graphs) - (SELECT count(*) FROM deleted_graphs),
  'remaining_dataset_count',
    (SELECT count(*) FROM target_datasets) - (SELECT count(*) FROM deleted_datasets),
  'remaining_agent_count',
    (SELECT count(*) FROM target_agents) - (SELECT count(*) FROM deleted_agents),
  'remaining_simulator_agent_count',
    (SELECT count(*) FROM target_simulator_agents) - (SELECT count(*) FROM deleted_simulator_agents)
);
`;
  return runPostgresJson(sql);
}

async function loadSimulationPersonaDbAudit({ personaIds, organizationId }) {
  const sql = `
WITH selected_ids AS (
  SELECT unnest(${sqlUuidArray(personaIds)}) AS id
),
selected_personas AS (
  SELECT p.*
  FROM simulate_personas p
  JOIN selected_ids ids ON ids.id = p.id
  WHERE p.organization_id = ${sqlUuid(organizationId)}
)
SELECT json_build_object(
  'personas', (
    SELECT COALESCE(json_agg(json_build_object(
      'id', id::text,
      'name', name,
      'persona_type', persona_type,
      'organization_id', organization_id::text,
      'workspace_id', workspace_id::text,
      'deleted', deleted,
      'deleted_at_set', deleted_at IS NOT NULL,
      'occupation', occupation,
      'languages', languages,
      'metadata', metadata,
      'simulation_type', simulation_type
    ) ORDER BY created_at), '[]'::json)
    FROM selected_personas
  ),
  'selected_count', (SELECT count(*) FROM selected_personas),
  'active_count', (
    SELECT count(*) FROM selected_personas WHERE deleted = false
  ),
  'deleted_at_count', (
    SELECT count(*) FROM selected_personas WHERE deleted = true AND deleted_at IS NOT NULL
  )
);
`;
  return runPostgresJson(sql);
}

async function insertOtherWorkspaceSimulationPersonaFixtureDb({
  namePrefix,
  organizationId,
  userId,
}) {
  const workspaceId = randomUUID();
  const personaId = randomUUID();
  const workspaceName = `${namePrefix} other workspace`;
  const personaName = `${namePrefix} other workspace source`;
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
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(workspaceId)},
    ${sqlString(workspaceName)},
    ${sqlString(workspaceName)},
    ${sqlString("Temporary workspace for API journey persona scoping.")},
    true,
    false,
    ${sqlUuid(userId)},
    ${sqlUuid(organizationId)}
  )
  RETURNING id, name
),
inserted_persona AS (
  INSERT INTO simulate_personas (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    persona_type,
    persona_id,
    name,
    description,
    gender,
    age_group,
    occupation,
    location,
    personality,
    communication_style,
    multilingual,
    languages,
    accent,
    conversation_speed,
    background_sound,
    finished_speaking_sensitivity,
    interrupt_sensitivity,
    keywords,
    metadata,
    additional_instruction,
    is_default,
    organization_id,
    workspace_id,
    simulation_type,
    punctuation,
    slang_usage,
    typos_frequency,
    regional_mix,
    emoji_usage,
    tone,
    verbosity
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(personaId)},
    'workspace',
    0,
    ${sqlString(personaName)},
    ${sqlString("Cross-workspace persona source for API journey regression.")},
    ${sqlJson(["male"])},
    ${sqlJson(["25-32"])},
    ${sqlJson(["Engineer"])},
    ${sqlJson(["United States"])},
    ${sqlJson(["Friendly and cooperative"])},
    ${sqlJson(["Direct and concise"])},
    false,
    ${sqlJson(["English"])},
    ${sqlJson(["american"])},
    ${sqlJson(["1.0"])},
    false,
    ${sqlJson(["5"])},
    ${sqlJson(["5"])},
    ${sqlJson(["api-journey"])},
    ${sqlJson({ source: "api-journey-other-workspace" })},
    ${sqlString("Should not be duplicable from another workspace.")},
    false,
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    'voice',
    'clean',
    'light',
    'rare',
    'light',
    'light',
    'casual',
    'balanced'
  )
  RETURNING id, name, workspace_id
)
SELECT json_build_object(
  'workspace_id', (SELECT id::text FROM inserted_workspace),
  'workspace_name', (SELECT name FROM inserted_workspace),
  'persona_id', (SELECT id::text FROM inserted_persona),
  'persona_name', (SELECT name FROM inserted_persona)
);
`;
  return runPostgresJson(sql);
}

async function deleteSimulationPersonaFixturesDb({
  namePrefix,
  organizationId,
}) {
  const sql = `
WITH target_personas AS (
  SELECT id
  FROM simulate_personas
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
deleted_personas AS (
  DELETE FROM simulate_personas p
  USING target_personas target
  WHERE p.id = target.id
  RETURNING p.id
),
target_workspaces AS (
  SELECT id
  FROM accounts_workspace
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix} other workspace%`)}
),
deleted_workspaces AS (
  DELETE FROM accounts_workspace w
  USING target_workspaces target
  WHERE w.id = target.id
  RETURNING w.id
)
SELECT json_build_object(
  'deleted_persona_count', (SELECT count(*) FROM deleted_personas),
  'remaining_persona_count', (
    SELECT count(*)
    FROM target_personas
    WHERE id NOT IN (SELECT id FROM deleted_personas)
  ),
  'deleted_workspace_count', (SELECT count(*) FROM deleted_workspaces),
  'remaining_workspace_count', (
    SELECT count(*)
    FROM target_workspaces
    WHERE id NOT IN (SELECT id FROM deleted_workspaces)
  )
);
`;
  return runPostgresJson(sql);
}

async function seedOtherWorkspaceSimulatorAgentFixtureDb({
  namePrefix,
  organizationId,
  userId,
}) {
  const workspaceId = randomUUID();
  const simulatorAgentId = randomUUID();
  const workspaceName = `${namePrefix} other workspace`;
  const simulatorAgentName = `${namePrefix} other workspace source`;
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
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(workspaceId)},
    ${sqlString(workspaceName)},
    ${sqlString(workspaceName)},
    ${sqlString("Temporary workspace for simulator-agent API journey scoping.")},
    true,
    false,
    ${sqlUuid(userId)},
    ${sqlUuid(organizationId)}
  )
  RETURNING id, name
),
inserted_simulator_agent AS (
  INSERT INTO simulator_agents (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    prompt,
    voice_provider,
    voice_name,
    interrupt_sensitivity,
    conversation_speed,
    finished_speaking_sensitivity,
    model,
    llm_temperature,
    max_call_duration_in_minutes,
    initial_message_delay,
    initial_message,
    organization_id,
    workspace_id
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(simulatorAgentId)},
    ${sqlString(simulatorAgentName)},
    ${sqlString("Hidden simulator prompt for workspace-scope API journey.")},
    'elevenlabs',
    'marissa',
    0.4,
    1.0,
    0.6,
    'gpt-4o-mini',
    0.2,
    10,
    0,
    ${sqlString("Hidden hello.")},
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)}
  )
  RETURNING id, name, workspace_id
)
SELECT json_build_object(
  'workspace_id', (SELECT id::text FROM inserted_workspace),
  'workspace_name', (SELECT name FROM inserted_workspace),
  'simulator_agent_id', (SELECT id::text FROM inserted_simulator_agent),
  'simulator_agent_name', (SELECT name FROM inserted_simulator_agent)
);
`;
  return runPostgresJson(sql);
}

async function loadSimulatorAgentDbAudit({ agentIds, organizationId }) {
  const sql = `
WITH selected_ids AS (
  SELECT unnest(${sqlUuidArray(agentIds)}) AS id
),
selected_agents AS (
  SELECT a.*
  FROM simulator_agents a
  JOIN selected_ids ids ON ids.id = a.id
  WHERE a.organization_id = ${sqlUuid(organizationId)}
)
SELECT json_build_object(
  'agents', (
    SELECT COALESCE(json_agg(json_build_object(
      'id', id::text,
      'name', name,
      'prompt', prompt,
      'voice_provider', voice_provider,
      'voice_name', voice_name,
      'model', model,
      'organization_id', organization_id::text,
      'workspace_id', workspace_id::text,
      'deleted', deleted,
      'deleted_at_set', deleted_at IS NOT NULL,
      'llm_temperature', llm_temperature,
      'initial_message', initial_message
    ) ORDER BY created_at), '[]'::json)
    FROM selected_agents
  ),
  'selected_count', (SELECT count(*) FROM selected_agents),
  'active_count', (
    SELECT count(*) FROM selected_agents WHERE deleted = false
  ),
  'deleted_at_count', (
    SELECT count(*) FROM selected_agents WHERE deleted = true AND deleted_at IS NOT NULL
  )
);
`;
  return runPostgresJson(sql);
}

async function deleteSimulatorAgentFixturesDb({ namePrefix, organizationId }) {
  const sql = `
WITH target_agents AS (
  SELECT id
  FROM simulator_agents
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
deleted_agents AS (
  DELETE FROM simulator_agents a
  USING target_agents target
  WHERE a.id = target.id
  RETURNING a.id
),
target_workspaces AS (
  SELECT id
  FROM accounts_workspace
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix} other workspace%`)}
),
deleted_workspaces AS (
  DELETE FROM accounts_workspace w
  USING target_workspaces target
  WHERE w.id = target.id
  RETURNING w.id
)
SELECT json_build_object(
  'deleted_simulator_agent_count', (SELECT count(*) FROM deleted_agents),
  'remaining_simulator_agent_count', (
    SELECT count(*)
    FROM target_agents
    WHERE id NOT IN (SELECT id FROM deleted_agents)
  ),
  'deleted_workspace_count', (SELECT count(*) FROM deleted_workspaces),
  'remaining_workspace_count', (
    SELECT count(*)
    FROM target_workspaces
    WHERE id NOT IN (SELECT id FROM deleted_workspaces)
  )
);
`;
  return runPostgresJson(sql);
}

async function loadSimulationAgentDefinitionDbAudit({
  agentIds,
  organizationId,
  rawApiKey,
  maskedApiKey,
}) {
  const sql = `
WITH selected_ids AS (
  SELECT unnest(${sqlUuidArray(agentIds)}) AS id
),
selected_agents AS (
  SELECT a.*
  FROM simulate_agent_definition a
  JOIN selected_ids ids ON ids.id = a.id
  WHERE a.organization_id = ${sqlUuid(organizationId)}
),
selected_versions AS (
  SELECT v.*
  FROM simulate_agent_version v
  JOIN selected_agents a ON a.id = v.agent_definition_id
),
selected_credentials AS (
  SELECT c.*
  FROM simulate_provider_credentials c
  JOIN selected_agents a ON a.id = c.agent_definition_id
)
SELECT json_build_object(
  'agents', (
    SELECT COALESCE(json_agg(json_build_object(
      'id', id::text,
      'agent_name', agent_name,
      'agent_type', agent_type,
      'organization_id', organization_id::text,
      'workspace_id', workspace_id::text,
      'deleted', deleted,
      'deleted_at_set', deleted_at IS NOT NULL,
      'api_key_raw_present', api_key = ${sqlString(rawApiKey)},
      'api_key_is_masked_value', api_key = ${sqlString(maskedApiKey)},
      'model_details', model_details
    ) ORDER BY created_at), '[]'::json)
    FROM selected_agents
  ),
  'versions', (
    SELECT COALESCE(json_agg(json_build_object(
      'id', id::text,
      'agent_definition_id', agent_definition_id::text,
      'version_number', version_number,
      'status', status,
      'deleted', deleted,
      'deleted_at_set', deleted_at IS NOT NULL,
      'snapshot_agent_name', configuration_snapshot->>'agent_name',
      'snapshot_api_key_raw_present', configuration_snapshot->>'api_key' = ${sqlString(rawApiKey)},
      'snapshot_api_key_is_masked_value', configuration_snapshot->>'api_key' = ${sqlString(maskedApiKey)}
    ) ORDER BY version_number), '[]'::json)
    FROM selected_versions
  ),
  'credentials', (
    SELECT COALESCE(json_agg(json_build_object(
      'id', id::text,
      'agent_definition_id', agent_definition_id::text,
      'provider_type', provider_type,
      'api_key_encrypted', api_key LIKE 'enc::%',
      'raw_key_present_in_ciphertext', position(${sqlString(rawApiKey)} in api_key) > 0,
      'assistant_id', assistant_id
    ) ORDER BY created_at), '[]'::json)
    FROM selected_credentials
  ),
  'agent_count', (SELECT count(*) FROM selected_agents),
  'deleted_agent_count', (
    SELECT count(*) FROM selected_agents WHERE deleted = true AND deleted_at IS NOT NULL
  ),
  'version_count', (SELECT count(*) FROM selected_versions),
  'active_version_count', (
    SELECT count(*) FROM selected_versions WHERE deleted = false AND status = 'active'
  ),
  'archived_version_count', (
    SELECT count(*) FROM selected_versions WHERE deleted = false AND status = 'archived'
  ),
  'deleted_version_count', (
    SELECT count(*) FROM selected_versions WHERE deleted = true AND deleted_at IS NOT NULL
  ),
  'credential_count', (SELECT count(*) FROM selected_credentials),
  'raw_key_present_in_credential_ciphertext', COALESCE((
    SELECT bool_or(position(${sqlString(rawApiKey)} in api_key) > 0)
    FROM selected_credentials
  ), false)
);
`;
  return runPostgresJson(sql);
}

async function deleteSimulationAgentDefinitionFixturesDb({
  namePrefix,
  organizationId,
}) {
  const sql = `
WITH target_agents AS (
  SELECT id
  FROM simulate_agent_definition
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND agent_name LIKE ${sqlString(`${namePrefix}%`)}
),
target_versions AS (
  SELECT id
  FROM simulate_agent_version
  WHERE agent_definition_id IN (SELECT id FROM target_agents)
),
target_credentials AS (
  SELECT id
  FROM simulate_provider_credentials
  WHERE agent_definition_id IN (SELECT id FROM target_agents)
),
deleted_credentials AS (
  DELETE FROM simulate_provider_credentials c
  USING target_credentials target
  WHERE c.id = target.id
  RETURNING c.id
),
deleted_versions AS (
  DELETE FROM simulate_agent_version v
  USING target_versions target
  WHERE v.id = target.id
  RETURNING v.id
),
deleted_agents AS (
  DELETE FROM simulate_agent_definition a
  USING target_agents target
  WHERE a.id = target.id
  RETURNING a.id
)
SELECT json_build_object(
  'deleted_agent_count', (SELECT count(*) FROM deleted_agents),
  'deleted_version_count', (SELECT count(*) FROM deleted_versions),
  'deleted_credential_count', (SELECT count(*) FROM deleted_credentials),
  'remaining_agent_count',
    (SELECT count(*) FROM target_agents) - (SELECT count(*) FROM deleted_agents),
  'remaining_version_count',
    (SELECT count(*) FROM target_versions) - (SELECT count(*) FROM deleted_versions),
  'remaining_credential_count',
    (SELECT count(*) FROM target_credentials) - (SELECT count(*) FROM deleted_credentials)
);
`;
  return runPostgresJson(sql);
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
  const check = asArray(payload?.checks).find(
    (item) => item?.name === checkName,
  );
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

    const activeConfig = await client.get(
      apiPath("/agentcc/org-configs/active/"),
    );
    if (activeConfig?.id !== originalActiveConfigId) {
      await client.post(
        apiPath("/agentcc/org-configs/{id}/activate/", {
          id: originalActiveConfigId,
        }),
        {},
      );
      restoreEvidence.activated_original = true;
    }

    const configs = collectionRows(
      await client.get(apiPath("/agentcc/org-configs/")),
    );
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

async function loadSimulationRunDbAudit(
  runTestId,
  organizationId,
  workspaceId,
) {
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

async function seedSimulationRunActionStateDb({
  namePrefix,
  runTestId,
  testExecutionId,
  callExecutionId,
  organizationId,
  workspaceId,
}) {
  const maxTurnMessages = Array.from({ length: 1200 }, (_, index) => ({
    role: "user",
    content: `seeded max-turn message ${index + 1}`,
  }));
  const sql = `
WITH target_call AS (
  SELECT ce.id, ce.row_id, ce.test_execution_id
  FROM simulate_call_execution ce
  JOIN simulate_test_execution te ON te.id = ce.test_execution_id
  JOIN simulate_run_test rt ON rt.id = te.run_test_id
  WHERE rt.id = ${sqlUuid(runTestId)}
    AND te.id = ${sqlUuid(testExecutionId)}
    AND ce.id = ${sqlUuid(callExecutionId)}
    AND rt.organization_id = ${sqlUuid(organizationId)}
    AND rt.deleted = false
    AND te.deleted = false
    AND ce.deleted = false
),
inserted_project AS (
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
    gen_random_uuid(),
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    'GenerativeLLM',
    ${sqlString(`${namePrefix} trace project`)},
    'observe',
    ${sqlJson({ source: "api-journey", fixture: "simulation-actions" })},
    '[]'::jsonb,
    '[]'::jsonb,
    NULL,
    'simulator',
    '[]'::jsonb
  )
  RETURNING id
),
inserted_trace_session AS (
  INSERT INTO trace_session (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    project_id,
    bookmarked,
    name
  )
  SELECT
    now(),
    now(),
    false,
    NULL,
    gen_random_uuid(),
    p.id,
    false,
    ${sqlString(`${namePrefix} comparison session`)}
  FROM inserted_project p
  RETURNING id, project_id
),
inserted_trace AS (
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
    now() - interval '1 minute',
    now(),
    false,
    NULL,
    gen_random_uuid(),
    s.project_id,
    NULL,
    ${sqlString(`${namePrefix} base trace`)},
    ${sqlJson({ source: "api-journey", fixture: "simulation-actions" })},
    ${sqlJson("baseline user input")},
    ${sqlJson("baseline assistant output")},
    NULL,
    s.id,
    ${sqlString(`${namePrefix}-base-trace`)},
    '[]'::jsonb,
    'completed'
  FROM inserted_trace_session s
  RETURNING id, project_id, session_id
),
inserted_span AS (
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
    now() - interval '1 minute',
    now(),
    false,
    NULL,
    ${sqlString(`${namePrefix}-comparison-span`)},
    t.project_id,
    NULL,
    t.id,
    NULL,
    ${sqlString(`${namePrefix} baseline llm`)},
    'llm',
    'chat',
    now() - interval '1 minute',
    now() - interval '55 seconds',
    ${sqlJson("baseline user input")},
    ${sqlJson("baseline assistant output")},
    'gpt-4o-mini',
    '{}'::jsonb,
    1000,
    ${sqlUuid(organizationId)},
    NULL,
    4,
    8,
    12,
    1.0,
    NULL,
    0,
    'OK',
    NULL,
    '[]'::jsonb,
    ${sqlJson({ source: "api-journey", fixture: "simulation-actions" })},
    '[]'::jsonb,
    'futureagi',
    '[]'::jsonb,
    '[]'::jsonb,
    '{}'::jsonb,
    NULL,
    'inactive',
    NULL,
    NULL,
    NULL,
    '{}'::jsonb,
    '{}'::jsonb,
    'traceai'
  FROM inserted_trace t
  RETURNING id
),
inserted_assistant AS (
  INSERT INTO simulate_chatsimulatorassistant (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    system_prompt,
    model,
    temperature,
    max_tokens,
    organization_id,
    workspace_id
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    gen_random_uuid(),
    ${sqlString(`${namePrefix} chat assistant`)},
    'Temporary chat assistant for successful action coverage.',
    'gpt-4o-mini',
    0.2,
    64,
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)}
  )
  RETURNING id
),
inserted_chat_session AS (
  INSERT INTO simulate_chatsimulatorsession (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    assistant_id,
    call_execution_id,
    messages,
    status,
    has_chat_ended,
    total_tokens,
    organization_id,
    workspace_id
  )
  SELECT
    now(),
    now(),
    false,
    NULL,
    gen_random_uuid(),
    a.id,
    ${sqlUuid(callExecutionId)},
    ${sqlJson(maxTurnMessages)},
    'active',
    false,
    0,
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)}
  FROM inserted_assistant a
  RETURNING id, assistant_id
),
updated_row AS (
  UPDATE model_hub_row r
  SET
    metadata = COALESCE(r.metadata, '{}'::jsonb)
      || jsonb_build_object(
        'session_id',
        (SELECT id::text FROM inserted_trace_session),
        'api_journey_fixture',
        'simulation-actions'
      ),
    updated_at = now()
  FROM target_call target
  WHERE r.id = target.row_id
  RETURNING r.id
),
updated_call AS (
  UPDATE simulate_call_execution ce
  SET
    status = 'ongoing',
    started_at = COALESCE(ce.started_at, now() - interval '30 seconds'),
    assistant_id = (SELECT assistant_id::text FROM inserted_chat_session),
    call_metadata = COALESCE(ce.call_metadata, '{}'::jsonb)
      || jsonb_build_object(
        'chat_session_id',
        (SELECT id::text FROM inserted_chat_session),
        'simulation_assistant_id',
        (SELECT assistant_id::text FROM inserted_chat_session),
        'chat_provider',
        'futureagi',
        'system_prompt',
        'Temporary chat prompt for successful action coverage.'
      ),
    updated_at = now()
  FROM target_call target
  WHERE ce.id = target.id
  RETURNING ce.id
),
updated_execution AS (
  UPDATE simulate_test_execution te
  SET
    status = 'running',
    picked_up_by_executor = true,
    total_calls = 1,
    completed_calls = 0,
    failed_calls = 0,
    updated_at = now()
  WHERE te.id = ${sqlUuid(testExecutionId)}
    AND EXISTS (SELECT 1 FROM target_call)
  RETURNING te.id
)
SELECT json_build_object(
  'project_id', (SELECT id::text FROM inserted_project),
  'trace_session_id', (SELECT id::text FROM inserted_trace_session),
  'trace_id', (SELECT id::text FROM inserted_trace),
  'chat_assistant_id', (SELECT assistant_id::text FROM inserted_chat_session),
  'chat_session_id', (SELECT id::text FROM inserted_chat_session),
  'call_row_id', (SELECT id::text FROM updated_row),
  'inserted_span_count', (SELECT count(*) FROM inserted_span),
  'updated_call_count', (SELECT count(*) FROM updated_call),
  'updated_execution_count', (SELECT count(*) FROM updated_execution)
);
`;
  return runPostgresJson(sql);
}

async function markSimulationRunActionFixtureCompletedDb({
  testExecutionId,
  callExecutionId,
  callExecutionIds,
  organizationId,
  workspaceId,
}) {
  const targetCallExecutionIds = asArray(callExecutionIds).length
    ? asArray(callExecutionIds)
    : [callExecutionId];
  assert(
    targetCallExecutionIds.every(isUuid),
    "Action completion DB seed needs call execution UUIDs.",
  );
  const sql = `
WITH target_call AS (
  SELECT ce.id
  FROM simulate_call_execution ce
  JOIN simulate_test_execution te ON te.id = ce.test_execution_id
  JOIN simulate_run_test rt ON rt.id = te.run_test_id
  WHERE te.id = ${sqlUuid(testExecutionId)}
    AND ce.id = ANY(${sqlUuidArray(targetCallExecutionIds)})
    AND rt.organization_id = ${sqlUuid(organizationId)}
),
updated_call AS (
  UPDATE simulate_call_execution ce
  SET
    status = 'completed',
    started_at = COALESCE(ce.started_at, now() - interval '45 seconds'),
    completed_at = COALESCE(ce.completed_at, now()),
    ended_at = COALESCE(ce.ended_at, now()),
    duration_seconds = COALESCE(ce.duration_seconds, 45),
    response_time_ms = COALESCE(ce.response_time_ms, 1000),
    overall_score = COALESCE(ce.overall_score, 9.0),
    message_count = COALESCE(ce.message_count, 2),
    transcript_available = true,
    conversation_metrics_data = COALESCE(
      ce.conversation_metrics_data,
      ${sqlJson({
        total_tokens: 36,
        input_tokens: 12,
        output_tokens: 24,
        avg_latency_ms: 1000,
        turn_count: 2,
        bot_message_count: 1,
        user_message_count: 1,
        csat_score: 5,
      })}
    ),
    call_summary = COALESCE(
      ce.call_summary,
      'Successful action journey completed chat call.'
    ),
    call_metadata = COALESCE(ce.call_metadata, '{}'::jsonb)
      || jsonb_build_object('eval_started', true, 'eval_completed', true),
    eval_outputs = COALESCE(ce.eval_outputs, '{}'::jsonb),
    updated_at = now()
  FROM target_call target
  WHERE ce.id = target.id
  RETURNING ce.id
),
inserted_messages AS (
  INSERT INTO simulate_chat_message (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    call_execution_id,
    role,
    messages,
    content,
    session_id,
    organization_id,
    workspace_id,
    tool_calls,
    tokens,
    latency_ms
  )
  SELECT
    now(),
    now(),
    false,
    NULL,
    gen_random_uuid(),
    tc.id,
    payload.role,
    payload.messages::jsonb,
    payload.content::jsonb,
    COALESCE(ce.call_metadata->>'chat_session_id', 'simulation-action-session'),
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    '[]'::jsonb,
    payload.tokens,
    payload.latency_ms
  FROM target_call tc
  JOIN simulate_call_execution ce ON ce.id = tc.id
  CROSS JOIN (
    VALUES
      ('user', '["baseline simulator reply"]', '[{"role":"user","content":"baseline simulator reply"}]', 12, NULL),
      ('assistant', '["agent response for comparison"]', '[{"role":"assistant","content":"agent response for comparison"}]', 24, 1000)
  ) AS payload(role, messages, content, tokens, latency_ms)
  WHERE NOT EXISTS (
    SELECT 1
    FROM simulate_chat_message existing
    WHERE existing.call_execution_id = tc.id
      AND existing.messages = payload.messages::jsonb
  )
  RETURNING id
),
updated_execution AS (
  UPDATE simulate_test_execution te
  SET
    status = 'completed',
    completed_at = COALESCE(te.completed_at, now()),
    total_calls = ${targetCallExecutionIds.length},
    completed_calls = ${targetCallExecutionIds.length},
    failed_calls = 0,
    picked_up_by_executor = true,
    updated_at = now()
  WHERE te.id = ${sqlUuid(testExecutionId)}
  RETURNING te.id
)
SELECT json_build_object(
  'updated_call_count', (SELECT count(*) FROM updated_call),
  'inserted_message_count', (SELECT count(*) FROM inserted_messages),
  'updated_execution_count', (SELECT count(*) FROM updated_execution)
);
`;
  return runPostgresJson(sql);
}

async function markSimulationEvalOutputsCompletedDb({
  testExecutionId,
  callExecutionIds,
  evalConfigId,
  evalName,
  organizationId,
  workspaceId,
}) {
  assert(
    isUuid(testExecutionId),
    "Eval-output seed needs a test execution UUID.",
  );
  assert(
    asArray(callExecutionIds).every(isUuid),
    "Eval-output seed needs call execution UUIDs.",
  );
  assert(isUuid(evalConfigId), "Eval-output seed needs an eval config UUID.");
  const evalPayload = {
    [evalConfigId]: {
      name: evalName,
      output: true,
      output_type: "Pass/Fail",
      reason: "SIM eval journey reason",
      status: "completed",
    },
  };
  const sql = `
WITH target_call AS (
  SELECT ce.id
  FROM simulate_call_execution ce
  JOIN simulate_test_execution te ON te.id = ce.test_execution_id
  JOIN simulate_run_test rt ON rt.id = te.run_test_id
  WHERE te.id = ${sqlUuid(testExecutionId)}
    AND ce.id = ANY(${sqlUuidArray(callExecutionIds)})
    AND rt.organization_id = ${sqlUuid(organizationId)}
    AND rt.workspace_id = ${sqlUuid(workspaceId)}
    AND rt.deleted = false
    AND te.deleted = false
    AND ce.deleted = false
),
updated_call AS (
  UPDATE simulate_call_execution ce
  SET
    status = 'completed',
    started_at = COALESCE(ce.started_at, now() - interval '45 seconds'),
    completed_at = COALESCE(ce.completed_at, now()),
    ended_at = COALESCE(ce.ended_at, now()),
    duration_seconds = COALESCE(ce.duration_seconds, 45),
    response_time_ms = COALESCE(ce.response_time_ms, 1000),
    overall_score = COALESCE(ce.overall_score, 9.0),
    message_count = COALESCE(ce.message_count, 2),
    transcript_available = true,
    call_metadata = COALESCE(ce.call_metadata, '{}'::jsonb)
      || jsonb_build_object('eval_started', true, 'eval_completed', true),
    eval_outputs = COALESCE(ce.eval_outputs, '{}'::jsonb) || ${sqlJson(evalPayload)},
    updated_at = now()
  FROM target_call target
  WHERE ce.id = target.id
  RETURNING ce.id
),
updated_execution AS (
  UPDATE simulate_test_execution te
  SET
    status = 'completed',
    completed_at = COALESCE(te.completed_at, now()),
    total_calls = GREATEST(COALESCE(te.total_calls, 0), (SELECT count(*) FROM target_call)),
    completed_calls = GREATEST(COALESCE(te.completed_calls, 0), (SELECT count(*) FROM target_call)),
    failed_calls = 0,
    picked_up_by_executor = true,
    updated_at = now()
  WHERE te.id = ${sqlUuid(testExecutionId)}
  RETURNING te.id
)
SELECT json_build_object(
  'updated_call_count', (SELECT count(*) FROM updated_call),
  'updated_execution_count', (SELECT count(*) FROM updated_execution),
  'eval_output_count', (
    SELECT count(*)
    FROM simulate_call_execution ce
    WHERE ce.id IN (SELECT id FROM target_call)
      AND ce.eval_outputs ? ${sqlString(evalConfigId)}
  )
);
`;
  return runPostgresJson(sql);
}

async function loadSimulationRunActionDbAudit({
  namePrefix,
  runTestId,
  testExecutionId,
  callExecutionId,
  evalConfigId,
  organizationId,
}) {
  const sql = `
WITH target_run AS (
  SELECT *
  FROM simulate_run_test
  WHERE id = ${sqlUuid(runTestId)}
    AND organization_id = ${sqlUuid(organizationId)}
),
target_execution AS (
  SELECT *
  FROM simulate_test_execution
  WHERE id = ${sqlUuid(testExecutionId)}
    AND run_test_id IN (SELECT id FROM target_run)
),
target_call AS (
  SELECT *
  FROM simulate_call_execution
  WHERE id = ${sqlUuid(callExecutionId)}
    AND test_execution_id IN (SELECT id FROM target_execution)
),
target_eval_config AS (
  SELECT *
  FROM simulate_eval_config
  WHERE id = ${sqlUuid(evalConfigId)}
    AND run_test_id IN (SELECT id FROM target_run)
),
target_optimisers AS (
  SELECT ao.*
  FROM agent_optimiser ao
  JOIN target_execution te ON te.agent_optimiser_id = ao.id
),
target_trace_projects AS (
  SELECT *
  FROM tracer_project
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
)
SELECT json_build_object(
  'run_test_status_deleted', (SELECT deleted FROM target_run),
  'test_execution_status', (SELECT status FROM target_execution),
  'call_status', (SELECT status FROM target_call),
  'call_eval_outputs', (SELECT eval_outputs FROM target_call),
  'eval_config_count', (SELECT count(*) FROM target_eval_config),
  'chat_session_count', (
    SELECT count(*)
    FROM simulate_chatsimulatorsession
    WHERE call_execution_id IN (SELECT id FROM target_call)
  ),
  'chat_message_count', (
    SELECT count(*)
    FROM simulate_chat_message
    WHERE call_execution_id IN (SELECT id FROM target_call)
  ),
  'trace_count', (
    SELECT count(*)
    FROM tracer_trace
    WHERE project_id IN (SELECT id FROM target_trace_projects)
  ),
  'trace_project_count', (
    SELECT count(*)
    FROM target_trace_projects
  ),
  'optimiser_count', (SELECT count(*) FROM target_optimisers),
  'optimiser_run_count', (
    SELECT count(*)
    FROM agent_optimiser_run
    WHERE agent_optimiser_id IN (SELECT id FROM target_optimisers)
  ),
  'call_snapshot_count', (
    SELECT count(*)
    FROM simulate_call_execution_snapshot
    WHERE call_execution_id IN (SELECT id FROM target_call)
  )
);
`;
  return runPostgresJson(sql);
}

async function loadSimulationCancellationDbAudit({
  runTestId,
  testExecutionId,
  callExecutionIds,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH target_run AS (
  SELECT *
  FROM simulate_run_test
  WHERE id = ${sqlUuid(runTestId)}
    AND organization_id = ${sqlUuid(organizationId)}
),
target_execution AS (
  SELECT *
  FROM simulate_test_execution
  WHERE id = ${sqlUuid(testExecutionId)}
    AND run_test_id IN (SELECT id FROM target_run)
),
target_calls AS (
  SELECT *
  FROM simulate_call_execution
  WHERE test_execution_id IN (SELECT id FROM target_execution)
),
target_create_calls AS (
  SELECT *
  FROM simulate_createcallexecution
  WHERE call_execution_id IN (SELECT id FROM target_calls)
),
call_status_counts AS (
  SELECT status, count(*) AS count
  FROM target_calls
  GROUP BY status
),
create_call_status_counts AS (
  SELECT status, count(*) AS count
  FROM target_create_calls
  GROUP BY status
)
SELECT json_build_object(
  'run_test_id', (SELECT id::text FROM target_run),
  'workspace_matches', (
    SELECT workspace_id = ${sqlUuid(workspaceId)}
    FROM target_run
  ),
  'test_execution_status', (SELECT status FROM target_execution),
  'test_execution_completed_at_set', (
    SELECT completed_at IS NOT NULL FROM target_execution
  ),
  'test_execution_picked_up', (
    SELECT picked_up_by_executor FROM target_execution
  ),
  'target_call_count', (
    SELECT count(*)
    FROM target_calls
    WHERE id = ANY(${sqlUuidArray(callExecutionIds)})
  ),
  'call_count', (SELECT count(*) FROM target_calls),
  'cancelled_call_count', (
    SELECT count(*)
    FROM target_calls
    WHERE status = 'cancelled'
  ),
  'active_call_count', (
    SELECT count(*)
    FROM target_calls
    WHERE status IN ('pending', 'queued', 'ongoing')
  ),
  'call_status_counts', (
    SELECT COALESCE(json_object_agg(status, count), '{}'::json)
    FROM call_status_counts
  ),
  'ended_reason_counts', (
    SELECT COALESCE(json_object_agg(COALESCE(ended_reason, ''), count), '{}'::json)
    FROM (
      SELECT ended_reason, count(*) AS count
      FROM target_calls
      GROUP BY ended_reason
    ) reason_counts
  ),
  'create_call_count', (SELECT count(*) FROM target_create_calls),
  'active_create_call_count', (
    SELECT count(*)
    FROM target_create_calls
    WHERE status IN ('pending', 'registered', 'ongoing')
  ),
  'create_call_status_counts', (
    SELECT COALESCE(json_object_agg(status, count), '{}'::json)
    FROM create_call_status_counts
  )
);
`;
  return runPostgresJson(sql);
}

async function loadVoiceSimulationCallOutputDbAudit({
  runTestId,
  testExecutionId,
  callExecutionId,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH target_run AS (
  SELECT rt.*, ad.agent_type
  FROM simulate_run_test rt
  JOIN simulate_agent_definition ad ON ad.id = rt.agent_definition_id
  WHERE rt.id = ${sqlUuid(runTestId)}
    AND rt.organization_id = ${sqlUuid(organizationId)}
),
target_execution AS (
  SELECT *
  FROM simulate_test_execution
  WHERE id = ${sqlUuid(testExecutionId)}
    AND run_test_id IN (SELECT id FROM target_run)
),
target_call AS (
  SELECT *
  FROM simulate_call_execution
  WHERE id = ${sqlUuid(callExecutionId)}
    AND test_execution_id IN (SELECT id FROM target_execution)
),
target_create_call AS (
  SELECT *
  FROM simulate_createcallexecution
  WHERE call_execution_id IN (SELECT id FROM target_call)
)
SELECT json_build_object(
  'run_test_id', (SELECT id::text FROM target_run),
  'workspace_matches', (
    SELECT workspace_id = ${sqlUuid(workspaceId)}
    FROM target_run
  ),
  'agent_type', (SELECT agent_type FROM target_run),
  'test_execution_status', (SELECT status FROM target_execution),
  'call_status', (SELECT status FROM target_call),
  'simulation_call_type', (SELECT simulation_call_type FROM target_call),
  'recording_available', (SELECT recording_available FROM target_call),
  'transcript_available', (SELECT transcript_available FROM target_call),
  'recording_url', (SELECT recording_url FROM target_call),
  'provider_keys', (
    SELECT COALESCE(json_agg(key ORDER BY key), '[]'::json)
    FROM target_call,
    LATERAL jsonb_object_keys(provider_call_data) AS keys(key)
  ),
  'transcript_count', (
    SELECT count(*)
    FROM simulate_call_transcript
    WHERE call_execution_id IN (SELECT id FROM target_call)
      AND deleted = false
  ),
  'create_call_count', (SELECT count(*) FROM target_create_call),
  'create_call_status', (SELECT status FROM target_create_call),
  'message_count', (SELECT message_count FROM target_call),
  'duration_seconds', (SELECT duration_seconds FROM target_call),
  'customer_call_id', (SELECT customer_call_id FROM target_call),
  'cost_cents', (SELECT cost_cents FROM target_call),
  'customer_cost_cents', (SELECT customer_cost_cents FROM target_call),
  'run_scenario_count', (
    SELECT count(*)
    FROM simulate_run_test_scenarios
    WHERE runtest_id IN (SELECT id FROM target_run)
  )
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteVoiceSimulationCallOutputFixturesDb({
  namePrefix,
  organizationId,
}) {
  const sql = `
WITH target_runs AS (
  SELECT id, agent_definition_id, agent_version_id, simulator_agent_id
  FROM simulate_run_test
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
target_run_scenarios AS (
  SELECT scenarios_id AS id
  FROM simulate_run_test_scenarios
  WHERE runtest_id IN (SELECT id FROM target_runs)
),
target_scenarios AS (
  SELECT id, agent_definition_id, simulator_agent_id
  FROM simulate_scenarios
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND (
      name LIKE ${sqlString(`${namePrefix}%`)}
      OR id IN (SELECT id FROM target_run_scenarios)
    )
),
target_executions AS (
  SELECT id
  FROM simulate_test_execution
  WHERE run_test_id IN (SELECT id FROM target_runs)
),
target_calls AS (
  SELECT id
  FROM simulate_call_execution
  WHERE test_execution_id IN (SELECT id FROM target_executions)
),
target_agents AS (
  SELECT id
  FROM simulate_agent_definition
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND (
      agent_name LIKE ${sqlString(`${namePrefix}%`)}
      OR id IN (SELECT agent_definition_id FROM target_runs WHERE agent_definition_id IS NOT NULL)
      OR id IN (SELECT agent_definition_id FROM target_scenarios WHERE agent_definition_id IS NOT NULL)
    )
),
target_versions AS (
  SELECT id
  FROM simulate_agent_version
  WHERE agent_definition_id IN (SELECT id FROM target_agents)
     OR id IN (SELECT agent_version_id FROM target_runs WHERE agent_version_id IS NOT NULL)
),
target_simulator_agents AS (
  SELECT id
  FROM simulator_agents
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND (
      name LIKE ${sqlString(`${namePrefix}%`)}
      OR id IN (SELECT simulator_agent_id FROM target_runs WHERE simulator_agent_id IS NOT NULL)
      OR id IN (SELECT simulator_agent_id FROM target_scenarios WHERE simulator_agent_id IS NOT NULL)
    )
),
updated_phone_numbers AS (
  UPDATE simulate_phone_numbers
  SET current_call_execution_id = NULL
  WHERE current_call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_queue_items AS (
  DELETE FROM model_hub_queueitem
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_scores AS (
  DELETE FROM model_hub_score
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_trial_results AS (
  DELETE FROM trial_item_result
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_snapshots AS (
  DELETE FROM simulate_call_execution_snapshot
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_transcripts AS (
  DELETE FROM simulate_call_transcript
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_logs AS (
  DELETE FROM simulate_call_log_entry
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_create_calls AS (
  DELETE FROM simulate_createcallexecution
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_calls AS (
  DELETE FROM simulate_call_execution
  WHERE id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_executions AS (
  DELETE FROM simulate_test_execution
  WHERE id IN (SELECT id FROM target_executions)
  RETURNING id
),
deleted_run_scenarios AS (
  DELETE FROM simulate_run_test_scenarios
  WHERE runtest_id IN (SELECT id FROM target_runs)
     OR scenarios_id IN (SELECT id FROM target_scenarios)
  RETURNING id
),
deleted_runs AS (
  DELETE FROM simulate_run_test
  WHERE id IN (SELECT id FROM target_runs)
  RETURNING id
),
deleted_graphs AS (
  DELETE FROM simulate_scenario_graph
  WHERE scenario_id IN (SELECT id FROM target_scenarios)
  RETURNING id
),
deleted_scenarios AS (
  DELETE FROM simulate_scenarios
  WHERE id IN (SELECT id FROM target_scenarios)
  RETURNING id
),
deleted_credentials AS (
  DELETE FROM simulate_provider_credentials
  WHERE agent_definition_id IN (SELECT id FROM target_agents)
  RETURNING id
),
deleted_versions AS (
  DELETE FROM simulate_agent_version
  WHERE id IN (SELECT id FROM target_versions)
  RETURNING id
),
deleted_agents AS (
  DELETE FROM simulate_agent_definition
  WHERE id IN (SELECT id FROM target_agents)
  RETURNING id
),
deleted_simulator_agents AS (
  DELETE FROM simulator_agents
  WHERE id IN (SELECT id FROM target_simulator_agents)
  RETURNING id
)
SELECT json_build_object(
  'deleted_run_test_count', (SELECT count(*) FROM deleted_runs),
  'deleted_test_execution_count', (SELECT count(*) FROM deleted_executions),
  'deleted_call_execution_count', (SELECT count(*) FROM deleted_calls),
  'deleted_create_call_count', (SELECT count(*) FROM deleted_create_calls),
  'deleted_transcript_count', (SELECT count(*) FROM deleted_transcripts),
  'deleted_scenario_count', (SELECT count(*) FROM deleted_scenarios),
  'deleted_graph_count', (SELECT count(*) FROM deleted_graphs),
  'deleted_agent_count', (SELECT count(*) FROM deleted_agents),
  'deleted_agent_version_count', (SELECT count(*) FROM deleted_versions),
  'deleted_simulator_agent_count', (SELECT count(*) FROM deleted_simulator_agents),
  'deleted_credential_count', (SELECT count(*) FROM deleted_credentials)
);
`;
  const cleanup = await runPostgresJson(sql);
  const remainingSql = `
WITH target_runs AS (
  SELECT id
  FROM simulate_run_test
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
target_executions AS (
  SELECT id
  FROM simulate_test_execution
  WHERE run_test_id IN (SELECT id FROM target_runs)
),
target_calls AS (
  SELECT id
  FROM simulate_call_execution
  WHERE test_execution_id IN (SELECT id FROM target_executions)
),
target_scenarios AS (
  SELECT id
  FROM simulate_scenarios
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
target_agents AS (
  SELECT id
  FROM simulate_agent_definition
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND agent_name LIKE ${sqlString(`${namePrefix}%`)}
),
target_simulator_agents AS (
  SELECT id
  FROM simulator_agents
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
)
SELECT json_build_object(
  'remaining_run_test_count', (SELECT count(*) FROM target_runs),
  'remaining_test_execution_count', (SELECT count(*) FROM target_executions),
  'remaining_call_execution_count', (SELECT count(*) FROM target_calls),
  'remaining_scenario_count', (SELECT count(*) FROM target_scenarios),
  'remaining_agent_count', (SELECT count(*) FROM target_agents),
  'remaining_simulator_agent_count', (SELECT count(*) FROM target_simulator_agents)
);
`;
  return {
    ...cleanup,
    ...(await runPostgresJson(remainingSql)),
  };
}

async function hardDeleteSimulationRunActionFixturesDb({
  namePrefix,
  organizationId,
}) {
  const sql = `
WITH target_runs AS (
  SELECT id
  FROM simulate_run_test
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
target_executions AS (
  SELECT id, agent_optimiser_id
  FROM simulate_test_execution
  WHERE run_test_id IN (SELECT id FROM target_runs)
),
target_calls AS (
  SELECT id
  FROM simulate_call_execution
  WHERE test_execution_id IN (SELECT id FROM target_executions)
),
target_optimisers AS (
  SELECT DISTINCT agent_optimiser_id AS id
  FROM target_executions
  WHERE agent_optimiser_id IS NOT NULL
),
target_trace_projects AS (
  SELECT id
  FROM tracer_project
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
updated_phone_numbers AS (
  UPDATE simulate_phone_numbers
  SET current_call_execution_id = NULL
  WHERE current_call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_queue_items AS (
  DELETE FROM model_hub_queueitem
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_scores AS (
  DELETE FROM model_hub_score
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_trial_results AS (
  DELETE FROM trial_item_result
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_snapshots AS (
  DELETE FROM simulate_call_execution_snapshot
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_transcripts AS (
  DELETE FROM simulate_call_transcript
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_logs AS (
  DELETE FROM simulate_call_log_entry
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_chat_messages AS (
  DELETE FROM simulate_chat_message
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_chat_sessions AS (
  DELETE FROM simulate_chatsimulatorsession
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id, assistant_id
),
deleted_chat_assistants AS (
  DELETE FROM simulate_chatsimulatorassistant
  WHERE id IN (SELECT assistant_id FROM deleted_chat_sessions)
     OR name LIKE ${sqlString(`${namePrefix}%`)}
  RETURNING id
),
deleted_create_call_rows AS (
  DELETE FROM simulate_createcallexecution
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_call_executions AS (
  DELETE FROM simulate_call_execution
  WHERE id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_prompt_optimiser_steps AS (
  DELETE FROM agent_prompt_optimiser_run_step
  WHERE agent_prompt_optimiser_run_id IN (
    SELECT id
    FROM agent_prompt_optimiser_run
    WHERE test_execution_id IN (SELECT id FROM target_executions)
       OR agent_optimiser_id IN (SELECT id FROM target_optimisers)
  )
  RETURNING id
),
deleted_prompt_optimiser_runs AS (
  DELETE FROM agent_prompt_optimiser_run
  WHERE test_execution_id IN (SELECT id FROM target_executions)
     OR agent_optimiser_id IN (SELECT id FROM target_optimisers)
  RETURNING id
),
deleted_optimiser_runs AS (
  DELETE FROM agent_optimiser_run
  WHERE agent_optimiser_id IN (SELECT id FROM target_optimisers)
  RETURNING id
),
deleted_test_executions AS (
  DELETE FROM simulate_test_execution
  WHERE id IN (SELECT id FROM target_executions)
  RETURNING id
),
deleted_optimisers AS (
  DELETE FROM agent_optimiser
  WHERE id IN (SELECT id FROM target_optimisers)
  RETURNING id
),
deleted_eval_configs AS (
  DELETE FROM simulate_eval_config
  WHERE run_test_id IN (SELECT id FROM target_runs)
  RETURNING id
),
deleted_replay_sessions AS (
  DELETE FROM tracer_replaysession
  WHERE run_test_id IN (SELECT id FROM target_runs)
  RETURNING id
),
deleted_run_scenarios AS (
  DELETE FROM simulate_run_test_scenarios
  WHERE runtest_id IN (SELECT id FROM target_runs)
  RETURNING id
),
deleted_run_tests AS (
  DELETE FROM simulate_run_test
  WHERE id IN (SELECT id FROM target_runs)
  RETURNING id
),
deleted_observation_spans AS (
  DELETE FROM tracer_observation_span
  WHERE project_id IN (SELECT id FROM target_trace_projects)
  RETURNING id
),
deleted_traces AS (
  DELETE FROM tracer_trace
  WHERE project_id IN (SELECT id FROM target_trace_projects)
  RETURNING id
),
deleted_trace_sessions AS (
  DELETE FROM trace_session
  WHERE project_id IN (SELECT id FROM target_trace_projects)
  RETURNING id
),
deleted_trace_projects AS (
  DELETE FROM tracer_project
  WHERE id IN (SELECT id FROM target_trace_projects)
  RETURNING id
)
SELECT json_build_object(
  'deleted_run_test_count', (SELECT count(*) FROM deleted_run_tests),
  'deleted_test_execution_count', (SELECT count(*) FROM deleted_test_executions),
  'deleted_call_execution_count', (SELECT count(*) FROM deleted_call_executions),
  'deleted_chat_session_count', (SELECT count(*) FROM deleted_chat_sessions),
  'deleted_chat_assistant_count', (SELECT count(*) FROM deleted_chat_assistants),
  'deleted_chat_message_count', (SELECT count(*) FROM deleted_chat_messages),
  'deleted_optimiser_count', (SELECT count(*) FROM deleted_optimisers),
  'deleted_optimiser_run_count', (SELECT count(*) FROM deleted_optimiser_runs),
  'deleted_trace_project_count', (SELECT count(*) FROM deleted_trace_projects),
  'deleted_trace_count', (SELECT count(*) FROM deleted_traces),
  'deleted_trace_session_count', (SELECT count(*) FROM deleted_trace_sessions),
  'deleted_observation_span_count', (SELECT count(*) FROM deleted_observation_spans)
);
`;
  const cleanup = await runPostgresJson(sql);
  const remainingSql = `
WITH target_runs AS (
  SELECT id
  FROM simulate_run_test
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
target_executions AS (
  SELECT id
  FROM simulate_test_execution
  WHERE run_test_id IN (SELECT id FROM target_runs)
),
target_calls AS (
  SELECT id
  FROM simulate_call_execution
  WHERE test_execution_id IN (SELECT id FROM target_executions)
),
target_trace_projects AS (
  SELECT id
  FROM tracer_project
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
)
SELECT json_build_object(
  'remaining_run_test_count', (SELECT count(*) FROM target_runs),
  'remaining_test_execution_count', (SELECT count(*) FROM target_executions),
  'remaining_call_execution_count', (SELECT count(*) FROM target_calls),
  'remaining_trace_project_count', (SELECT count(*) FROM target_trace_projects)
);
`;
  return {
    ...cleanup,
    ...(await runPostgresJson(remainingSql)),
  };
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

async function loadAgentccBlocklistDbAudit({ blocklistId, organizationId }) {
  const sql = `
SELECT COALESCE((
  SELECT json_build_object(
    'id', id::text,
    'organization_id', organization_id::text,
    'name', name,
    'description', description,
    'words', words,
    'is_active', is_active,
    'deleted', deleted,
    'deleted_at_set', deleted_at IS NOT NULL
  )
  FROM agentcc_blocklist
  WHERE id = ${sqlUuid(blocklistId)}
    AND organization_id = ${sqlUuid(organizationId)}
), '{}'::json);
`;
  return runPostgresJson(sql);
}

async function loadAgentccCustomPropertyDbAudit({
  propertyId,
  organizationId,
}) {
  const sql = `
SELECT COALESCE((
  SELECT json_build_object(
    'id', id::text,
    'organization_id', organization_id::text,
    'project_id', project_id::text,
    'name', name,
    'property_type', property_type,
    'required', required,
    'allowed_values', allowed_values,
    'default_value', default_value,
    'deleted', deleted,
    'deleted_at_set', deleted_at IS NOT NULL
  )
  FROM agentcc_custom_property_schema
  WHERE id = ${sqlUuid(propertyId)}
    AND organization_id = ${sqlUuid(organizationId)}
), '{}'::json);
`;
  return runPostgresJson(sql);
}

async function deleteAgentccCustomPropertyRowsDb({ name, organizationId }) {
  const sql = `
WITH deleted_rows AS (
  DELETE FROM agentcc_custom_property_schema
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name = ${sqlString(name)}
  RETURNING id
)
SELECT json_build_object(
  'deleted_count', (SELECT count(*) FROM deleted_rows),
  'remaining_count', (
    SELECT count(*)
    FROM agentcc_custom_property_schema
    WHERE organization_id = ${sqlUuid(organizationId)}
      AND name = ${sqlString(name)}
      AND id NOT IN (SELECT id FROM deleted_rows)
  )
);
`;
  return runPostgresJson(sql);
}

async function loadAgentccRoutingPolicyDbAudit({
  policyIds,
  organizationId,
  policyName,
  createdConfigIds,
}) {
  const sql = `
WITH selected_policy_ids AS (
  SELECT unnest(${sqlUuidArray(policyIds)}) AS id
),
selected_policies AS (
  SELECT p.*
  FROM agentcc_routing_policy p
  JOIN selected_policy_ids ids ON ids.id = p.id
  WHERE p.organization_id = ${sqlUuid(organizationId)}
),
created_config_ids AS (
  SELECT unnest(${sqlUuidArray(createdConfigIds)}) AS id
),
active_config AS (
  SELECT id, routing
  FROM agentcc_org_config
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND deleted = false
    AND is_active = true
  LIMIT 1
),
created_configs AS (
  SELECT c.id, c.version, c.deleted, c.is_active
  FROM agentcc_org_config c
  JOIN created_config_ids ids ON ids.id = c.id
  WHERE c.organization_id = ${sqlUuid(organizationId)}
)
SELECT json_build_object(
  'policy_count', (SELECT count(*) FROM selected_policies),
  'active_policy_count', (
    SELECT count(*)
    FROM selected_policies
    WHERE deleted = false AND is_active = true
  ),
  'deleted_policy_count', (
    SELECT count(*)
    FROM selected_policies
    WHERE deleted = true
  ),
  'deleted_at_count', (
    SELECT count(*)
    FROM selected_policies
    WHERE deleted_at IS NOT NULL
  ),
  'policy_versions', (
    SELECT COALESCE(json_agg(version ORDER BY version), '[]'::json)
    FROM selected_policies
  ),
  'policy_configs', (
    SELECT COALESCE(json_agg(config ORDER BY version), '[]'::json)
    FROM selected_policies
  ),
  'active_config_policy_present',
    COALESCE((SELECT routing->'policies' ? ${sqlString(policyName)} FROM active_config), false),
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
    'applied_keys', applied_keys,
    'applied_projects', applied_projects,
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

async function loadAgentccGatewayActionDbAudit({
  organizationId,
  originalConfigId,
  createdConfigIds,
  providerName,
  guardrailName,
}) {
  const sql = `
WITH created_ids AS (
  SELECT unnest(${sqlUuidArray(createdConfigIds)}::uuid[]) AS id
),
active_config AS (
  SELECT id, version, guardrails
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
),
provider_rows AS (
  SELECT id, deleted, deleted_at IS NOT NULL AS deleted_at_set
  FROM agentcc_provider_credential
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND provider_name = ${sqlString(providerName)}
)
SELECT json_build_object(
  'active_config_id', (SELECT id::text FROM active_config),
  'active_config_is_original',
    COALESCE((SELECT id = ${sqlUuid(originalConfigId)} FROM active_config), false),
  'active_guardrail_present',
    COALESCE((
      SELECT EXISTS (
        SELECT 1
        FROM jsonb_array_elements(COALESCE(guardrails->'rules', '[]'::jsonb)) AS rule
        WHERE rule->>'name' = ${sqlString(guardrailName)}
      )
      FROM active_config
    ), false),
  'provider_row_count', (SELECT count(*) FROM provider_rows),
  'provider_deleted',
    COALESCE((SELECT bool_and(deleted) FROM provider_rows), false),
  'provider_deleted_at_set',
    COALESCE((SELECT bool_and(deleted_at_set) FROM provider_rows), false),
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

async function hardDeleteAgentccProviderCredentialByName({
  organizationId,
  providerName,
}) {
  const sql = `
WITH deleted_rows AS (
  DELETE FROM agentcc_provider_credential
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND provider_name = ${sqlString(providerName)}
  RETURNING id
)
SELECT json_build_object(
  'deleted_provider_count', (SELECT count(*) FROM deleted_rows),
  'remaining_provider_count', (
    SELECT count(*)
    FROM agentcc_provider_credential
    WHERE organization_id = ${sqlUuid(organizationId)}
      AND provider_name = ${sqlString(providerName)}
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

async function loadAgentccSessionDbAudit({
  sessionUuid,
  organizationId,
  workspaceId,
  sessionId,
}) {
  const sql = `
SELECT COALESCE((
  SELECT json_build_object(
    'id', s.id::text,
    'organization_id', s.organization_id::text,
    'workspace_id', s.workspace_id::text,
    'session_id', s.session_id,
    'name', s.name,
    'status', s.status,
    'metadata', s.metadata,
    'deleted', s.deleted,
    'deleted_at_set', s.deleted_at IS NOT NULL,
    'request_log_count', (
      SELECT count(*)
      FROM agentcc_request_log l
      WHERE l.organization_id = ${sqlUuid(organizationId)}
        AND l.workspace_id = ${sqlUuid(workspaceId)}
        AND l.session_id = ${sqlString(sessionId)}
        AND l.deleted = false
    ),
    'other_session_log_count', (
      SELECT count(*)
      FROM agentcc_request_log l
      WHERE l.organization_id = ${sqlUuid(organizationId)}
        AND l.workspace_id = ${sqlUuid(workspaceId)}
        AND l.session_id = ${sqlString(`${sessionId}_other`)}
        AND l.deleted = false
    )
  )
  FROM agentcc_session s
  WHERE s.id = ${sqlUuid(sessionUuid)}
    AND s.organization_id = ${sqlUuid(organizationId)}
), '{}'::json);
`;
  return runPostgresJson(sql);
}

async function deleteAgentccSessionFixtureDb({
  sessionUuid,
  organizationId,
  logIds,
}) {
  const sql = `
WITH target_logs AS (
  SELECT id
  FROM agentcc_request_log
  WHERE id = ANY(${sqlUuidArray(logIds)})
    AND organization_id = ${sqlUuid(organizationId)}
),
target_session AS (
  SELECT id
  FROM agentcc_session
  WHERE id = ${sqlUuid(sessionUuid)}
    AND organization_id = ${sqlUuid(organizationId)}
),
deleted_logs AS (
  DELETE FROM agentcc_request_log
  USING target_logs
  WHERE agentcc_request_log.id = target_logs.id
  RETURNING agentcc_request_log.id
),
deleted_session AS (
  DELETE FROM agentcc_session
  USING target_session
  WHERE agentcc_session.id = target_session.id
  RETURNING agentcc_session.id
)
SELECT json_build_object(
  'deleted_log_count', (SELECT count(*) FROM deleted_logs),
  'deleted_session_count', (SELECT count(*) FROM deleted_session),
  'remaining_log_count',
    (SELECT count(*) FROM target_logs) - (SELECT count(*) FROM deleted_logs),
  'remaining_session_count',
    (SELECT count(*) FROM target_session) - (SELECT count(*) FROM deleted_session)
);
`;
  return runPostgresJson(sql);
}

async function loadAgentccWebhookDbAudit({
  webhookId,
  organizationId,
  eventIds,
  rawSecret,
}) {
  const sql = `
WITH target_events AS (
  SELECT unnest(${sqlUuidArray(eventIds)}::uuid[]) AS id
),
event_rows AS (
  SELECT e.*
  FROM agentcc_webhook_event e
  JOIN target_events te ON te.id = e.id
  WHERE e.organization_id = ${sqlUuid(organizationId)}
    AND e.webhook_id = ${sqlUuid(webhookId)}
)
SELECT COALESCE((
  SELECT json_build_object(
    'id', w.id::text,
    'organization_id', w.organization_id::text,
    'name', w.name,
    'url', w.url,
    'events', w.events,
    'is_active', w.is_active,
    'raw_secret_present', w.secret = ${sqlString(rawSecret)},
    'deleted', w.deleted,
    'deleted_at_set', w.deleted_at IS NOT NULL,
    'event_count', (SELECT count(*) FROM event_rows),
    'pending_event_count', (
      SELECT count(*) FROM event_rows WHERE status = 'pending'
    ),
    'failed_event_count', (
      SELECT count(*) FROM event_rows WHERE status = 'failed'
    ),
    'delivered_event_count', (
      SELECT count(*) FROM event_rows WHERE status = 'delivered'
    )
  )
  FROM agentcc_webhook w
  WHERE w.id = ${sqlUuid(webhookId)}
    AND w.organization_id = ${sqlUuid(organizationId)}
), '{}'::json);
`;
  return runPostgresJson(sql);
}

async function seedAgentccWebhookDb({
  webhookId,
  organizationId,
  name,
  rawSecret,
  runId,
}) {
  const sql = `
WITH stale_events AS (
  DELETE FROM agentcc_webhook_event
  WHERE webhook_id IN (
    SELECT id
    FROM agentcc_webhook
    WHERE organization_id = ${sqlUuid(organizationId)}
      AND name = ${sqlString(name)}
  )
  RETURNING id
),
stale_webhook AS (
  DELETE FROM agentcc_webhook
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name = ${sqlString(name)}
  RETURNING id
),
inserted AS (
  INSERT INTO agentcc_webhook (
    id,
    organization_id,
    name,
    url,
    secret,
    events,
    is_active,
    headers,
    description,
    created_at,
    updated_at,
    deleted,
    deleted_at
  )
  VALUES (
    ${sqlUuid(webhookId)},
    ${sqlUuid(organizationId)},
    ${sqlString(name)},
    'https://example.com/futureagi-api-journey-webhook',
    ${sqlString(rawSecret)},
    ${sqlJson(["request.completed", "error.occurred"])},
    true,
    ${sqlJson({ "X-API-Journey": runId })},
    'Temporary webhook for API journey regression.',
    now(),
    now(),
    false,
    NULL
  )
  RETURNING
    id::text,
    name,
    url,
    events,
    is_active,
    headers,
    description
)
SELECT COALESCE((
  SELECT json_build_object(
    'id', id,
    'name', name,
    'url', url,
    'events', events,
    'is_active', is_active,
    'headers', headers,
    'description', description,
    'seeded_after_entitlement', true
  )
  FROM inserted
), '{}'::json);
`;
  return runPostgresJson(sql);
}

async function seedAgentccWebhookEventsDb({
  organizationId,
  webhookId,
  eventIds,
  marker,
}) {
  const [failedEventId, deliveredEventId] = eventIds;
  assert(
    isUuid(failedEventId) && isUuid(deliveredEventId),
    "Webhook event seed requires two UUIDs.",
  );

  const sql = `
WITH stale AS (
  DELETE FROM agentcc_webhook_event
  WHERE webhook_id = ${sqlUuid(webhookId)}
    AND organization_id = ${sqlUuid(organizationId)}
    AND payload->>'marker' = ${sqlString(marker)}
  RETURNING id
),
inserted AS (
  INSERT INTO agentcc_webhook_event (
    id,
    organization_id,
    webhook_id,
    event_type,
    payload,
    status,
    attempts,
    max_attempts,
    last_attempt_at,
    last_response_code,
    last_error,
    next_retry_at,
    created_at,
    updated_at,
    deleted,
    deleted_at
  )
  VALUES
    (
      ${sqlUuid(failedEventId)},
      ${sqlUuid(organizationId)},
      ${sqlUuid(webhookId)},
      'error.occurred',
      ${sqlJson({
        marker,
        event: "error.occurred",
        request_id: `${marker}_failed_request`,
      })},
      'failed',
      2,
      5,
      now() - interval '5 minutes',
      503,
      ${sqlString(`${marker} temporary delivery failure`)},
      now() + interval '10 minutes',
      now() - interval '5 minutes',
      now() - interval '5 minutes',
      false,
      NULL
    ),
    (
      ${sqlUuid(deliveredEventId)},
      ${sqlUuid(organizationId)},
      ${sqlUuid(webhookId)},
      'request.completed',
      ${sqlJson({
        marker,
        event: "request.completed",
        request_id: `${marker}_delivered_request`,
      })},
      'delivered',
      1,
      5,
      now() - interval '3 minutes',
      204,
      '',
      NULL,
      now() - interval '3 minutes',
      now() - interval '3 minutes',
      false,
      NULL
    )
  RETURNING id
)
SELECT json_build_object(
  'inserted_count', (SELECT count(*) FROM inserted),
  'stale_deleted_count', (SELECT count(*) FROM stale)
);
`;
  return runPostgresJson(sql);
}

async function deleteAgentccWebhookFixtureDb({ webhookId, organizationId }) {
  const sql = `
WITH target_events AS (
  SELECT id
  FROM agentcc_webhook_event
  WHERE webhook_id = ${sqlUuid(webhookId)}
    AND organization_id = ${sqlUuid(organizationId)}
),
target_webhook AS (
  SELECT id
  FROM agentcc_webhook
  WHERE id = ${sqlUuid(webhookId)}
    AND organization_id = ${sqlUuid(organizationId)}
),
deleted_events AS (
  DELETE FROM agentcc_webhook_event
  USING target_events
  WHERE agentcc_webhook_event.id = target_events.id
  RETURNING agentcc_webhook_event.id
),
deleted_webhook AS (
  DELETE FROM agentcc_webhook
  USING target_webhook
  WHERE agentcc_webhook.id = target_webhook.id
  RETURNING agentcc_webhook.id
)
SELECT json_build_object(
  'deleted_event_count', (SELECT count(*) FROM deleted_events),
  'deleted_webhook_count', (SELECT count(*) FROM deleted_webhook),
  'remaining_event_count',
    (SELECT count(*) FROM target_events) - (SELECT count(*) FROM deleted_events),
  'remaining_webhook_count',
    (SELECT count(*) FROM target_webhook) - (SELECT count(*) FROM deleted_webhook)
);
`;
  return runPostgresJson(sql);
}

async function seedAgentccRequestLogsDb({
  organizationId,
  workspaceId,
  logIds,
  apiKeyId,
  marker,
  sharedSessionId,
  soloSessionId,
}) {
  const rows = [
    {
      id: logIds[0],
      requestId: `${marker}_success`,
      model: "gpt-4o-mini",
      provider: "openai",
      resolvedModel: "openai/gpt-4o-mini",
      latencyMs: 120,
      startedOffset: "30 minutes",
      inputTokens: 50,
      outputTokens: 70,
      totalTokens: 120,
      cost: "0.001200",
      statusCode: 200,
      isStream: false,
      isError: false,
      errorMessage: "",
      cacheHit: true,
      fallbackUsed: false,
      guardrailTriggered: false,
      userId: `${marker}_user_alpha`,
      sessionId: sharedSessionId,
      routingStrategy: "primary",
      metadata: { marker, lane: "success", tier: "gold" },
      requestBody: {
        messages: [{ role: "user", content: `${marker} success` }],
      },
      responseBody: { choices: [{ message: { content: "ok" } }] },
      requestHeaders: { "x-api-journey": marker },
      responseHeaders: { "x-request-outcome": "success" },
      guardrailResults: { checks: [] },
    },
    {
      id: logIds[1],
      requestId: `${marker}_error`,
      model: "gpt-4o",
      provider: "openai",
      resolvedModel: "openai/gpt-4o",
      latencyMs: 250,
      startedOffset: "20 minutes",
      inputTokens: 40,
      outputTokens: 20,
      totalTokens: 60,
      cost: "0.002500",
      statusCode: 503,
      isStream: false,
      isError: true,
      errorMessage: `${marker} provider unavailable`,
      cacheHit: false,
      fallbackUsed: false,
      guardrailTriggered: true,
      userId: `${marker}_user_beta`,
      sessionId: sharedSessionId,
      routingStrategy: "primary",
      metadata: { marker, lane: "error", retryable: true },
      requestBody: { messages: [{ role: "user", content: `${marker} error` }] },
      responseBody: { error: { code: "provider_unavailable" } },
      requestHeaders: { "x-api-journey": marker },
      responseHeaders: { "x-request-outcome": "error" },
      guardrailResults: {
        checks: [{ name: "toxicity", action: "monitor", triggered: true }],
      },
    },
    {
      id: logIds[2],
      requestId: `${marker}_stream`,
      model: "claude-3-haiku",
      provider: "anthropic",
      resolvedModel: "anthropic/claude-3-haiku",
      latencyMs: 420,
      startedOffset: "10 minutes",
      inputTokens: 300,
      outputTokens: 400,
      totalTokens: 700,
      cost: "0.005500",
      statusCode: 201,
      isStream: true,
      isError: false,
      errorMessage: "",
      cacheHit: false,
      fallbackUsed: true,
      guardrailTriggered: false,
      userId: `${marker}_user_gamma`,
      sessionId: soloSessionId,
      routingStrategy: "fallback",
      metadata: { marker, lane: "stream", fallback: true },
      requestBody: {
        messages: [{ role: "user", content: `${marker} stream` }],
      },
      responseBody: { stream: true, chunks: 3 },
      requestHeaders: { "x-api-journey": marker },
      responseHeaders: { "x-request-outcome": "stream" },
      guardrailResults: { checks: [] },
    },
  ];

  const values = rows
    .map(
      (row) => `(
        ${sqlUuid(row.id)},
        ${sqlUuid(organizationId)},
        ${sqlUuid(workspaceId)},
        ${sqlString(row.requestId)},
        ${sqlString(row.model)},
        ${sqlString(row.provider)},
        ${sqlString(row.resolvedModel)},
        ${row.latencyMs},
        now() - ${sqlString(row.startedOffset)}::interval,
        ${row.inputTokens},
        ${row.outputTokens},
        ${row.totalTokens},
        ${sqlString(row.cost)}::numeric,
        ${row.statusCode},
        ${row.isStream},
        ${row.isError},
        ${sqlString(row.errorMessage)},
        ${row.cacheHit},
        ${row.fallbackUsed},
        ${row.guardrailTriggered},
        ${sqlString(apiKeyId)},
        ${sqlString(row.userId)},
        ${sqlString(row.sessionId)},
        ${sqlString(row.routingStrategy)},
        ${sqlJson(row.metadata)},
        ${sqlJson(row.requestBody)},
        ${sqlJson(row.responseBody)},
        ${sqlJson(row.requestHeaders)},
        ${sqlJson(row.responseHeaders)},
        ${sqlJson(row.guardrailResults)},
        now(),
        now(),
        false,
        NULL
      )`,
    )
    .join(",\n");

  const sql = `
WITH stale AS (
  DELETE FROM agentcc_request_log
  WHERE request_id LIKE ${sqlString(`${marker}%`)}
    AND organization_id = ${sqlUuid(organizationId)}
  RETURNING id
),
inserted AS (
  INSERT INTO agentcc_request_log (
    id,
    organization_id,
    workspace_id,
    request_id,
    model,
    provider,
    resolved_model,
    latency_ms,
    started_at,
    input_tokens,
    output_tokens,
    total_tokens,
    cost,
    status_code,
    is_stream,
    is_error,
    error_message,
    cache_hit,
    fallback_used,
    guardrail_triggered,
    api_key_id,
    user_id,
    session_id,
    routing_strategy,
    metadata,
    request_body,
    response_body,
    request_headers,
    response_headers,
    guardrail_results,
    created_at,
    updated_at,
    deleted,
    deleted_at
  )
  VALUES ${values}
  RETURNING id
)
SELECT json_build_object(
  'inserted_count', (SELECT count(*) FROM inserted),
  'marker_count', (SELECT count(*) FROM inserted)
);
`;
  const result = await runPostgresJson(sql);
  assert(
    Number(result.inserted_count) === rows.length &&
      Number(result.marker_count) === rows.length,
    `Failed to seed disposable gateway request-log rows: ${JSON.stringify(result)}`,
  );
  return result;
}

async function deleteAgentccRequestLogsDb({ logIds, organizationId }) {
  const sql = `
WITH target AS (
  SELECT unnest(${sqlUuidArray(logIds)}) AS id
),
deleted_rows AS (
  DELETE FROM agentcc_request_log l
  USING target
  WHERE l.id = target.id
    AND l.organization_id = ${sqlUuid(organizationId)}
  RETURNING l.id
)
SELECT json_build_object(
  'deleted_count', (SELECT count(*) FROM deleted_rows),
  'remaining_count', (
    (SELECT count(*) FROM target) - (SELECT count(*) FROM deleted_rows)
  )
);
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

function sqlJson(value) {
  return `${sqlString(JSON.stringify(value ?? null))}::jsonb`;
}
