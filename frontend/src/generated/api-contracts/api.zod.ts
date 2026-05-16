/**
 * Auto-generated from the Django backend OpenAPI schema.
 * To modify these types, update Django serializers/views, regenerate OpenAPI, then run:
 *   yarn contracts:generate
 *
 * TFC Management API - annotation/filter contracts
 * OpenAPI spec version: v1
 */
import * as zod from 'zod';

export const ModelHubAnnotationQueuesListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.'),
  "status": zod.string().optional(),
  "search": zod.string().optional(),
  "include_counts": zod.boolean().optional()
})

export const modelHubAnnotationQueuesListResponseResultsItemNameMax = 255;

export const modelHubAnnotationQueuesListResponseResultsItemAnnotationsRequiredMin = -2147483648;
export const modelHubAnnotationQueuesListResponseResultsItemAnnotationsRequiredMax = 2147483647;

export const modelHubAnnotationQueuesListResponseResultsItemReservationTimeoutMinutesMin = -2147483648;
export const modelHubAnnotationQueuesListResponseResultsItemReservationTimeoutMinutesMax = 2147483647;



export const modelHubAnnotationQueuesListResponseResultsItemLabelsItemOrderMin = -2147483648;
export const modelHubAnnotationQueuesListResponseResultsItemLabelsItemOrderMax = 2147483647;



export const modelHubAnnotationQueuesListResponseResultsItemAnnotatorsItemRoleDefault = `annotator`;

export const modelHubAnnotationQueuesListResponseResultsItemLabelIdsDefault = [];
export const modelHubAnnotationQueuesListResponseResultsItemAnnotatorIdsDefault = [];
export const modelHubAnnotationQueuesListResponseResultsItemAnnotatorRolesDefault = {  };


export const ModelHubAnnotationQueuesListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationQueuesListResponseResultsItemNameMax),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "status": zod.enum(['draft', 'active', 'paused', 'completed']).optional(),
  "assignment_strategy": zod.enum(['manual', 'round_robin', 'load_balanced']).optional(),
  "annotations_required": zod.number().min(modelHubAnnotationQueuesListResponseResultsItemAnnotationsRequiredMin).max(modelHubAnnotationQueuesListResponseResultsItemAnnotationsRequiredMax).optional(),
  "reservation_timeout_minutes": zod.number().min(modelHubAnnotationQueuesListResponseResultsItemReservationTimeoutMinutesMin).max(modelHubAnnotationQueuesListResponseResultsItemReservationTimeoutMinutesMax).optional(),
  "requires_review": zod.boolean().optional(),
  "auto_assign": zod.boolean().optional().describe('When enabled, all queue members can annotate any item without explicit assignment.'),
  "organization": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "dataset": zod.string().uuid().optional(),
  "agent_definition": zod.string().uuid().optional(),
  "is_default": zod.boolean().optional(),
  "labels": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "label_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "type": zod.string().min(1).optional(),
  "required": zod.boolean().optional(),
  "order": zod.number().min(modelHubAnnotationQueuesListResponseResultsItemLabelsItemOrderMin).max(modelHubAnnotationQueuesListResponseResultsItemLabelsItemOrderMax).optional()
})).optional(),
  "annotators": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "user_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional(),
  "role": zod.string().min(1).default(modelHubAnnotationQueuesListResponseResultsItemAnnotatorsItemRoleDefault),
  "roles": zod.string().optional()
})).optional(),
  "label_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesListResponseResultsItemLabelIdsDefault),
  "annotator_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesListResponseResultsItemAnnotatorIdsDefault),
  "annotator_roles": zod.record(zod.string(), zod.object({

}).passthrough()).default(modelHubAnnotationQueuesListResponseResultsItemAnnotatorRolesDefault),
  "label_count": zod.number().optional(),
  "annotator_count": zod.number().optional(),
  "item_count": zod.number().optional(),
  "completed_count": zod.number().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_by_name": zod.string().min(1).optional(),
  "viewer_role": zod.string().optional(),
  "viewer_roles": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


export const modelHubAnnotationQueuesCreateBodyNameMax = 255;

export const modelHubAnnotationQueuesCreateBodyAnnotationsRequiredMin = -2147483648;
export const modelHubAnnotationQueuesCreateBodyAnnotationsRequiredMax = 2147483647;

export const modelHubAnnotationQueuesCreateBodyReservationTimeoutMinutesMin = -2147483648;
export const modelHubAnnotationQueuesCreateBodyReservationTimeoutMinutesMax = 2147483647;

export const modelHubAnnotationQueuesCreateBodyLabelIdsDefault = [];
export const modelHubAnnotationQueuesCreateBodyAnnotatorIdsDefault = [];
export const modelHubAnnotationQueuesCreateBodyAnnotatorRolesDefault = {  };

export const ModelHubAnnotationQueuesCreateBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationQueuesCreateBodyNameMax),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "assignment_strategy": zod.enum(['manual', 'round_robin', 'load_balanced']).optional(),
  "annotations_required": zod.number().min(modelHubAnnotationQueuesCreateBodyAnnotationsRequiredMin).max(modelHubAnnotationQueuesCreateBodyAnnotationsRequiredMax).optional(),
  "reservation_timeout_minutes": zod.number().min(modelHubAnnotationQueuesCreateBodyReservationTimeoutMinutesMin).max(modelHubAnnotationQueuesCreateBodyReservationTimeoutMinutesMax).optional(),
  "requires_review": zod.boolean().optional(),
  "auto_assign": zod.boolean().optional().describe('When enabled, all queue members can annotate any item without explicit assignment.'),
  "label_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesCreateBodyLabelIdsDefault),
  "annotator_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesCreateBodyAnnotatorIdsDefault),
  "annotator_roles": zod.record(zod.string(), zod.object({

}).passthrough()).default(modelHubAnnotationQueuesCreateBodyAnnotatorRolesDefault)
})


/**
 * Find annotation queues for a given source that the current user can annotate.
Includes queues where:
- The source is a queue item AND the user is an annotator in that queue
  (regardless of whether the item is explicitly assigned to them)

Query params:
  - source_type, source_id  (single source)
  - OR sources (JSON array of {source_type, source_id} objects for multi-source lookup)
 */
export const ModelHubAnnotationQueuesForSourceQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.'),
  "source_type": zod.string().optional(),
  "source_id": zod.string().uuid().optional(),
  "sources": zod.string().optional()
})

export const modelHubAnnotationQueuesForSourceResponseStatusDefault = true;

export const ModelHubAnnotationQueuesForSourceResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesForSourceResponseStatusDefault),
  "result": zod.object({

}).passthrough()
})


/**
 * Get or create the default annotation queue for a project, dataset, or agent definition.
Default queues are open to all org members (no annotator restriction).

Body params (one of):
  - project_id
  - dataset_id
  - agent_definition_id
 */
export const ModelHubAnnotationQueuesGetOrCreateDefaultBody = zod.object({
  "project_id": zod.string().uuid().optional(),
  "dataset_id": zod.string().uuid().optional(),
  "agent_definition_id": zod.string().uuid().optional()
})

export const modelHubAnnotationQueuesGetOrCreateDefaultResponseStatusDefault = true;





export const ModelHubAnnotationQueuesGetOrCreateDefaultResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesGetOrCreateDefaultResponseStatusDefault),
  "result": zod.object({
  "queue": zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "status": zod.string().min(1),
  "is_default": zod.boolean()
}),
  "labels": zod.array(zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "type": zod.string().min(1),
  "settings": zod.object({

}).passthrough(),
  "description": zod.string().optional(),
  "allow_notes": zod.boolean(),
  "required": zod.boolean(),
  "order": zod.number()
})),
  "created": zod.boolean(),
  "action": zod.enum(['created', 'restored', 'fetched'])
})
})


export const ModelHubAnnotationQueuesReadParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})

export const modelHubAnnotationQueuesReadResponseNameMax = 255;

export const modelHubAnnotationQueuesReadResponseAnnotationsRequiredMin = -2147483648;
export const modelHubAnnotationQueuesReadResponseAnnotationsRequiredMax = 2147483647;

export const modelHubAnnotationQueuesReadResponseReservationTimeoutMinutesMin = -2147483648;
export const modelHubAnnotationQueuesReadResponseReservationTimeoutMinutesMax = 2147483647;



export const modelHubAnnotationQueuesReadResponseLabelsItemOrderMin = -2147483648;
export const modelHubAnnotationQueuesReadResponseLabelsItemOrderMax = 2147483647;



export const modelHubAnnotationQueuesReadResponseAnnotatorsItemRoleDefault = `annotator`;

export const modelHubAnnotationQueuesReadResponseLabelIdsDefault = [];
export const modelHubAnnotationQueuesReadResponseAnnotatorIdsDefault = [];
export const modelHubAnnotationQueuesReadResponseAnnotatorRolesDefault = {  };


export const ModelHubAnnotationQueuesReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationQueuesReadResponseNameMax),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "status": zod.enum(['draft', 'active', 'paused', 'completed']).optional(),
  "assignment_strategy": zod.enum(['manual', 'round_robin', 'load_balanced']).optional(),
  "annotations_required": zod.number().min(modelHubAnnotationQueuesReadResponseAnnotationsRequiredMin).max(modelHubAnnotationQueuesReadResponseAnnotationsRequiredMax).optional(),
  "reservation_timeout_minutes": zod.number().min(modelHubAnnotationQueuesReadResponseReservationTimeoutMinutesMin).max(modelHubAnnotationQueuesReadResponseReservationTimeoutMinutesMax).optional(),
  "requires_review": zod.boolean().optional(),
  "auto_assign": zod.boolean().optional().describe('When enabled, all queue members can annotate any item without explicit assignment.'),
  "organization": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "dataset": zod.string().uuid().optional(),
  "agent_definition": zod.string().uuid().optional(),
  "is_default": zod.boolean().optional(),
  "labels": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "label_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "type": zod.string().min(1).optional(),
  "required": zod.boolean().optional(),
  "order": zod.number().min(modelHubAnnotationQueuesReadResponseLabelsItemOrderMin).max(modelHubAnnotationQueuesReadResponseLabelsItemOrderMax).optional()
})).optional(),
  "annotators": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "user_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional(),
  "role": zod.string().min(1).default(modelHubAnnotationQueuesReadResponseAnnotatorsItemRoleDefault),
  "roles": zod.string().optional()
})).optional(),
  "label_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesReadResponseLabelIdsDefault),
  "annotator_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesReadResponseAnnotatorIdsDefault),
  "annotator_roles": zod.record(zod.string(), zod.object({

}).passthrough()).default(modelHubAnnotationQueuesReadResponseAnnotatorRolesDefault),
  "label_count": zod.number().optional(),
  "annotator_count": zod.number().optional(),
  "item_count": zod.number().optional(),
  "completed_count": zod.number().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_by_name": zod.string().min(1).optional(),
  "viewer_role": zod.string().optional(),
  "viewer_roles": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Only managers of the queue may update queue settings.
 */
export const ModelHubAnnotationQueuesUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})

export const modelHubAnnotationQueuesUpdateBodyNameMax = 255;

export const modelHubAnnotationQueuesUpdateBodyAnnotationsRequiredMin = -2147483648;
export const modelHubAnnotationQueuesUpdateBodyAnnotationsRequiredMax = 2147483647;

export const modelHubAnnotationQueuesUpdateBodyReservationTimeoutMinutesMin = -2147483648;
export const modelHubAnnotationQueuesUpdateBodyReservationTimeoutMinutesMax = 2147483647;

export const modelHubAnnotationQueuesUpdateBodyLabelIdsDefault = [];
export const modelHubAnnotationQueuesUpdateBodyAnnotatorIdsDefault = [];
export const modelHubAnnotationQueuesUpdateBodyAnnotatorRolesDefault = {  };

export const ModelHubAnnotationQueuesUpdateBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationQueuesUpdateBodyNameMax),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "assignment_strategy": zod.enum(['manual', 'round_robin', 'load_balanced']).optional(),
  "annotations_required": zod.number().min(modelHubAnnotationQueuesUpdateBodyAnnotationsRequiredMin).max(modelHubAnnotationQueuesUpdateBodyAnnotationsRequiredMax).optional(),
  "reservation_timeout_minutes": zod.number().min(modelHubAnnotationQueuesUpdateBodyReservationTimeoutMinutesMin).max(modelHubAnnotationQueuesUpdateBodyReservationTimeoutMinutesMax).optional(),
  "requires_review": zod.boolean().optional(),
  "auto_assign": zod.boolean().optional().describe('When enabled, all queue members can annotate any item without explicit assignment.'),
  "label_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesUpdateBodyLabelIdsDefault),
  "annotator_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesUpdateBodyAnnotatorIdsDefault),
  "annotator_roles": zod.record(zod.string(), zod.object({

}).passthrough()).default(modelHubAnnotationQueuesUpdateBodyAnnotatorRolesDefault)
})

export const modelHubAnnotationQueuesUpdateResponseNameMax = 255;

export const modelHubAnnotationQueuesUpdateResponseAnnotationsRequiredMin = -2147483648;
export const modelHubAnnotationQueuesUpdateResponseAnnotationsRequiredMax = 2147483647;

export const modelHubAnnotationQueuesUpdateResponseReservationTimeoutMinutesMin = -2147483648;
export const modelHubAnnotationQueuesUpdateResponseReservationTimeoutMinutesMax = 2147483647;



export const modelHubAnnotationQueuesUpdateResponseLabelsItemOrderMin = -2147483648;
export const modelHubAnnotationQueuesUpdateResponseLabelsItemOrderMax = 2147483647;



export const modelHubAnnotationQueuesUpdateResponseAnnotatorsItemRoleDefault = `annotator`;

export const modelHubAnnotationQueuesUpdateResponseLabelIdsDefault = [];
export const modelHubAnnotationQueuesUpdateResponseAnnotatorIdsDefault = [];
export const modelHubAnnotationQueuesUpdateResponseAnnotatorRolesDefault = {  };


export const ModelHubAnnotationQueuesUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationQueuesUpdateResponseNameMax),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "status": zod.enum(['draft', 'active', 'paused', 'completed']).optional(),
  "assignment_strategy": zod.enum(['manual', 'round_robin', 'load_balanced']).optional(),
  "annotations_required": zod.number().min(modelHubAnnotationQueuesUpdateResponseAnnotationsRequiredMin).max(modelHubAnnotationQueuesUpdateResponseAnnotationsRequiredMax).optional(),
  "reservation_timeout_minutes": zod.number().min(modelHubAnnotationQueuesUpdateResponseReservationTimeoutMinutesMin).max(modelHubAnnotationQueuesUpdateResponseReservationTimeoutMinutesMax).optional(),
  "requires_review": zod.boolean().optional(),
  "auto_assign": zod.boolean().optional().describe('When enabled, all queue members can annotate any item without explicit assignment.'),
  "organization": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "dataset": zod.string().uuid().optional(),
  "agent_definition": zod.string().uuid().optional(),
  "is_default": zod.boolean().optional(),
  "labels": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "label_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "type": zod.string().min(1).optional(),
  "required": zod.boolean().optional(),
  "order": zod.number().min(modelHubAnnotationQueuesUpdateResponseLabelsItemOrderMin).max(modelHubAnnotationQueuesUpdateResponseLabelsItemOrderMax).optional()
})).optional(),
  "annotators": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "user_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional(),
  "role": zod.string().min(1).default(modelHubAnnotationQueuesUpdateResponseAnnotatorsItemRoleDefault),
  "roles": zod.string().optional()
})).optional(),
  "label_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesUpdateResponseLabelIdsDefault),
  "annotator_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesUpdateResponseAnnotatorIdsDefault),
  "annotator_roles": zod.record(zod.string(), zod.object({

}).passthrough()).default(modelHubAnnotationQueuesUpdateResponseAnnotatorRolesDefault),
  "label_count": zod.number().optional(),
  "annotator_count": zod.number().optional(),
  "item_count": zod.number().optional(),
  "completed_count": zod.number().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_by_name": zod.string().min(1).optional(),
  "viewer_role": zod.string().optional(),
  "viewer_roles": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


export const ModelHubAnnotationQueuesPartialUpdateParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})

export const modelHubAnnotationQueuesPartialUpdateBodyNameMax = 255;

export const modelHubAnnotationQueuesPartialUpdateBodyAnnotationsRequiredMin = -2147483648;
export const modelHubAnnotationQueuesPartialUpdateBodyAnnotationsRequiredMax = 2147483647;

export const modelHubAnnotationQueuesPartialUpdateBodyReservationTimeoutMinutesMin = -2147483648;
export const modelHubAnnotationQueuesPartialUpdateBodyReservationTimeoutMinutesMax = 2147483647;

export const modelHubAnnotationQueuesPartialUpdateBodyLabelIdsDefault = [];
export const modelHubAnnotationQueuesPartialUpdateBodyAnnotatorIdsDefault = [];
export const modelHubAnnotationQueuesPartialUpdateBodyAnnotatorRolesDefault = {  };

export const ModelHubAnnotationQueuesPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationQueuesPartialUpdateBodyNameMax),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "assignment_strategy": zod.enum(['manual', 'round_robin', 'load_balanced']).optional(),
  "annotations_required": zod.number().min(modelHubAnnotationQueuesPartialUpdateBodyAnnotationsRequiredMin).max(modelHubAnnotationQueuesPartialUpdateBodyAnnotationsRequiredMax).optional(),
  "reservation_timeout_minutes": zod.number().min(modelHubAnnotationQueuesPartialUpdateBodyReservationTimeoutMinutesMin).max(modelHubAnnotationQueuesPartialUpdateBodyReservationTimeoutMinutesMax).optional(),
  "requires_review": zod.boolean().optional(),
  "auto_assign": zod.boolean().optional().describe('When enabled, all queue members can annotate any item without explicit assignment.'),
  "label_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesPartialUpdateBodyLabelIdsDefault),
  "annotator_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesPartialUpdateBodyAnnotatorIdsDefault),
  "annotator_roles": zod.record(zod.string(), zod.object({

}).passthrough()).default(modelHubAnnotationQueuesPartialUpdateBodyAnnotatorRolesDefault)
})

export const modelHubAnnotationQueuesPartialUpdateResponseNameMax = 255;

export const modelHubAnnotationQueuesPartialUpdateResponseAnnotationsRequiredMin = -2147483648;
export const modelHubAnnotationQueuesPartialUpdateResponseAnnotationsRequiredMax = 2147483647;

export const modelHubAnnotationQueuesPartialUpdateResponseReservationTimeoutMinutesMin = -2147483648;
export const modelHubAnnotationQueuesPartialUpdateResponseReservationTimeoutMinutesMax = 2147483647;



export const modelHubAnnotationQueuesPartialUpdateResponseLabelsItemOrderMin = -2147483648;
export const modelHubAnnotationQueuesPartialUpdateResponseLabelsItemOrderMax = 2147483647;



export const modelHubAnnotationQueuesPartialUpdateResponseAnnotatorsItemRoleDefault = `annotator`;

export const modelHubAnnotationQueuesPartialUpdateResponseLabelIdsDefault = [];
export const modelHubAnnotationQueuesPartialUpdateResponseAnnotatorIdsDefault = [];
export const modelHubAnnotationQueuesPartialUpdateResponseAnnotatorRolesDefault = {  };


export const ModelHubAnnotationQueuesPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationQueuesPartialUpdateResponseNameMax),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "status": zod.enum(['draft', 'active', 'paused', 'completed']).optional(),
  "assignment_strategy": zod.enum(['manual', 'round_robin', 'load_balanced']).optional(),
  "annotations_required": zod.number().min(modelHubAnnotationQueuesPartialUpdateResponseAnnotationsRequiredMin).max(modelHubAnnotationQueuesPartialUpdateResponseAnnotationsRequiredMax).optional(),
  "reservation_timeout_minutes": zod.number().min(modelHubAnnotationQueuesPartialUpdateResponseReservationTimeoutMinutesMin).max(modelHubAnnotationQueuesPartialUpdateResponseReservationTimeoutMinutesMax).optional(),
  "requires_review": zod.boolean().optional(),
  "auto_assign": zod.boolean().optional().describe('When enabled, all queue members can annotate any item without explicit assignment.'),
  "organization": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "dataset": zod.string().uuid().optional(),
  "agent_definition": zod.string().uuid().optional(),
  "is_default": zod.boolean().optional(),
  "labels": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "label_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "type": zod.string().min(1).optional(),
  "required": zod.boolean().optional(),
  "order": zod.number().min(modelHubAnnotationQueuesPartialUpdateResponseLabelsItemOrderMin).max(modelHubAnnotationQueuesPartialUpdateResponseLabelsItemOrderMax).optional()
})).optional(),
  "annotators": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "user_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional(),
  "role": zod.string().min(1).default(modelHubAnnotationQueuesPartialUpdateResponseAnnotatorsItemRoleDefault),
  "roles": zod.string().optional()
})).optional(),
  "label_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesPartialUpdateResponseLabelIdsDefault),
  "annotator_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesPartialUpdateResponseAnnotatorIdsDefault),
  "annotator_roles": zod.record(zod.string(), zod.object({

}).passthrough()).default(modelHubAnnotationQueuesPartialUpdateResponseAnnotatorRolesDefault),
  "label_count": zod.number().optional(),
  "annotator_count": zod.number().optional(),
  "item_count": zod.number().optional(),
  "completed_count": zod.number().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_by_name": zod.string().min(1).optional(),
  "viewer_role": zod.string().optional(),
  "viewer_roles": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * ``BaseModel.delete()`` flips ``deleted=True`` instead of removing
the row. Attached automation rules go dormant (the scheduler
filters ``queue__deleted=False``), items stay invisible but
recoverable, label bindings preserved.

For truly destructive removal, use the ``hard-delete`` action
below.
 * @summary Archive a queue (soft delete).
 */
export const ModelHubAnnotationQueuesDeleteParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})


/**
 * Add a label to an annotation queue.
Labels apply to all sources in the queue's project (for default queues).
Queue items are created lazily when someone actually annotates.
 */
export const ModelHubAnnotationQueuesAddLabelParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})

export const modelHubAnnotationQueuesAddLabelBodyRequiredDefault = true;

export const ModelHubAnnotationQueuesAddLabelBody = zod.object({
  "label_id": zod.string().uuid(),
  "required": zod.boolean().default(modelHubAnnotationQueuesAddLabelBodyRequiredDefault)
})

export const modelHubAnnotationQueuesAddLabelResponseStatusDefault = true;




export const ModelHubAnnotationQueuesAddLabelResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesAddLabelResponseStatusDefault),
  "result": zod.object({
  "label": zod.object({
  "id": zod.string().uuid(),
  "name": zod.string().min(1),
  "type": zod.string().min(1),
  "settings": zod.object({

}).passthrough(),
  "description": zod.string().optional(),
  "allow_notes": zod.boolean(),
  "required": zod.boolean(),
  "order": zod.number()
}),
  "created": zod.boolean(),
  "reopened_items": zod.number(),
  "queue_status": zod.string().min(1)
})
})


/**
 * Calculate inter-annotator agreement metrics.
 */
export const ModelHubAnnotationQueuesAgreementParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})

export const modelHubAnnotationQueuesAgreementResponseStatusDefault = true;

export const ModelHubAnnotationQueuesAgreementResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesAgreementResponseStatusDefault),
  "result": zod.object({

}).passthrough()
})


/**
 * Queue analytics: throughput, annotator performance, label distribution.
 */
export const ModelHubAnnotationQueuesAnalyticsParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})

export const modelHubAnnotationQueuesAnalyticsResponseStatusDefault = true;

export const ModelHubAnnotationQueuesAnalyticsResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesAnalyticsResponseStatusDefault),
  "result": zod.object({

}).passthrough()
})


/**
 * Return source/label/attribute fields available for dataset export.
 */
export const ModelHubAnnotationQueuesExportFieldsParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})

export const modelHubAnnotationQueuesExportFieldsResponseStatusDefault = true;

export const ModelHubAnnotationQueuesExportFieldsResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesExportFieldsResponseStatusDefault),
  "result": zod.object({

}).passthrough()
})


/**
 * Export queue items to a dataset using a user-editable column mapping.
 */
export const ModelHubAnnotationQueuesExportToDatasetParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})

export const modelHubAnnotationQueuesExportToDatasetBodyStatusFilterDefault = `completed`;
export const modelHubAnnotationQueuesExportToDatasetBodyColumnMappingItemEnabledDefault = true;
export const modelHubAnnotationQueuesExportToDatasetBodyColumnMappingItemDefault = { enabled: true };
export const modelHubAnnotationQueuesExportToDatasetBodyColumnMappingDefault = [];

export const ModelHubAnnotationQueuesExportToDatasetBody = zod.object({
  "dataset_id": zod.string().uuid().optional(),
  "dataset_name": zod.string().optional(),
  "status_filter": zod.string().default(modelHubAnnotationQueuesExportToDatasetBodyStatusFilterDefault),
  "column_mapping": zod.array(zod.object({
  "field": zod.string().optional(),
  "id": zod.string().optional(),
  "column": zod.string().optional(),
  "enabled": zod.boolean().default(modelHubAnnotationQueuesExportToDatasetBodyColumnMappingItemEnabledDefault)
}).default(modelHubAnnotationQueuesExportToDatasetBodyColumnMappingItemDefault)).default(modelHubAnnotationQueuesExportToDatasetBodyColumnMappingDefault)
})

export const modelHubAnnotationQueuesExportToDatasetResponseStatusDefault = true;



export const ModelHubAnnotationQueuesExportToDatasetResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesExportToDatasetResponseStatusDefault),
  "result": zod.object({
  "dataset_id": zod.string().uuid(),
  "dataset_name": zod.string().min(1),
  "rows_created": zod.number(),
  "columns": zod.array(zod.string().min(1))
})
})


/**
 * Export all items with their annotations.
 */
export const ModelHubAnnotationQueuesExportAnnotationsParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})

export const ModelHubAnnotationQueuesExportAnnotationsQueryParams = zod.object({
  "export_format": zod.enum(['json', 'csv']).optional(),
  "format": zod.enum(['json', 'csv']).optional(),
  "status": zod.string().optional()
})

export const modelHubAnnotationQueuesExportAnnotationsResponseStatusDefault = true;

export const ModelHubAnnotationQueuesExportAnnotationsResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesExportAnnotationsResponseStatusDefault),
  "result": zod.array(zod.object({

}).passthrough())
})


/**
 * Hard delete cascades through the FK graph (rules, items,
assignments, scores) via ``on_delete=CASCADE``. There is no
recovery — callers must pass ``force=true`` AND the queue's
exact name as ``confirm_name`` so the action can't fire from
a typo'd request.
 * @summary Permanently remove a queue + everything attached.
 */
export const ModelHubAnnotationQueuesHardDeleteParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})




export const ModelHubAnnotationQueuesHardDeleteBody = zod.object({
  "force": zod.boolean(),
  "confirm_name": zod.string().min(1)
})

export const modelHubAnnotationQueuesHardDeleteResponseStatusDefault = true;

export const ModelHubAnnotationQueuesHardDeleteResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesHardDeleteResponseStatusDefault),
  "result": zod.object({
  "deleted": zod.boolean(),
  "hard_deleted": zod.boolean().optional(),
  "archived": zod.boolean().optional(),
  "queue_id": zod.string().uuid()
})
})


export const ModelHubAnnotationQueuesProgressParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})

export const modelHubAnnotationQueuesProgressResponseStatusDefault = true;


export const ModelHubAnnotationQueuesProgressResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesProgressResponseStatusDefault),
  "result": zod.object({
  "total": zod.number(),
  "pending": zod.number(),
  "in_progress": zod.number(),
  "in_review": zod.number(),
  "completed": zod.number(),
  "skipped": zod.number(),
  "progress_pct": zod.number(),
  "annotator_stats": zod.array(zod.object({
  "user_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "completed": zod.number(),
  "pending": zod.number(),
  "in_progress": zod.number(),
  "in_review": zod.number(),
  "annotations_count": zod.number()
})),
  "user_progress": zod.object({
  "total": zod.number(),
  "completed": zod.number(),
  "pending": zod.number(),
  "in_progress": zod.number(),
  "in_review": zod.number(),
  "skipped": zod.number(),
  "progress_pct": zod.number()
})
})
})


/**
 * Remove a label from an annotation queue.
 */
export const ModelHubAnnotationQueuesRemoveLabelParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})

export const modelHubAnnotationQueuesRemoveLabelBodyRequiredDefault = true;

export const ModelHubAnnotationQueuesRemoveLabelBody = zod.object({
  "label_id": zod.string().uuid(),
  "required": zod.boolean().default(modelHubAnnotationQueuesRemoveLabelBodyRequiredDefault)
})

export const modelHubAnnotationQueuesRemoveLabelResponseStatusDefault = true;

export const ModelHubAnnotationQueuesRemoveLabelResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesRemoveLabelResponseStatusDefault),
  "result": zod.record(zod.string(), zod.boolean())
})


export const ModelHubAnnotationQueuesRestoreParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})

export const modelHubAnnotationQueuesRestoreResponseStatusDefault = true;
export const modelHubAnnotationQueuesRestoreResponseResultNameMax = 255;

export const modelHubAnnotationQueuesRestoreResponseResultAnnotationsRequiredMin = -2147483648;
export const modelHubAnnotationQueuesRestoreResponseResultAnnotationsRequiredMax = 2147483647;

export const modelHubAnnotationQueuesRestoreResponseResultReservationTimeoutMinutesMin = -2147483648;
export const modelHubAnnotationQueuesRestoreResponseResultReservationTimeoutMinutesMax = 2147483647;



export const modelHubAnnotationQueuesRestoreResponseResultLabelsItemOrderMin = -2147483648;
export const modelHubAnnotationQueuesRestoreResponseResultLabelsItemOrderMax = 2147483647;



export const modelHubAnnotationQueuesRestoreResponseResultAnnotatorsItemRoleDefault = `annotator`;

export const modelHubAnnotationQueuesRestoreResponseResultLabelIdsDefault = [];
export const modelHubAnnotationQueuesRestoreResponseResultAnnotatorIdsDefault = [];
export const modelHubAnnotationQueuesRestoreResponseResultAnnotatorRolesDefault = {  };


export const ModelHubAnnotationQueuesRestoreResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesRestoreResponseStatusDefault),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationQueuesRestoreResponseResultNameMax),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "status": zod.enum(['draft', 'active', 'paused', 'completed']).optional(),
  "assignment_strategy": zod.enum(['manual', 'round_robin', 'load_balanced']).optional(),
  "annotations_required": zod.number().min(modelHubAnnotationQueuesRestoreResponseResultAnnotationsRequiredMin).max(modelHubAnnotationQueuesRestoreResponseResultAnnotationsRequiredMax).optional(),
  "reservation_timeout_minutes": zod.number().min(modelHubAnnotationQueuesRestoreResponseResultReservationTimeoutMinutesMin).max(modelHubAnnotationQueuesRestoreResponseResultReservationTimeoutMinutesMax).optional(),
  "requires_review": zod.boolean().optional(),
  "auto_assign": zod.boolean().optional().describe('When enabled, all queue members can annotate any item without explicit assignment.'),
  "organization": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "dataset": zod.string().uuid().optional(),
  "agent_definition": zod.string().uuid().optional(),
  "is_default": zod.boolean().optional(),
  "labels": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "label_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "type": zod.string().min(1).optional(),
  "required": zod.boolean().optional(),
  "order": zod.number().min(modelHubAnnotationQueuesRestoreResponseResultLabelsItemOrderMin).max(modelHubAnnotationQueuesRestoreResponseResultLabelsItemOrderMax).optional()
})).optional(),
  "annotators": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "user_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional(),
  "role": zod.string().min(1).default(modelHubAnnotationQueuesRestoreResponseResultAnnotatorsItemRoleDefault),
  "roles": zod.string().optional()
})).optional(),
  "label_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesRestoreResponseResultLabelIdsDefault),
  "annotator_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesRestoreResponseResultAnnotatorIdsDefault),
  "annotator_roles": zod.record(zod.string(), zod.object({

}).passthrough()).default(modelHubAnnotationQueuesRestoreResponseResultAnnotatorRolesDefault),
  "label_count": zod.number().optional(),
  "annotator_count": zod.number().optional(),
  "item_count": zod.number().optional(),
  "completed_count": zod.number().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_by_name": zod.string().min(1).optional(),
  "viewer_role": zod.string().optional(),
  "viewer_roles": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})
})


export const ModelHubAnnotationQueuesUpdateStatusParams = zod.object({
  "id": zod.string().uuid().describe('A UUID string identifying this annotation queue.')
})

export const ModelHubAnnotationQueuesUpdateStatusBody = zod.object({
  "status": zod.enum(['draft', 'active', 'paused', 'completed'])
})

export const modelHubAnnotationQueuesUpdateStatusResponseStatusDefault = true;
export const modelHubAnnotationQueuesUpdateStatusResponseResultNameMax = 255;

export const modelHubAnnotationQueuesUpdateStatusResponseResultAnnotationsRequiredMin = -2147483648;
export const modelHubAnnotationQueuesUpdateStatusResponseResultAnnotationsRequiredMax = 2147483647;

export const modelHubAnnotationQueuesUpdateStatusResponseResultReservationTimeoutMinutesMin = -2147483648;
export const modelHubAnnotationQueuesUpdateStatusResponseResultReservationTimeoutMinutesMax = 2147483647;



export const modelHubAnnotationQueuesUpdateStatusResponseResultLabelsItemOrderMin = -2147483648;
export const modelHubAnnotationQueuesUpdateStatusResponseResultLabelsItemOrderMax = 2147483647;



export const modelHubAnnotationQueuesUpdateStatusResponseResultAnnotatorsItemRoleDefault = `annotator`;

export const modelHubAnnotationQueuesUpdateStatusResponseResultLabelIdsDefault = [];
export const modelHubAnnotationQueuesUpdateStatusResponseResultAnnotatorIdsDefault = [];
export const modelHubAnnotationQueuesUpdateStatusResponseResultAnnotatorRolesDefault = {  };


export const ModelHubAnnotationQueuesUpdateStatusResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesUpdateStatusResponseStatusDefault),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationQueuesUpdateStatusResponseResultNameMax),
  "description": zod.string().optional(),
  "instructions": zod.string().optional(),
  "status": zod.enum(['draft', 'active', 'paused', 'completed']).optional(),
  "assignment_strategy": zod.enum(['manual', 'round_robin', 'load_balanced']).optional(),
  "annotations_required": zod.number().min(modelHubAnnotationQueuesUpdateStatusResponseResultAnnotationsRequiredMin).max(modelHubAnnotationQueuesUpdateStatusResponseResultAnnotationsRequiredMax).optional(),
  "reservation_timeout_minutes": zod.number().min(modelHubAnnotationQueuesUpdateStatusResponseResultReservationTimeoutMinutesMin).max(modelHubAnnotationQueuesUpdateStatusResponseResultReservationTimeoutMinutesMax).optional(),
  "requires_review": zod.boolean().optional(),
  "auto_assign": zod.boolean().optional().describe('When enabled, all queue members can annotate any item without explicit assignment.'),
  "organization": zod.string().uuid().optional(),
  "project": zod.string().uuid().optional(),
  "dataset": zod.string().uuid().optional(),
  "agent_definition": zod.string().uuid().optional(),
  "is_default": zod.boolean().optional(),
  "labels": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "label_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "type": zod.string().min(1).optional(),
  "required": zod.boolean().optional(),
  "order": zod.number().min(modelHubAnnotationQueuesUpdateStatusResponseResultLabelsItemOrderMin).max(modelHubAnnotationQueuesUpdateStatusResponseResultLabelsItemOrderMax).optional()
})).optional(),
  "annotators": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "user_id": zod.string().uuid(),
  "name": zod.string().min(1).optional(),
  "email": zod.string().email().min(1).optional(),
  "role": zod.string().min(1).default(modelHubAnnotationQueuesUpdateStatusResponseResultAnnotatorsItemRoleDefault),
  "roles": zod.string().optional()
})).optional(),
  "label_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesUpdateStatusResponseResultLabelIdsDefault),
  "annotator_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesUpdateStatusResponseResultAnnotatorIdsDefault),
  "annotator_roles": zod.record(zod.string(), zod.object({

}).passthrough()).default(modelHubAnnotationQueuesUpdateStatusResponseResultAnnotatorRolesDefault),
  "label_count": zod.number().optional(),
  "annotator_count": zod.number().optional(),
  "item_count": zod.number().optional(),
  "completed_count": zod.number().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_by_name": zod.string().min(1).optional(),
  "viewer_role": zod.string().optional(),
  "viewer_roles": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})
})


export const ModelHubAnnotationQueuesAutomationRulesListParams = zod.object({
  "queue_id": zod.string()
})

export const ModelHubAnnotationQueuesAutomationRulesListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const modelHubAnnotationQueuesAutomationRulesListResponseResultsItemNameMax = 255;




export const ModelHubAnnotationQueuesAutomationRulesListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationQueuesAutomationRulesListResponseResultsItemNameMax),
  "queue": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "conditions": zod.object({

}).passthrough().optional(),
  "enabled": zod.boolean().optional(),
  "trigger_frequency": zod.enum(['manual', 'hourly', 'daily', 'weekly', 'monthly']).optional(),
  "organization": zod.string().uuid().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_by_name": zod.string().min(1).optional(),
  "last_triggered_at": zod.string().datetime({"offset":true}).optional(),
  "trigger_count": zod.number().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


export const ModelHubAnnotationQueuesAutomationRulesCreateParams = zod.object({
  "queue_id": zod.string()
})

export const modelHubAnnotationQueuesAutomationRulesCreateBodyNameMax = 255;



export const ModelHubAnnotationQueuesAutomationRulesCreateBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationQueuesAutomationRulesCreateBodyNameMax),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "conditions": zod.object({

}).passthrough().optional(),
  "enabled": zod.boolean().optional(),
  "trigger_frequency": zod.enum(['manual', 'hourly', 'daily', 'weekly', 'monthly']).optional()
})


export const ModelHubAnnotationQueuesAutomationRulesReadParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this automation rule.')
})

export const modelHubAnnotationQueuesAutomationRulesReadResponseNameMax = 255;




export const ModelHubAnnotationQueuesAutomationRulesReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationQueuesAutomationRulesReadResponseNameMax),
  "queue": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "conditions": zod.object({

}).passthrough().optional(),
  "enabled": zod.boolean().optional(),
  "trigger_frequency": zod.enum(['manual', 'hourly', 'daily', 'weekly', 'monthly']).optional(),
  "organization": zod.string().uuid().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_by_name": zod.string().min(1).optional(),
  "last_triggered_at": zod.string().datetime({"offset":true}).optional(),
  "trigger_count": zod.number().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


export const ModelHubAnnotationQueuesAutomationRulesUpdateParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this automation rule.')
})

export const modelHubAnnotationQueuesAutomationRulesUpdateBodyNameMax = 255;



export const ModelHubAnnotationQueuesAutomationRulesUpdateBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationQueuesAutomationRulesUpdateBodyNameMax),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "conditions": zod.object({

}).passthrough().optional(),
  "enabled": zod.boolean().optional(),
  "trigger_frequency": zod.enum(['manual', 'hourly', 'daily', 'weekly', 'monthly']).optional()
})

export const modelHubAnnotationQueuesAutomationRulesUpdateResponseNameMax = 255;




export const ModelHubAnnotationQueuesAutomationRulesUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationQueuesAutomationRulesUpdateResponseNameMax),
  "queue": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "conditions": zod.object({

}).passthrough().optional(),
  "enabled": zod.boolean().optional(),
  "trigger_frequency": zod.enum(['manual', 'hourly', 'daily', 'weekly', 'monthly']).optional(),
  "organization": zod.string().uuid().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_by_name": zod.string().min(1).optional(),
  "last_triggered_at": zod.string().datetime({"offset":true}).optional(),
  "trigger_count": zod.number().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


export const ModelHubAnnotationQueuesAutomationRulesPartialUpdateParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this automation rule.')
})

export const modelHubAnnotationQueuesAutomationRulesPartialUpdateBodyNameMax = 255;



export const ModelHubAnnotationQueuesAutomationRulesPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationQueuesAutomationRulesPartialUpdateBodyNameMax),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "conditions": zod.object({

}).passthrough().optional(),
  "enabled": zod.boolean().optional(),
  "trigger_frequency": zod.enum(['manual', 'hourly', 'daily', 'weekly', 'monthly']).optional()
})

export const modelHubAnnotationQueuesAutomationRulesPartialUpdateResponseNameMax = 255;




export const ModelHubAnnotationQueuesAutomationRulesPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationQueuesAutomationRulesPartialUpdateResponseNameMax),
  "queue": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "conditions": zod.object({

}).passthrough().optional(),
  "enabled": zod.boolean().optional(),
  "trigger_frequency": zod.enum(['manual', 'hourly', 'daily', 'weekly', 'monthly']).optional(),
  "organization": zod.string().uuid().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_by_name": zod.string().min(1).optional(),
  "last_triggered_at": zod.string().datetime({"offset":true}).optional(),
  "trigger_count": zod.number().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


export const ModelHubAnnotationQueuesAutomationRulesDeleteParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this automation rule.')
})


/**
 * Small runs (filter resolves to ≤ ``RULE_RUN_SYNC_THRESHOLD``) finish
in the HTTP request and return 200 with the result — fast feedback
for the common case. Large runs (mostly first-ever runs on backlogs
or rules with wide filters) hand the work to a Temporal activity and
return 202 immediately. The activity emails creator + queue managers
on completion.

The peek is a cheap dry-run (``[:cap+1]`` LIMIT, no COUNT(*)) — sub-
100ms even on 10M+ row trace tables — so this branch costs little
even when it ends up taking the sync path.
 * @summary Trigger a manual rule run with a sync-or-async branch.
 */
export const ModelHubAnnotationQueuesAutomationRulesEvaluateParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this automation rule.')
})

export const modelHubAnnotationQueuesAutomationRulesEvaluateResponseStatusDefault = true;

export const ModelHubAnnotationQueuesAutomationRulesEvaluateResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesAutomationRulesEvaluateResponseStatusDefault),
  "result": zod.object({

}).passthrough()
})


/**
 * Preview how many items match a rule (dry run).
 */
export const ModelHubAnnotationQueuesAutomationRulesPreviewParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this automation rule.')
})

export const modelHubAnnotationQueuesAutomationRulesPreviewResponseStatusDefault = true;

export const ModelHubAnnotationQueuesAutomationRulesPreviewResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesAutomationRulesPreviewResponseStatusDefault),
  "result": zod.object({

}).passthrough()
})


export const ModelHubAnnotationQueuesItemsListParams = zod.object({
  "queue_id": zod.string()
})

export const ModelHubAnnotationQueuesItemsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.'),
  "status": zod.string().optional(),
  "source_type": zod.string().optional(),
  "assigned_to": zod.string().optional(),
  "review_status": zod.string().optional(),
  "ordering": zod.enum(['created_at', '-created_at']).optional()
})


export const modelHubAnnotationQueuesItemsListResponseResultsItemPriorityMin = -2147483648;
export const modelHubAnnotationQueuesItemsListResponseResultsItemPriorityMax = 2147483647;

export const modelHubAnnotationQueuesItemsListResponseResultsItemOrderMin = -2147483648;
export const modelHubAnnotationQueuesItemsListResponseResultsItemOrderMax = 2147483647;



export const modelHubAnnotationQueuesItemsListResponseResultsItemReviewStatusMax = 20;




export const ModelHubAnnotationQueuesItemsListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "queue": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().min(1).optional(),
  "status": zod.enum(['pending', 'in_progress', 'completed', 'skipped']).optional(),
  "workflow_status": zod.string().optional(),
  "workflow_status_label": zod.string().optional(),
  "priority": zod.number().min(modelHubAnnotationQueuesItemsListResponseResultsItemPriorityMin).max(modelHubAnnotationQueuesItemsListResponseResultsItemPriorityMax).optional(),
  "order": zod.number().min(modelHubAnnotationQueuesItemsListResponseResultsItemOrderMin).max(modelHubAnnotationQueuesItemsListResponseResultsItemOrderMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "assigned_to": zod.string().uuid().optional(),
  "assigned_to_name": zod.string().min(1).optional(),
  "assigned_users": zod.string().optional(),
  "reserved_by": zod.string().uuid().optional(),
  "reserved_by_name": zod.string().min(1).optional(),
  "reservation_expires_at": zod.string().datetime({"offset":true}).optional(),
  "review_status": zod.string().max(modelHubAnnotationQueuesItemsListResponseResultsItemReviewStatusMax).optional(),
  "reviewed_by": zod.string().uuid().optional(),
  "reviewed_by_name": zod.string().min(1).optional(),
  "reviewed_at": zod.string().datetime({"offset":true}).optional(),
  "review_notes": zod.string().optional(),
  "source_preview": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


export const ModelHubAnnotationQueuesItemsCreateParams = zod.object({
  "queue_id": zod.string()
})


export const modelHubAnnotationQueuesItemsCreateBodyPriorityMin = -2147483648;
export const modelHubAnnotationQueuesItemsCreateBodyPriorityMax = 2147483647;

export const modelHubAnnotationQueuesItemsCreateBodyOrderMin = -2147483648;
export const modelHubAnnotationQueuesItemsCreateBodyOrderMax = 2147483647;

export const modelHubAnnotationQueuesItemsCreateBodyReviewStatusMax = 20;



export const ModelHubAnnotationQueuesItemsCreateBody = zod.object({
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().min(1).optional(),
  "status": zod.enum(['pending', 'in_progress', 'completed', 'skipped']).optional(),
  "priority": zod.number().min(modelHubAnnotationQueuesItemsCreateBodyPriorityMin).max(modelHubAnnotationQueuesItemsCreateBodyPriorityMax).optional(),
  "order": zod.number().min(modelHubAnnotationQueuesItemsCreateBodyOrderMin).max(modelHubAnnotationQueuesItemsCreateBodyOrderMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "assigned_to": zod.string().uuid().optional(),
  "reserved_by": zod.string().uuid().optional(),
  "reservation_expires_at": zod.string().datetime({"offset":true}).optional(),
  "review_status": zod.string().max(modelHubAnnotationQueuesItemsCreateBodyReviewStatusMax).optional(),
  "reviewed_by": zod.string().uuid().optional(),
  "reviewed_at": zod.string().datetime({"offset":true}).optional(),
  "review_notes": zod.string().optional()
})


export const ModelHubAnnotationQueuesItemsAddItemsParams = zod.object({
  "queue_id": zod.string()
})

export const modelHubAnnotationQueuesItemsAddItemsBodySelectionFilterDefault = [];
export const modelHubAnnotationQueuesItemsAddItemsBodySelectionExcludeIdsDefault = [];
export const modelHubAnnotationQueuesItemsAddItemsBodySelectionRemoveSimulationCallsDefault = false;
export const modelHubAnnotationQueuesItemsAddItemsBodySelectionIsVoiceCallDefault = false;

export const ModelHubAnnotationQueuesItemsAddItemsBody = zod.object({
  "items": zod.array(zod.record(zod.string(), zod.string())).optional(),
  "selection": zod.object({
  "mode": zod.enum(['filter']),
  "source_type": zod.enum(['call_execution', 'observation_span', 'trace', 'trace_session']),
  "project_id": zod.string().uuid(),
  "filter": zod.array(zod.record(zod.string(), zod.string())).default(modelHubAnnotationQueuesItemsAddItemsBodySelectionFilterDefault),
  "exclude_ids": zod.array(zod.string().min(1)).default(modelHubAnnotationQueuesItemsAddItemsBodySelectionExcludeIdsDefault),
  "remove_simulation_calls": zod.boolean().default(modelHubAnnotationQueuesItemsAddItemsBodySelectionRemoveSimulationCallsDefault),
  "is_voice_call": zod.boolean().default(modelHubAnnotationQueuesItemsAddItemsBodySelectionIsVoiceCallDefault)
}).optional()
})

export const modelHubAnnotationQueuesItemsAddItemsResponseStatusDefault = true;



export const ModelHubAnnotationQueuesItemsAddItemsResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsAddItemsResponseStatusDefault),
  "result": zod.object({
  "added": zod.number(),
  "duplicates": zod.number(),
  "errors": zod.array(zod.string().min(1)),
  "queue_status": zod.string().min(1),
  "total_matching": zod.number().optional()
})
})


/**
 * Assign items to one or more annotators.
 */
export const ModelHubAnnotationQueuesItemsAssignItemsParams = zod.object({
  "queue_id": zod.string()
})


export const modelHubAnnotationQueuesItemsAssignItemsBodyUserIdsDefault = [];
export const modelHubAnnotationQueuesItemsAssignItemsBodyActionDefault = `add`;

export const ModelHubAnnotationQueuesItemsAssignItemsBody = zod.object({
  "item_ids": zod.array(zod.string().uuid()).min(1),
  "user_ids": zod.array(zod.string().uuid()).default(modelHubAnnotationQueuesItemsAssignItemsBodyUserIdsDefault),
  "user_id": zod.string().uuid().optional(),
  "action": zod.enum(['add', 'set', 'remove']).default(modelHubAnnotationQueuesItemsAssignItemsBodyActionDefault)
})

export const modelHubAnnotationQueuesItemsAssignItemsResponseStatusDefault = true;

export const ModelHubAnnotationQueuesItemsAssignItemsResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsAssignItemsResponseStatusDefault),
  "result": zod.record(zod.string(), zod.number())
})


export const ModelHubAnnotationQueuesItemsBulkRemoveParams = zod.object({
  "queue_id": zod.string()
})




export const ModelHubAnnotationQueuesItemsBulkRemoveBody = zod.object({
  "item_ids": zod.array(zod.string().uuid()).min(1)
})

export const modelHubAnnotationQueuesItemsBulkRemoveResponseStatusDefault = true;

export const ModelHubAnnotationQueuesItemsBulkRemoveResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsBulkRemoveResponseStatusDefault),
  "result": zod.record(zod.string(), zod.number())
})


/**
 * Query params:
  exclude: comma-separated item IDs to skip
  before:  item ID — returns the item immediately before this one in order
  review_status: optional review status filter (for reviewer queues)
  exclude_review_status: optional review status to omit (for annotator queues)
  include_completed: when true, navigation can visit completed items too
 * @summary Get the next or previous item in the queue.
 */
export const ModelHubAnnotationQueuesItemsNextItemParams = zod.object({
  "queue_id": zod.string()
})

export const ModelHubAnnotationQueuesItemsNextItemQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.'),
  "exclude": zod.string().optional(),
  "before": zod.string().uuid().optional(),
  "review_status": zod.string().optional(),
  "exclude_review_status": zod.string().optional(),
  "include_completed": zod.boolean().optional(),
  "view_mode": zod.string().optional()
})

export const modelHubAnnotationQueuesItemsNextItemResponseStatusDefault = true;

export const ModelHubAnnotationQueuesItemsNextItemResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsNextItemResponseStatusDefault),
  "result": zod.object({
  "item": zod.object({

}).passthrough()
})
})


export const ModelHubAnnotationQueuesItemsReadParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.')
})


export const modelHubAnnotationQueuesItemsReadResponsePriorityMin = -2147483648;
export const modelHubAnnotationQueuesItemsReadResponsePriorityMax = 2147483647;

export const modelHubAnnotationQueuesItemsReadResponseOrderMin = -2147483648;
export const modelHubAnnotationQueuesItemsReadResponseOrderMax = 2147483647;



export const modelHubAnnotationQueuesItemsReadResponseReviewStatusMax = 20;




export const ModelHubAnnotationQueuesItemsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "queue": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().min(1).optional(),
  "status": zod.enum(['pending', 'in_progress', 'completed', 'skipped']).optional(),
  "workflow_status": zod.string().optional(),
  "workflow_status_label": zod.string().optional(),
  "priority": zod.number().min(modelHubAnnotationQueuesItemsReadResponsePriorityMin).max(modelHubAnnotationQueuesItemsReadResponsePriorityMax).optional(),
  "order": zod.number().min(modelHubAnnotationQueuesItemsReadResponseOrderMin).max(modelHubAnnotationQueuesItemsReadResponseOrderMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "assigned_to": zod.string().uuid().optional(),
  "assigned_to_name": zod.string().min(1).optional(),
  "assigned_users": zod.string().optional(),
  "reserved_by": zod.string().uuid().optional(),
  "reserved_by_name": zod.string().min(1).optional(),
  "reservation_expires_at": zod.string().datetime({"offset":true}).optional(),
  "review_status": zod.string().max(modelHubAnnotationQueuesItemsReadResponseReviewStatusMax).optional(),
  "reviewed_by": zod.string().uuid().optional(),
  "reviewed_by_name": zod.string().min(1).optional(),
  "reviewed_at": zod.string().datetime({"offset":true}).optional(),
  "review_notes": zod.string().optional(),
  "source_preview": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


export const ModelHubAnnotationQueuesItemsUpdateParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.')
})


export const modelHubAnnotationQueuesItemsUpdateBodyPriorityMin = -2147483648;
export const modelHubAnnotationQueuesItemsUpdateBodyPriorityMax = 2147483647;

export const modelHubAnnotationQueuesItemsUpdateBodyOrderMin = -2147483648;
export const modelHubAnnotationQueuesItemsUpdateBodyOrderMax = 2147483647;

export const modelHubAnnotationQueuesItemsUpdateBodyReviewStatusMax = 20;



export const ModelHubAnnotationQueuesItemsUpdateBody = zod.object({
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().min(1).optional(),
  "status": zod.enum(['pending', 'in_progress', 'completed', 'skipped']).optional(),
  "priority": zod.number().min(modelHubAnnotationQueuesItemsUpdateBodyPriorityMin).max(modelHubAnnotationQueuesItemsUpdateBodyPriorityMax).optional(),
  "order": zod.number().min(modelHubAnnotationQueuesItemsUpdateBodyOrderMin).max(modelHubAnnotationQueuesItemsUpdateBodyOrderMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "assigned_to": zod.string().uuid().optional(),
  "reserved_by": zod.string().uuid().optional(),
  "reservation_expires_at": zod.string().datetime({"offset":true}).optional(),
  "review_status": zod.string().max(modelHubAnnotationQueuesItemsUpdateBodyReviewStatusMax).optional(),
  "reviewed_by": zod.string().uuid().optional(),
  "reviewed_at": zod.string().datetime({"offset":true}).optional(),
  "review_notes": zod.string().optional()
})


export const modelHubAnnotationQueuesItemsUpdateResponsePriorityMin = -2147483648;
export const modelHubAnnotationQueuesItemsUpdateResponsePriorityMax = 2147483647;

export const modelHubAnnotationQueuesItemsUpdateResponseOrderMin = -2147483648;
export const modelHubAnnotationQueuesItemsUpdateResponseOrderMax = 2147483647;



export const modelHubAnnotationQueuesItemsUpdateResponseReviewStatusMax = 20;




export const ModelHubAnnotationQueuesItemsUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "queue": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().min(1).optional(),
  "status": zod.enum(['pending', 'in_progress', 'completed', 'skipped']).optional(),
  "workflow_status": zod.string().optional(),
  "workflow_status_label": zod.string().optional(),
  "priority": zod.number().min(modelHubAnnotationQueuesItemsUpdateResponsePriorityMin).max(modelHubAnnotationQueuesItemsUpdateResponsePriorityMax).optional(),
  "order": zod.number().min(modelHubAnnotationQueuesItemsUpdateResponseOrderMin).max(modelHubAnnotationQueuesItemsUpdateResponseOrderMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "assigned_to": zod.string().uuid().optional(),
  "assigned_to_name": zod.string().min(1).optional(),
  "assigned_users": zod.string().optional(),
  "reserved_by": zod.string().uuid().optional(),
  "reserved_by_name": zod.string().min(1).optional(),
  "reservation_expires_at": zod.string().datetime({"offset":true}).optional(),
  "review_status": zod.string().max(modelHubAnnotationQueuesItemsUpdateResponseReviewStatusMax).optional(),
  "reviewed_by": zod.string().uuid().optional(),
  "reviewed_by_name": zod.string().min(1).optional(),
  "reviewed_at": zod.string().datetime({"offset":true}).optional(),
  "review_notes": zod.string().optional(),
  "source_preview": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


export const ModelHubAnnotationQueuesItemsPartialUpdateParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.')
})


