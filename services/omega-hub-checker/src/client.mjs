/** The checker's HTTP client for Django's internal endpoints (SPEC §9.4). One shared secret. */
export function registryClient({ baseUrl, token, checkerId, fetchImpl = globalThis.fetch }) {
  const headers = { "X-Omega-Hub-Checker-Token": token, "X-Omega-Hub-Checker-Id": checkerId };
  const url = (path) => new URL(path, baseUrl).toString();
  const ok = async (response, what) => {
    if (!response.ok) throw new Error(`${what}: HTTP ${response.status} ${(await response.text()).slice(0, 300)}`);
    return response;
  };
  return {
    async claim() {
      const response = await ok(await fetchImpl(url("/omega-hub/internal/checker/claim"), { method: "POST", headers }), "claim");
      return response.status === 204 ? null : response.json();
    },
    async bytes(path) {
      const response = await ok(await fetchImpl(url(path), { headers }), `GET ${path}`);
      return Buffer.from(await response.arrayBuffer());
    },
    async context(job, body) {
      const response = await fetchImpl(url(job.contextUrl), { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(body) });
      return (await ok(response, "context")).json();
    },
    async heartbeat(job) {
      await ok(await fetchImpl(url(job.heartbeatUrl), { method: "POST", headers }), "heartbeat");
    },
    async result(job, { report, bundle, log, cast }) {
      const form = new FormData();
      form.append("report", new Blob([JSON.stringify(report)], { type: "application/json" }), "report.json");
      if (bundle) form.append("bundle", new Blob([bundle], { type: "application/gzip" }), "bundle.tar.gz");
      if (log) form.append("log", new Blob([log], { type: "text/plain" }), "log.txt");
      if (cast) form.append("cast", new Blob([cast], { type: "text/plain" }), "demo.cast");
      return (await ok(await fetchImpl(url(job.resultUrl), { method: "POST", headers, body: form }), "result")).json();
    },
  };
}
