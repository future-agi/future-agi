package main

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
)

// A /-/batches batch's items are judged by every IP rule (an org's ip_acl,
// the node's ip_acl, a key's allowed_ips) by the address that submitted the
// batch, as the Test Playground's calls are: each item carries the submit
// request's client address and its X-Forwarded-For / X-Real-IP (R7-BATCH,
// internal/batch CallerFor). For a console batch that is the app backend's
// address. These tests run the real binary in a child process, as
// readiness_test.go does, and submit from 127.0.0.1.
//
// Since 733 a batch is submitted the way FA submits a person's console batch:
// an fe1 request envelope bound to the route and the exact body, beside that
// person's batch grant, never with the admin bearer or a batch key. Its items
// run as that person in the envelope's org. The app below lists no API key at
// all; its delegation snapshot names the person as a live member of both orgs.

const (
	r7fBatchOrgIn  = "7f000001-0000-4000-8000-000000000001" // allow list [127.0.0.1/32]
	r7fBatchOrgOut = "7f000001-0000-4000-8000-000000000002" // allow list [10.9.9.9/32]
	r7fBatchUser   = "7f000001-0000-4000-8000-0000000000aa" // a member of both orgs, holding no API key
	r7fBatchRun    = "7f000001-0000-4000-8000-0000000000bb" // the grant's batch:<id> subject
)

// r7fOrgConfig gives an org the gateway's openai provider with a key of its
// own, as readiness_test.go's app does, and an ip_acl allowing only allow.
func r7fOrgConfig(allow string) string {
	return `{"providers":{"openai":{"enabled":true,"api_key":"org-provider","models":["gpt-4o"]}},` +
		`"ip_acl":{"enabled":true,"allow":["` + allow + `"]}}`
}

// r7fControlPlane stands in for the app: an authoritative key list with no
// keys, the delegation snapshot, and both orgs' configs.
func r7fControlPlane(t *testing.T) *httptest.Server {
	t.Helper()
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/agentcc/api-keys/bulk/":
			fmt.Fprintf(w, `{"status":true,"result":[],"snapshot":{"complete":true,"issued_ever":0},"retired":[],"config_conflicts":[],`+
				`"delegation":{"complete":true,"fetched_at":%q,"members":[`+
				`{"org_id":%q,"user_id":%q,"workspace_id":"-"},{"org_id":%q,"user_id":%q,"workspace_id":"-"}],"revoked_grants":[]}}`,
				time.Now().UTC().Format(time.RFC3339), r7fBatchOrgIn, r7fBatchUser, r7fBatchOrgOut, r7fBatchUser)
		case "/agentcc/org-configs/bulk/":
			fmt.Fprintf(w, `{"status":true,"result":{%q:%s,%q:%s}}`,
				r7fBatchOrgIn, r7fOrgConfig("127.0.0.1/32"), r7fBatchOrgOut, r7fOrgConfig("10.9.9.9/32"))
		default:
			fmt.Fprint(w, `{"status":true,"result":{}}`)
		}
	}))
	t.Cleanup(app.Close)
	return app
}