export const modelHubAnnotationQueuesItemsPartialUpdateBodyPriorityMin = -2147483648;
export const modelHubAnnotationQueuesItemsPartialUpdateBodyPriorityMax = 2147483647;

export const modelHubAnnotationQueuesItemsPartialUpdateBodyOrderMin = -2147483648;
export const modelHubAnnotationQueuesItemsPartialUpdateBodyOrderMax = 2147483647;

export const modelHubAnnotationQueuesItemsPartialUpdateBodyReviewStatusMax = 20;



export const ModelHubAnnotationQueuesItemsPartialUpdateBody = zod.object({
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().min(1).optional(),
  "status": zod.enum(['pending', 'in_progress', 'completed', 'skipped']).optional(),
  "priority": zod.number().min(modelHubAnnotationQueuesItemsPartialUpdateBodyPriorityMin).max(modelHubAnnotationQueuesItemsPartialUpdateBodyPriorityMax).optional(),
  "order": zod.number().min(modelHubAnnotationQueuesItemsPartialUpdateBodyOrderMin).max(modelHubAnnotationQueuesItemsPartialUpdateBodyOrderMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "assigned_to": zod.string().uuid().optional(),
  "reserved_by": zod.string().uuid().optional(),
  "reservation_expires_at": zod.string().datetime({"offset":true}).optional(),
  "review_status": zod.string().max(modelHubAnnotationQueuesItemsPartialUpdateBodyReviewStatusMax).optional(),
  "reviewed_by": zod.string().uuid().optional(),
  "reviewed_at": zod.string().datetime({"offset":true}).optional(),
  "review_notes": zod.string().optional()
})


export const modelHubAnnotationQueuesItemsPartialUpdateResponsePriorityMin = -2147483648;
export const modelHubAnnotationQueuesItemsPartialUpdateResponsePriorityMax = 2147483647;

export const modelHubAnnotationQueuesItemsPartialUpdateResponseOrderMin = -2147483648;
export const modelHubAnnotationQueuesItemsPartialUpdateResponseOrderMax = 2147483647;



export const modelHubAnnotationQueuesItemsPartialUpdateResponseReviewStatusMax = 20;




export const ModelHubAnnotationQueuesItemsPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "queue": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().min(1).optional(),
  "status": zod.enum(['pending', 'in_progress', 'completed', 'skipped']).optional(),
  "workflow_status": zod.string().optional(),
  "workflow_status_label": zod.string().optional(),
  "priority": zod.number().min(modelHubAnnotationQueuesItemsPartialUpdateResponsePriorityMin).max(modelHubAnnotationQueuesItemsPartialUpdateResponsePriorityMax).optional(),
  "order": zod.number().min(modelHubAnnotationQueuesItemsPartialUpdateResponseOrderMin).max(modelHubAnnotationQueuesItemsPartialUpdateResponseOrderMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "assigned_to": zod.string().uuid().optional(),
  "assigned_to_name": zod.string().min(1).optional(),
  "assigned_users": zod.string().optional(),
  "reserved_by": zod.string().uuid().optional(),
  "reserved_by_name": zod.string().min(1).optional(),
  "reservation_expires_at": zod.string().datetime({"offset":true}).optional(),
  "review_status": zod.string().max(modelHubAnnotationQueuesItemsPartialUpdateResponseReviewStatusMax).optional(),
  "reviewed_by": zod.string().uuid().optional(),
  "reviewed_by_name": zod.string().min(1).optional(),
  "reviewed_at": zod.string().datetime({"offset":true}).optional(),
  "review_notes": zod.string().optional(),
  "source_preview": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


export const ModelHubAnnotationQueuesItemsDeleteParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.')
})


/**
 * Get full annotation workspace data for an item.
 */
export const ModelHubAnnotationQueuesItemsAnnotateDetailParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.')
})

export const ModelHubAnnotationQueuesItemsAnnotateDetailQueryParams = zod.object({
  "annotator_id": zod.string().uuid().optional(),
  "include_completed": zod.boolean().optional(),
  "view_mode": zod.string().optional(),
  "mode": zod.string().optional(),
  "review_status": zod.string().optional(),
  "exclude_review_status": zod.string().optional(),
  "include_all_annotations": zod.boolean().optional()
})

export const modelHubAnnotationQueuesItemsAnnotateDetailResponseStatusDefault = true;

export const ModelHubAnnotationQueuesItemsAnnotateDetailResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsAnnotateDetailResponseStatusDefault),
  "result": zod.object({

}).passthrough()
})


/**
 * List all annotations for a queue item (across all annotators).
 */
export const ModelHubAnnotationQueuesItemsAnnotationsListParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.')
})

export const modelHubAnnotationQueuesItemsAnnotationsListResponseStatusDefault = true;





export const ModelHubAnnotationQueuesItemsAnnotationsListResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsAnnotationsListResponseStatusDefault),
  "result": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().optional(),
  "label_id": zod.string().uuid().optional(),
  "label_name": zod.string().min(1).optional(),
  "label_type": zod.string().min(1).optional(),
  "label_settings": zod.object({

}).passthrough().optional(),
  "label_allow_notes": zod.boolean().optional(),
  "value": zod.object({

}).passthrough(),
  "score_source": zod.enum(['human', 'api', 'auto', 'imported']).optional(),
  "notes": zod.string().optional(),
  "annotator": zod.string().uuid().optional(),
  "annotator_name": zod.string().min(1).optional(),
  "annotator_email": zod.string().min(1).optional(),
  "queue_item": zod.string().uuid().optional(),
  "queue_id": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Import annotations from external sources.
 */
export const ModelHubAnnotationQueuesItemsAnnotationsImportAnnotationsParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.')
})

export const ModelHubAnnotationQueuesItemsAnnotationsImportAnnotationsBody = zod.object({
  "annotations": zod.array(zod.object({
  "label_id": zod.string().uuid(),
  "value": zod.object({

}).passthrough(),
  "notes": zod.string().optional(),
  "score_source": zod.string().optional()
})),
  "annotator_id": zod.string().uuid().optional()
})

export const modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponseStatusDefault = true;

export const ModelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsAnnotationsImportAnnotationsResponseStatusDefault),
  "result": zod.record(zod.string(), zod.number())
})


/**
 * Submit or update annotations for a queue item.
 */
export const ModelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.')
})


export const modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsBodyNotesDefault = ``;

export const ModelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsBody = zod.object({
  "annotations": zod.array(zod.record(zod.string(), zod.string())).min(1),
  "notes": zod.string().default(modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsBodyNotesDefault),
  "item_notes": zod.string().optional()
})

export const modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponseStatusDefault = true;

export const ModelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsAnnotationsSubmitAnnotationsResponseStatusDefault),
  "result": zod.record(zod.string(), zod.number())
})


/**
 * Mark item as completed and return next pending item.
 */
export const ModelHubAnnotationQueuesItemsCompleteItemParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.')
})


export const modelHubAnnotationQueuesItemsCompleteItemBodyExcludeDefault = [];
export const modelHubAnnotationQueuesItemsCompleteItemBodyIncludeCompletedDefault = false;

export const ModelHubAnnotationQueuesItemsCompleteItemBody = zod.object({
  "exclude": zod.array(zod.string().min(1)).default(modelHubAnnotationQueuesItemsCompleteItemBodyExcludeDefault),
  "exclude_review_status": zod.string().optional(),
  "include_completed": zod.boolean().default(modelHubAnnotationQueuesItemsCompleteItemBodyIncludeCompletedDefault)
})

export const modelHubAnnotationQueuesItemsCompleteItemResponseStatusDefault = true;

export const ModelHubAnnotationQueuesItemsCompleteItemResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsCompleteItemResponseStatusDefault),
  "result": zod.object({
  "completed_item_id": zod.string().uuid().optional(),
  "skipped_item_id": zod.string().uuid().optional(),
  "next_item": zod.object({

}).passthrough()
})
})


/**
 * List or create non-blocking discussion comments for a queue item.
 */
export const ModelHubAnnotationQueuesItemsDiscussionReadParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.')
})

export const modelHubAnnotationQueuesItemsDiscussionReadResponseStatusDefault = true;

export const ModelHubAnnotationQueuesItemsDiscussionReadResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsDiscussionReadResponseStatusDefault),
  "result": zod.object({

}).passthrough()
})


/**
 * List or create non-blocking discussion comments for a queue item.
 */
export const ModelHubAnnotationQueuesItemsDiscussionCreateParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.')
})

export const ModelHubAnnotationQueuesItemsDiscussionCreateBody = zod.object({
  "comment": zod.string().optional(),
  "content": zod.string().optional(),
  "label_id": zod.string().uuid().optional(),
  "label": zod.string().uuid().optional(),
  "target_annotator_id": zod.string().uuid().optional(),
  "thread_id": zod.string().uuid().optional(),
  "thread": zod.string().uuid().optional(),
  "mentioned_user_ids": zod.array(zod.string()).optional(),
  "mentions": zod.array(zod.string()).optional()
})

export const modelHubAnnotationQueuesItemsDiscussionCreateResponseStatusDefault = true;

export const ModelHubAnnotationQueuesItemsDiscussionCreateResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsDiscussionCreateResponseStatusDefault),
  "result": zod.object({

}).passthrough()
})


/**
 * Toggle the current user's reaction on a discussion comment.
 */
export const ModelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.'),
  "comment_id": zod.string()
})

export const modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionBodyEmojiMax = 16;

export const modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionBodyReactionMax = 16;



export const ModelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionBody = zod.object({
  "emoji": zod.string().max(modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionBodyEmojiMax).optional(),
  "reaction": zod.string().max(modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionBodyReactionMax).optional()
})

export const modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponseStatusDefault = true;

export const ModelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsDiscussionCommentsDiscussionCommentReactionResponseStatusDefault),
  "result": zod.object({

}).passthrough()
})


export const ModelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.'),
  "thread_id": zod.string()
})

export const ModelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadBody = zod.object({
  "comment": zod.string().optional()
})

export const modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponseStatusDefault = true;

export const ModelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsDiscussionReopenDiscussionThreadResponseStatusDefault),
  "result": zod.object({

}).passthrough()
})


export const ModelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.'),
  "thread_id": zod.string()
})

export const ModelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadBody = zod.object({
  "comment": zod.string().optional()
})

export const modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponseStatusDefault = true;

export const ModelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsDiscussionResolveDiscussionThreadResponseStatusDefault),
  "result": zod.object({

}).passthrough()
})


/**
 * Release reservation on an item.
 */
export const ModelHubAnnotationQueuesItemsReleaseReservationParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.')
})

export const modelHubAnnotationQueuesItemsReleaseReservationResponseStatusDefault = true;

export const ModelHubAnnotationQueuesItemsReleaseReservationResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsReleaseReservationResponseStatusDefault),
  "result": zod.record(zod.string(), zod.boolean())
})


/**
 * Approve, request changes, or leave reviewer feedback on an item.
 */
export const ModelHubAnnotationQueuesItemsReviewItemParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.')
})

export const modelHubAnnotationQueuesItemsReviewItemBodyLabelCommentsItemDefault = {  };
export const modelHubAnnotationQueuesItemsReviewItemBodyLabelCommentsDefault = [];

export const ModelHubAnnotationQueuesItemsReviewItemBody = zod.object({
  "action": zod.enum(['approve', 'request_changes', 'reject', 'comment']),
  "notes": zod.string().optional(),
  "label_comments": zod.array(zod.object({
  "label_id": zod.string().uuid().optional(),
  "label": zod.string().uuid().optional(),
  "target_annotator_id": zod.string().uuid().optional(),
  "annotator_id": zod.string().uuid().optional(),
  "comment": zod.string().optional(),
  "notes": zod.string().optional()
}).default(modelHubAnnotationQueuesItemsReviewItemBodyLabelCommentsItemDefault)).default(modelHubAnnotationQueuesItemsReviewItemBodyLabelCommentsDefault)
})

export const modelHubAnnotationQueuesItemsReviewItemResponseStatusDefault = true;


export const ModelHubAnnotationQueuesItemsReviewItemResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsReviewItemResponseStatusDefault),
  "result": zod.object({
  "reviewed_item_id": zod.string().uuid(),
  "action": zod.string().min(1),
  "next_item": zod.object({

}).passthrough(),
  "review_comments": zod.array(zod.object({

}).passthrough()),
  "review_threads": zod.array(zod.object({

}).passthrough())
})
})


/**
 * Mark item as skipped and return next pending item.
 */
export const ModelHubAnnotationQueuesItemsSkipItemParams = zod.object({
  "queue_id": zod.string(),
  "id": zod.string().uuid().describe('A UUID string identifying this queue item.')
})


export const modelHubAnnotationQueuesItemsSkipItemBodyExcludeDefault = [];
export const modelHubAnnotationQueuesItemsSkipItemBodyIncludeCompletedDefault = false;

export const ModelHubAnnotationQueuesItemsSkipItemBody = zod.object({
  "exclude": zod.array(zod.string().min(1)).default(modelHubAnnotationQueuesItemsSkipItemBodyExcludeDefault),
  "exclude_review_status": zod.string().optional(),
  "include_completed": zod.boolean().default(modelHubAnnotationQueuesItemsSkipItemBodyIncludeCompletedDefault)
})

export const modelHubAnnotationQueuesItemsSkipItemResponseStatusDefault = true;

export const ModelHubAnnotationQueuesItemsSkipItemResponse = zod.object({
  "status": zod.boolean().default(modelHubAnnotationQueuesItemsSkipItemResponseStatusDefault),
  "result": zod.object({
  "completed_item_id": zod.string().uuid().optional(),
  "skipped_item_id": zod.string().uuid().optional(),
  "next_item": zod.object({

}).passthrough()
})
})


export const ModelHubAnnotationTasksListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemEmailMax = 254;

export const modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemNameMax = 255;

export const modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemOrganizationNameMax = 255;

export const modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemOrganizationDisplayNameMax = 255;

export const modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemOrganizationRegionMax = 16;

export const modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemOrganizationRequire2faGracePeriodDaysMin = 0;
export const modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemOrganizationRequire2faGracePeriodDaysMax = 32767;

export const modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemRoleMax = 255;

export const modelHubAnnotationTasksListResponseResultsItemAiModelMonitorsItemNameMax = 255;

export const modelHubAnnotationTasksListResponseResultsItemAiModelMonitorsItemDimensionMax = 255;

export const modelHubAnnotationTasksListResponseResultsItemAiModelMonitorsItemMetricMax = 255;

export const modelHubAnnotationTasksListResponseResultsItemAiModelUserModelIdMax = 255;

export const modelHubAnnotationTasksListResponseResultsItemAiModelDeletedDefault = false;
export const modelHubAnnotationTasksListResponseResultsItemAiModelBaselineModelVersionMax = 255;

export const modelHubAnnotationTasksListResponseResultsItemTaskNameMax = 255;



export const ModelHubAnnotationTasksListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "assigned_users": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "email": zod.string().email().min(1).max(modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemEmailMax),
  "name": zod.string().min(1).max(modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemNameMax),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional(),
  "organization": zod.object({
  "id": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1).max(modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemOrganizationNameMax),
  "display_name": zod.string().max(modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemOrganizationDisplayNameMax).optional(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).max(modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemOrganizationRegionMax).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().min(modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemOrganizationRequire2faGracePeriodDaysMin).max(modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemOrganizationRequire2faGracePeriodDaysMax).optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "status": zod.string().optional(),
  "role": zod.string().max(modelHubAnnotationTasksListResponseResultsItemAssignedUsersItemRoleMax).optional().describe('User\'s job role (e.g., Data Scientist, ML Engineer, or custom role)'),
  "goals": zod.object({

}).passthrough().optional().describe('List of user\'s goals for using the platform')
})).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "ai_model": zod.object({
  "id": zod.string().uuid().optional(),
  "monitors": zod.array(zod.object({
  "id": zod.number().optional(),
  "status": zod.boolean().optional().describe('Indicates if the alert is executed'),
  "name": zod.string().min(1).max(modelHubAnnotationTasksListResponseResultsItemAiModelMonitorsItemNameMax).describe('Name of the monitor'),
  "monitor_type": zod.enum(['Analytics', 'DataDrift', 'Performance']),
  "dimension": zod.string().min(1).max(modelHubAnnotationTasksListResponseResultsItemAiModelMonitorsItemDimensionMax).describe('Dimension of the monitor'),
  "metric": zod.string().min(1).max(modelHubAnnotationTasksListResponseResultsItemAiModelMonitorsItemMetricMax).describe('Metric used by the monitor'),
  "current_value": zod.number().describe('Current value of the metric'),
  "trigger_value": zod.number().describe('Value at which the alert is triggered'),
  "is_mute": zod.boolean().optional().describe('Indicates if the monitor is muted'),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "user_model_id": zod.string().min(1).max(modelHubAnnotationTasksListResponseResultsItemAiModelUserModelIdMax),
  "deleted": zod.boolean().default(modelHubAnnotationTasksListResponseResultsItemAiModelDeletedDefault),
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "baseline_model_environment": zod.enum(['Production', 'Training', 'Validation', 'Corpus']).optional(),
  "baseline_model_version": zod.string().max(modelHubAnnotationTasksListResponseResultsItemAiModelBaselineModelVersionMax).optional(),
  "default_metric": zod.string().uuid().optional(),
  "organization": zod.string().uuid(),
  "workspace": zod.string().uuid().optional()
}).optional(),
  "task_name": zod.string().min(1).max(modelHubAnnotationTasksListResponseResultsItemTaskNameMax)
}))
})


export const ModelHubAnnotationTasksReadParams = zod.object({
  "id": zod.string()
})

export const modelHubAnnotationTasksReadResponseAssignedUsersItemEmailMax = 254;

export const modelHubAnnotationTasksReadResponseAssignedUsersItemNameMax = 255;

export const modelHubAnnotationTasksReadResponseAssignedUsersItemOrganizationNameMax = 255;

export const modelHubAnnotationTasksReadResponseAssignedUsersItemOrganizationDisplayNameMax = 255;

export const modelHubAnnotationTasksReadResponseAssignedUsersItemOrganizationRegionMax = 16;

export const modelHubAnnotationTasksReadResponseAssignedUsersItemOrganizationRequire2faGracePeriodDaysMin = 0;
export const modelHubAnnotationTasksReadResponseAssignedUsersItemOrganizationRequire2faGracePeriodDaysMax = 32767;

export const modelHubAnnotationTasksReadResponseAssignedUsersItemRoleMax = 255;

export const modelHubAnnotationTasksReadResponseAiModelMonitorsItemNameMax = 255;

export const modelHubAnnotationTasksReadResponseAiModelMonitorsItemDimensionMax = 255;

export const modelHubAnnotationTasksReadResponseAiModelMonitorsItemMetricMax = 255;

export const modelHubAnnotationTasksReadResponseAiModelUserModelIdMax = 255;

export const modelHubAnnotationTasksReadResponseAiModelDeletedDefault = false;
export const modelHubAnnotationTasksReadResponseAiModelBaselineModelVersionMax = 255;

export const modelHubAnnotationTasksReadResponseTaskNameMax = 255;



export const ModelHubAnnotationTasksReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "assigned_users": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "email": zod.string().email().min(1).max(modelHubAnnotationTasksReadResponseAssignedUsersItemEmailMax),
  "name": zod.string().min(1).max(modelHubAnnotationTasksReadResponseAssignedUsersItemNameMax),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional(),
  "organization": zod.object({
  "id": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1).max(modelHubAnnotationTasksReadResponseAssignedUsersItemOrganizationNameMax),
  "display_name": zod.string().max(modelHubAnnotationTasksReadResponseAssignedUsersItemOrganizationDisplayNameMax).optional(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).max(modelHubAnnotationTasksReadResponseAssignedUsersItemOrganizationRegionMax).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().min(modelHubAnnotationTasksReadResponseAssignedUsersItemOrganizationRequire2faGracePeriodDaysMin).max(modelHubAnnotationTasksReadResponseAssignedUsersItemOrganizationRequire2faGracePeriodDaysMax).optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "status": zod.string().optional(),
  "role": zod.string().max(modelHubAnnotationTasksReadResponseAssignedUsersItemRoleMax).optional().describe('User\'s job role (e.g., Data Scientist, ML Engineer, or custom role)'),
  "goals": zod.object({

}).passthrough().optional().describe('List of user\'s goals for using the platform')
})).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "ai_model": zod.object({
  "id": zod.string().uuid().optional(),
  "monitors": zod.array(zod.object({
  "id": zod.number().optional(),
  "status": zod.boolean().optional().describe('Indicates if the alert is executed'),
  "name": zod.string().min(1).max(modelHubAnnotationTasksReadResponseAiModelMonitorsItemNameMax).describe('Name of the monitor'),
  "monitor_type": zod.enum(['Analytics', 'DataDrift', 'Performance']),
  "dimension": zod.string().min(1).max(modelHubAnnotationTasksReadResponseAiModelMonitorsItemDimensionMax).describe('Dimension of the monitor'),
  "metric": zod.string().min(1).max(modelHubAnnotationTasksReadResponseAiModelMonitorsItemMetricMax).describe('Metric used by the monitor'),
  "current_value": zod.number().describe('Current value of the metric'),
  "trigger_value": zod.number().describe('Value at which the alert is triggered'),
  "is_mute": zod.boolean().optional().describe('Indicates if the monitor is muted'),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "user_model_id": zod.string().min(1).max(modelHubAnnotationTasksReadResponseAiModelUserModelIdMax),
  "deleted": zod.boolean().default(modelHubAnnotationTasksReadResponseAiModelDeletedDefault),
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "baseline_model_environment": zod.enum(['Production', 'Training', 'Validation', 'Corpus']).optional(),
  "baseline_model_version": zod.string().max(modelHubAnnotationTasksReadResponseAiModelBaselineModelVersionMax).optional(),
  "default_metric": zod.string().uuid().optional(),
  "organization": zod.string().uuid(),
  "workspace": zod.string().uuid().optional()
}).optional(),
  "task_name": zod.string().min(1).max(modelHubAnnotationTasksReadResponseTaskNameMax)
})


export const ModelHubAnnotationsLabelsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const modelHubAnnotationsLabelsListResponseResultsItemNameMax = 255;



export const ModelHubAnnotationsLabelsListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationsLabelsListResponseResultsItemNameMax),
  "type": zod.enum(['text', 'numeric', 'categorical', 'star', 'thumbs_up_down']),
  "organization": zod.string().uuid().optional(),
  "settings": zod.object({

}).passthrough().optional(),
  "project": zod.string().uuid().optional(),
  "description": zod.string().optional(),
  "allow_notes": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "trace_annotations_count": zod.number().optional(),
  "annotation_count": zod.number().optional()
}))
})


/**
 * Custom create to provide clearer error responses in GM format.
 */
export const modelHubAnnotationsLabelsCreateBodyNameMax = 255;



export const ModelHubAnnotationsLabelsCreateBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationsLabelsCreateBodyNameMax),
  "type": zod.enum(['text', 'numeric', 'categorical', 'star', 'thumbs_up_down']),
  "settings": zod.object({

}).passthrough().optional(),
  "project": zod.string().uuid().optional(),
  "description": zod.string().optional(),
  "allow_notes": zod.boolean().optional()
})


export const ModelHubAnnotationsLabelsReadParams = zod.object({
  "id": zod.string()
})

export const modelHubAnnotationsLabelsReadResponseNameMax = 255;



export const ModelHubAnnotationsLabelsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationsLabelsReadResponseNameMax),
  "type": zod.enum(['text', 'numeric', 'categorical', 'star', 'thumbs_up_down']),
  "organization": zod.string().uuid().optional(),
  "settings": zod.object({

}).passthrough().optional(),
  "project": zod.string().uuid().optional(),
  "description": zod.string().optional(),
  "allow_notes": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "trace_annotations_count": zod.number().optional(),
  "annotation_count": zod.number().optional()
})


export const ModelHubAnnotationsLabelsUpdateParams = zod.object({
  "id": zod.string()
})

export const modelHubAnnotationsLabelsUpdateBodyNameMax = 255;



export const ModelHubAnnotationsLabelsUpdateBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationsLabelsUpdateBodyNameMax),
  "type": zod.enum(['text', 'numeric', 'categorical', 'star', 'thumbs_up_down']),
  "settings": zod.object({

}).passthrough().optional(),
  "project": zod.string().uuid().optional(),
  "description": zod.string().optional(),
  "allow_notes": zod.boolean().optional()
})

export const modelHubAnnotationsLabelsUpdateResponseNameMax = 255;



export const ModelHubAnnotationsLabelsUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationsLabelsUpdateResponseNameMax),
  "type": zod.enum(['text', 'numeric', 'categorical', 'star', 'thumbs_up_down']),
  "organization": zod.string().uuid().optional(),
  "settings": zod.object({

}).passthrough().optional(),
  "project": zod.string().uuid().optional(),
  "description": zod.string().optional(),
  "allow_notes": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "trace_annotations_count": zod.number().optional(),
  "annotation_count": zod.number().optional()
})


export const ModelHubAnnotationsLabelsPartialUpdateParams = zod.object({
  "id": zod.string()
})

export const modelHubAnnotationsLabelsPartialUpdateBodyNameMax = 255;



export const ModelHubAnnotationsLabelsPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationsLabelsPartialUpdateBodyNameMax),
  "type": zod.enum(['text', 'numeric', 'categorical', 'star', 'thumbs_up_down']),
  "settings": zod.object({

}).passthrough().optional(),
  "project": zod.string().uuid().optional(),
  "description": zod.string().optional(),
  "allow_notes": zod.boolean().optional()
})

export const modelHubAnnotationsLabelsPartialUpdateResponseNameMax = 255;



export const ModelHubAnnotationsLabelsPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationsLabelsPartialUpdateResponseNameMax),
  "type": zod.enum(['text', 'numeric', 'categorical', 'star', 'thumbs_up_down']),
  "organization": zod.string().uuid().optional(),
  "settings": zod.object({

}).passthrough().optional(),
  "project": zod.string().uuid().optional(),
  "description": zod.string().optional(),
  "allow_notes": zod.boolean().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "trace_annotations_count": zod.number().optional(),
  "annotation_count": zod.number().optional()
})


export const ModelHubAnnotationsLabelsDeleteParams = zod.object({
  "id": zod.string()
})


/**
 * Restore a soft-deleted (archived) annotation label.
 */
export const ModelHubAnnotationsLabelsRestoreParams = zod.object({
  "id": zod.string()
})


export const ModelHubAnnotationsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const modelHubAnnotationsListResponseResultsItemNameMax = 255;

