package main

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// An IP rule applies on every route that runs the request pipeline, not only
// on chat: the pipeline's ip_acl (node or org) and a key's allowed_ips judge
// the caller's resolved client IP on each of them. These tests run the real
// binary (main) in a child process, as readiness_test.go does, and call it
// from 127.0.0.1.

const (
	keyIPIn    = "sk-agentcc-check-ipacl-in-key-0123456789abcd"
	keyIPOut   = "sk-agentcc-check-ipacl-out-key-0123456789abc"
	keyKeyIPIn = "sk-agentcc-check-keyip-in-key-0123456789abc"
)

// startMediaProvider is an OpenAI-format upstream that answers chat,
// embeddings, image generation and speech.
func startMediaProvider(t *testing.T) *httptest.Server {
	t.Helper()
	p := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		switch {
		case strings.HasSuffix(r.URL.Path, "/embeddings"):
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"object":"list","data":[{"object":"embedding","index":0,"embedding":[0.1,0.2]}],"model":"text-embedding-3-small","usage":{"prompt_tokens":1,"total_tokens":1}}`)
		case strings.HasSuffix(r.URL.Path, "/images/generations"):
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"created":1,"data":[{"url":"https://example.invalid/i.png"}]}`)
		case strings.HasSuffix(r.URL.Path, "/audio/speech"):
			w.Header().Set("Content-Type", "audio/mpeg")
			w.Write([]byte("ID3fake-audio"))
		default:
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"id":"x","object":"chat.completion","created":1,"model":"gpt-4o",`+
				`"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],`+
				`"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
		}
	}))
	t.Cleanup(p.Close)
	return p
}

// pipelineRoutes are a chat call and one call on each of three non-chat
// routes that run the request pipeline.
var pipelineRoutes = []struct{ path, body string }{
	{"/v1/chat/completions", `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`},
	{"/v1/embeddings", `{"model":"text-embedding-3-small","input":"hi"}`},
	{"/v1/images/generations", `{"model":"gpt-image-2","prompt":"a lighthouse"}`},
	{"/v1/audio/speech", `{"model":"tts-1","input":"hi","voice":"alloy"}`},
}

// pipelinePost sends body to path as key and returns the status and body.
func pipelinePost(t *testing.T, g *readinessGateway, key, path, body string) (int, string) {
	t.Helper()
	req, _ := http.NewRequest(http.MethodPost, g.url+path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("POST %s: %v", path, err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, strings.TrimSpace(string(b))
}

// ipProviderBlock is the gateway's own provider, serving every model the
// routes above call.
func ipProviderBlock(url string) string {
	return `providers:
  openai:
    base_url: ` + url + `
    api_key: fake-operator-key
    api_format: openai
    models: [gpt-4o, text-embedding-3-small, gpt-image-2, tts-1]
`
}

// TestOrgIPACLEveryPipelineRoute: on a managed gateway (admin token set,
// node ip_acl off, as the shipped configs run) that resolves client IPs (a
// trusted proxy is set), an org whose ip_acl allows the caller is served on
// every pipeline route, and an org whose ip_acl does not is refused on every
// one with the IP rule's own answer.
func TestOrgIPACLEveryPipelineRoute(t *testing.T) {
	provider := startMediaProvider(t)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
  trusted_proxies: ["192.0.2.255/32"] # resolves client IPs; the peer stays the client
`+ipProviderBlock(provider.URL)+`auth:
  enabled: true
  keys:
    - name: ip-in
      key: `+keyIPIn+`
      owner: org-ip-in
      key_type: internal
      metadata: {org_id: org-ip-in}
    - name: ip-out
      key: `+keyIPOut+`
      owner: org-ip-out
      key_type: internal
      metadata: {org_id: org-ip-out}
admin:
  token: `+testAdminToken+`
`)
	for org, body := range map[string]string{
		"org-ip-in":  `{"ip_acl":{"enabled":true,"allow":["127.0.0.1/32"]}}`,
		"org-ip-out": `{"ip_acl":{"enabled":true,"allow":["10.9.9.9/32"]}}`,
	} {
		if code, resp := adminCall(t, g, http.MethodPut, "/-/orgs/"+org+"/config", body); code != http.StatusOK {
			t.Fatalf("PUT /-/orgs/%s/config -> %d %s", org, code, resp)
		}
	}
	for _, rt := range pipelineRoutes {
		if code, body := pipelinePost(t, g, keyIPIn, rt.path, rt.body); code != http.StatusOK {
			t.Errorf("org ip_acl allow [127.0.0.1/32], caller 127.0.0.1: POST %s -> %d %.200s; want 200", rt.path, code, body)
		}
		code, body := pipelinePost(t, g, keyIPOut, rt.path, rt.body)
		if code != http.StatusForbidden || !strings.Contains(body, "IP address is not allowed") {
			t.Errorf("org ip_acl allow [10.9.9.9/32], caller 127.0.0.1: POST %s -> %d %.200s; want 403 \"Access denied: IP address is not allowed\"", rt.path, code, body)
		}
	}
}

