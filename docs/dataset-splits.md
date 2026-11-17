# Dataset split CRUD

## Contract change and availability

GRIDB F50 removes the two split CRUD templates below from the frontend prototype-refusal inventory in contract change `450e7c72f11874b11d71c4879e9944c5c066eb49`.
Requests now reach the authenticated GRIDB views and their declared API contracts instead of being rejected by the frontend launch guard with its synthetic refusal response.
This is an unreleased contract description, not a claim that a deployment or release qualification has completed.

The existing `dataset.splits` capability belongs to GRID; visibility still depends on the registered UI gates, and API access still depends on authorization.
This change does not enable F51A, which stays hidden and default-OFF. Results, runs and expression refusals are unchanged.

## Endpoints

- `GET /model-hub/datasets/{dataset_id}/splits/` lists the dataset's splits and live membership counts. The success result contains `splits` and `no_split_count`.
- `POST /model-hub/datasets/{dataset_id}/splits/` creates a split and returns its `id`, `name`, `color`, `description` and `row_count`.
- `PATCH /model-hub/datasets/{dataset_id}/splits/{split_id}/` updates the supplied split fields and returns the same split representation.
- `DELETE /model-hub/datasets/{dataset_id}/splits/{split_id}/` deletes that split; the success result is `{ "deleted": "<split UUID>" }`.

Replace the placeholders with actual dataset and split UUIDs. These canonical backend URLs end in `/`; successful responses use HTTP 200 and `{ "status": true, "result": ... }`.
The frontend guard no longer refuses either slash spelling. That guard equivalence does not promise that the backend serves a slashless alias without a redirect; clients should use the canonical URLs.

## Access and validation

All four operations require authentication, an active organization/workspace pair and access to that workspace. The dataset must belong to that organization and workspace and must not be deleted.
Create, update and delete also require workspace write access (member level or higher). Read-only access does not permit mutation.
Detail operations resolve the split within the authorized dataset, not by an unrestricted split ID; removing the frontend refusal does not bypass these checks.

Create requires `name`: 1-30 characters, starting with a lowercase letter or digit, followed by lowercase letters, digits, `.`, `_` or `-`; the name `none` is reserved.
Optional `color` is one of `info`, `secondary`, `error`, `warning`, `success`, `primary`, `grey` or `dark` (default `info`); optional `description` is at most 500 characters and may be blank (default empty).
Patch accepts these fields but requires at least one. A dataset supports at most 100 splits; a duplicate split name returns HTTP 409.

## Source contract and claims

The hand-written [URL routes](../futureagi/model_hub/urls.py), [GRIDB views and request/response declarations](../futureagi/model_hub/views/dataset_splits.py), [serializers](../futureagi/model_hub/serializers/dataset_splits.py) and [scoped access service](../futureagi/model_hub/services/dataset_splits.py) define this behavior. The views' `validated_request` declarations feed the [OpenAPI schema generator](../scripts/generate-openapi-schema.sh); generated artifacts are not edited to establish this contract.
The claims inventory is [MADE_REAL.GRID](../frontend/src/launch/madeReal.js), with `dataset.splits` and its `DATA-E2E-008` reference. That claim does not assert F51A enablement or replace runtime qualification. See also the [Unreleased changelog](../CHANGELOG.md#unreleased).