export const modelHubAnnotationsListResponseResultsItemResponsesMin = -2147483648;
export const modelHubAnnotationsListResponseResultsItemResponsesMax = 2147483647;



export const ModelHubAnnotationsListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationsListResponseResultsItemNameMax),
  "assigned_users": zod.string().optional(),
  "organization": zod.string().uuid().optional(),
  "labels": zod.string().optional(),
  "columns": zod.array(zod.string().uuid()).optional(),
  "static_fields": zod.object({

}).passthrough().optional(),
  "response_fields": zod.object({

}).passthrough().optional(),
  "dataset": zod.string().uuid().optional(),
  "summary": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "responses": zod.number().min(modelHubAnnotationsListResponseResultsItemResponsesMin).max(modelHubAnnotationsListResponseResultsItemResponsesMax).optional(),
  "lowest_unfinished_row": zod.string().optional(),
  "label_requirements": zod.string().optional()
}))
})


export const modelHubAnnotationsCreateBodyNameMax = 255;

export const modelHubAnnotationsCreateBodyResponsesMin = -2147483648;
export const modelHubAnnotationsCreateBodyResponsesMax = 2147483647;



export const ModelHubAnnotationsCreateBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationsCreateBodyNameMax),
  "columns": zod.array(zod.string().uuid()).optional(),
  "static_fields": zod.object({

}).passthrough().optional(),
  "response_fields": zod.object({

}).passthrough().optional(),
  "dataset": zod.string().uuid().optional(),
  "responses": zod.number().min(modelHubAnnotationsCreateBodyResponsesMin).max(modelHubAnnotationsCreateBodyResponsesMax).optional()
})


/**
 * Bulk delete annotations and their associated data
Expected input: {"annotation_ids": ["uuid1", "uuid2", ...]}
 */
export const modelHubAnnotationsBulkDestroyBodyNameMax = 255;

export const modelHubAnnotationsBulkDestroyBodyResponsesMin = -2147483648;
export const modelHubAnnotationsBulkDestroyBodyResponsesMax = 2147483647;



export const ModelHubAnnotationsBulkDestroyBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationsBulkDestroyBodyNameMax),
  "columns": zod.array(zod.string().uuid()).optional(),
  "static_fields": zod.object({

}).passthrough().optional(),
  "response_fields": zod.object({

}).passthrough().optional(),
  "dataset": zod.string().uuid().optional(),
  "responses": zod.number().min(modelHubAnnotationsBulkDestroyBodyResponsesMin).max(modelHubAnnotationsBulkDestroyBodyResponsesMax).optional()
})


/**
 * Preview the first row of data for specified columns in a dataset.
 */
export const modelHubAnnotationsPreviewAnnotationsBodyNameMax = 255;

export const modelHubAnnotationsPreviewAnnotationsBodyResponsesMin = -2147483648;
export const modelHubAnnotationsPreviewAnnotationsBodyResponsesMax = 2147483647;



export const ModelHubAnnotationsPreviewAnnotationsBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationsPreviewAnnotationsBodyNameMax),
  "columns": zod.array(zod.string().uuid()).optional(),
  "static_fields": zod.object({

}).passthrough().optional(),
  "response_fields": zod.object({

}).passthrough().optional(),
  "dataset": zod.string().uuid().optional(),
  "responses": zod.number().min(modelHubAnnotationsPreviewAnnotationsBodyResponsesMin).max(modelHubAnnotationsPreviewAnnotationsBodyResponsesMax).optional()
})


export const ModelHubAnnotationsReadParams = zod.object({
  "id": zod.string()
})

export const modelHubAnnotationsReadResponseNameMax = 255;

export const modelHubAnnotationsReadResponseResponsesMin = -2147483648;
export const modelHubAnnotationsReadResponseResponsesMax = 2147483647;



export const ModelHubAnnotationsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationsReadResponseNameMax),
  "assigned_users": zod.string().optional(),
  "organization": zod.string().uuid().optional(),
  "labels": zod.string().optional(),
  "columns": zod.array(zod.string().uuid()).optional(),
  "static_fields": zod.object({

}).passthrough().optional(),
  "response_fields": zod.object({

}).passthrough().optional(),
  "dataset": zod.string().uuid().optional(),
  "summary": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "responses": zod.number().min(modelHubAnnotationsReadResponseResponsesMin).max(modelHubAnnotationsReadResponseResponsesMax).optional(),
  "lowest_unfinished_row": zod.string().optional(),
  "label_requirements": zod.string().optional()
})


export const ModelHubAnnotationsUpdateParams = zod.object({
  "id": zod.string()
})

export const modelHubAnnotationsUpdateBodyNameMax = 255;

export const modelHubAnnotationsUpdateBodyResponsesMin = -2147483648;
export const modelHubAnnotationsUpdateBodyResponsesMax = 2147483647;



export const ModelHubAnnotationsUpdateBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationsUpdateBodyNameMax),
  "columns": zod.array(zod.string().uuid()).optional(),
  "static_fields": zod.object({

}).passthrough().optional(),
  "response_fields": zod.object({

}).passthrough().optional(),
  "dataset": zod.string().uuid().optional(),
  "responses": zod.number().min(modelHubAnnotationsUpdateBodyResponsesMin).max(modelHubAnnotationsUpdateBodyResponsesMax).optional()
})

export const modelHubAnnotationsUpdateResponseNameMax = 255;

export const modelHubAnnotationsUpdateResponseResponsesMin = -2147483648;
export const modelHubAnnotationsUpdateResponseResponsesMax = 2147483647;



export const ModelHubAnnotationsUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationsUpdateResponseNameMax),
  "assigned_users": zod.string().optional(),
  "organization": zod.string().uuid().optional(),
  "labels": zod.string().optional(),
  "columns": zod.array(zod.string().uuid()).optional(),
  "static_fields": zod.object({

}).passthrough().optional(),
  "response_fields": zod.object({

}).passthrough().optional(),
  "dataset": zod.string().uuid().optional(),
  "summary": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "responses": zod.number().min(modelHubAnnotationsUpdateResponseResponsesMin).max(modelHubAnnotationsUpdateResponseResponsesMax).optional(),
  "lowest_unfinished_row": zod.string().optional(),
  "label_requirements": zod.string().optional()
})


export const ModelHubAnnotationsPartialUpdateParams = zod.object({
  "id": zod.string()
})

export const modelHubAnnotationsPartialUpdateBodyNameMax = 255;

export const modelHubAnnotationsPartialUpdateBodyResponsesMin = -2147483648;
export const modelHubAnnotationsPartialUpdateBodyResponsesMax = 2147483647;



export const ModelHubAnnotationsPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationsPartialUpdateBodyNameMax),
  "columns": zod.array(zod.string().uuid()).optional(),
  "static_fields": zod.object({

}).passthrough().optional(),
  "response_fields": zod.object({

}).passthrough().optional(),
  "dataset": zod.string().uuid().optional(),
  "responses": zod.number().min(modelHubAnnotationsPartialUpdateBodyResponsesMin).max(modelHubAnnotationsPartialUpdateBodyResponsesMax).optional()
})

export const modelHubAnnotationsPartialUpdateResponseNameMax = 255;

export const modelHubAnnotationsPartialUpdateResponseResponsesMin = -2147483648;
export const modelHubAnnotationsPartialUpdateResponseResponsesMax = 2147483647;



export const ModelHubAnnotationsPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationsPartialUpdateResponseNameMax),
  "assigned_users": zod.string().optional(),
  "organization": zod.string().uuid().optional(),
  "labels": zod.string().optional(),
  "columns": zod.array(zod.string().uuid()).optional(),
  "static_fields": zod.object({

}).passthrough().optional(),
  "response_fields": zod.object({

}).passthrough().optional(),
  "dataset": zod.string().uuid().optional(),
  "summary": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "responses": zod.number().min(modelHubAnnotationsPartialUpdateResponseResponsesMin).max(modelHubAnnotationsPartialUpdateResponseResponsesMax).optional(),
  "lowest_unfinished_row": zod.string().optional(),
  "label_requirements": zod.string().optional()
})


export const ModelHubAnnotationsDeleteParams = zod.object({
  "id": zod.string()
})


/**
 * Annotate a specific row with the provided values.
 */
export const ModelHubAnnotationsAnnotateRowParams = zod.object({
  "id": zod.string()
})

export const modelHubAnnotationsAnnotateRowResponseNameMax = 255;

export const modelHubAnnotationsAnnotateRowResponseResponsesMin = -2147483648;
export const modelHubAnnotationsAnnotateRowResponseResponsesMax = 2147483647;



export const ModelHubAnnotationsAnnotateRowResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(modelHubAnnotationsAnnotateRowResponseNameMax),
  "assigned_users": zod.string().optional(),
  "organization": zod.string().uuid().optional(),
  "labels": zod.string().optional(),
  "columns": zod.array(zod.string().uuid()).optional(),
  "static_fields": zod.object({

}).passthrough().optional(),
  "response_fields": zod.object({

}).passthrough().optional(),
  "dataset": zod.string().uuid().optional(),
  "summary": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "responses": zod.number().min(modelHubAnnotationsAnnotateRowResponseResponsesMin).max(modelHubAnnotationsAnnotateRowResponseResponsesMax).optional(),
  "lowest_unfinished_row": zod.string().optional(),
  "label_requirements": zod.string().optional()
})


export const ModelHubAnnotationsResetAnnotationsParams = zod.object({
  "id": zod.string()
})

export const modelHubAnnotationsResetAnnotationsBodyNameMax = 255;

export const modelHubAnnotationsResetAnnotationsBodyResponsesMin = -2147483648;
export const modelHubAnnotationsResetAnnotationsBodyResponsesMax = 2147483647;



export const ModelHubAnnotationsResetAnnotationsBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationsResetAnnotationsBodyNameMax),
  "columns": zod.array(zod.string().uuid()).optional(),
  "static_fields": zod.object({

}).passthrough().optional(),
  "response_fields": zod.object({

}).passthrough().optional(),
  "dataset": zod.string().uuid().optional(),
  "responses": zod.number().min(modelHubAnnotationsResetAnnotationsBodyResponsesMin).max(modelHubAnnotationsResetAnnotationsBodyResponsesMax).optional()
})


export const ModelHubAnnotationsUpdateCellsParams = zod.object({
  "id": zod.string()
})

export const modelHubAnnotationsUpdateCellsBodyNameMax = 255;

export const modelHubAnnotationsUpdateCellsBodyResponsesMin = -2147483648;
export const modelHubAnnotationsUpdateCellsBodyResponsesMax = 2147483647;



export const ModelHubAnnotationsUpdateCellsBody = zod.object({
  "name": zod.string().min(1).max(modelHubAnnotationsUpdateCellsBodyNameMax),
  "columns": zod.array(zod.string().uuid()).optional(),
  "static_fields": zod.object({

}).passthrough().optional(),
  "response_fields": zod.object({

}).passthrough().optional(),
  "dataset": zod.string().uuid().optional(),
  "responses": zod.number().min(modelHubAnnotationsUpdateCellsBodyResponsesMin).max(modelHubAnnotationsUpdateCellsBodyResponsesMax).optional()
})


export const ModelHubDatasetAnnotationSummaryListParams = zod.object({
  "dataset_id": zod.string()
})


/**
 * GET    /model-hub/scores/?source_type=trace&source_id=<uuid>
POST   /model-hub/scores/                 (single score)
POST   /model-hub/scores/bulk/            (multiple scores on one source)
DELETE /model-hub/scores/<id>/
 * @summary Universal Score CRUD.
 */
export const ModelHubScoresListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.'),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']).optional(),
  "source_id": zod.string().optional(),
  "label_id": zod.string().uuid().optional(),
  "annotator_id": zod.string().uuid().optional()
})







export const ModelHubScoresListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().optional(),
  "label_id": zod.string().uuid().optional(),
  "label_name": zod.string().min(1).optional(),
  "label_type": zod.string().min(1).optional(),
  "label_settings": zod.object({

}).passthrough().optional(),
  "label_allow_notes": zod.boolean().optional(),
  "value": zod.object({

}).passthrough(),
  "score_source": zod.enum(['human', 'api', 'auto', 'imported']).optional(),
  "notes": zod.string().optional(),
  "annotator": zod.string().uuid().optional(),
  "annotator_name": zod.string().min(1).optional(),
  "annotator_email": zod.string().min(1).optional(),
  "queue_item": zod.string().uuid().optional(),
  "queue_id": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Create a single score.
 */

export const modelHubScoresCreateBodyNotesDefault = ``;
export const modelHubScoresCreateBodyScoreSourceDefault = `human`;

export const ModelHubScoresCreateBody = zod.object({
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().min(1),
  "label_id": zod.string().uuid(),
  "value": zod.object({

}).passthrough(),
  "notes": zod.string().default(modelHubScoresCreateBodyNotesDefault),
  "score_source": zod.enum(['human', 'api', 'auto', 'imported']).default(modelHubScoresCreateBodyScoreSourceDefault),
  "queue_item_id": zod.string().uuid().optional()
})

export const modelHubScoresCreateResponseStatusDefault = true;





export const ModelHubScoresCreateResponse = zod.object({
  "status": zod.boolean().default(modelHubScoresCreateResponseStatusDefault),
  "result": zod.object({
  "id": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().optional(),
  "label_id": zod.string().uuid().optional(),
  "label_name": zod.string().min(1).optional(),
  "label_type": zod.string().min(1).optional(),
  "label_settings": zod.object({

}).passthrough().optional(),
  "label_allow_notes": zod.boolean().optional(),
  "value": zod.object({

}).passthrough(),
  "score_source": zod.enum(['human', 'api', 'auto', 'imported']).optional(),
  "notes": zod.string().optional(),
  "annotator": zod.string().uuid().optional(),
  "annotator_name": zod.string().min(1).optional(),
  "annotator_email": zod.string().min(1).optional(),
  "queue_item": zod.string().uuid().optional(),
  "queue_id": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})
})


/**
 * Create multiple scores on a single source (e.g. from inline annotator).
 */


export const modelHubScoresBulkCreateBodyNotesDefault = ``;

export const ModelHubScoresBulkCreateBody = zod.object({
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().min(1),
  "scores": zod.array(zod.record(zod.string(), zod.string())).min(1),
  "notes": zod.string().default(modelHubScoresBulkCreateBodyNotesDefault),
  "span_notes": zod.string().optional(),
  "span_notes_source_id": zod.string().optional(),
  "queue_item_id": zod.string().uuid().optional()
})

export const modelHubScoresBulkCreateResponseStatusDefault = true;






export const ModelHubScoresBulkCreateResponse = zod.object({
  "status": zod.boolean().default(modelHubScoresBulkCreateResponseStatusDefault),
  "result": zod.object({
  "scores": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().optional(),
  "label_id": zod.string().uuid().optional(),
  "label_name": zod.string().min(1).optional(),
  "label_type": zod.string().min(1).optional(),
  "label_settings": zod.object({

}).passthrough().optional(),
  "label_allow_notes": zod.boolean().optional(),
  "value": zod.object({

}).passthrough(),
  "score_source": zod.enum(['human', 'api', 'auto', 'imported']).optional(),
  "notes": zod.string().optional(),
  "annotator": zod.string().uuid().optional(),
  "annotator_name": zod.string().min(1).optional(),
  "annotator_email": zod.string().min(1).optional(),
  "queue_item": zod.string().uuid().optional(),
  "queue_id": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})),
  "errors": zod.array(zod.string().min(1))
})
})


/**
 * Get all scores for a specific source.
GET /model-hub/scores/for-source/?source_type=trace&source_id=<uuid>
 */



export const ModelHubScoresForSourceQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.'),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().min(1)
})

export const modelHubScoresForSourceResponseStatusDefault = true;





export const ModelHubScoresForSourceResponse = zod.object({
  "status": zod.boolean().default(modelHubScoresForSourceResponseStatusDefault),
  "result": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().optional(),
  "label_id": zod.string().uuid().optional(),
  "label_name": zod.string().min(1).optional(),
  "label_type": zod.string().min(1).optional(),
  "label_settings": zod.object({

}).passthrough().optional(),
  "label_allow_notes": zod.boolean().optional(),
  "value": zod.object({

}).passthrough(),
  "score_source": zod.enum(['human', 'api', 'auto', 'imported']).optional(),
  "notes": zod.string().optional(),
  "annotator": zod.string().uuid().optional(),
  "annotator_name": zod.string().min(1).optional(),
  "annotator_email": zod.string().min(1).optional(),
  "queue_item": zod.string().uuid().optional(),
  "queue_id": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})),
  "span_notes": zod.array(zod.object({

}).passthrough()).optional()
})


/**
 * GET    /model-hub/scores/?source_type=trace&source_id=<uuid>
POST   /model-hub/scores/                 (single score)
POST   /model-hub/scores/bulk/            (multiple scores on one source)
DELETE /model-hub/scores/<id>/
 * @summary Universal Score CRUD.
 */
export const ModelHubScoresReadParams = zod.object({
  "id": zod.string()
})







export const ModelHubScoresReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().optional(),
  "label_id": zod.string().uuid().optional(),
  "label_name": zod.string().min(1).optional(),
  "label_type": zod.string().min(1).optional(),
  "label_settings": zod.object({

}).passthrough().optional(),
  "label_allow_notes": zod.boolean().optional(),
  "value": zod.object({

}).passthrough(),
  "score_source": zod.enum(['human', 'api', 'auto', 'imported']).optional(),
  "notes": zod.string().optional(),
  "annotator": zod.string().uuid().optional(),
  "annotator_name": zod.string().min(1).optional(),
  "annotator_email": zod.string().min(1).optional(),
  "queue_item": zod.string().uuid().optional(),
  "queue_id": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * GET    /model-hub/scores/?source_type=trace&source_id=<uuid>
POST   /model-hub/scores/                 (single score)
POST   /model-hub/scores/bulk/            (multiple scores on one source)
DELETE /model-hub/scores/<id>/
 * @summary Universal Score CRUD.
 */
export const ModelHubScoresUpdateParams = zod.object({
  "id": zod.string()
})

export const ModelHubScoresUpdateBody = zod.object({
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "value": zod.object({

}).passthrough(),
  "score_source": zod.enum(['human', 'api', 'auto', 'imported']).optional(),
  "notes": zod.string().optional()
})







export const ModelHubScoresUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().optional(),
  "label_id": zod.string().uuid().optional(),
  "label_name": zod.string().min(1).optional(),
  "label_type": zod.string().min(1).optional(),
  "label_settings": zod.object({

}).passthrough().optional(),
  "label_allow_notes": zod.boolean().optional(),
  "value": zod.object({

}).passthrough(),
  "score_source": zod.enum(['human', 'api', 'auto', 'imported']).optional(),
  "notes": zod.string().optional(),
  "annotator": zod.string().uuid().optional(),
  "annotator_name": zod.string().min(1).optional(),
  "annotator_email": zod.string().min(1).optional(),
  "queue_item": zod.string().uuid().optional(),
  "queue_id": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * GET    /model-hub/scores/?source_type=trace&source_id=<uuid>
POST   /model-hub/scores/                 (single score)
POST   /model-hub/scores/bulk/            (multiple scores on one source)
DELETE /model-hub/scores/<id>/
 * @summary Universal Score CRUD.
 */
export const ModelHubScoresPartialUpdateParams = zod.object({
  "id": zod.string()
})

export const ModelHubScoresPartialUpdateBody = zod.object({
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "value": zod.object({

}).passthrough(),
  "score_source": zod.enum(['human', 'api', 'auto', 'imported']).optional(),
  "notes": zod.string().optional()
})







export const ModelHubScoresPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "source_type": zod.enum(['dataset_row', 'trace', 'observation_span', 'prototype_run', 'call_execution', 'trace_session']),
  "source_id": zod.string().optional(),
  "label_id": zod.string().uuid().optional(),
  "label_name": zod.string().min(1).optional(),
  "label_type": zod.string().min(1).optional(),
  "label_settings": zod.object({

}).passthrough().optional(),
  "label_allow_notes": zod.boolean().optional(),
  "value": zod.object({

}).passthrough(),
  "score_source": zod.enum(['human', 'api', 'auto', 'imported']).optional(),
  "notes": zod.string().optional(),
  "annotator": zod.string().uuid().optional(),
  "annotator_name": zod.string().min(1).optional(),
  "annotator_email": zod.string().min(1).optional(),
  "queue_item": zod.string().uuid().optional(),
  "queue_id": zod.string().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


/**
 * Only the annotator who created the score or an org Owner/Admin may
delete it.
 * @summary Soft-delete a score.
 */
export const ModelHubScoresDeleteParams = zod.object({
  "id": zod.string()
})

export const modelHubScoresDeleteResponseStatusDefault = true;

export const ModelHubScoresDeleteResponse = zod.object({
  "status": zod.boolean().default(modelHubScoresDeleteResponseStatusDefault),
  "result": zod.record(zod.string(), zod.boolean())
})


export const TracerDashboardListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerDashboardListResponseResultsItemNameMax = 255;

export const tracerDashboardListResponseResultsItemCreatedByEmailMax = 254;

export const tracerDashboardListResponseResultsItemCreatedByNameMax = 255;

export const tracerDashboardListResponseResultsItemCreatedByOrganizationNameMax = 255;

export const tracerDashboardListResponseResultsItemCreatedByOrganizationDisplayNameMax = 255;

export const tracerDashboardListResponseResultsItemCreatedByOrganizationRegionMax = 16;

export const tracerDashboardListResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMin = 0;
export const tracerDashboardListResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMax = 32767;

export const tracerDashboardListResponseResultsItemCreatedByRoleMax = 255;

export const tracerDashboardListResponseResultsItemUpdatedByEmailMax = 254;

export const tracerDashboardListResponseResultsItemUpdatedByNameMax = 255;

export const tracerDashboardListResponseResultsItemUpdatedByOrganizationNameMax = 255;

export const tracerDashboardListResponseResultsItemUpdatedByOrganizationDisplayNameMax = 255;

export const tracerDashboardListResponseResultsItemUpdatedByOrganizationRegionMax = 16;

export const tracerDashboardListResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMin = 0;
export const tracerDashboardListResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMax = 32767;

export const tracerDashboardListResponseResultsItemUpdatedByRoleMax = 255;



export const TracerDashboardListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(tracerDashboardListResponseResultsItemNameMax),
  "description": zod.string().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "email": zod.string().email().min(1).max(tracerDashboardListResponseResultsItemCreatedByEmailMax),
  "name": zod.string().min(1).max(tracerDashboardListResponseResultsItemCreatedByNameMax),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional(),
  "organization": zod.object({
  "id": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1).max(tracerDashboardListResponseResultsItemCreatedByOrganizationNameMax),
  "display_name": zod.string().max(tracerDashboardListResponseResultsItemCreatedByOrganizationDisplayNameMax).optional(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).max(tracerDashboardListResponseResultsItemCreatedByOrganizationRegionMax).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().min(tracerDashboardListResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMin).max(tracerDashboardListResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMax).optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "status": zod.string().optional(),
  "role": zod.string().max(tracerDashboardListResponseResultsItemCreatedByRoleMax).optional().describe('User\'s job role (e.g., Data Scientist, ML Engineer, or custom role)'),
  "goals": zod.object({

}).passthrough().optional().describe('List of user\'s goals for using the platform')
}).optional(),
  "updated_by": zod.object({
  "id": zod.string().uuid().optional(),
  "email": zod.string().email().min(1).max(tracerDashboardListResponseResultsItemUpdatedByEmailMax),
  "name": zod.string().min(1).max(tracerDashboardListResponseResultsItemUpdatedByNameMax),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional(),
  "organization": zod.object({
  "id": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1).max(tracerDashboardListResponseResultsItemUpdatedByOrganizationNameMax),
  "display_name": zod.string().max(tracerDashboardListResponseResultsItemUpdatedByOrganizationDisplayNameMax).optional(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).max(tracerDashboardListResponseResultsItemUpdatedByOrganizationRegionMax).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().min(tracerDashboardListResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMin).max(tracerDashboardListResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMax).optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "status": zod.string().optional(),
  "role": zod.string().max(tracerDashboardListResponseResultsItemUpdatedByRoleMax).optional().describe('User\'s job role (e.g., Data Scientist, ML Engineer, or custom role)'),
  "goals": zod.object({

}).passthrough().optional().describe('List of user\'s goals for using the platform')
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "widget_count": zod.string().optional()
}))
})


export const tracerDashboardCreateBodyNameMax = 255;



export const TracerDashboardCreateBody = zod.object({
  "name": zod.string().min(1).max(tracerDashboardCreateBodyNameMax),
  "description": zod.string().optional()
})


/**
 * Return distinct values for a given metric/attribute, for filter value picker.
 */
export const TracerDashboardFilterValuesQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerDashboardFilterValuesResponseResultsItemNameMax = 255;

export const tracerDashboardFilterValuesResponseResultsItemCreatedByEmailMax = 254;

export const tracerDashboardFilterValuesResponseResultsItemCreatedByNameMax = 255;

export const tracerDashboardFilterValuesResponseResultsItemCreatedByOrganizationNameMax = 255;

export const tracerDashboardFilterValuesResponseResultsItemCreatedByOrganizationDisplayNameMax = 255;

export const tracerDashboardFilterValuesResponseResultsItemCreatedByOrganizationRegionMax = 16;

export const tracerDashboardFilterValuesResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMin = 0;
export const tracerDashboardFilterValuesResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMax = 32767;

export const tracerDashboardFilterValuesResponseResultsItemCreatedByRoleMax = 255;

export const tracerDashboardFilterValuesResponseResultsItemUpdatedByEmailMax = 254;

export const tracerDashboardFilterValuesResponseResultsItemUpdatedByNameMax = 255;

export const tracerDashboardFilterValuesResponseResultsItemUpdatedByOrganizationNameMax = 255;

export const tracerDashboardFilterValuesResponseResultsItemUpdatedByOrganizationDisplayNameMax = 255;

export const tracerDashboardFilterValuesResponseResultsItemUpdatedByOrganizationRegionMax = 16;

export const tracerDashboardFilterValuesResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMin = 0;
export const tracerDashboardFilterValuesResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMax = 32767;

export const tracerDashboardFilterValuesResponseResultsItemUpdatedByRoleMax = 255;



export const TracerDashboardFilterValuesResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(tracerDashboardFilterValuesResponseResultsItemNameMax),
  "description": zod.string().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "email": zod.string().email().min(1).max(tracerDashboardFilterValuesResponseResultsItemCreatedByEmailMax),
  "name": zod.string().min(1).max(tracerDashboardFilterValuesResponseResultsItemCreatedByNameMax),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional(),
  "organization": zod.object({
  "id": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1).max(tracerDashboardFilterValuesResponseResultsItemCreatedByOrganizationNameMax),
  "display_name": zod.string().max(tracerDashboardFilterValuesResponseResultsItemCreatedByOrganizationDisplayNameMax).optional(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).max(tracerDashboardFilterValuesResponseResultsItemCreatedByOrganizationRegionMax).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().min(tracerDashboardFilterValuesResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMin).max(tracerDashboardFilterValuesResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMax).optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "status": zod.string().optional(),
  "role": zod.string().max(tracerDashboardFilterValuesResponseResultsItemCreatedByRoleMax).optional().describe('User\'s job role (e.g., Data Scientist, ML Engineer, or custom role)'),
  "goals": zod.object({

}).passthrough().optional().describe('List of user\'s goals for using the platform')
}).optional(),
  "updated_by": zod.object({
  "id": zod.string().uuid().optional(),
  "email": zod.string().email().min(1).max(tracerDashboardFilterValuesResponseResultsItemUpdatedByEmailMax),
  "name": zod.string().min(1).max(tracerDashboardFilterValuesResponseResultsItemUpdatedByNameMax),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional(),
  "organization": zod.object({
  "id": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1).max(tracerDashboardFilterValuesResponseResultsItemUpdatedByOrganizationNameMax),
  "display_name": zod.string().max(tracerDashboardFilterValuesResponseResultsItemUpdatedByOrganizationDisplayNameMax).optional(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).max(tracerDashboardFilterValuesResponseResultsItemUpdatedByOrganizationRegionMax).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().min(tracerDashboardFilterValuesResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMin).max(tracerDashboardFilterValuesResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMax).optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "status": zod.string().optional(),
  "role": zod.string().max(tracerDashboardFilterValuesResponseResultsItemUpdatedByRoleMax).optional().describe('User\'s job role (e.g., Data Scientist, ML Engineer, or custom role)'),
  "goals": zod.object({

}).passthrough().optional().describe('List of user\'s goals for using the platform')
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "widget_count": zod.string().optional()
}))
})