// TestNodeIPACLEveryPipelineRoute: a node with ip_acl on and an allow list
// that holds the caller serves every pipeline route.
func TestNodeIPACLEveryPipelineRoute(t *testing.T) {
	provider := startMediaProvider(t)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
`+ipProviderBlock(provider.URL)+`auth:
  enabled: true
  keys:
    - name: ip-in
      key: `+keyIPIn+`
      owner: org-ip-in
      key_type: internal
      metadata: {org_id: org-ip-in}
ip_acl:
  enabled: true
  allow: ["127.0.0.1/32"]
`)
	for _, rt := range pipelineRoutes {
		if code, body := pipelinePost(t, g, keyIPIn, rt.path, rt.body); code != http.StatusOK {
			t.Errorf("node ip_acl allow [127.0.0.1/32], caller 127.0.0.1: POST %s -> %d %.200s; want 200", rt.path, code, body)
		}
	}
}

// TestKeyAllowedIPsEveryPipelineRoute: a key whose allowed_ips holds the
// caller is served on every pipeline route, on a gateway with no ip_acl and no
// org configs (no admin token, no control plane).
func TestKeyAllowedIPsEveryPipelineRoute(t *testing.T) {
	provider := startMediaProvider(t)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
`+ipProviderBlock(provider.URL)+`auth:
  enabled: true
  keys:
    - name: keyip-in
      key: `+keyKeyIPIn+`
      owner: org-keyip
      key_type: internal
      allowed_ips: ["127.0.0.1/32"]
      metadata: {org_id: org-keyip}
`)
	for _, rt := range pipelineRoutes {
		if code, body := pipelinePost(t, g, keyKeyIPIn, rt.path, rt.body); code != http.StatusOK {
			t.Errorf("key allowed_ips [127.0.0.1/32], caller 127.0.0.1: POST %s -> %d %.200s; want 200", rt.path, code, body)
		}
	}
}

