import fixtures from "../../../../../api_contracts/host-update/fixtures.json";
import schema from "../../../../../api_contracts/host-update/public-status.v1.schema.json";

export { fixtures, schema };
export function responseFixture(name = "available_current") {
  return structuredClone(
    fixtures.valid_api.find((entry) => entry.name === name).response,
  );
}
export function statusFixture(name) {
  const response = responseFixture();
  response.result.snapshot = structuredClone(
    fixtures.valid_status.find((entry) => entry.name === name).snapshot,
  );
  return response;
}