/**
 * Backward compat: if ``workflow`` param is provided, return only
that source's metrics in the old grouped format.
 * @summary Return all available metrics across traces and datasets.
 */
export const TracerDashboardMetricsQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerDashboardMetricsResponseResultsItemNameMax = 255;

export const tracerDashboardMetricsResponseResultsItemCreatedByEmailMax = 254;

export const tracerDashboardMetricsResponseResultsItemCreatedByNameMax = 255;

export const tracerDashboardMetricsResponseResultsItemCreatedByOrganizationNameMax = 255;

export const tracerDashboardMetricsResponseResultsItemCreatedByOrganizationDisplayNameMax = 255;

export const tracerDashboardMetricsResponseResultsItemCreatedByOrganizationRegionMax = 16;

export const tracerDashboardMetricsResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMin = 0;
export const tracerDashboardMetricsResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMax = 32767;

export const tracerDashboardMetricsResponseResultsItemCreatedByRoleMax = 255;

export const tracerDashboardMetricsResponseResultsItemUpdatedByEmailMax = 254;

export const tracerDashboardMetricsResponseResultsItemUpdatedByNameMax = 255;

export const tracerDashboardMetricsResponseResultsItemUpdatedByOrganizationNameMax = 255;

export const tracerDashboardMetricsResponseResultsItemUpdatedByOrganizationDisplayNameMax = 255;

export const tracerDashboardMetricsResponseResultsItemUpdatedByOrganizationRegionMax = 16;

export const tracerDashboardMetricsResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMin = 0;
export const tracerDashboardMetricsResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMax = 32767;

export const tracerDashboardMetricsResponseResultsItemUpdatedByRoleMax = 255;



export const TracerDashboardMetricsResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(tracerDashboardMetricsResponseResultsItemNameMax),
  "description": zod.string().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "email": zod.string().email().min(1).max(tracerDashboardMetricsResponseResultsItemCreatedByEmailMax),
  "name": zod.string().min(1).max(tracerDashboardMetricsResponseResultsItemCreatedByNameMax),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional(),
  "organization": zod.object({
  "id": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1).max(tracerDashboardMetricsResponseResultsItemCreatedByOrganizationNameMax),
  "display_name": zod.string().max(tracerDashboardMetricsResponseResultsItemCreatedByOrganizationDisplayNameMax).optional(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).max(tracerDashboardMetricsResponseResultsItemCreatedByOrganizationRegionMax).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().min(tracerDashboardMetricsResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMin).max(tracerDashboardMetricsResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMax).optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "status": zod.string().optional(),
  "role": zod.string().max(tracerDashboardMetricsResponseResultsItemCreatedByRoleMax).optional().describe('User\'s job role (e.g., Data Scientist, ML Engineer, or custom role)'),
  "goals": zod.object({

}).passthrough().optional().describe('List of user\'s goals for using the platform')
}).optional(),
  "updated_by": zod.object({
  "id": zod.string().uuid().optional(),
  "email": zod.string().email().min(1).max(tracerDashboardMetricsResponseResultsItemUpdatedByEmailMax),
  "name": zod.string().min(1).max(tracerDashboardMetricsResponseResultsItemUpdatedByNameMax),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional(),
  "organization": zod.object({
  "id": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1).max(tracerDashboardMetricsResponseResultsItemUpdatedByOrganizationNameMax),
  "display_name": zod.string().max(tracerDashboardMetricsResponseResultsItemUpdatedByOrganizationDisplayNameMax).optional(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).max(tracerDashboardMetricsResponseResultsItemUpdatedByOrganizationRegionMax).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().min(tracerDashboardMetricsResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMin).max(tracerDashboardMetricsResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMax).optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "status": zod.string().optional(),
  "role": zod.string().max(tracerDashboardMetricsResponseResultsItemUpdatedByRoleMax).optional().describe('User\'s job role (e.g., Data Scientist, ML Engineer, or custom role)'),
  "goals": zod.object({

}).passthrough().optional().describe('List of user\'s goals for using the platform')
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "widget_count": zod.string().optional()
}))
})


/**
 * Each metric carries a ``source`` field ("traces" or "datasets").
Metrics are partitioned by source and dispatched to the appropriate
query builder.  Results are merged into a single response.

Backward compat: if ``workflow`` is present and metrics lack
``source``, infer source from workflow.
 * @summary Execute a widget query and return chart data.
 */
export const tracerDashboardQueryBodyNameMax = 255;

export const tracerDashboardQueryBodyCreatedByEmailMax = 254;

export const tracerDashboardQueryBodyCreatedByNameMax = 255;

export const tracerDashboardQueryBodyCreatedByOrganizationNameMax = 255;

export const tracerDashboardQueryBodyCreatedByOrganizationDisplayNameMax = 255;

export const tracerDashboardQueryBodyCreatedByOrganizationRegionMax = 16;

export const tracerDashboardQueryBodyCreatedByOrganizationRequire2faGracePeriodDaysMin = 0;
export const tracerDashboardQueryBodyCreatedByOrganizationRequire2faGracePeriodDaysMax = 32767;

export const tracerDashboardQueryBodyCreatedByRoleMax = 255;

export const tracerDashboardQueryBodyUpdatedByEmailMax = 254;

export const tracerDashboardQueryBodyUpdatedByNameMax = 255;

export const tracerDashboardQueryBodyUpdatedByOrganizationNameMax = 255;

export const tracerDashboardQueryBodyUpdatedByOrganizationDisplayNameMax = 255;

export const tracerDashboardQueryBodyUpdatedByOrganizationRegionMax = 16;

export const tracerDashboardQueryBodyUpdatedByOrganizationRequire2faGracePeriodDaysMin = 0;
export const tracerDashboardQueryBodyUpdatedByOrganizationRequire2faGracePeriodDaysMax = 32767;

export const tracerDashboardQueryBodyUpdatedByRoleMax = 255;



export const TracerDashboardQueryBody = zod.object({
  "name": zod.string().min(1).max(tracerDashboardQueryBodyNameMax),
  "description": zod.string().optional(),
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "email": zod.string().email().min(1).max(tracerDashboardQueryBodyCreatedByEmailMax),
  "name": zod.string().min(1).max(tracerDashboardQueryBodyCreatedByNameMax),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional(),
  "organization": zod.object({
  "id": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1).max(tracerDashboardQueryBodyCreatedByOrganizationNameMax),
  "display_name": zod.string().max(tracerDashboardQueryBodyCreatedByOrganizationDisplayNameMax).optional(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).max(tracerDashboardQueryBodyCreatedByOrganizationRegionMax).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().min(tracerDashboardQueryBodyCreatedByOrganizationRequire2faGracePeriodDaysMin).max(tracerDashboardQueryBodyCreatedByOrganizationRequire2faGracePeriodDaysMax).optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "status": zod.string().optional(),
  "role": zod.string().max(tracerDashboardQueryBodyCreatedByRoleMax).optional().describe('User\'s job role (e.g., Data Scientist, ML Engineer, or custom role)'),
  "goals": zod.object({

}).passthrough().optional().describe('List of user\'s goals for using the platform')
}).optional(),
  "updated_by": zod.object({
  "id": zod.string().uuid().optional(),
  "email": zod.string().email().min(1).max(tracerDashboardQueryBodyUpdatedByEmailMax),
  "name": zod.string().min(1).max(tracerDashboardQueryBodyUpdatedByNameMax),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional(),
  "organization": zod.object({
  "id": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1).max(tracerDashboardQueryBodyUpdatedByOrganizationNameMax),
  "display_name": zod.string().max(tracerDashboardQueryBodyUpdatedByOrganizationDisplayNameMax).optional(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).max(tracerDashboardQueryBodyUpdatedByOrganizationRegionMax).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().min(tracerDashboardQueryBodyUpdatedByOrganizationRequire2faGracePeriodDaysMin).max(tracerDashboardQueryBodyUpdatedByOrganizationRequire2faGracePeriodDaysMax).optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "status": zod.string().optional(),
  "role": zod.string().max(tracerDashboardQueryBodyUpdatedByRoleMax).optional().describe('User\'s job role (e.g., Data Scientist, ML Engineer, or custom role)'),
  "goals": zod.object({

}).passthrough().optional().describe('List of user\'s goals for using the platform')
}).optional()
})


/**
 * Return simulation agents with their observability project links.
 */
export const TracerDashboardSimulationAgentsQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerDashboardSimulationAgentsResponseResultsItemNameMax = 255;

export const tracerDashboardSimulationAgentsResponseResultsItemCreatedByEmailMax = 254;

export const tracerDashboardSimulationAgentsResponseResultsItemCreatedByNameMax = 255;

export const tracerDashboardSimulationAgentsResponseResultsItemCreatedByOrganizationNameMax = 255;

export const tracerDashboardSimulationAgentsResponseResultsItemCreatedByOrganizationDisplayNameMax = 255;

export const tracerDashboardSimulationAgentsResponseResultsItemCreatedByOrganizationRegionMax = 16;

export const tracerDashboardSimulationAgentsResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMin = 0;
export const tracerDashboardSimulationAgentsResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMax = 32767;

export const tracerDashboardSimulationAgentsResponseResultsItemCreatedByRoleMax = 255;

export const tracerDashboardSimulationAgentsResponseResultsItemUpdatedByEmailMax = 254;

export const tracerDashboardSimulationAgentsResponseResultsItemUpdatedByNameMax = 255;

export const tracerDashboardSimulationAgentsResponseResultsItemUpdatedByOrganizationNameMax = 255;

export const tracerDashboardSimulationAgentsResponseResultsItemUpdatedByOrganizationDisplayNameMax = 255;

export const tracerDashboardSimulationAgentsResponseResultsItemUpdatedByOrganizationRegionMax = 16;

export const tracerDashboardSimulationAgentsResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMin = 0;
export const tracerDashboardSimulationAgentsResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMax = 32767;

export const tracerDashboardSimulationAgentsResponseResultsItemUpdatedByRoleMax = 255;



export const TracerDashboardSimulationAgentsResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(tracerDashboardSimulationAgentsResponseResultsItemNameMax),
  "description": zod.string().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "email": zod.string().email().min(1).max(tracerDashboardSimulationAgentsResponseResultsItemCreatedByEmailMax),
  "name": zod.string().min(1).max(tracerDashboardSimulationAgentsResponseResultsItemCreatedByNameMax),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional(),
  "organization": zod.object({
  "id": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1).max(tracerDashboardSimulationAgentsResponseResultsItemCreatedByOrganizationNameMax),
  "display_name": zod.string().max(tracerDashboardSimulationAgentsResponseResultsItemCreatedByOrganizationDisplayNameMax).optional(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).max(tracerDashboardSimulationAgentsResponseResultsItemCreatedByOrganizationRegionMax).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().min(tracerDashboardSimulationAgentsResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMin).max(tracerDashboardSimulationAgentsResponseResultsItemCreatedByOrganizationRequire2faGracePeriodDaysMax).optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "status": zod.string().optional(),
  "role": zod.string().max(tracerDashboardSimulationAgentsResponseResultsItemCreatedByRoleMax).optional().describe('User\'s job role (e.g., Data Scientist, ML Engineer, or custom role)'),
  "goals": zod.object({

}).passthrough().optional().describe('List of user\'s goals for using the platform')
}).optional(),
  "updated_by": zod.object({
  "id": zod.string().uuid().optional(),
  "email": zod.string().email().min(1).max(tracerDashboardSimulationAgentsResponseResultsItemUpdatedByEmailMax),
  "name": zod.string().min(1).max(tracerDashboardSimulationAgentsResponseResultsItemUpdatedByNameMax),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional(),
  "organization": zod.object({
  "id": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1).max(tracerDashboardSimulationAgentsResponseResultsItemUpdatedByOrganizationNameMax),
  "display_name": zod.string().max(tracerDashboardSimulationAgentsResponseResultsItemUpdatedByOrganizationDisplayNameMax).optional(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).max(tracerDashboardSimulationAgentsResponseResultsItemUpdatedByOrganizationRegionMax).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().min(tracerDashboardSimulationAgentsResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMin).max(tracerDashboardSimulationAgentsResponseResultsItemUpdatedByOrganizationRequire2faGracePeriodDaysMax).optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "status": zod.string().optional(),
  "role": zod.string().max(tracerDashboardSimulationAgentsResponseResultsItemUpdatedByRoleMax).optional().describe('User\'s job role (e.g., Data Scientist, ML Engineer, or custom role)'),
  "goals": zod.object({

}).passthrough().optional().describe('List of user\'s goals for using the platform')
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "widget_count": zod.string().optional()
}))
})


export const TracerDashboardWidgetsListParams = zod.object({
  "dashboard_pk": zod.string()
})

export const TracerDashboardWidgetsListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerDashboardWidgetsListResponseResultsItemNameMax = 255;

export const tracerDashboardWidgetsListResponseResultsItemPositionMin = -2147483648;
export const tracerDashboardWidgetsListResponseResultsItemPositionMax = 2147483647;

export const tracerDashboardWidgetsListResponseResultsItemWidthMin = -2147483648;
export const tracerDashboardWidgetsListResponseResultsItemWidthMax = 2147483647;

export const tracerDashboardWidgetsListResponseResultsItemHeightMin = -2147483648;
export const tracerDashboardWidgetsListResponseResultsItemHeightMax = 2147483647;



export const TracerDashboardWidgetsListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(tracerDashboardWidgetsListResponseResultsItemNameMax).optional(),
  "description": zod.string().optional(),
  "position": zod.number().min(tracerDashboardWidgetsListResponseResultsItemPositionMin).max(tracerDashboardWidgetsListResponseResultsItemPositionMax).optional(),
  "width": zod.number().min(tracerDashboardWidgetsListResponseResultsItemWidthMin).max(tracerDashboardWidgetsListResponseResultsItemWidthMax).optional(),
  "height": zod.number().min(tracerDashboardWidgetsListResponseResultsItemHeightMin).max(tracerDashboardWidgetsListResponseResultsItemHeightMax).optional(),
  "query_config": zod.object({

}).passthrough().optional(),
  "chart_config": zod.object({

}).passthrough().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
}))
})


export const TracerDashboardWidgetsCreateParams = zod.object({
  "dashboard_pk": zod.string()
})

export const tracerDashboardWidgetsCreateBodyNameMax = 255;

export const tracerDashboardWidgetsCreateBodyPositionMin = -2147483648;
export const tracerDashboardWidgetsCreateBodyPositionMax = 2147483647;

export const tracerDashboardWidgetsCreateBodyWidthMin = -2147483648;
export const tracerDashboardWidgetsCreateBodyWidthMax = 2147483647;

export const tracerDashboardWidgetsCreateBodyHeightMin = -2147483648;
export const tracerDashboardWidgetsCreateBodyHeightMax = 2147483647;



export const TracerDashboardWidgetsCreateBody = zod.object({
  "name": zod.string().min(1).max(tracerDashboardWidgetsCreateBodyNameMax).optional(),
  "description": zod.string().optional(),
  "position": zod.number().min(tracerDashboardWidgetsCreateBodyPositionMin).max(tracerDashboardWidgetsCreateBodyPositionMax).optional(),
  "width": zod.number().min(tracerDashboardWidgetsCreateBodyWidthMin).max(tracerDashboardWidgetsCreateBodyWidthMax).optional(),
  "height": zod.number().min(tracerDashboardWidgetsCreateBodyHeightMin).max(tracerDashboardWidgetsCreateBodyHeightMax).optional(),
  "query_config": zod.object({

}).passthrough().optional(),
  "chart_config": zod.object({

}).passthrough().optional()
})


/**
 * Execute an ad-hoc query_config without saving, for live preview.
 */
export const TracerDashboardWidgetsPreviewQueryParams = zod.object({
  "dashboard_pk": zod.string()
})

export const tracerDashboardWidgetsPreviewQueryBodyNameMax = 255;

export const tracerDashboardWidgetsPreviewQueryBodyPositionMin = -2147483648;
export const tracerDashboardWidgetsPreviewQueryBodyPositionMax = 2147483647;

export const tracerDashboardWidgetsPreviewQueryBodyWidthMin = -2147483648;
export const tracerDashboardWidgetsPreviewQueryBodyWidthMax = 2147483647;

export const tracerDashboardWidgetsPreviewQueryBodyHeightMin = -2147483648;
export const tracerDashboardWidgetsPreviewQueryBodyHeightMax = 2147483647;



export const TracerDashboardWidgetsPreviewQueryBody = zod.object({
  "name": zod.string().min(1).max(tracerDashboardWidgetsPreviewQueryBodyNameMax).optional(),
  "description": zod.string().optional(),
  "position": zod.number().min(tracerDashboardWidgetsPreviewQueryBodyPositionMin).max(tracerDashboardWidgetsPreviewQueryBodyPositionMax).optional(),
  "width": zod.number().min(tracerDashboardWidgetsPreviewQueryBodyWidthMin).max(tracerDashboardWidgetsPreviewQueryBodyWidthMax).optional(),
  "height": zod.number().min(tracerDashboardWidgetsPreviewQueryBodyHeightMin).max(tracerDashboardWidgetsPreviewQueryBodyHeightMax).optional(),
  "query_config": zod.object({

}).passthrough().optional(),
  "chart_config": zod.object({

}).passthrough().optional()
})


/**
 * Batch update widget positions.
 */
export const TracerDashboardWidgetsReorderParams = zod.object({
  "dashboard_pk": zod.string()
})

export const tracerDashboardWidgetsReorderBodyNameMax = 255;

export const tracerDashboardWidgetsReorderBodyPositionMin = -2147483648;
export const tracerDashboardWidgetsReorderBodyPositionMax = 2147483647;

export const tracerDashboardWidgetsReorderBodyWidthMin = -2147483648;
export const tracerDashboardWidgetsReorderBodyWidthMax = 2147483647;

export const tracerDashboardWidgetsReorderBodyHeightMin = -2147483648;
export const tracerDashboardWidgetsReorderBodyHeightMax = 2147483647;



export const TracerDashboardWidgetsReorderBody = zod.object({
  "name": zod.string().min(1).max(tracerDashboardWidgetsReorderBodyNameMax).optional(),
  "description": zod.string().optional(),
  "position": zod.number().min(tracerDashboardWidgetsReorderBodyPositionMin).max(tracerDashboardWidgetsReorderBodyPositionMax).optional(),
  "width": zod.number().min(tracerDashboardWidgetsReorderBodyWidthMin).max(tracerDashboardWidgetsReorderBodyWidthMax).optional(),
  "height": zod.number().min(tracerDashboardWidgetsReorderBodyHeightMin).max(tracerDashboardWidgetsReorderBodyHeightMax).optional(),
  "query_config": zod.object({

}).passthrough().optional(),
  "chart_config": zod.object({

}).passthrough().optional()
})


export const TracerDashboardWidgetsReadParams = zod.object({
  "dashboard_pk": zod.string(),
  "id": zod.string()
})

export const tracerDashboardWidgetsReadResponseNameMax = 255;

export const tracerDashboardWidgetsReadResponsePositionMin = -2147483648;
export const tracerDashboardWidgetsReadResponsePositionMax = 2147483647;

export const tracerDashboardWidgetsReadResponseWidthMin = -2147483648;
export const tracerDashboardWidgetsReadResponseWidthMax = 2147483647;

export const tracerDashboardWidgetsReadResponseHeightMin = -2147483648;
export const tracerDashboardWidgetsReadResponseHeightMax = 2147483647;



export const TracerDashboardWidgetsReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(tracerDashboardWidgetsReadResponseNameMax).optional(),
  "description": zod.string().optional(),
  "position": zod.number().min(tracerDashboardWidgetsReadResponsePositionMin).max(tracerDashboardWidgetsReadResponsePositionMax).optional(),
  "width": zod.number().min(tracerDashboardWidgetsReadResponseWidthMin).max(tracerDashboardWidgetsReadResponseWidthMax).optional(),
  "height": zod.number().min(tracerDashboardWidgetsReadResponseHeightMin).max(tracerDashboardWidgetsReadResponseHeightMax).optional(),
  "query_config": zod.object({

}).passthrough().optional(),
  "chart_config": zod.object({

}).passthrough().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


export const TracerDashboardWidgetsUpdateParams = zod.object({
  "dashboard_pk": zod.string(),
  "id": zod.string()
})

export const tracerDashboardWidgetsUpdateBodyNameMax = 255;

export const tracerDashboardWidgetsUpdateBodyPositionMin = -2147483648;
export const tracerDashboardWidgetsUpdateBodyPositionMax = 2147483647;

export const tracerDashboardWidgetsUpdateBodyWidthMin = -2147483648;
export const tracerDashboardWidgetsUpdateBodyWidthMax = 2147483647;

export const tracerDashboardWidgetsUpdateBodyHeightMin = -2147483648;
export const tracerDashboardWidgetsUpdateBodyHeightMax = 2147483647;



export const TracerDashboardWidgetsUpdateBody = zod.object({
  "name": zod.string().min(1).max(tracerDashboardWidgetsUpdateBodyNameMax).optional(),
  "description": zod.string().optional(),
  "position": zod.number().min(tracerDashboardWidgetsUpdateBodyPositionMin).max(tracerDashboardWidgetsUpdateBodyPositionMax).optional(),
  "width": zod.number().min(tracerDashboardWidgetsUpdateBodyWidthMin).max(tracerDashboardWidgetsUpdateBodyWidthMax).optional(),
  "height": zod.number().min(tracerDashboardWidgetsUpdateBodyHeightMin).max(tracerDashboardWidgetsUpdateBodyHeightMax).optional(),
  "query_config": zod.object({

}).passthrough().optional(),
  "chart_config": zod.object({

}).passthrough().optional()
})

export const tracerDashboardWidgetsUpdateResponseNameMax = 255;

export const tracerDashboardWidgetsUpdateResponsePositionMin = -2147483648;
export const tracerDashboardWidgetsUpdateResponsePositionMax = 2147483647;

export const tracerDashboardWidgetsUpdateResponseWidthMin = -2147483648;
export const tracerDashboardWidgetsUpdateResponseWidthMax = 2147483647;

export const tracerDashboardWidgetsUpdateResponseHeightMin = -2147483648;
export const tracerDashboardWidgetsUpdateResponseHeightMax = 2147483647;



export const TracerDashboardWidgetsUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(tracerDashboardWidgetsUpdateResponseNameMax).optional(),
  "description": zod.string().optional(),
  "position": zod.number().min(tracerDashboardWidgetsUpdateResponsePositionMin).max(tracerDashboardWidgetsUpdateResponsePositionMax).optional(),
  "width": zod.number().min(tracerDashboardWidgetsUpdateResponseWidthMin).max(tracerDashboardWidgetsUpdateResponseWidthMax).optional(),
  "height": zod.number().min(tracerDashboardWidgetsUpdateResponseHeightMin).max(tracerDashboardWidgetsUpdateResponseHeightMax).optional(),
  "query_config": zod.object({

}).passthrough().optional(),
  "chart_config": zod.object({

}).passthrough().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


export const TracerDashboardWidgetsPartialUpdateParams = zod.object({
  "dashboard_pk": zod.string(),
  "id": zod.string()
})

export const tracerDashboardWidgetsPartialUpdateBodyNameMax = 255;

export const tracerDashboardWidgetsPartialUpdateBodyPositionMin = -2147483648;
export const tracerDashboardWidgetsPartialUpdateBodyPositionMax = 2147483647;

export const tracerDashboardWidgetsPartialUpdateBodyWidthMin = -2147483648;
export const tracerDashboardWidgetsPartialUpdateBodyWidthMax = 2147483647;

export const tracerDashboardWidgetsPartialUpdateBodyHeightMin = -2147483648;
export const tracerDashboardWidgetsPartialUpdateBodyHeightMax = 2147483647;



export const TracerDashboardWidgetsPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(tracerDashboardWidgetsPartialUpdateBodyNameMax).optional(),
  "description": zod.string().optional(),
  "position": zod.number().min(tracerDashboardWidgetsPartialUpdateBodyPositionMin).max(tracerDashboardWidgetsPartialUpdateBodyPositionMax).optional(),
  "width": zod.number().min(tracerDashboardWidgetsPartialUpdateBodyWidthMin).max(tracerDashboardWidgetsPartialUpdateBodyWidthMax).optional(),
  "height": zod.number().min(tracerDashboardWidgetsPartialUpdateBodyHeightMin).max(tracerDashboardWidgetsPartialUpdateBodyHeightMax).optional(),
  "query_config": zod.object({

}).passthrough().optional(),
  "chart_config": zod.object({

}).passthrough().optional()
})

export const tracerDashboardWidgetsPartialUpdateResponseNameMax = 255;

export const tracerDashboardWidgetsPartialUpdateResponsePositionMin = -2147483648;
export const tracerDashboardWidgetsPartialUpdateResponsePositionMax = 2147483647;

export const tracerDashboardWidgetsPartialUpdateResponseWidthMin = -2147483648;
export const tracerDashboardWidgetsPartialUpdateResponseWidthMax = 2147483647;

export const tracerDashboardWidgetsPartialUpdateResponseHeightMin = -2147483648;
export const tracerDashboardWidgetsPartialUpdateResponseHeightMax = 2147483647;



export const TracerDashboardWidgetsPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(tracerDashboardWidgetsPartialUpdateResponseNameMax).optional(),
  "description": zod.string().optional(),
  "position": zod.number().min(tracerDashboardWidgetsPartialUpdateResponsePositionMin).max(tracerDashboardWidgetsPartialUpdateResponsePositionMax).optional(),
  "width": zod.number().min(tracerDashboardWidgetsPartialUpdateResponseWidthMin).max(tracerDashboardWidgetsPartialUpdateResponseWidthMax).optional(),
  "height": zod.number().min(tracerDashboardWidgetsPartialUpdateResponseHeightMin).max(tracerDashboardWidgetsPartialUpdateResponseHeightMax).optional(),
  "query_config": zod.object({

}).passthrough().optional(),
  "chart_config": zod.object({

}).passthrough().optional(),
  "created_by": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional()
})


export const TracerDashboardWidgetsDeleteParams = zod.object({
  "dashboard_pk": zod.string(),
  "id": zod.string()
})


/**
 * Duplicate a widget.
 */
export const TracerDashboardWidgetsDuplicateWidgetParams = zod.object({
  "dashboard_pk": zod.string(),
  "id": zod.string()
})

export const tracerDashboardWidgetsDuplicateWidgetBodyNameMax = 255;

export const tracerDashboardWidgetsDuplicateWidgetBodyPositionMin = -2147483648;
export const tracerDashboardWidgetsDuplicateWidgetBodyPositionMax = 2147483647;

export const tracerDashboardWidgetsDuplicateWidgetBodyWidthMin = -2147483648;
export const tracerDashboardWidgetsDuplicateWidgetBodyWidthMax = 2147483647;

