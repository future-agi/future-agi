// Shared by the snippet runs (snippets.gateway.test.js here, snippets.stack.test.js
// on the cloud rig): the distinct requests to run, and the page's own call for
// each one through the tab's client, so a run can compare the two.

/** One of each distinct request (same call, path and body), in first-seen order. */
export function uniqueRequests(requests) {
  const seen = new Set();
  return requests.filter((request) => {
    const id = `${request.id} ${request.path} ${JSON.stringify(request.query ?? null)} ${JSON.stringify(request.json ?? null)}`;
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

const segment = (request, index) =>
  decodeURIComponent(request.path.split("?")[0].split("/")[index]);

/**
 * The call the page itself makes for `request`, through `apis` ({ sandboxes,
 * deployments } from createSandboxesApi / createDeploymentsApi). Streams are
 * read to their end. Resolves with the call's answer, or the error it raised.
 */
export async function clientCall(
  apis,
  request,
  { idempotencyKey, deployInput } = {},
) {
  const sbx = apis.sandboxes;
  const dep = apis.deployments;
  const id = segment(request, 3);
  const keyed = { idempotencyKey };
  const quiet = { onEvent: () => {}, onState: () => {} };
  try {
    switch (request.id) {
      case "sandboxes.list":
        return await sbx.list();
      case "sandboxes.create":
        return await sbx.create(request.json, keyed);
      case "sandboxes.get":
        return await sbx.get(id);
      case "sandboxes.exec":
        return await sbx.exec(id, request.json, quiet).done;
      case "sandboxes.files":
        return await sbx.listFiles(id);
      case "sandboxes.snapshots":
        return await sbx.snapshots(id);
      case "sandboxes.snapshot":
        return await sbx.snapshot(id, keyed);
      case "sandboxes.activity":
        return await sbx.activity(id);
      case "sandboxes.hibernate":
        return await sbx.hibernate(id);
      case "sandboxes.resume":
        return await sbx.resume(id);
      case "sandboxes.delete":
        return await sbx.destroy(id);
      case "sandboxes.settings":
        return await sbx.settings();
      case "deployments.list":
        return await dep.list();
      case "deployments.create": {
        const { bundle, settings, checks } = deployInput;
        return await dep.create(
          {
            name: settings.name,
            bundle,
            env: checks.env,
            secretRefs: settings.secretRefs,
            studio: settings.studio,
          },
          keyed,
        );
      }
      case "deployments.get":
        return await dep.get(id);
      case "deployments.health":
        return await dep.health(id);
      case "deployments.logs":
        return await dep.logs(id, { ...request.query, ...quiet }).done;
      case "deployments.rollback":
        return await dep.rollback(
          id,
          Number(segment(request, 5)),
          request.json,
          keyed,
        );
      case "deployments.rotateToken":
        return await dep.rotateToken(id, keyed);
      case "deployments.delete":
        return await dep.remove(id);
      case "credentials.list":
        return await dep.credentials();
      default:
        throw new Error(`No page call for ${request.id}`);
    }
  } catch (error) {
    return error;
  }
}
