package edge

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The generated worker serves a cached response only to a request that
// presents the same credential, path and body; a request without a credential
// is never served from or stored in the cache. These tests run the generated
// script in Node with a Cache API that matches on the key URL alone, the way
// Cloudflare's does, and an upstream that answers every call with a fresh,
// numbered reply.

// workerHarness imports the generated worker, stands in for caches.default
// and fetch, runs the steps it is given in order, and prints one result per
// step. The fake upstream sets x-agentcc-cache from the request's
// x-test-gateway-cache header ("none" leaves it off), so a step can say what
// the gateway's own response cache reported.
const workerHarness = `
import { readFileSync } from "node:fs";
import worker from "./worker.mjs";

const store = new Map();
globalThis.caches = {
  default: {
    async match(request) {
      const hit = store.get(new URL(request.url).href);
      return hit ? new Response(hit.body, { status: hit.status, headers: hit.headers }) : undefined;
    },
    async put(request, response) {
      if (request.method !== "GET") throw new Error("put needs a GET request");
      store.set(new URL(request.url).href, {
        status: response.status,
        headers: [...response.headers],
        body: await response.text(),
      });
    },
  },
};

let upstreamCalls = 0;
globalThis.fetch = async (url, init) => {
  upstreamCalls++;
  const headers = { "content-type": "application/json" };
  const gatewayCache = init.headers.get("x-test-gateway-cache") || "miss";
  if (gatewayCache !== "none") headers["x-agentcc-cache"] = gatewayCache;
  return new Response(JSON.stringify({ answer: "upstream answer " + upstreamCalls }), { status: 200, headers });
};

const steps = JSON.parse(readFileSync(process.argv[2], "utf8"));
const results = [];
for (const step of steps) {
  const pending = [];
  const ctx = { waitUntil(p) { pending.push(p); } };
  const request = new Request("https://edge.example.com" + step.path, {
    method: "POST",
    headers: step.headers || {},
    body: step.body,
  });
  const response = await worker.fetch(request, {}, ctx);
  const payload = JSON.parse(await response.text());
  await Promise.all(pending);
  results.push({
    name: step.name,
    edge: response.headers.get("x-agentcc-edge-cache"),
    answer: payload.answer,
    upstreamCalls,
  });
}
console.log(JSON.stringify(results));
`

type workerStep struct {
	Name    string            `json:"name"`
	Path    string            `json:"path"`
	Headers map[string]string `json:"headers"`
	Body    string            `json:"body"`
}

type workerResult struct {
	Name          string `json:"name"`
	Edge          string `json:"edge"`
	Answer        string `json:"answer"`
	UpstreamCalls int    `json:"upstreamCalls"`
}

// runWorker runs steps through the worker generated from testConfig() and
// returns the results by step name.
func runWorker(t *testing.T, steps []workerStep) map[string]workerResult {
	t.Helper()
	node, err := exec.LookPath("node")
	if err != nil {
		t.Skip("node is not installed; the generated worker cannot be run")
	}
	dir := t.TempDir()
	stepsJSON, err := json.Marshal(steps)
	if err != nil {
		t.Fatal(err)
	}
	for name, content := range map[string]string{
		"worker.mjs":  Generate(testConfig()),
		"harness.mjs": workerHarness,
		"steps.json":  string(stepsJSON),
	} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, node, filepath.Join(dir, "harness.mjs"), filepath.Join(dir, "steps.json"))
	cmd.Dir = dir
	out, err := cmd.Output()
	if err != nil {
		stderr := ""
		if ee, ok := err.(*exec.ExitError); ok {
			stderr = string(ee.Stderr)
		}
		t.Fatalf("running the generated worker: %v\n%s", err, stderr)
	}
	var results []workerResult
	if err := json.Unmarshal(out, &results); err != nil {
		t.Fatalf("decoding worker results %q: %v", out, err)
	}
	byName := make(map[string]workerResult, len(results))
	for _, r := range results {
		byName[r.Name] = r
	}
	return byName
}

const workerChatBody = `{"model":"gpt-4o","messages":[{"role":"user","content":"org A private prompt"}]}`

func bearer(key string) map[string]string {
	return map[string]string{"Authorization": "Bearer " + key, "Content-Type": "application/json"}
}