// TestOrgIPACLReadsTheTrustedClientIP: the pipeline's rules read the address
// TrustedClientIP resolved, not the TCP peer. Behind a trusted proxy
// (127.0.0.1), a request whose X-Forwarded-For names 10.9.9.9 is from
// 10.9.9.9: an org allowing 10.9.9.9 is served on embeddings, an org allowing
// only 127.0.0.1 is refused there.
func TestOrgIPACLReadsTheTrustedClientIP(t *testing.T) {
	provider := startMediaProvider(t)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
  trusted_proxies: ["127.0.0.1/32"]
`+ipProviderBlock(provider.URL)+`auth:
  enabled: true
  keys:
    - name: ip-in
      key: `+keyIPIn+`
      owner: org-ip-in
      key_type: internal
      metadata: {org_id: org-ip-in}
    - name: ip-out
      key: `+keyIPOut+`
      owner: org-ip-out
      key_type: internal
      metadata: {org_id: org-ip-out}
admin:
  token: `+testAdminToken+`
`)
	for org, body := range map[string]string{
		"org-ip-in":  `{"ip_acl":{"enabled":true,"allow":["10.9.9.9/32"]}}`,
		"org-ip-out": `{"ip_acl":{"enabled":true,"allow":["127.0.0.1/32"]}}`,
	} {
		if code, resp := adminCall(t, g, http.MethodPut, "/-/orgs/"+org+"/config", body); code != http.StatusOK {
			t.Fatalf("PUT /-/orgs/%s/config -> %d %s", org, code, resp)
		}
	}
	embed := func(key string) (int, string) {
		req, _ := http.NewRequest(http.MethodPost, g.url+"/v1/embeddings", strings.NewReader(`{"model":"text-embedding-3-small","input":"hi"}`))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+key)
		req.Header.Set("X-Forwarded-For", "10.9.9.9")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("POST /v1/embeddings: %v", err)
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, strings.TrimSpace(string(b))
	}
	if code, body := embed(keyIPIn); code != http.StatusOK {
		t.Errorf("org allow [10.9.9.9/32], X-Forwarded-For 10.9.9.9 via trusted 127.0.0.1: embeddings -> %d %.200s; want 200", code, body)
	}
	if code, body := embed(keyIPOut); code != http.StatusForbidden || !strings.Contains(body, "IP address is not allowed") {
		t.Errorf("org allow [127.0.0.1/32], X-Forwarded-For 10.9.9.9 via trusted 127.0.0.1: embeddings -> %d %.200s; want 403 (the client is 10.9.9.9, not the proxy)", code, body)
	}
}

// TestClientIPPlugin: the plugin fills client_ip from the address
// TrustedClientIP recorded, keeps a value a handler set, and leaves the key
// unset when the context carries no address (the IP rules then refuse).
func TestClientIPPlugin(t *testing.T) {
	resolver, err := middleware.NewClientIPResolver(config.ServerConfig{TrustedProxies: []string{"192.0.2.10/32"}})
	if err != nil {
		t.Fatal(err)
	}
	// requestCtx is the context a handler builds its pipeline call from:
	// the request's, after TrustedClientIP, with a timeout on it.
	requestCtx := func(peer, xff string) context.Context {
		var got context.Context
		h := middleware.TrustedClientIP(resolver)(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
			ctx, cancel := context.WithCancel(r.Context())
			t.Cleanup(cancel)
			got = ctx
		}))
		r := httptest.NewRequest(http.MethodPost, "/v1/embeddings", nil)
		r.RemoteAddr = peer
		if xff != "" {
			r.Header.Set("X-Forwarded-For", xff)
		}
		h.ServeHTTP(httptest.NewRecorder(), r)
		return got
	}
	run := func(ctx context.Context, preset string) (string, bool) {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		if preset != "" {
			rc.Metadata["client_ip"] = preset
		}
		if res := (clientIPPlugin{}).ProcessRequest(ctx, rc); res.Error != nil || res.Action != pipeline.Continue {
			t.Fatalf("ProcessRequest = %+v, want Continue", res)
		}
		v, ok := rc.Metadata["client_ip"]
		return v, ok
	}
	if v, _ := run(requestCtx("203.0.113.7:4000", ""), ""); v != "203.0.113.7" {
		t.Errorf("no handler value, peer 203.0.113.7: client_ip = %q, want 203.0.113.7", v)
	}
	if v, _ := run(requestCtx("192.0.2.10:4000", "198.51.100.4"), ""); v != "198.51.100.4" {
		t.Errorf("trusted proxy 192.0.2.10, X-Forwarded-For 198.51.100.4: client_ip = %q, want 198.51.100.4", v)
	}
	if v, _ := run(requestCtx("203.0.113.7:4000", ""), "198.51.100.99"); v != "198.51.100.99" {
		t.Errorf("handler set 198.51.100.99: client_ip = %q, want it kept", v)
	}
	if v, ok := run(context.Background(), ""); ok {
		t.Errorf("context with no resolved address: client_ip = %q, want it unset", v)
	}
}

// TestOrgIPACLEveryKeyGatedRouteWithTheNodeSwitch: with ip_acl.enabled and
// no node list (no node rule), an org's ip_acl also covers the key-gated
// routes that never run the pipeline (the models list, files), as
// config.example and the Helm values note tell operators.
func TestOrgIPACLEveryKeyGatedRouteWithTheNodeSwitch(t *testing.T) {
	provider := startMediaProvider(t)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
`+ipProviderBlock(provider.URL)+`auth:
  enabled: true
  keys:
    - name: ip-in
      key: `+keyIPIn+`
      owner: org-ip-in
      key_type: internal
      metadata: {org_id: org-ip-in}
    - name: ip-out
      key: `+keyIPOut+`
      owner: org-ip-out
      key_type: internal
      metadata: {org_id: org-ip-out}
admin:
  token: `+testAdminToken+`
ip_acl:
  enabled: true
`)
	for org, body := range map[string]string{
		"org-ip-in":  `{"ip_acl":{"enabled":true,"allow":["127.0.0.1/32"]}}`,
		"org-ip-out": `{"ip_acl":{"enabled":true,"allow":["10.9.9.9/32"]}}`,
	} {
		if code, resp := adminCall(t, g, http.MethodPut, "/-/orgs/"+org+"/config", body); code != http.StatusOK {
			t.Fatalf("PUT /-/orgs/%s/config -> %d %s", org, code, resp)
		}
	}
	get := func(key, path string) (int, string) {
		req, _ := http.NewRequest(http.MethodGet, g.url+path, nil)
		req.Header.Set("Authorization", "Bearer "+key)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("GET %s: %v", path, err)
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, strings.TrimSpace(string(b))
	}
	for _, path := range []string{"/v1/models", "/v1/files"} {
		if code, body := get(keyIPIn, path); code != http.StatusOK {
			t.Errorf("ip_acl.enabled, no node list; org allow [127.0.0.1/32]: GET %s -> %d %.200s; want 200", path, code, body)
		}
		if code, body := get(keyIPOut, path); code != http.StatusForbidden || !strings.Contains(body, "IP address is not allowed") {
			t.Errorf("ip_acl.enabled, no node list; org allow [10.9.9.9/32], caller 127.0.0.1: GET %s -> %d %.200s; want 403", path, code, body)
		}
	}
	for _, rt := range pipelineRoutes {
		if code, body := pipelinePost(t, g, keyIPIn, rt.path, rt.body); code != http.StatusOK {
			t.Errorf("ip_acl.enabled, no node list; org allow [127.0.0.1/32]: POST %s -> %d %.200s; want 200", rt.path, code, body)
		}
		if code, body := pipelinePost(t, g, keyIPOut, rt.path, rt.body); code != http.StatusForbidden {
			t.Errorf("ip_acl.enabled, no node list; org allow [10.9.9.9/32]: POST %s -> %d %.200s; want 403", rt.path, code, body)
		}
	}
}