export const tracerDashboardWidgetsDuplicateWidgetBodyHeightMin = -2147483648;
export const tracerDashboardWidgetsDuplicateWidgetBodyHeightMax = 2147483647;



export const TracerDashboardWidgetsDuplicateWidgetBody = zod.object({
  "name": zod.string().min(1).max(tracerDashboardWidgetsDuplicateWidgetBodyNameMax).optional(),
  "description": zod.string().optional(),
  "position": zod.number().min(tracerDashboardWidgetsDuplicateWidgetBodyPositionMin).max(tracerDashboardWidgetsDuplicateWidgetBodyPositionMax).optional(),
  "width": zod.number().min(tracerDashboardWidgetsDuplicateWidgetBodyWidthMin).max(tracerDashboardWidgetsDuplicateWidgetBodyWidthMax).optional(),
  "height": zod.number().min(tracerDashboardWidgetsDuplicateWidgetBodyHeightMin).max(tracerDashboardWidgetsDuplicateWidgetBodyHeightMax).optional(),
  "query_config": zod.object({

}).passthrough().optional(),
  "chart_config": zod.object({

}).passthrough().optional()
})


/**
 * Execute the widget's query_config against ClickHouse and return results.
 */
export const TracerDashboardWidgetsExecuteQueryParams = zod.object({
  "dashboard_pk": zod.string(),
  "id": zod.string()
})

export const tracerDashboardWidgetsExecuteQueryBodyNameMax = 255;

export const tracerDashboardWidgetsExecuteQueryBodyPositionMin = -2147483648;
export const tracerDashboardWidgetsExecuteQueryBodyPositionMax = 2147483647;

export const tracerDashboardWidgetsExecuteQueryBodyWidthMin = -2147483648;
export const tracerDashboardWidgetsExecuteQueryBodyWidthMax = 2147483647;

export const tracerDashboardWidgetsExecuteQueryBodyHeightMin = -2147483648;
export const tracerDashboardWidgetsExecuteQueryBodyHeightMax = 2147483647;



export const TracerDashboardWidgetsExecuteQueryBody = zod.object({
  "name": zod.string().min(1).max(tracerDashboardWidgetsExecuteQueryBodyNameMax).optional(),
  "description": zod.string().optional(),
  "position": zod.number().min(tracerDashboardWidgetsExecuteQueryBodyPositionMin).max(tracerDashboardWidgetsExecuteQueryBodyPositionMax).optional(),
  "width": zod.number().min(tracerDashboardWidgetsExecuteQueryBodyWidthMin).max(tracerDashboardWidgetsExecuteQueryBodyWidthMax).optional(),
  "height": zod.number().min(tracerDashboardWidgetsExecuteQueryBodyHeightMin).max(tracerDashboardWidgetsExecuteQueryBodyHeightMax).optional(),
  "query_config": zod.object({

}).passthrough().optional(),
  "chart_config": zod.object({

}).passthrough().optional()
})


export const TracerDashboardReadParams = zod.object({
  "id": zod.string()
})

export const tracerDashboardReadResponseNameMax = 255;

export const tracerDashboardReadResponseCreatedByEmailMax = 254;

export const tracerDashboardReadResponseCreatedByNameMax = 255;

export const tracerDashboardReadResponseCreatedByOrganizationNameMax = 255;

export const tracerDashboardReadResponseCreatedByOrganizationDisplayNameMax = 255;

export const tracerDashboardReadResponseCreatedByOrganizationRegionMax = 16;

export const tracerDashboardReadResponseCreatedByOrganizationRequire2faGracePeriodDaysMin = 0;
export const tracerDashboardReadResponseCreatedByOrganizationRequire2faGracePeriodDaysMax = 32767;

export const tracerDashboardReadResponseCreatedByRoleMax = 255;

export const tracerDashboardReadResponseUpdatedByEmailMax = 254;

export const tracerDashboardReadResponseUpdatedByNameMax = 255;

export const tracerDashboardReadResponseUpdatedByOrganizationNameMax = 255;

export const tracerDashboardReadResponseUpdatedByOrganizationDisplayNameMax = 255;

export const tracerDashboardReadResponseUpdatedByOrganizationRegionMax = 16;

export const tracerDashboardReadResponseUpdatedByOrganizationRequire2faGracePeriodDaysMin = 0;
export const tracerDashboardReadResponseUpdatedByOrganizationRequire2faGracePeriodDaysMax = 32767;

export const tracerDashboardReadResponseUpdatedByRoleMax = 255;



export const TracerDashboardReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "name": zod.string().min(1).max(tracerDashboardReadResponseNameMax),
  "description": zod.string().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_by": zod.object({
  "id": zod.string().uuid().optional(),
  "email": zod.string().email().min(1).max(tracerDashboardReadResponseCreatedByEmailMax),
  "name": zod.string().min(1).max(tracerDashboardReadResponseCreatedByNameMax),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional(),
  "organization": zod.object({
  "id": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1).max(tracerDashboardReadResponseCreatedByOrganizationNameMax),
  "display_name": zod.string().max(tracerDashboardReadResponseCreatedByOrganizationDisplayNameMax).optional(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).max(tracerDashboardReadResponseCreatedByOrganizationRegionMax).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().min(tracerDashboardReadResponseCreatedByOrganizationRequire2faGracePeriodDaysMin).max(tracerDashboardReadResponseCreatedByOrganizationRequire2faGracePeriodDaysMax).optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "status": zod.string().optional(),
  "role": zod.string().max(tracerDashboardReadResponseCreatedByRoleMax).optional().describe('User\'s job role (e.g., Data Scientist, ML Engineer, or custom role)'),
  "goals": zod.object({

}).passthrough().optional().describe('List of user\'s goals for using the platform')
}).optional(),
  "updated_by": zod.object({
  "id": zod.string().uuid().optional(),
  "email": zod.string().email().min(1).max(tracerDashboardReadResponseUpdatedByEmailMax),
  "name": zod.string().min(1).max(tracerDashboardReadResponseUpdatedByNameMax),
  "organization_role": zod.enum(['Owner', 'Admin', 'Member', 'Viewer', 'workspace_admin', 'workspace_member', 'workspace_viewer']).optional(),
  "organization": zod.object({
  "id": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "name": zod.string().min(1).max(tracerDashboardReadResponseUpdatedByOrganizationNameMax),
  "display_name": zod.string().max(tracerDashboardReadResponseUpdatedByOrganizationDisplayNameMax).optional(),
  "is_new": zod.boolean().optional(),
  "ws_enabled": zod.boolean().optional(),
  "region": zod.string().min(1).max(tracerDashboardReadResponseUpdatedByOrganizationRegionMax).optional(),
  "require_2fa": zod.boolean().optional(),
  "require_2fa_grace_period_days": zod.number().min(tracerDashboardReadResponseUpdatedByOrganizationRequire2faGracePeriodDaysMin).max(tracerDashboardReadResponseUpdatedByOrganizationRequire2faGracePeriodDaysMax).optional(),
  "require_2fa_enforced_at": zod.string().datetime({"offset":true}).optional()
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "status": zod.string().optional(),
  "role": zod.string().max(tracerDashboardReadResponseUpdatedByRoleMax).optional().describe('User\'s job role (e.g., Data Scientist, ML Engineer, or custom role)'),
  "goals": zod.object({

}).passthrough().optional().describe('List of user\'s goals for using the platform')
}).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "widgets": zod.string().optional()
})


export const TracerDashboardUpdateParams = zod.object({
  "id": zod.string()
})

export const tracerDashboardUpdateBodyNameMax = 255;



export const TracerDashboardUpdateBody = zod.object({
  "name": zod.string().min(1).max(tracerDashboardUpdateBodyNameMax),
  "description": zod.string().optional()
})

export const tracerDashboardUpdateResponseNameMax = 255;



export const TracerDashboardUpdateResponse = zod.object({
  "name": zod.string().min(1).max(tracerDashboardUpdateResponseNameMax),
  "description": zod.string().optional()
})


export const TracerDashboardPartialUpdateParams = zod.object({
  "id": zod.string()
})

export const tracerDashboardPartialUpdateBodyNameMax = 255;



export const TracerDashboardPartialUpdateBody = zod.object({
  "name": zod.string().min(1).max(tracerDashboardPartialUpdateBodyNameMax),
  "description": zod.string().optional()
})

export const tracerDashboardPartialUpdateResponseNameMax = 255;



export const TracerDashboardPartialUpdateResponse = zod.object({
  "name": zod.string().min(1).max(tracerDashboardPartialUpdateResponseNameMax),
  "description": zod.string().optional()
})


export const TracerDashboardDeleteParams = zod.object({
  "id": zod.string()
})


export const TracerObservationSpanListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})


export const tracerObservationSpanListResponseResultsItemParentSpanIdMax = 255;

export const tracerObservationSpanListResponseResultsItemNameMax = 2000;

export const tracerObservationSpanListResponseResultsItemModelMax = 255;

export const tracerObservationSpanListResponseResultsItemLatencyMsMin = -2147483648;
export const tracerObservationSpanListResponseResultsItemLatencyMsMax = 2147483647;

export const tracerObservationSpanListResponseResultsItemPromptTokensMin = -2147483648;
export const tracerObservationSpanListResponseResultsItemPromptTokensMax = 2147483647;

export const tracerObservationSpanListResponseResultsItemCompletionTokensMin = -2147483648;
export const tracerObservationSpanListResponseResultsItemCompletionTokensMax = 2147483647;

export const tracerObservationSpanListResponseResultsItemTotalTokensMin = -2147483648;
export const tracerObservationSpanListResponseResultsItemTotalTokensMax = 2147483647;

export const tracerObservationSpanListResponseResultsItemEvalIdMax = 255;

export const tracerObservationSpanListResponseResultsItemProviderMax = 255;



export const TracerObservationSpanListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().min(1).optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanListResponseResultsItemParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanListResponseResultsItemNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanListResponseResultsItemModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanListResponseResultsItemLatencyMsMin).max(tracerObservationSpanListResponseResultsItemLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanListResponseResultsItemPromptTokensMin).max(tracerObservationSpanListResponseResultsItemPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanListResponseResultsItemCompletionTokensMin).max(tracerObservationSpanListResponseResultsItemCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanListResponseResultsItemTotalTokensMin).max(tracerObservationSpanListResponseResultsItemTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanListResponseResultsItemEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanListResponseResultsItemProviderMax).optional(),
  "provider_logo": zod.string().optional(),
  "span_attributes": zod.string().optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
}))
})


export const tracerObservationSpanCreateBodyParentSpanIdMax = 255;

export const tracerObservationSpanCreateBodyNameMax = 2000;

export const tracerObservationSpanCreateBodyModelMax = 255;

export const tracerObservationSpanCreateBodyLatencyMsMin = -2147483648;
export const tracerObservationSpanCreateBodyLatencyMsMax = 2147483647;

export const tracerObservationSpanCreateBodyPromptTokensMin = -2147483648;
export const tracerObservationSpanCreateBodyPromptTokensMax = 2147483647;

export const tracerObservationSpanCreateBodyCompletionTokensMin = -2147483648;
export const tracerObservationSpanCreateBodyCompletionTokensMax = 2147483647;

export const tracerObservationSpanCreateBodyTotalTokensMin = -2147483648;
export const tracerObservationSpanCreateBodyTotalTokensMax = 2147483647;

export const tracerObservationSpanCreateBodyEvalIdMax = 255;

export const tracerObservationSpanCreateBodyProviderMax = 255;



export const TracerObservationSpanCreateBody = zod.object({
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanCreateBodyParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanCreateBodyNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanCreateBodyModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanCreateBodyLatencyMsMin).max(tracerObservationSpanCreateBodyLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanCreateBodyPromptTokensMin).max(tracerObservationSpanCreateBodyPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanCreateBodyCompletionTokensMin).max(tracerObservationSpanCreateBodyCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanCreateBodyTotalTokensMin).max(tracerObservationSpanCreateBodyTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanCreateBodyEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanCreateBodyProviderMax).optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
})


export const TracerObservationSpanAddAnnotationsBody = zod.object({
  "observation_span_id": zod.string().optional(),
  "trace_id": zod.string().uuid().optional(),
  "annotation_values": zod.record(zod.string(), zod.object({

}).passthrough()),
  "notes": zod.string().optional()
})


export const tracerObservationSpanBulkCreateBodyParentSpanIdMax = 255;

export const tracerObservationSpanBulkCreateBodyNameMax = 2000;

export const tracerObservationSpanBulkCreateBodyModelMax = 255;

export const tracerObservationSpanBulkCreateBodyLatencyMsMin = -2147483648;
export const tracerObservationSpanBulkCreateBodyLatencyMsMax = 2147483647;

export const tracerObservationSpanBulkCreateBodyPromptTokensMin = -2147483648;
export const tracerObservationSpanBulkCreateBodyPromptTokensMax = 2147483647;

export const tracerObservationSpanBulkCreateBodyCompletionTokensMin = -2147483648;
export const tracerObservationSpanBulkCreateBodyCompletionTokensMax = 2147483647;

export const tracerObservationSpanBulkCreateBodyTotalTokensMin = -2147483648;
export const tracerObservationSpanBulkCreateBodyTotalTokensMax = 2147483647;

export const tracerObservationSpanBulkCreateBodyEvalIdMax = 255;

export const tracerObservationSpanBulkCreateBodyProviderMax = 255;



export const TracerObservationSpanBulkCreateBody = zod.object({
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanBulkCreateBodyParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanBulkCreateBodyNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanBulkCreateBodyModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanBulkCreateBodyLatencyMsMin).max(tracerObservationSpanBulkCreateBodyLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanBulkCreateBodyPromptTokensMin).max(tracerObservationSpanBulkCreateBodyPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanBulkCreateBodyCompletionTokensMin).max(tracerObservationSpanBulkCreateBodyCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanBulkCreateBodyTotalTokensMin).max(tracerObservationSpanBulkCreateBodyTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanBulkCreateBodyEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanBulkCreateBodyProviderMax).optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
})


export const tracerObservationSpanCreateOtelSpanBodyParentSpanIdMax = 255;

export const tracerObservationSpanCreateOtelSpanBodyNameMax = 2000;

export const tracerObservationSpanCreateOtelSpanBodyModelMax = 255;

export const tracerObservationSpanCreateOtelSpanBodyLatencyMsMin = -2147483648;
export const tracerObservationSpanCreateOtelSpanBodyLatencyMsMax = 2147483647;

export const tracerObservationSpanCreateOtelSpanBodyPromptTokensMin = -2147483648;
export const tracerObservationSpanCreateOtelSpanBodyPromptTokensMax = 2147483647;

export const tracerObservationSpanCreateOtelSpanBodyCompletionTokensMin = -2147483648;
export const tracerObservationSpanCreateOtelSpanBodyCompletionTokensMax = 2147483647;

export const tracerObservationSpanCreateOtelSpanBodyTotalTokensMin = -2147483648;
export const tracerObservationSpanCreateOtelSpanBodyTotalTokensMax = 2147483647;

export const tracerObservationSpanCreateOtelSpanBodyEvalIdMax = 255;

export const tracerObservationSpanCreateOtelSpanBodyProviderMax = 255;



export const TracerObservationSpanCreateOtelSpanBody = zod.object({
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanCreateOtelSpanBodyParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanCreateOtelSpanBodyNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanCreateOtelSpanBodyModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanCreateOtelSpanBodyLatencyMsMin).max(tracerObservationSpanCreateOtelSpanBodyLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanCreateOtelSpanBodyPromptTokensMin).max(tracerObservationSpanCreateOtelSpanBodyPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanCreateOtelSpanBodyCompletionTokensMin).max(tracerObservationSpanCreateOtelSpanBodyCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanCreateOtelSpanBodyTotalTokensMin).max(tracerObservationSpanCreateOtelSpanBodyTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanCreateOtelSpanBodyEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanCreateOtelSpanBodyProviderMax).optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
})


/**
 * Query params:
    filters: JSON {"project_id": "<uuid>"} (required)
    row_type: spans | traces | sessions (default spans;
              voiceCalls aliases to spans)
 * @summary Attribute paths the EvalPicker exposes per row_type.
 */
export const TracerObservationSpanGetEvalAttributesListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})


export const tracerObservationSpanGetEvalAttributesListResponseResultsItemParentSpanIdMax = 255;

export const tracerObservationSpanGetEvalAttributesListResponseResultsItemNameMax = 2000;

export const tracerObservationSpanGetEvalAttributesListResponseResultsItemModelMax = 255;

export const tracerObservationSpanGetEvalAttributesListResponseResultsItemLatencyMsMin = -2147483648;
export const tracerObservationSpanGetEvalAttributesListResponseResultsItemLatencyMsMax = 2147483647;

export const tracerObservationSpanGetEvalAttributesListResponseResultsItemPromptTokensMin = -2147483648;
export const tracerObservationSpanGetEvalAttributesListResponseResultsItemPromptTokensMax = 2147483647;

export const tracerObservationSpanGetEvalAttributesListResponseResultsItemCompletionTokensMin = -2147483648;
export const tracerObservationSpanGetEvalAttributesListResponseResultsItemCompletionTokensMax = 2147483647;

export const tracerObservationSpanGetEvalAttributesListResponseResultsItemTotalTokensMin = -2147483648;
export const tracerObservationSpanGetEvalAttributesListResponseResultsItemTotalTokensMax = 2147483647;

export const tracerObservationSpanGetEvalAttributesListResponseResultsItemEvalIdMax = 255;

export const tracerObservationSpanGetEvalAttributesListResponseResultsItemProviderMax = 255;



export const TracerObservationSpanGetEvalAttributesListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().min(1).optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanGetEvalAttributesListResponseResultsItemParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanGetEvalAttributesListResponseResultsItemNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanGetEvalAttributesListResponseResultsItemModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanGetEvalAttributesListResponseResultsItemLatencyMsMin).max(tracerObservationSpanGetEvalAttributesListResponseResultsItemLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanGetEvalAttributesListResponseResultsItemPromptTokensMin).max(tracerObservationSpanGetEvalAttributesListResponseResultsItemPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanGetEvalAttributesListResponseResultsItemCompletionTokensMin).max(tracerObservationSpanGetEvalAttributesListResponseResultsItemCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanGetEvalAttributesListResponseResultsItemTotalTokensMin).max(tracerObservationSpanGetEvalAttributesListResponseResultsItemTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanGetEvalAttributesListResponseResultsItemEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanGetEvalAttributesListResponseResultsItemProviderMax).optional(),
  "provider_logo": zod.string().optional(),
  "span_attributes": zod.string().optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
}))
})


export const TracerObservationSpanGetEvaluationDetailsQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})


export const tracerObservationSpanGetEvaluationDetailsResponseResultsItemParentSpanIdMax = 255;

export const tracerObservationSpanGetEvaluationDetailsResponseResultsItemNameMax = 2000;

export const tracerObservationSpanGetEvaluationDetailsResponseResultsItemModelMax = 255;

export const tracerObservationSpanGetEvaluationDetailsResponseResultsItemLatencyMsMin = -2147483648;
export const tracerObservationSpanGetEvaluationDetailsResponseResultsItemLatencyMsMax = 2147483647;

export const tracerObservationSpanGetEvaluationDetailsResponseResultsItemPromptTokensMin = -2147483648;
export const tracerObservationSpanGetEvaluationDetailsResponseResultsItemPromptTokensMax = 2147483647;

export const tracerObservationSpanGetEvaluationDetailsResponseResultsItemCompletionTokensMin = -2147483648;
export const tracerObservationSpanGetEvaluationDetailsResponseResultsItemCompletionTokensMax = 2147483647;

export const tracerObservationSpanGetEvaluationDetailsResponseResultsItemTotalTokensMin = -2147483648;
export const tracerObservationSpanGetEvaluationDetailsResponseResultsItemTotalTokensMax = 2147483647;

export const tracerObservationSpanGetEvaluationDetailsResponseResultsItemEvalIdMax = 255;

export const tracerObservationSpanGetEvaluationDetailsResponseResultsItemProviderMax = 255;



export const TracerObservationSpanGetEvaluationDetailsResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().min(1).optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanGetEvaluationDetailsResponseResultsItemParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanGetEvaluationDetailsResponseResultsItemNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanGetEvaluationDetailsResponseResultsItemModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanGetEvaluationDetailsResponseResultsItemLatencyMsMin).max(tracerObservationSpanGetEvaluationDetailsResponseResultsItemLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanGetEvaluationDetailsResponseResultsItemPromptTokensMin).max(tracerObservationSpanGetEvaluationDetailsResponseResultsItemPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanGetEvaluationDetailsResponseResultsItemCompletionTokensMin).max(tracerObservationSpanGetEvaluationDetailsResponseResultsItemCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanGetEvaluationDetailsResponseResultsItemTotalTokensMin).max(tracerObservationSpanGetEvaluationDetailsResponseResultsItemTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanGetEvaluationDetailsResponseResultsItemEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanGetEvaluationDetailsResponseResultsItemProviderMax).optional(),
  "provider_logo": zod.string().optional(),
  "span_attributes": zod.string().optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
}))
})


/**
 * Fetch data for the observe graph with optimized queries
 */
export const tracerObservationSpanGetGraphMethodsBodyParentSpanIdMax = 255;

export const tracerObservationSpanGetGraphMethodsBodyNameMax = 2000;

export const tracerObservationSpanGetGraphMethodsBodyModelMax = 255;

export const tracerObservationSpanGetGraphMethodsBodyLatencyMsMin = -2147483648;
export const tracerObservationSpanGetGraphMethodsBodyLatencyMsMax = 2147483647;

export const tracerObservationSpanGetGraphMethodsBodyPromptTokensMin = -2147483648;
export const tracerObservationSpanGetGraphMethodsBodyPromptTokensMax = 2147483647;

export const tracerObservationSpanGetGraphMethodsBodyCompletionTokensMin = -2147483648;
export const tracerObservationSpanGetGraphMethodsBodyCompletionTokensMax = 2147483647;

export const tracerObservationSpanGetGraphMethodsBodyTotalTokensMin = -2147483648;
export const tracerObservationSpanGetGraphMethodsBodyTotalTokensMax = 2147483647;

export const tracerObservationSpanGetGraphMethodsBodyEvalIdMax = 255;

export const tracerObservationSpanGetGraphMethodsBodyProviderMax = 255;



export const TracerObservationSpanGetGraphMethodsBody = zod.object({
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanGetGraphMethodsBodyParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanGetGraphMethodsBodyNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanGetGraphMethodsBodyModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanGetGraphMethodsBodyLatencyMsMin).max(tracerObservationSpanGetGraphMethodsBodyLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanGetGraphMethodsBodyPromptTokensMin).max(tracerObservationSpanGetGraphMethodsBodyPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanGetGraphMethodsBodyCompletionTokensMin).max(tracerObservationSpanGetGraphMethodsBodyCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanGetGraphMethodsBodyTotalTokensMin).max(tracerObservationSpanGetGraphMethodsBodyTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanGetGraphMethodsBodyEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanGetGraphMethodsBodyProviderMax).optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
})


export const TracerObservationSpanGetObservationSpanFieldsQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})


export const tracerObservationSpanGetObservationSpanFieldsResponseResultsItemParentSpanIdMax = 255;

export const tracerObservationSpanGetObservationSpanFieldsResponseResultsItemNameMax = 2000;

export const tracerObservationSpanGetObservationSpanFieldsResponseResultsItemModelMax = 255;

export const tracerObservationSpanGetObservationSpanFieldsResponseResultsItemLatencyMsMin = -2147483648;
export const tracerObservationSpanGetObservationSpanFieldsResponseResultsItemLatencyMsMax = 2147483647;

export const tracerObservationSpanGetObservationSpanFieldsResponseResultsItemPromptTokensMin = -2147483648;
export const tracerObservationSpanGetObservationSpanFieldsResponseResultsItemPromptTokensMax = 2147483647;

export const tracerObservationSpanGetObservationSpanFieldsResponseResultsItemCompletionTokensMin = -2147483648;
export const tracerObservationSpanGetObservationSpanFieldsResponseResultsItemCompletionTokensMax = 2147483647;

export const tracerObservationSpanGetObservationSpanFieldsResponseResultsItemTotalTokensMin = -2147483648;
export const tracerObservationSpanGetObservationSpanFieldsResponseResultsItemTotalTokensMax = 2147483647;

export const tracerObservationSpanGetObservationSpanFieldsResponseResultsItemEvalIdMax = 255;

export const tracerObservationSpanGetObservationSpanFieldsResponseResultsItemProviderMax = 255;



export const TracerObservationSpanGetObservationSpanFieldsResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().min(1).optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanGetObservationSpanFieldsResponseResultsItemParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanGetObservationSpanFieldsResponseResultsItemNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanGetObservationSpanFieldsResponseResultsItemModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanGetObservationSpanFieldsResponseResultsItemLatencyMsMin).max(tracerObservationSpanGetObservationSpanFieldsResponseResultsItemLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanGetObservationSpanFieldsResponseResultsItemPromptTokensMin).max(tracerObservationSpanGetObservationSpanFieldsResponseResultsItemPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanGetObservationSpanFieldsResponseResultsItemCompletionTokensMin).max(tracerObservationSpanGetObservationSpanFieldsResponseResultsItemCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanGetObservationSpanFieldsResponseResultsItemTotalTokensMin).max(tracerObservationSpanGetObservationSpanFieldsResponseResultsItemTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanGetObservationSpanFieldsResponseResultsItemEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanGetObservationSpanFieldsResponseResultsItemProviderMax).optional(),
  "provider_logo": zod.string().optional(),
  "span_attributes": zod.string().optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
}))
})


/**
 * Query params:
    filters: JSON {"project_id": "<uuid>"} (required)
 * @summary Distinct span_attributes keys for a project (spans surface).
 */
export const TracerObservationSpanGetSpanAttributesListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})


export const tracerObservationSpanGetSpanAttributesListResponseResultsItemParentSpanIdMax = 255;

export const tracerObservationSpanGetSpanAttributesListResponseResultsItemNameMax = 2000;

export const tracerObservationSpanGetSpanAttributesListResponseResultsItemModelMax = 255;

export const tracerObservationSpanGetSpanAttributesListResponseResultsItemLatencyMsMin = -2147483648;
export const tracerObservationSpanGetSpanAttributesListResponseResultsItemLatencyMsMax = 2147483647;

export const tracerObservationSpanGetSpanAttributesListResponseResultsItemPromptTokensMin = -2147483648;
export const tracerObservationSpanGetSpanAttributesListResponseResultsItemPromptTokensMax = 2147483647;

export const tracerObservationSpanGetSpanAttributesListResponseResultsItemCompletionTokensMin = -2147483648;
export const tracerObservationSpanGetSpanAttributesListResponseResultsItemCompletionTokensMax = 2147483647;

export const tracerObservationSpanGetSpanAttributesListResponseResultsItemTotalTokensMin = -2147483648;
export const tracerObservationSpanGetSpanAttributesListResponseResultsItemTotalTokensMax = 2147483647;

export const tracerObservationSpanGetSpanAttributesListResponseResultsItemEvalIdMax = 255;

export const tracerObservationSpanGetSpanAttributesListResponseResultsItemProviderMax = 255;



export const TracerObservationSpanGetSpanAttributesListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().min(1).optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanGetSpanAttributesListResponseResultsItemParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanGetSpanAttributesListResponseResultsItemNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanGetSpanAttributesListResponseResultsItemModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanGetSpanAttributesListResponseResultsItemLatencyMsMin).max(tracerObservationSpanGetSpanAttributesListResponseResultsItemLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanGetSpanAttributesListResponseResultsItemPromptTokensMin).max(tracerObservationSpanGetSpanAttributesListResponseResultsItemPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanGetSpanAttributesListResponseResultsItemCompletionTokensMin).max(tracerObservationSpanGetSpanAttributesListResponseResultsItemCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanGetSpanAttributesListResponseResultsItemTotalTokensMin).max(tracerObservationSpanGetSpanAttributesListResponseResultsItemTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanGetSpanAttributesListResponseResultsItemEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanGetSpanAttributesListResponseResultsItemProviderMax).optional(),
  "provider_logo": zod.string().optional(),
  "span_attributes": zod.string().optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
}))
})


export const TracerObservationSpanGetSpansExportDataQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})


export const tracerObservationSpanGetSpansExportDataResponseResultsItemParentSpanIdMax = 255;

export const tracerObservationSpanGetSpansExportDataResponseResultsItemNameMax = 2000;

export const tracerObservationSpanGetSpansExportDataResponseResultsItemModelMax = 255;

export const tracerObservationSpanGetSpansExportDataResponseResultsItemLatencyMsMin = -2147483648;
export const tracerObservationSpanGetSpansExportDataResponseResultsItemLatencyMsMax = 2147483647;

export const tracerObservationSpanGetSpansExportDataResponseResultsItemPromptTokensMin = -2147483648;
export const tracerObservationSpanGetSpansExportDataResponseResultsItemPromptTokensMax = 2147483647;

export const tracerObservationSpanGetSpansExportDataResponseResultsItemCompletionTokensMin = -2147483648;
export const tracerObservationSpanGetSpansExportDataResponseResultsItemCompletionTokensMax = 2147483647;

export const tracerObservationSpanGetSpansExportDataResponseResultsItemTotalTokensMin = -2147483648;
export const tracerObservationSpanGetSpansExportDataResponseResultsItemTotalTokensMax = 2147483647;

export const tracerObservationSpanGetSpansExportDataResponseResultsItemEvalIdMax = 255;

export const tracerObservationSpanGetSpansExportDataResponseResultsItemProviderMax = 255;



export const TracerObservationSpanGetSpansExportDataResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().min(1).optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanGetSpansExportDataResponseResultsItemParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanGetSpansExportDataResponseResultsItemNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanGetSpansExportDataResponseResultsItemModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanGetSpansExportDataResponseResultsItemLatencyMsMin).max(tracerObservationSpanGetSpansExportDataResponseResultsItemLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanGetSpansExportDataResponseResultsItemPromptTokensMin).max(tracerObservationSpanGetSpansExportDataResponseResultsItemPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanGetSpansExportDataResponseResultsItemCompletionTokensMin).max(tracerObservationSpanGetSpansExportDataResponseResultsItemCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanGetSpansExportDataResponseResultsItemTotalTokensMin).max(tracerObservationSpanGetSpansExportDataResponseResultsItemTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanGetSpansExportDataResponseResultsItemEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanGetSpansExportDataResponseResultsItemProviderMax).optional(),
  "provider_logo": zod.string().optional(),
  "span_attributes": zod.string().optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
}))
})


/**
 * Get the previous and next span id by index for non-observe projects.
Mirrors the query/filter logic of list_spans.
 */
export const TracerObservationSpanGetTraceIdByIndexSpansAsBaseQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})


export const tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemParentSpanIdMax = 255;

export const tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemNameMax = 2000;

export const tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemModelMax = 255;

export const tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemLatencyMsMin = -2147483648;
export const tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemLatencyMsMax = 2147483647;

export const tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemPromptTokensMin = -2147483648;
export const tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemPromptTokensMax = 2147483647;

export const tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemCompletionTokensMin = -2147483648;
export const tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemCompletionTokensMax = 2147483647;

export const tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemTotalTokensMin = -2147483648;
export const tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemTotalTokensMax = 2147483647;

export const tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemEvalIdMax = 255;

export const tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemProviderMax = 255;



export const TracerObservationSpanGetTraceIdByIndexSpansAsBaseResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().min(1).optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemLatencyMsMin).max(tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemPromptTokensMin).max(tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemCompletionTokensMin).max(tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemTotalTokensMin).max(tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanGetTraceIdByIndexSpansAsBaseResponseResultsItemProviderMax).optional(),
  "provider_logo": zod.string().optional(),
  "span_attributes": zod.string().optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
}))
})


/**
 * Get the previous and next trace id by index for observe projects.
Mirrors the query/filter logic of list_spans_as_observe.
 */
export const TracerObservationSpanGetTraceIdByIndexSpansAsObserveQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})


export const tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemParentSpanIdMax = 255;

export const tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemNameMax = 2000;

export const tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemModelMax = 255;

export const tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemLatencyMsMin = -2147483648;
export const tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemLatencyMsMax = 2147483647;

export const tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemPromptTokensMin = -2147483648;
export const tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemPromptTokensMax = 2147483647;

export const tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemCompletionTokensMin = -2147483648;
export const tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemCompletionTokensMax = 2147483647;

export const tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemTotalTokensMin = -2147483648;
export const tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemTotalTokensMax = 2147483647;

export const tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemEvalIdMax = 255;

export const tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemProviderMax = 255;



export const TracerObservationSpanGetTraceIdByIndexSpansAsObserveResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().min(1).optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemLatencyMsMin).max(tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemPromptTokensMin).max(tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemCompletionTokensMin).max(tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemTotalTokensMin).max(tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanGetTraceIdByIndexSpansAsObserveResponseResultsItemProviderMax).optional(),
  "provider_logo": zod.string().optional(),
  "span_attributes": zod.string().optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
}))
})


/**
 * List spans filtered by project ID and project version ID with optimized queries.
 */
export const TracerObservationSpanListSpansQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})


export const tracerObservationSpanListSpansResponseResultsItemParentSpanIdMax = 255;

export const tracerObservationSpanListSpansResponseResultsItemNameMax = 2000;

export const tracerObservationSpanListSpansResponseResultsItemModelMax = 255;

export const tracerObservationSpanListSpansResponseResultsItemLatencyMsMin = -2147483648;
export const tracerObservationSpanListSpansResponseResultsItemLatencyMsMax = 2147483647;

export const tracerObservationSpanListSpansResponseResultsItemPromptTokensMin = -2147483648;
export const tracerObservationSpanListSpansResponseResultsItemPromptTokensMax = 2147483647;

export const tracerObservationSpanListSpansResponseResultsItemCompletionTokensMin = -2147483648;
export const tracerObservationSpanListSpansResponseResultsItemCompletionTokensMax = 2147483647;

export const tracerObservationSpanListSpansResponseResultsItemTotalTokensMin = -2147483648;
export const tracerObservationSpanListSpansResponseResultsItemTotalTokensMax = 2147483647;

export const tracerObservationSpanListSpansResponseResultsItemEvalIdMax = 255;

export const tracerObservationSpanListSpansResponseResultsItemProviderMax = 255;



export const TracerObservationSpanListSpansResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().min(1).optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanListSpansResponseResultsItemParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanListSpansResponseResultsItemNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanListSpansResponseResultsItemModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanListSpansResponseResultsItemLatencyMsMin).max(tracerObservationSpanListSpansResponseResultsItemLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanListSpansResponseResultsItemPromptTokensMin).max(tracerObservationSpanListSpansResponseResultsItemPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanListSpansResponseResultsItemCompletionTokensMin).max(tracerObservationSpanListSpansResponseResultsItemCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanListSpansResponseResultsItemTotalTokensMin).max(tracerObservationSpanListSpansResponseResultsItemTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanListSpansResponseResultsItemEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanListSpansResponseResultsItemProviderMax).optional(),
  "provider_logo": zod.string().optional(),
  "span_attributes": zod.string().optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
}))
})


export const TracerObservationSpanListSpansObserveQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})


export const tracerObservationSpanListSpansObserveResponseResultsItemParentSpanIdMax = 255;

export const tracerObservationSpanListSpansObserveResponseResultsItemNameMax = 2000;

export const tracerObservationSpanListSpansObserveResponseResultsItemModelMax = 255;

export const tracerObservationSpanListSpansObserveResponseResultsItemLatencyMsMin = -2147483648;
export const tracerObservationSpanListSpansObserveResponseResultsItemLatencyMsMax = 2147483647;

export const tracerObservationSpanListSpansObserveResponseResultsItemPromptTokensMin = -2147483648;
export const tracerObservationSpanListSpansObserveResponseResultsItemPromptTokensMax = 2147483647;

export const tracerObservationSpanListSpansObserveResponseResultsItemCompletionTokensMin = -2147483648;
export const tracerObservationSpanListSpansObserveResponseResultsItemCompletionTokensMax = 2147483647;

export const tracerObservationSpanListSpansObserveResponseResultsItemTotalTokensMin = -2147483648;
export const tracerObservationSpanListSpansObserveResponseResultsItemTotalTokensMax = 2147483647;

export const tracerObservationSpanListSpansObserveResponseResultsItemEvalIdMax = 255;

export const tracerObservationSpanListSpansObserveResponseResultsItemProviderMax = 255;



export const TracerObservationSpanListSpansObserveResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().min(1).optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanListSpansObserveResponseResultsItemParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanListSpansObserveResponseResultsItemNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanListSpansObserveResponseResultsItemModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanListSpansObserveResponseResultsItemLatencyMsMin).max(tracerObservationSpanListSpansObserveResponseResultsItemLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanListSpansObserveResponseResultsItemPromptTokensMin).max(tracerObservationSpanListSpansObserveResponseResultsItemPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanListSpansObserveResponseResultsItemCompletionTokensMin).max(tracerObservationSpanListSpansObserveResponseResultsItemCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanListSpansObserveResponseResultsItemTotalTokensMin).max(tracerObservationSpanListSpansObserveResponseResultsItemTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanListSpansObserveResponseResultsItemEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanListSpansObserveResponseResultsItemProviderMax).optional(),
  "provider_logo": zod.string().optional(),
  "span_attributes": zod.string().optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
}))
})


export const TracerObservationSpanRetrieveLoadingQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})


export const tracerObservationSpanRetrieveLoadingResponseResultsItemParentSpanIdMax = 255;

export const tracerObservationSpanRetrieveLoadingResponseResultsItemNameMax = 2000;

export const tracerObservationSpanRetrieveLoadingResponseResultsItemModelMax = 255;

export const tracerObservationSpanRetrieveLoadingResponseResultsItemLatencyMsMin = -2147483648;
export const tracerObservationSpanRetrieveLoadingResponseResultsItemLatencyMsMax = 2147483647;

export const tracerObservationSpanRetrieveLoadingResponseResultsItemPromptTokensMin = -2147483648;
export const tracerObservationSpanRetrieveLoadingResponseResultsItemPromptTokensMax = 2147483647;

export const tracerObservationSpanRetrieveLoadingResponseResultsItemCompletionTokensMin = -2147483648;
export const tracerObservationSpanRetrieveLoadingResponseResultsItemCompletionTokensMax = 2147483647;

export const tracerObservationSpanRetrieveLoadingResponseResultsItemTotalTokensMin = -2147483648;
export const tracerObservationSpanRetrieveLoadingResponseResultsItemTotalTokensMax = 2147483647;

export const tracerObservationSpanRetrieveLoadingResponseResultsItemEvalIdMax = 255;

export const tracerObservationSpanRetrieveLoadingResponseResultsItemProviderMax = 255;



export const TracerObservationSpanRetrieveLoadingResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().min(1).optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanRetrieveLoadingResponseResultsItemParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanRetrieveLoadingResponseResultsItemNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanRetrieveLoadingResponseResultsItemModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanRetrieveLoadingResponseResultsItemLatencyMsMin).max(tracerObservationSpanRetrieveLoadingResponseResultsItemLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanRetrieveLoadingResponseResultsItemPromptTokensMin).max(tracerObservationSpanRetrieveLoadingResponseResultsItemPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanRetrieveLoadingResponseResultsItemCompletionTokensMin).max(tracerObservationSpanRetrieveLoadingResponseResultsItemCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanRetrieveLoadingResponseResultsItemTotalTokensMin).max(tracerObservationSpanRetrieveLoadingResponseResultsItemTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanRetrieveLoadingResponseResultsItemEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanRetrieveLoadingResponseResultsItemProviderMax).optional(),
  "provider_logo": zod.string().optional(),
  "span_attributes": zod.string().optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
}))
})


/**
 * Given a list of trace_ids, return the root span ID for each trace.
Root span = the span where parent_span_id IS NULL for that trace.

Query param: trace_ids (repeated, e.g. ?trace_ids=<id>&trace_ids=<id>)
 */
export const TracerObservationSpanRootSpansQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})


export const tracerObservationSpanRootSpansResponseResultsItemParentSpanIdMax = 255;

export const tracerObservationSpanRootSpansResponseResultsItemNameMax = 2000;

export const tracerObservationSpanRootSpansResponseResultsItemModelMax = 255;

export const tracerObservationSpanRootSpansResponseResultsItemLatencyMsMin = -2147483648;
export const tracerObservationSpanRootSpansResponseResultsItemLatencyMsMax = 2147483647;

export const tracerObservationSpanRootSpansResponseResultsItemPromptTokensMin = -2147483648;
export const tracerObservationSpanRootSpansResponseResultsItemPromptTokensMax = 2147483647;

export const tracerObservationSpanRootSpansResponseResultsItemCompletionTokensMin = -2147483648;
export const tracerObservationSpanRootSpansResponseResultsItemCompletionTokensMax = 2147483647;

export const tracerObservationSpanRootSpansResponseResultsItemTotalTokensMin = -2147483648;
export const tracerObservationSpanRootSpansResponseResultsItemTotalTokensMax = 2147483647;

export const tracerObservationSpanRootSpansResponseResultsItemEvalIdMax = 255;

export const tracerObservationSpanRootSpansResponseResultsItemProviderMax = 255;



export const TracerObservationSpanRootSpansResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().min(1).optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanRootSpansResponseResultsItemParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanRootSpansResponseResultsItemNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanRootSpansResponseResultsItemModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanRootSpansResponseResultsItemLatencyMsMin).max(tracerObservationSpanRootSpansResponseResultsItemLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanRootSpansResponseResultsItemPromptTokensMin).max(tracerObservationSpanRootSpansResponseResultsItemPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanRootSpansResponseResultsItemCompletionTokensMin).max(tracerObservationSpanRootSpansResponseResultsItemCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanRootSpansResponseResultsItemTotalTokensMin).max(tracerObservationSpanRootSpansResponseResultsItemTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanRootSpansResponseResultsItemEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanRootSpansResponseResultsItemProviderMax).optional(),
  "provider_logo": zod.string().optional(),
  "span_attributes": zod.string().optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
}))
})


export const tracerObservationSpanSubmitFeedbackBodyParentSpanIdMax = 255;

export const tracerObservationSpanSubmitFeedbackBodyNameMax = 2000;

export const tracerObservationSpanSubmitFeedbackBodyModelMax = 255;

export const tracerObservationSpanSubmitFeedbackBodyLatencyMsMin = -2147483648;
export const tracerObservationSpanSubmitFeedbackBodyLatencyMsMax = 2147483647;

export const tracerObservationSpanSubmitFeedbackBodyPromptTokensMin = -2147483648;
export const tracerObservationSpanSubmitFeedbackBodyPromptTokensMax = 2147483647;

export const tracerObservationSpanSubmitFeedbackBodyCompletionTokensMin = -2147483648;
export const tracerObservationSpanSubmitFeedbackBodyCompletionTokensMax = 2147483647;

export const tracerObservationSpanSubmitFeedbackBodyTotalTokensMin = -2147483648;
export const tracerObservationSpanSubmitFeedbackBodyTotalTokensMax = 2147483647;

export const tracerObservationSpanSubmitFeedbackBodyEvalIdMax = 255;

export const tracerObservationSpanSubmitFeedbackBodyProviderMax = 255;



export const TracerObservationSpanSubmitFeedbackBody = zod.object({
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanSubmitFeedbackBodyParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanSubmitFeedbackBodyNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanSubmitFeedbackBodyModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanSubmitFeedbackBodyLatencyMsMin).max(tracerObservationSpanSubmitFeedbackBodyLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanSubmitFeedbackBodyPromptTokensMin).max(tracerObservationSpanSubmitFeedbackBodyPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanSubmitFeedbackBodyCompletionTokensMin).max(tracerObservationSpanSubmitFeedbackBodyCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanSubmitFeedbackBodyTotalTokensMin).max(tracerObservationSpanSubmitFeedbackBodyTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanSubmitFeedbackBodyEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanSubmitFeedbackBodyProviderMax).optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
})


export const tracerObservationSpanSubmitFeedbackActionTypeBodyParentSpanIdMax = 255;

export const tracerObservationSpanSubmitFeedbackActionTypeBodyNameMax = 2000;

export const tracerObservationSpanSubmitFeedbackActionTypeBodyModelMax = 255;

export const tracerObservationSpanSubmitFeedbackActionTypeBodyLatencyMsMin = -2147483648;
export const tracerObservationSpanSubmitFeedbackActionTypeBodyLatencyMsMax = 2147483647;

export const tracerObservationSpanSubmitFeedbackActionTypeBodyPromptTokensMin = -2147483648;
export const tracerObservationSpanSubmitFeedbackActionTypeBodyPromptTokensMax = 2147483647;

export const tracerObservationSpanSubmitFeedbackActionTypeBodyCompletionTokensMin = -2147483648;
export const tracerObservationSpanSubmitFeedbackActionTypeBodyCompletionTokensMax = 2147483647;

export const tracerObservationSpanSubmitFeedbackActionTypeBodyTotalTokensMin = -2147483648;
export const tracerObservationSpanSubmitFeedbackActionTypeBodyTotalTokensMax = 2147483647;

export const tracerObservationSpanSubmitFeedbackActionTypeBodyEvalIdMax = 255;

export const tracerObservationSpanSubmitFeedbackActionTypeBodyProviderMax = 255;



export const TracerObservationSpanSubmitFeedbackActionTypeBody = zod.object({
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanSubmitFeedbackActionTypeBodyParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanSubmitFeedbackActionTypeBodyNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanSubmitFeedbackActionTypeBodyModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanSubmitFeedbackActionTypeBodyLatencyMsMin).max(tracerObservationSpanSubmitFeedbackActionTypeBodyLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanSubmitFeedbackActionTypeBodyPromptTokensMin).max(tracerObservationSpanSubmitFeedbackActionTypeBodyPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanSubmitFeedbackActionTypeBodyCompletionTokensMin).max(tracerObservationSpanSubmitFeedbackActionTypeBodyCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanSubmitFeedbackActionTypeBodyTotalTokensMin).max(tracerObservationSpanSubmitFeedbackActionTypeBodyTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanSubmitFeedbackActionTypeBodyEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanSubmitFeedbackActionTypeBodyProviderMax).optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
})


/**
 * Update tags for an observation span.
 */
export const tracerObservationSpanUpdateTagsBodyParentSpanIdMax = 255;

export const tracerObservationSpanUpdateTagsBodyNameMax = 2000;

export const tracerObservationSpanUpdateTagsBodyModelMax = 255;

export const tracerObservationSpanUpdateTagsBodyLatencyMsMin = -2147483648;
export const tracerObservationSpanUpdateTagsBodyLatencyMsMax = 2147483647;

export const tracerObservationSpanUpdateTagsBodyPromptTokensMin = -2147483648;
export const tracerObservationSpanUpdateTagsBodyPromptTokensMax = 2147483647;

export const tracerObservationSpanUpdateTagsBodyCompletionTokensMin = -2147483648;
export const tracerObservationSpanUpdateTagsBodyCompletionTokensMax = 2147483647;

export const tracerObservationSpanUpdateTagsBodyTotalTokensMin = -2147483648;
export const tracerObservationSpanUpdateTagsBodyTotalTokensMax = 2147483647;

export const tracerObservationSpanUpdateTagsBodyEvalIdMax = 255;

export const tracerObservationSpanUpdateTagsBodyProviderMax = 255;



export const TracerObservationSpanUpdateTagsBody = zod.object({
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanUpdateTagsBodyParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanUpdateTagsBodyNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanUpdateTagsBodyModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanUpdateTagsBodyLatencyMsMin).max(tracerObservationSpanUpdateTagsBodyLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanUpdateTagsBodyPromptTokensMin).max(tracerObservationSpanUpdateTagsBodyPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanUpdateTagsBodyCompletionTokensMin).max(tracerObservationSpanUpdateTagsBodyCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanUpdateTagsBodyTotalTokensMin).max(tracerObservationSpanUpdateTagsBodyTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanUpdateTagsBodyEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanUpdateTagsBodyProviderMax).optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
})


export const TracerObservationSpanReadParams = zod.object({
  "id": zod.string()
})


export const tracerObservationSpanReadResponseParentSpanIdMax = 255;

export const tracerObservationSpanReadResponseNameMax = 2000;

export const tracerObservationSpanReadResponseModelMax = 255;

export const tracerObservationSpanReadResponseLatencyMsMin = -2147483648;
export const tracerObservationSpanReadResponseLatencyMsMax = 2147483647;

export const tracerObservationSpanReadResponsePromptTokensMin = -2147483648;
export const tracerObservationSpanReadResponsePromptTokensMax = 2147483647;

export const tracerObservationSpanReadResponseCompletionTokensMin = -2147483648;
export const tracerObservationSpanReadResponseCompletionTokensMax = 2147483647;

export const tracerObservationSpanReadResponseTotalTokensMin = -2147483648;
export const tracerObservationSpanReadResponseTotalTokensMax = 2147483647;

export const tracerObservationSpanReadResponseEvalIdMax = 255;

export const tracerObservationSpanReadResponseProviderMax = 255;



export const TracerObservationSpanReadResponse = zod.object({
  "id": zod.string().min(1).optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanReadResponseParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanReadResponseNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanReadResponseModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanReadResponseLatencyMsMin).max(tracerObservationSpanReadResponseLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanReadResponsePromptTokensMin).max(tracerObservationSpanReadResponsePromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanReadResponseCompletionTokensMin).max(tracerObservationSpanReadResponseCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanReadResponseTotalTokensMin).max(tracerObservationSpanReadResponseTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanReadResponseEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanReadResponseProviderMax).optional(),
  "provider_logo": zod.string().optional(),
  "span_attributes": zod.string().optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
})


export const TracerObservationSpanUpdateParams = zod.object({
  "id": zod.string()
})

export const tracerObservationSpanUpdateBodyParentSpanIdMax = 255;

export const tracerObservationSpanUpdateBodyNameMax = 2000;

export const tracerObservationSpanUpdateBodyModelMax = 255;

export const tracerObservationSpanUpdateBodyLatencyMsMin = -2147483648;
export const tracerObservationSpanUpdateBodyLatencyMsMax = 2147483647;

export const tracerObservationSpanUpdateBodyPromptTokensMin = -2147483648;
export const tracerObservationSpanUpdateBodyPromptTokensMax = 2147483647;

export const tracerObservationSpanUpdateBodyCompletionTokensMin = -2147483648;
export const tracerObservationSpanUpdateBodyCompletionTokensMax = 2147483647;

export const tracerObservationSpanUpdateBodyTotalTokensMin = -2147483648;
export const tracerObservationSpanUpdateBodyTotalTokensMax = 2147483647;

export const tracerObservationSpanUpdateBodyEvalIdMax = 255;

export const tracerObservationSpanUpdateBodyProviderMax = 255;



export const TracerObservationSpanUpdateBody = zod.object({
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanUpdateBodyParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanUpdateBodyNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanUpdateBodyModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanUpdateBodyLatencyMsMin).max(tracerObservationSpanUpdateBodyLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanUpdateBodyPromptTokensMin).max(tracerObservationSpanUpdateBodyPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanUpdateBodyCompletionTokensMin).max(tracerObservationSpanUpdateBodyCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanUpdateBodyTotalTokensMin).max(tracerObservationSpanUpdateBodyTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanUpdateBodyEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanUpdateBodyProviderMax).optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
})


export const tracerObservationSpanUpdateResponseParentSpanIdMax = 255;

export const tracerObservationSpanUpdateResponseNameMax = 2000;

export const tracerObservationSpanUpdateResponseModelMax = 255;

export const tracerObservationSpanUpdateResponseLatencyMsMin = -2147483648;
export const tracerObservationSpanUpdateResponseLatencyMsMax = 2147483647;

export const tracerObservationSpanUpdateResponsePromptTokensMin = -2147483648;
export const tracerObservationSpanUpdateResponsePromptTokensMax = 2147483647;

export const tracerObservationSpanUpdateResponseCompletionTokensMin = -2147483648;
export const tracerObservationSpanUpdateResponseCompletionTokensMax = 2147483647;

export const tracerObservationSpanUpdateResponseTotalTokensMin = -2147483648;
export const tracerObservationSpanUpdateResponseTotalTokensMax = 2147483647;

export const tracerObservationSpanUpdateResponseEvalIdMax = 255;

export const tracerObservationSpanUpdateResponseProviderMax = 255;



export const TracerObservationSpanUpdateResponse = zod.object({
  "id": zod.string().min(1).optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanUpdateResponseParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanUpdateResponseNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanUpdateResponseModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanUpdateResponseLatencyMsMin).max(tracerObservationSpanUpdateResponseLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanUpdateResponsePromptTokensMin).max(tracerObservationSpanUpdateResponsePromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanUpdateResponseCompletionTokensMin).max(tracerObservationSpanUpdateResponseCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanUpdateResponseTotalTokensMin).max(tracerObservationSpanUpdateResponseTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanUpdateResponseEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanUpdateResponseProviderMax).optional(),
  "provider_logo": zod.string().optional(),
  "span_attributes": zod.string().optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
})


export const TracerObservationSpanPartialUpdateParams = zod.object({
  "id": zod.string()
})

export const tracerObservationSpanPartialUpdateBodyParentSpanIdMax = 255;

export const tracerObservationSpanPartialUpdateBodyNameMax = 2000;

export const tracerObservationSpanPartialUpdateBodyModelMax = 255;

export const tracerObservationSpanPartialUpdateBodyLatencyMsMin = -2147483648;
export const tracerObservationSpanPartialUpdateBodyLatencyMsMax = 2147483647;

export const tracerObservationSpanPartialUpdateBodyPromptTokensMin = -2147483648;
export const tracerObservationSpanPartialUpdateBodyPromptTokensMax = 2147483647;

export const tracerObservationSpanPartialUpdateBodyCompletionTokensMin = -2147483648;
export const tracerObservationSpanPartialUpdateBodyCompletionTokensMax = 2147483647;

export const tracerObservationSpanPartialUpdateBodyTotalTokensMin = -2147483648;
export const tracerObservationSpanPartialUpdateBodyTotalTokensMax = 2147483647;

export const tracerObservationSpanPartialUpdateBodyEvalIdMax = 255;

export const tracerObservationSpanPartialUpdateBodyProviderMax = 255;



export const TracerObservationSpanPartialUpdateBody = zod.object({
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanPartialUpdateBodyParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanPartialUpdateBodyNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanPartialUpdateBodyModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanPartialUpdateBodyLatencyMsMin).max(tracerObservationSpanPartialUpdateBodyLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanPartialUpdateBodyPromptTokensMin).max(tracerObservationSpanPartialUpdateBodyPromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanPartialUpdateBodyCompletionTokensMin).max(tracerObservationSpanPartialUpdateBodyCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanPartialUpdateBodyTotalTokensMin).max(tracerObservationSpanPartialUpdateBodyTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanPartialUpdateBodyEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanPartialUpdateBodyProviderMax).optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
})


export const tracerObservationSpanPartialUpdateResponseParentSpanIdMax = 255;

export const tracerObservationSpanPartialUpdateResponseNameMax = 2000;

export const tracerObservationSpanPartialUpdateResponseModelMax = 255;

export const tracerObservationSpanPartialUpdateResponseLatencyMsMin = -2147483648;
export const tracerObservationSpanPartialUpdateResponseLatencyMsMax = 2147483647;