func TestGeneratedWorker_CacheNeverCrossesCredentials(t *testing.T) {
	got := runWorker(t, []workerStep{
		{Name: "a1", Path: "/v1/chat/completions", Headers: bearer("sk-agentcc-org-a"), Body: workerChatBody},
		{Name: "a2", Path: "/v1/chat/completions", Headers: bearer("sk-agentcc-org-a"), Body: workerChatBody},
		{Name: "b1", Path: "/v1/chat/completions", Headers: bearer("sk-agentcc-org-b"), Body: workerChatBody},
		{Name: "xapi", Path: "/v1/chat/completions", Headers: map[string]string{"x-api-key": "sk-agentcc-org-b"}, Body: workerChatBody},
		{Name: "anon1", Path: "/v1/chat/completions", Headers: map[string]string{"Content-Type": "application/json"}, Body: workerChatBody},
		{Name: "anon2", Path: "/v1/chat/completions", Headers: map[string]string{"Content-Type": "application/json"}, Body: workerChatBody},
		{Name: "otherpath", Path: "/v1/embeddings", Headers: bearer("sk-agentcc-org-a"), Body: workerChatBody},
	})

	a1 := got["a1"]
	if a1.Edge != "miss" || a1.Answer != "upstream answer 1" {
		t.Fatalf("org A's first call should go upstream: %+v", a1)
	}
	// Without this the rest would pass against a worker that never caches.
	if a2 := got["a2"]; a2.Edge != "hit" || a2.Answer != a1.Answer || a2.UpstreamCalls != 1 {
		t.Fatalf("org A's repeat should be served from the edge cache: %+v", a2)
	}
	for _, name := range []string{"b1", "xapi", "anon1", "anon2", "otherpath"} {
		r := got[name]
		if r.Edge != "miss" || r.Answer == a1.Answer {
			t.Errorf("%s was served org A's cached response: %+v", name, r)
		}
	}
	if anon2 := got["anon2"]; anon2.UpstreamCalls != got["anon1"].UpstreamCalls+1 {
		t.Errorf("a request with no credential must never be stored: %+v", anon2)
	}
}

func TestGeneratedWorker_StoresOnlyWhatTheGatewayCached(t *testing.T) {
	steps := []workerStep{}
	for _, status := range []string{"skip_org_disabled", "skip", "none"} {
		h := bearer("sk-agentcc-" + status)
		h["x-test-gateway-cache"] = status
		steps = append(steps,
			workerStep{Name: status + "-1", Path: "/v1/chat/completions", Headers: h, Body: workerChatBody},
			workerStep{Name: status + "-2", Path: "/v1/chat/completions", Headers: h, Body: workerChatBody})
	}
	for _, status := range []string{"hit_exact", "hit_semantic"} {
		h := bearer("sk-agentcc-" + status)
		h["x-test-gateway-cache"] = status
		steps = append(steps,
			workerStep{Name: status + "-1", Path: "/v1/chat/completions", Headers: h, Body: workerChatBody},
			workerStep{Name: status + "-2", Path: "/v1/chat/completions", Headers: h, Body: workerChatBody})
	}
	got := runWorker(t, steps)

	// The org has not opted in, the request was not cacheable, or the
	// gateway's response cache is off: the edge must not cache it either.
	for _, status := range []string{"skip_org_disabled", "skip", "none"} {
		if r := got[status+"-2"]; r.Edge != "miss" || r.Answer == got[status+"-1"].Answer {
			t.Errorf("x-agentcc-cache %q was stored at the edge: %+v", status, r)
		}
	}
	for _, status := range []string{"hit_exact", "hit_semantic"} {
		if r := got[status+"-2"]; r.Edge != "hit" || r.Answer != got[status+"-1"].Answer {
			t.Errorf("x-agentcc-cache %q should be stored at the edge: %+v", status, r)
		}
	}
}

func TestGeneratedWorker_GeminiQueryKeyIsACredential(t *testing.T) {
	path := "/v1beta/models/gemini-2.0-flash:generateContent?key="
	got := runWorker(t, []workerStep{
		{Name: "q1", Path: path + "sk-agentcc-org-a", Body: workerChatBody},
		{Name: "q2", Path: path + "sk-agentcc-org-a", Body: workerChatBody},
		{Name: "q3", Path: path + "sk-agentcc-org-b", Body: workerChatBody},
		{Name: "goog", Path: "/v1beta/models/gemini-2.0-flash:generateContent", Headers: map[string]string{"x-goog-api-key": "sk-agentcc-org-b"}, Body: workerChatBody},
	})
	if q2 := got["q2"]; q2.Edge != "hit" || q2.Answer != got["q1"].Answer {
		t.Fatalf("the same ?key= should be served from the edge cache: %+v", q2)
	}
	for _, name := range []string{"q3", "goog"} {
		if r := got[name]; r.Edge != "miss" || r.Answer == got["q1"].Answer {
			t.Errorf("%s was served another key's cached response: %+v", name, r)
		}
	}
}

// Without Node the behaviour above is not run, so pin the shape of the key in
// the script itself.
func TestGenerateCacheKeyIncludesCredential(t *testing.T) {
	result := Generate(testConfig())
	for _, want := range []string{
		`request.headers.get("authorization")`,
		`request.headers.get("x-api-key")`,
		`request.headers.get("x-goog-api-key")`,
		`url.searchParams.get("key")`,
		`credentialHash + "\n" + url.pathname + url.search + "\n" + body`,
		`if (cacheKey) {`,
		`GATEWAY_CACHEABLE.has(proxyResponse.headers.get("x-agentcc-cache")`,
	} {
		if !strings.Contains(result, want) {
			t.Errorf("generated worker is missing %s", want)
		}
	}
}