func r7fBatchGateway(t *testing.T, serverExtra, extra string) *readinessGateway {
	t.Helper()
	provider := startR7MediaProvider(t)
	app := r7fControlPlane(t)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
`+serverExtra+r7IPProviderBlock(provider.URL)+`auth:
  enabled: true
control_plane:
  url: `+app.URL+`
  sync_on_startup: true
admin:
  token: `+testAdminToken+`
`+extra)
	// Ready: past the nonce epoch E + 5 s, with the first key list, and so
	// the delegation snapshot, applied. Then both orgs' configs have loaded.
	for deadline := time.Now().Add(30 * time.Second); g.get("/readyz") != http.StatusOK; time.Sleep(20 * time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatal("/readyz did not answer 200 within 30 s")
		}
	}
	for _, org := range []string{r7fBatchOrgIn, r7fBatchOrgOut} {
		for deadline := time.Now().Add(15 * time.Second); ; time.Sleep(20 * time.Millisecond) {
			code, out := r7Admin(t, g, http.MethodGet, "/-/orgs/"+org+"/config", "")
			if code == http.StatusOK {
				break
			}
			if time.Now().After(deadline) {
				t.Fatalf("GET /-/orgs/%s/config -> %d %s: the app's org config never loaded", org, code, out)
			}
		}
	}
	return g
}

// r7fDelegation is FA's side of r7fBatchUser's console batch in org: a batch
// grant and a request envelope bound to POST /-/batches and body. The subject
// here is the IP rules, not the wire, so the envelope comes from
// auth.SignEnvelope; internal/auth and internal/server pin the wire itself
// with independent builders and the shared vectors. The gateway cannot verify
// the grant's own MAC (FA's), so any 32 bytes stand in for it.
func r7fDelegation(t *testing.T, org, body string) (envelope, grant string) {
	t.Helper()
	// /readyz answered 200, so now >= E + 5 s: the next whole second is past
	// the nonce epoch and within the 5 s of allowed clock skew.
	iat := time.Now().Unix() + 1
	grantID, err := auth.NewEnvelopeID()
	if err != nil {
		t.Fatal(err)
	}
	canonical := strings.Join([]string{"fe1", "grant", auth.EnvelopeKid, "fa", "fa-eval-grant", "-", "-",
		strconv.FormatInt(iat, 10), strconv.FormatInt(iat+600, 10), grantID, "batch", org, "-", r7fBatchUser,
		"person", "batch:" + r7fBatchRun, "eval", "-", "-"}, "\n")
	standIn := sha256.Sum256([]byte("r7f stand-in for FA's grant MAC\n" + canonical))
	grant = "fe1." + base64.RawURLEncoding.EncodeToString([]byte(canonical)) + "." + base64.RawURLEncoding.EncodeToString(standIn[:])
	id, err := auth.NewEnvelopeID()
	if err != nil {
		t.Fatal(err)
	}
	envelope, err = auth.SignEnvelope(testAdminToken, auth.EnvelopeClaims{
		Mode: "request", Kid: auth.EnvelopeKid, Iss: "fa", Aud: "gw-delegated",
		Method: http.MethodPost, Route: "/-/batches", Iat: iat, Exp: iat + 60, ID: id,
		Kind: "-", Org: org, Workspace: "-", User: r7fBatchUser, Via: "grant:" + grantID, Sub: "-",
		Scope: "batch", Body: auth.EnvelopeDigest([]byte(body)), Grant: auth.EnvelopeDigest([]byte(grant)),
	})
	if err != nil {
		t.Fatalf("sign the batch envelope: %v", err)
	}
	return envelope, grant
}

// r7fRunBatch submits a two-item batch as FA does for r7fBatchUser in org (a
// fresh envelope and grant, and the given forwarded address, if any), waits
// for it to finish and returns each item's status and error.
func r7fRunBatch(t *testing.T, g *readinessGateway, org, forwardedFor string) []map[string]any {
	t.Helper()
	body := `{"requests":[{"model":"gpt-4o","messages":[{"role":"user","content":"a"}]},` +
		`{"model":"gpt-4o","messages":[{"role":"user","content":"b"}]}],"max_concurrency":2}`
	req, _ := http.NewRequest(http.MethodPost, g.url+"/-/batches", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	envelope, grant := r7fDelegation(t, org, body)
	req.Header.Set(auth.EnvelopeHeader, envelope)
	req.Header.Set(auth.GrantHeader, grant)
	if forwardedFor != "" {
		req.Header.Set("X-Forwarded-For", forwardedFor)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("POST /-/batches: %v", err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("POST /-/batches -> %d %s", resp.StatusCode, raw)
	}
	var sub struct {
		BatchID string `json:"batch_id"`
	}
	if err := json.Unmarshal(raw, &sub); err != nil || sub.BatchID == "" {
		t.Fatalf("POST /-/batches body %s: no batch_id", raw)
	}
	path := fmt.Sprintf("/-/batches/%s?org_id=%s", sub.BatchID, org)
	for deadline := time.Now().Add(15 * time.Second); ; time.Sleep(20 * time.Millisecond) {
		code, out := r7Admin(t, g, http.MethodGet, path, "")
		if code != http.StatusOK {
			t.Fatalf("GET %s -> %d %s", path, code, out)
		}
		var b struct {
			Status  string           `json:"status"`
			Results []map[string]any `json:"results"`
		}
		if err := json.Unmarshal([]byte(out), &b); err != nil {
			t.Fatalf("GET %s body %s: %v", path, out, err)
		}
		if b.Status == "completed" || b.Status == "cancelled" {
			if len(b.Results) != 2 {
				t.Fatalf("batch %s: %d results, want 2: %s", sub.BatchID, len(b.Results), out)
			}
			return b.Results
		}
		if time.Now().After(deadline) {
			t.Fatalf("batch %s still %s after 15 s", sub.BatchID, b.Status)
		}
	}
}

func r7fExpectItems(t *testing.T, what string, items []map[string]any, allowed bool) {
	t.Helper()
	for i, item := range items {
		status, _ := item["status"].(string)
		errText := fmt.Sprint(item["error"])
		switch {
		case allowed && status != "success":
			t.Errorf("%s: item %d status %q error %s; want success", what, i, status, errText)
		case !allowed && (status != "error" || !strings.Contains(errText, "IP address is not allowed")):
			t.Errorf("%s: item %d status %q error %s; want error \"Access denied: IP address is not allowed\"", what, i, status, errText)
		}
	}
}

func TestR7fBatchItemsAreJudgedByTheSubmittingAddress(t *testing.T) {
	t.Run("org ip_acl, node switch off, trusted proxy set", func(t *testing.T) {
		g := r7fBatchGateway(t, r7iUnusedProxy, "")
		r7fExpectItems(t, "submit from 127.0.0.1, org allow list [127.0.0.1/32]",
			r7fRunBatch(t, g, r7fBatchOrgIn, ""), true)
		r7fExpectItems(t, "submit from 127.0.0.1, org allow list [10.9.9.9/32]",
			r7fRunBatch(t, g, r7fBatchOrgOut, ""), false)
	})

	t.Run("node ip_acl.enabled, no trusted proxy", func(t *testing.T) {
		g := r7fBatchGateway(t, "", "ip_acl:\n  enabled: true\n")
		r7fExpectItems(t, "ip_acl.enabled, submit from 127.0.0.1, org allow list [127.0.0.1/32]",
			r7fRunBatch(t, g, r7fBatchOrgIn, ""), true)
		r7fExpectItems(t, "ip_acl.enabled, submit from 127.0.0.1, org allow list [10.9.9.9/32]",
			r7fRunBatch(t, g, r7fBatchOrgOut, ""), false)
	})

	// The node's own allow list judges a batch's items by the same address
	// (the org's list allows it, so only the node's list decides).
	t.Run("node ip_acl allow list", func(t *testing.T) {
		for _, c := range []struct {
			allow   string
			allowed bool
		}{{"127.0.0.1/32", true}, {"10.9.9.9/32", false}} {
			g := r7fBatchGateway(t, "", "ip_acl:\n  enabled: true\n  allow: [\""+c.allow+"\"]\n")
			r7fExpectItems(t, "node allow list ["+c.allow+"], submit from 127.0.0.1, org allow list [127.0.0.1/32]",
				r7fRunBatch(t, g, r7fBatchOrgIn, ""), c.allowed)
		}
	})

	// Behind a trusted proxy the submitting address is the one the proxy
	// vouches for, as on any request.
	t.Run("forwarded address from a trusted proxy", func(t *testing.T) {
		g := r7fBatchGateway(t, "  trusted_proxies: [\"127.0.0.1/32\"]\n", "")
		r7fExpectItems(t, "submit forwarded for 10.9.9.9, org allow list [10.9.9.9/32]",
			r7fRunBatch(t, g, r7fBatchOrgOut, "10.9.9.9"), true)
		r7fExpectItems(t, "submit forwarded for 10.9.9.9, org allow list [127.0.0.1/32]",
			r7fRunBatch(t, g, r7fBatchOrgIn, "10.9.9.9"), false)
		r7fExpectItems(t, "submit from the proxy itself, org allow list [10.9.9.9/32]",
			r7fRunBatch(t, g, r7fBatchOrgOut, ""), false)
	})
}

// The shipped texts say how a batch's items are judged: by the address that
// submitted the batch (config.example.yaml in the org-sections note and in
// the ip_acl block, the Helm chart's values.yaml, release note U16).
func TestR7fBatchIPRuleTextsMatchTheGateway(t *testing.T) {
	stale := []*regexp.Regexp{
		regexp.MustCompile(`(?i)batch[^.]{0,80}no client IP`),
		regexp.MustCompile(`(?i)refuses every (item of an admin batch|/-/batches item)`),
		regexp.MustCompile(`(?i)every item of an admin batch[^.]{0,40}fails`),
	}
	const want = "by the address that submitted the batch"
	for _, f := range []struct {
		path string
		min  int
	}{
		{"../../config.example.yaml", 2},
		{"../../../deploy/helm/futureagi/values.yaml", 1},
		{"../../../docs/gateway/release-notes.md", 1},
	} {
		raw, err := os.ReadFile(f.path)
		if err != nil {
			t.Fatal(err)
		}
		// Comments wrap: read each file as one line of words, without the
		// YAML comment markers.
		text := strings.Join(strings.Fields(strings.ReplaceAll(string(raw), "#", " ")), " ")
		for _, re := range stale {
			if m := re.FindString(text); m != "" {
				t.Errorf("%s says %q: a batch's items carry the submitting address", f.path, m)
			}
		}
		if n := strings.Count(text, want); n < f.min {
			t.Errorf("%s says %q %d times, want at least %d", f.path, want, n, f.min)
		}
	}
}