export const tracerObservationSpanPartialUpdateResponsePromptTokensMin = -2147483648;
export const tracerObservationSpanPartialUpdateResponsePromptTokensMax = 2147483647;

export const tracerObservationSpanPartialUpdateResponseCompletionTokensMin = -2147483648;
export const tracerObservationSpanPartialUpdateResponseCompletionTokensMax = 2147483647;

export const tracerObservationSpanPartialUpdateResponseTotalTokensMin = -2147483648;
export const tracerObservationSpanPartialUpdateResponseTotalTokensMax = 2147483647;

export const tracerObservationSpanPartialUpdateResponseEvalIdMax = 255;

export const tracerObservationSpanPartialUpdateResponseProviderMax = 255;



export const TracerObservationSpanPartialUpdateResponse = zod.object({
  "id": zod.string().min(1).optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "trace": zod.string().uuid(),
  "parent_span_id": zod.string().max(tracerObservationSpanPartialUpdateResponseParentSpanIdMax).optional(),
  "name": zod.string().min(1).max(tracerObservationSpanPartialUpdateResponseNameMax),
  "observation_type": zod.enum(['tool', 'chain', 'llm', 'retriever', 'embedding', 'agent', 'reranker', 'unknown', 'guardrail', 'evaluator', 'conversation']),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "model": zod.string().max(tracerObservationSpanPartialUpdateResponseModelMax).optional(),
  "model_parameters": zod.object({

}).passthrough().optional(),
  "latency_ms": zod.number().min(tracerObservationSpanPartialUpdateResponseLatencyMsMin).max(tracerObservationSpanPartialUpdateResponseLatencyMsMax).optional(),
  "org_id": zod.string().uuid().optional(),
  "org_user_id": zod.string().uuid().optional(),
  "prompt_tokens": zod.number().min(tracerObservationSpanPartialUpdateResponsePromptTokensMin).max(tracerObservationSpanPartialUpdateResponsePromptTokensMax).optional(),
  "completion_tokens": zod.number().min(tracerObservationSpanPartialUpdateResponseCompletionTokensMin).max(tracerObservationSpanPartialUpdateResponseCompletionTokensMax).optional(),
  "total_tokens": zod.number().min(tracerObservationSpanPartialUpdateResponseTotalTokensMin).max(tracerObservationSpanPartialUpdateResponseTotalTokensMax).optional(),
  "response_time": zod.number().optional(),
  "eval_id": zod.string().max(tracerObservationSpanPartialUpdateResponseEvalIdMax).optional(),
  "cost": zod.number().optional(),
  "status": zod.enum(['UNSET', 'OK', 'ERROR']).optional(),
  "status_message": zod.string().optional(),
  "tags": zod.object({

}).passthrough().optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "span_events": zod.object({

}).passthrough().optional(),
  "provider": zod.string().max(tracerObservationSpanPartialUpdateResponseProviderMax).optional(),
  "provider_logo": zod.string().optional(),
  "span_attributes": zod.string().optional(),
  "custom_eval_config": zod.string().uuid().optional(),
  "eval_status": zod.enum(['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive', 'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing', 'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled']).optional(),
  "prompt_version": zod.string().uuid().optional()
})


export const TracerObservationSpanDeleteParams = zod.object({
  "id": zod.string()
})


export const tracerProjectVersionAddAnnotationsBodyNameMax = 255;



export const TracerProjectVersionAddAnnotationsBody = zod.object({
  "project": zod.string().uuid(),
  "name": zod.string().min(1).max(tracerProjectVersionAddAnnotationsBodyNameMax),
  "metadata": zod.object({

}).passthrough().optional(),
  "start_time": zod.string().datetime({"offset":true}).optional(),
  "end_time": zod.string().datetime({"offset":true}).optional(),
  "error": zod.object({

}).passthrough().optional(),
  "eval_tags": zod.object({

}).passthrough().optional(),
  "avg_eval_score": zod.number().optional(),
  "annotations": zod.string().uuid().optional()
})


/**
 * Get a paginated list of all projects for the organization.
 */
export const TracerProjectListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerProjectListResponseResultsItemNameMax = 255;



export const TracerProjectListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectListResponseResultsItemNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


/**
 * Create a new project.
 */
export const tracerProjectCreateBodyNameMax = 255;



export const TracerProjectCreateBody = zod.object({
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectCreateBodyNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
})


export const TracerProjectFetchSystemMetricsQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerProjectFetchSystemMetricsResponseResultsItemNameMax = 255;



export const TracerProjectFetchSystemMetricsResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectFetchSystemMetricsResponseResultsItemNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


export const TracerProjectGetGraphDataQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerProjectGetGraphDataResponseResultsItemNameMax = 255;



export const TracerProjectGetGraphDataResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectGetGraphDataResponseResultsItemNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


export const tracerProjectGetUserGraphDataBodyNameMax = 255;



export const TracerProjectGetUserGraphDataBody = zod.object({
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectGetUserGraphDataBodyNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
})


export const tracerProjectGetUserMetricsBodyNameMax = 255;



export const TracerProjectGetUserMetricsBody = zod.object({
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectGetUserMetricsBodyNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
})


/**
 * Supports SYSTEM_METRIC, EVAL, and ANNOTATION types.
All metrics are aggregated at the user level.
 * @summary Fetch time-series aggregate user metrics for the observe graph.
 */
export const tracerProjectGetUsersAggregateGraphDataBodyNameMax = 255;



export const TracerProjectGetUsersAggregateGraphDataBody = zod.object({
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectGetUsersAggregateGraphDataBodyNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
})


/**
 * List project ids for a given project.
 */
export const TracerProjectListProjectIdsQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerProjectListProjectIdsResponseResultsItemNameMax = 255;



export const TracerProjectListProjectIdsResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectListProjectIdsResponseResultsItemNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


/**
 * Volume counts come from ClickHouse (fast) instead of a PG
JOIN on observation_spans (was 12+ seconds).
 * @summary List projects filtered by organization ID.
 */
export const TracerProjectListProjectsQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerProjectListProjectsResponseResultsItemNameMax = 255;



export const TracerProjectListProjectsResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectListProjectsResponseResultsItemNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


export const TracerProjectProjectSdkCodeQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerProjectProjectSdkCodeResponseResultsItemNameMax = 255;



export const TracerProjectProjectSdkCodeResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectProjectSdkCodeResponseResultsItemNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


export const tracerProjectUpdateProjectConfigBodyNameMax = 255;



export const TracerProjectUpdateProjectConfigBody = zod.object({
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectUpdateProjectConfigBodyNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
})


export const tracerProjectUpdateProjectNameBodyNameMax = 255;



export const TracerProjectUpdateProjectNameBody = zod.object({
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectUpdateProjectNameBodyNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
})


export const tracerProjectUpdateProjectSessionConfigBodyNameMax = 255;



export const TracerProjectUpdateProjectSessionConfigBody = zod.object({
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectUpdateProjectSessionConfigBodyNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
})


/**
 * Get a single project by ID with sampling rate.
 */
export const TracerProjectReadParams = zod.object({
  "id": zod.string()
})

export const tracerProjectReadResponseNameMax = 255;



export const TracerProjectReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectReadResponseNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
})


export const TracerProjectUpdateParams = zod.object({
  "id": zod.string()
})

export const tracerProjectUpdateBodyNameMax = 255;



export const TracerProjectUpdateBody = zod.object({
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectUpdateBodyNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
})

export const tracerProjectUpdateResponseNameMax = 255;



export const TracerProjectUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectUpdateResponseNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
})


export const TracerProjectPartialUpdateParams = zod.object({
  "id": zod.string()
})

export const tracerProjectPartialUpdateBodyNameMax = 255;



export const TracerProjectPartialUpdateBody = zod.object({
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectPartialUpdateBodyNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
})

export const tracerProjectPartialUpdateResponseNameMax = 255;



export const TracerProjectPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectPartialUpdateResponseNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
})


export const TracerProjectDeleteParams = zod.object({
  "id": zod.string()
})


/**
 * Update tags for a project.
 */
export const TracerProjectUpdateTagsParams = zod.object({
  "id": zod.string()
})

export const tracerProjectUpdateTagsBodyNameMax = 255;



export const TracerProjectUpdateTagsBody = zod.object({
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectUpdateTagsBodyNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
})

export const tracerProjectUpdateTagsResponseNameMax = 255;



export const TracerProjectUpdateTagsResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "model_type": zod.enum(['Numeric', 'ScoreCategorical', 'Ranking', 'BinaryClassification', 'Regression', 'ObjectDetection', 'Segmentation', 'GenerativeLLM', 'GenerativeImage', 'GenerativeVideo', 'TTS', 'STT', 'MultiModal']),
  "name": zod.string().min(1).max(tracerProjectUpdateTagsResponseNameMax),
  "trace_type": zod.enum(['experiment', 'observe']),
  "metadata": zod.object({

}).passthrough().optional(),
  "organization": zod.string().uuid().optional(),
  "workspace": zod.string().uuid().optional(),
  "created_at": zod.string().datetime({"offset":true}).optional(),
  "updated_at": zod.string().datetime({"offset":true}).optional(),
  "config": zod.object({

}).passthrough().optional(),
  "source": zod.enum(['demo', 'prototype', 'simulator']).optional(),
  "session_config": zod.object({

}).passthrough().optional(),
  "tags": zod.object({

}).passthrough().optional()
})


export const TracerTraceAnnotationListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceAnnotationListResponseResultsItemObservationSpanIdMax = 255;



export const TracerTraceAnnotationListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "observation_span_id": zod.string().min(1).max(tracerTraceAnnotationListResponseResultsItemObservationSpanIdMax).optional(),
  "trace_id": zod.string().uuid().optional(),
  "annotators": zod.array(zod.string().uuid()).optional(),
  "exclude_annotators": zod.array(zod.string().uuid()).optional()
}))
})


export const tracerTraceAnnotationCreateBodyObservationSpanIdMax = 255;



export const TracerTraceAnnotationCreateBody = zod.object({
  "observation_span_id": zod.string().min(1).max(tracerTraceAnnotationCreateBodyObservationSpanIdMax).optional(),
  "trace_id": zod.string().uuid().optional(),
  "annotators": zod.array(zod.string().uuid()).optional(),
  "exclude_annotators": zod.array(zod.string().uuid()).optional()
})


export const TracerTraceAnnotationGetAnnotationValuesQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceAnnotationGetAnnotationValuesResponseResultsItemObservationSpanIdMax = 255;



export const TracerTraceAnnotationGetAnnotationValuesResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "observation_span_id": zod.string().min(1).max(tracerTraceAnnotationGetAnnotationValuesResponseResultsItemObservationSpanIdMax).optional(),
  "trace_id": zod.string().uuid().optional(),
  "annotators": zod.array(zod.string().uuid()).optional(),
  "exclude_annotators": zod.array(zod.string().uuid()).optional()
}))
})


export const TracerTraceAnnotationReadParams = zod.object({
  "id": zod.string()
})

export const tracerTraceAnnotationReadResponseObservationSpanIdMax = 255;



export const TracerTraceAnnotationReadResponse = zod.object({
  "observation_span_id": zod.string().min(1).max(tracerTraceAnnotationReadResponseObservationSpanIdMax).optional(),
  "trace_id": zod.string().uuid().optional(),
  "annotators": zod.array(zod.string().uuid()).optional(),
  "exclude_annotators": zod.array(zod.string().uuid()).optional()
})


export const TracerTraceAnnotationUpdateParams = zod.object({
  "id": zod.string()
})

export const tracerTraceAnnotationUpdateBodyObservationSpanIdMax = 255;



export const TracerTraceAnnotationUpdateBody = zod.object({
  "observation_span_id": zod.string().min(1).max(tracerTraceAnnotationUpdateBodyObservationSpanIdMax).optional(),
  "trace_id": zod.string().uuid().optional(),
  "annotators": zod.array(zod.string().uuid()).optional(),
  "exclude_annotators": zod.array(zod.string().uuid()).optional()
})

export const tracerTraceAnnotationUpdateResponseObservationSpanIdMax = 255;



export const TracerTraceAnnotationUpdateResponse = zod.object({
  "observation_span_id": zod.string().min(1).max(tracerTraceAnnotationUpdateResponseObservationSpanIdMax).optional(),
  "trace_id": zod.string().uuid().optional(),
  "annotators": zod.array(zod.string().uuid()).optional(),
  "exclude_annotators": zod.array(zod.string().uuid()).optional()
})


export const TracerTraceAnnotationPartialUpdateParams = zod.object({
  "id": zod.string()
})

export const tracerTraceAnnotationPartialUpdateBodyObservationSpanIdMax = 255;



export const TracerTraceAnnotationPartialUpdateBody = zod.object({
  "observation_span_id": zod.string().min(1).max(tracerTraceAnnotationPartialUpdateBodyObservationSpanIdMax).optional(),
  "trace_id": zod.string().uuid().optional(),
  "annotators": zod.array(zod.string().uuid()).optional(),
  "exclude_annotators": zod.array(zod.string().uuid()).optional()
})

export const tracerTraceAnnotationPartialUpdateResponseObservationSpanIdMax = 255;



export const TracerTraceAnnotationPartialUpdateResponse = zod.object({
  "observation_span_id": zod.string().min(1).max(tracerTraceAnnotationPartialUpdateResponseObservationSpanIdMax).optional(),
  "trace_id": zod.string().uuid().optional(),
  "annotators": zod.array(zod.string().uuid()).optional(),
  "exclude_annotators": zod.array(zod.string().uuid()).optional()
})


export const TracerTraceAnnotationDeleteParams = zod.object({
  "id": zod.string()
})


export const TracerTraceSessionListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceSessionListResponseResultsItemNameMax = 255;



export const TracerTraceSessionListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "bookmarked": zod.boolean().optional(),
  "name": zod.string().max(tracerTraceSessionListResponseResultsItemNameMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


export const tracerTraceSessionCreateBodyNameMax = 255;



export const TracerTraceSessionCreateBody = zod.object({
  "project": zod.string().uuid(),
  "bookmarked": zod.boolean().optional(),
  "name": zod.string().max(tracerTraceSessionCreateBodyNameMax).optional()
})


/**
 * Return distinct values for a session-level column.
Used by the filter panel's value picker for session-specific fields
(session_id, user_id, first_message, etc.).

Query params:
    project_id: required
    column: the session column name (camelCase, e.g. "sessionId")
    search: optional search substring
    page: page number (0-based), default 0
    page_size: default 50
 */
export const TracerTraceSessionGetSessionFilterValuesQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceSessionGetSessionFilterValuesResponseResultsItemNameMax = 255;



export const TracerTraceSessionGetSessionFilterValuesResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "bookmarked": zod.boolean().optional(),
  "name": zod.string().max(tracerTraceSessionGetSessionFilterValuesResponseResultsItemNameMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * Supports the same metric types as the trace graph endpoint:
- SYSTEM_METRIC: latency, tokens, cost, error_rate, session_count,
  avg_duration, avg_traces_per_session — all aggregated at session level
- EVAL: eval scores averaged across sessions
- ANNOTATION: annotation scores averaged across sessions

Response shape matches trace graph: {metric_name, data: [{timestamp, value, primary_traffic}]}
 * @summary Fetch time-series session metrics for the observe graph.
 */
export const tracerTraceSessionGetSessionGraphDataBodyNameMax = 255;



export const TracerTraceSessionGetSessionGraphDataBody = zod.object({
  "project": zod.string().uuid(),
  "bookmarked": zod.boolean().optional(),
  "name": zod.string().max(tracerTraceSessionGetSessionGraphDataBodyNameMax).optional()
})


/**
 * Export traces filtered by project ID and project version ID with optimized queries.
 */
export const TracerTraceSessionGetTraceSessionExportDataQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceSessionGetTraceSessionExportDataResponseResultsItemNameMax = 255;



export const TracerTraceSessionGetTraceSessionExportDataResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "bookmarked": zod.boolean().optional(),
  "name": zod.string().max(tracerTraceSessionGetTraceSessionExportDataResponseResultsItemNameMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


/**
 * List traces filtered by project ID and project version ID with optimized queries.
 */
export const TracerTraceSessionListSessionsQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceSessionListSessionsResponseResultsItemNameMax = 255;



export const TracerTraceSessionListSessionsResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "bookmarked": zod.boolean().optional(),
  "name": zod.string().max(tracerTraceSessionListSessionsResponseResultsItemNameMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
}))
})


export const TracerTraceSessionReadParams = zod.object({
  "id": zod.string()
})

export const tracerTraceSessionReadResponseNameMax = 255;



export const TracerTraceSessionReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "bookmarked": zod.boolean().optional(),
  "name": zod.string().max(tracerTraceSessionReadResponseNameMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


export const TracerTraceSessionUpdateParams = zod.object({
  "id": zod.string()
})

export const tracerTraceSessionUpdateBodyNameMax = 255;



export const TracerTraceSessionUpdateBody = zod.object({
  "project": zod.string().uuid(),
  "bookmarked": zod.boolean().optional(),
  "name": zod.string().max(tracerTraceSessionUpdateBodyNameMax).optional()
})

export const tracerTraceSessionUpdateResponseNameMax = 255;



export const TracerTraceSessionUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "bookmarked": zod.boolean().optional(),
  "name": zod.string().max(tracerTraceSessionUpdateResponseNameMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


export const TracerTraceSessionPartialUpdateParams = zod.object({
  "id": zod.string()
})

export const tracerTraceSessionPartialUpdateBodyNameMax = 255;



export const TracerTraceSessionPartialUpdateBody = zod.object({
  "project": zod.string().uuid(),
  "bookmarked": zod.boolean().optional(),
  "name": zod.string().max(tracerTraceSessionPartialUpdateBodyNameMax).optional()
})

export const tracerTraceSessionPartialUpdateResponseNameMax = 255;



export const TracerTraceSessionPartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "bookmarked": zod.boolean().optional(),
  "name": zod.string().max(tracerTraceSessionPartialUpdateResponseNameMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


export const TracerTraceSessionDeleteParams = zod.object({
  "id": zod.string()
})


/**
 * Session-level eval results are walled off from span/trace surfaces
by ``target_type='session'`` — this endpoint is the only place
they appear.

Query params:
    page (int, 1-indexed, default 1)
    page_size (int, default 25, max 100)
 * @summary Session-scoped eval log feed for TracesDrawer's "Evals" tab.
 */
export const TracerTraceSessionEvalLogsParams = zod.object({
  "id": zod.string()
})

export const tracerTraceSessionEvalLogsResponseNameMax = 255;



export const TracerTraceSessionEvalLogsResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "bookmarked": zod.boolean().optional(),
  "name": zod.string().max(tracerTraceSessionEvalLogsResponseNameMax).optional(),
  "created_at": zod.string().datetime({"offset":true}).optional()
})


export const TracerTraceListQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceListResponseResultsItemNameMax = 2000;

export const tracerTraceListResponseResultsItemExternalIdMax = 255;



export const TracerTraceListResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceListResponseResultsItemNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceListResponseResultsItemExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


export const tracerTraceCreateBodyNameMax = 2000;

export const tracerTraceCreateBodyExternalIdMax = 255;



export const TracerTraceCreateBody = zod.object({
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceCreateBodyNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceCreateBodyExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
})


/**
 * Computes nodes (distinct span types/names) and edges (parent→child
transitions) across all traces in the given time window.
 * @summary Return the aggregate agent graph for a project.
 */
export const TracerTraceAgentGraphQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceAgentGraphResponseResultsItemNameMax = 2000;

export const tracerTraceAgentGraphResponseResultsItemExternalIdMax = 255;



export const TracerTraceAgentGraphResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceAgentGraphResponseResultsItemNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceAgentGraphResponseResultsItemExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


export const tracerTraceBulkCreateBodyNameMax = 2000;

export const tracerTraceBulkCreateBodyExternalIdMax = 255;



export const TracerTraceBulkCreateBody = zod.object({
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceBulkCreateBodyNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceBulkCreateBodyExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
})


/**
 * Compare traces across project versions with optimized queries.
 */
export const tracerTraceCompareTracesBodyNameMax = 2000;

export const tracerTraceCompareTracesBodyExternalIdMax = 255;



export const TracerTraceCompareTracesBody = zod.object({
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceCompareTracesBodyNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceCompareTracesBodyExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
})


/**
 * Fetch all evaluation template names.
 */
export const TracerTraceGetEvalNamesQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceGetEvalNamesResponseResultsItemNameMax = 2000;

export const tracerTraceGetEvalNamesResponseResultsItemExternalIdMax = 255;



export const TracerTraceGetEvalNamesResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceGetEvalNamesResponseResultsItemNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceGetEvalNamesResponseResultsItemExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


/**
 * Fetch data for the observe graph with optimized queries
 */
export const tracerTraceGetGraphMethodsBodyNameMax = 2000;

export const tracerTraceGetGraphMethodsBodyExternalIdMax = 255;



export const TracerTraceGetGraphMethodsBody = zod.object({
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceGetGraphMethodsBodyNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceGetGraphMethodsBodyExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
})


/**
 * Fetch all properties for graphing.
 */
export const TracerTraceGetPropertiesQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceGetPropertiesResponseResultsItemNameMax = 2000;

export const tracerTraceGetPropertiesResponseResultsItemExternalIdMax = 255;



export const TracerTraceGetPropertiesResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceGetPropertiesResponseResultsItemNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceGetPropertiesResponseResultsItemExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


/**
 * Export traces filtered by project ID with optimized queries.
Auto-detects voice/conversation projects and exports voice-specific fields.
 */
export const TracerTraceGetTraceExportDataQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceGetTraceExportDataResponseResultsItemNameMax = 2000;

export const tracerTraceGetTraceExportDataResponseResultsItemExternalIdMax = 255;



export const TracerTraceGetTraceExportDataResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceGetTraceExportDataResponseResultsItemNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceGetTraceExportDataResponseResultsItemExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


/**
 * Get the previous and next trace id by index using efficient database queries.
 */
export const TracerTraceGetTraceIdByIndexQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceGetTraceIdByIndexResponseResultsItemNameMax = 2000;

export const tracerTraceGetTraceIdByIndexResponseResultsItemExternalIdMax = 255;



export const TracerTraceGetTraceIdByIndexResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceGetTraceIdByIndexResponseResultsItemNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceGetTraceIdByIndexResponseResultsItemExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


/**
 * Get the previous and next trace id by index.
 */
export const TracerTraceGetTraceIdByIndexObserveQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceGetTraceIdByIndexObserveResponseResultsItemNameMax = 2000;

export const tracerTraceGetTraceIdByIndexObserveResponseResultsItemExternalIdMax = 255;



export const TracerTraceGetTraceIdByIndexObserveResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceGetTraceIdByIndexObserveResponseResultsItemNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceGetTraceIdByIndexObserveResponseResultsItemExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


/**
 * List traces filtered by project ID and project version ID with optimized queries.
 */
export const TracerTraceListTracesQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceListTracesResponseResultsItemNameMax = 2000;

export const tracerTraceListTracesResponseResultsItemExternalIdMax = 255;



export const TracerTraceListTracesResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceListTracesResponseResultsItemNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceListTracesResponseResultsItemExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


/**
 * List traces filtered by project ID with optimized queries.
 */
export const TracerTraceListTracesOfSessionQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceListTracesOfSessionResponseResultsItemNameMax = 2000;

export const tracerTraceListTracesOfSessionResponseResultsItemExternalIdMax = 255;



export const TracerTraceListTracesOfSessionResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceListTracesOfSessionResponseResultsItemNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceListTracesOfSessionResponseResultsItemExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


/**
 * List voice/conversation traces for a project in an optimized way and
return a response similar to the provided call object schema.

Query params:
- project_id (required)
- page (1-based, optional, default 1)
- page_size (optional, default 30)
 */
export const TracerTraceListVoiceCallsQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceListVoiceCallsResponseResultsItemNameMax = 2000;

export const tracerTraceListVoiceCallsResponseResultsItemExternalIdMax = 255;



export const TracerTraceListVoiceCallsResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceListVoiceCallsResponseResultsItemNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceListVoiceCallsResponseResultsItemExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


/**
 * Query params:
- trace_id (required) — UUID of the voice call trace.
 * @summary Return the heavy / detail-only fields for a single voice call.
 */
export const TracerTraceVoiceCallDetailQueryParams = zod.object({
  "page": zod.number().optional().describe('A page number within the paginated result set.'),
  "limit": zod.number().optional().describe('Number of results to return per page.')
})

export const tracerTraceVoiceCallDetailResponseResultsItemNameMax = 2000;

export const tracerTraceVoiceCallDetailResponseResultsItemExternalIdMax = 255;



export const TracerTraceVoiceCallDetailResponse = zod.object({
  "count": zod.number(),
  "next": zod.string().url().optional(),
  "previous": zod.string().url().optional(),
  "results": zod.array(zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceVoiceCallDetailResponseResultsItemNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceVoiceCallDetailResponseResultsItemExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
}))
})


/**
 * Retrieve a trace by its ID.
 */
export const TracerTraceReadParams = zod.object({
  "id": zod.string()
})

export const tracerTraceReadResponseNameMax = 2000;

export const tracerTraceReadResponseExternalIdMax = 255;



export const TracerTraceReadResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceReadResponseNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceReadResponseExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
})


export const TracerTraceUpdateParams = zod.object({
  "id": zod.string()
})

export const tracerTraceUpdateBodyNameMax = 2000;

export const tracerTraceUpdateBodyExternalIdMax = 255;



export const TracerTraceUpdateBody = zod.object({
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceUpdateBodyNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceUpdateBodyExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
})

export const tracerTraceUpdateResponseNameMax = 2000;

export const tracerTraceUpdateResponseExternalIdMax = 255;



export const TracerTraceUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTraceUpdateResponseNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTraceUpdateResponseExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
})


export const TracerTracePartialUpdateParams = zod.object({
  "id": zod.string()
})

export const tracerTracePartialUpdateBodyNameMax = 2000;

export const tracerTracePartialUpdateBodyExternalIdMax = 255;



export const TracerTracePartialUpdateBody = zod.object({
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTracePartialUpdateBodyNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTracePartialUpdateBodyExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
})

export const tracerTracePartialUpdateResponseNameMax = 2000;

export const tracerTracePartialUpdateResponseExternalIdMax = 255;



export const TracerTracePartialUpdateResponse = zod.object({
  "id": zod.string().uuid().optional(),
  "project": zod.string().uuid(),
  "project_version": zod.string().uuid().optional(),
  "name": zod.string().max(tracerTracePartialUpdateResponseNameMax).optional(),
  "metadata": zod.object({

}).passthrough().optional(),
  "input": zod.object({

}).passthrough().optional(),
  "output": zod.object({

}).passthrough().optional(),
  "error": zod.object({

}).passthrough().optional(),
  "session": zod.string().uuid().optional(),
  "external_id": zod.string().max(tracerTracePartialUpdateResponseExternalIdMax).optional(),
  "tags": zod.object({

}).passthrough().optional()
})


export const TracerTraceDeleteParams = zod.object({
  "id": zod.string()
})


/**
 * Update tags for a trace.
 */
export const TracerTraceUpdateTagsParams = zod.object({
  "id": zod.string()
})




export const TracerTraceUpdateTagsBody = zod.object({
  "tags": zod.array(zod.string().min(1))
})




export const TracerTraceUpdateTagsResponse = zod.object({
  "tags": zod.array(zod.string().min(1))
})
