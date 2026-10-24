package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"regexp"
	"strings"
	"testing"
	"time"
)

// A /-/batches batch's items are judged by every IP rule (an org's ip_acl,
// the node's ip_acl, a key's allowed_ips) by the address that submitted the
// batch, as the Test Playground's calls are: each item carries the submit
// request's client address and its X-Forwarded-For / X-Real-IP
// (internal/batch CallerFor). For a console batch that is the app backend's
// address. These tests run the real binary in a child process, as
// readiness_test.go does, and submit from 127.0.0.1.

const (
	batchKeyIn  = "sk-agentcc-chk-batch-in-key-0123456789abcdef"
	batchKeyOut = "sk-agentcc-chk-batch-out-key-0123456789abcde"
	batchOrgIn  = "org-batch-in"  // allow list [127.0.0.1/32]
	batchOrgOut = "org-batch-out" // allow list [10.9.9.9/32]
)

func batchGateway(t *testing.T, serverExtra, extra string) *readinessGateway {
	t.Helper()
	provider := startMediaProvider(t)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
`+serverExtra+ipProviderBlock(provider.URL)+`auth:
  enabled: true
  keys:
    - name: batch-in
      key: `+batchKeyIn+`
      owner: `+batchOrgIn+`
      key_type: internal
      metadata: {org_id: `+batchOrgIn+`}
    - name: batch-out
      key: `+batchKeyOut+`
      owner: `+batchOrgOut+`
      key_type: internal
      metadata: {org_id: `+batchOrgOut+`}
admin:
  token: `+testAdminToken+`
`+extra)
	for org, body := range map[string]string{
		batchOrgIn:  `{"ip_acl":{"enabled":true,"allow":["127.0.0.1/32"]}}`,
		batchOrgOut: `{"ip_acl":{"enabled":true,"allow":["10.9.9.9/32"]}}`,
	} {
		if code, resp := adminCall(t, g, http.MethodPut, "/-/orgs/"+org+"/config", body); code != http.StatusOK {
			t.Fatalf("PUT /-/orgs/%s/config -> %d %s", org, code, resp)
		}
	}
	return g
}

// runBatch submits a two-item batch as the console does (the admin token,
// the org's key in x-agentcc-batch-key, and the given forwarded address, if
// any), waits for it to finish and returns each item's status and error.
func runBatch(t *testing.T, g *readinessGateway, key, org, forwardedFor string) []map[string]any {
	t.Helper()
	body := `{"requests":[{"model":"gpt-4o","messages":[{"role":"user","content":"a"}]},` +
		`{"model":"gpt-4o","messages":[{"role":"user","content":"b"}]}],"max_concurrency":2}`
	req, _ := http.NewRequest(http.MethodPost, g.url+"/-/batches", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+testAdminToken)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-agentcc-batch-key", key)
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
		code, out := adminCall(t, g, http.MethodGet, path, "")
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

func expectBatchItems(t *testing.T, what string, items []map[string]any, allowed bool) {
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

func TestBatchItemsAreJudgedByTheSubmittingAddress(t *testing.T) {
	t.Run("org ip_acl, node switch off, trusted proxy set", func(t *testing.T) {
		g := batchGateway(t, unusedTrustedProxy, "")
		expectBatchItems(t, "submit from 127.0.0.1, org allow list [127.0.0.1/32]",
			runBatch(t, g, batchKeyIn, batchOrgIn, ""), true)
		expectBatchItems(t, "submit from 127.0.0.1, org allow list [10.9.9.9/32]",
			runBatch(t, g, batchKeyOut, batchOrgOut, ""), false)
	})

	t.Run("node ip_acl.enabled, no trusted proxy", func(t *testing.T) {
		g := batchGateway(t, "", "ip_acl:\n  enabled: true\n")
		expectBatchItems(t, "ip_acl.enabled, submit from 127.0.0.1, org allow list [127.0.0.1/32]",
			runBatch(t, g, batchKeyIn, batchOrgIn, ""), true)
		expectBatchItems(t, "ip_acl.enabled, submit from 127.0.0.1, org allow list [10.9.9.9/32]",
			runBatch(t, g, batchKeyOut, batchOrgOut, ""), false)
	})

	// The node's own allow list judges a batch's items by the same address
	// (the org's list allows it, so only the node's list decides).
	t.Run("node ip_acl allow list", func(t *testing.T) {
		for _, c := range []struct {
			allow   string
			allowed bool
		}{{"127.0.0.1/32", true}, {"10.9.9.9/32", false}} {
			g := batchGateway(t, "", "ip_acl:\n  enabled: true\n  allow: [\""+c.allow+"\"]\n")
			expectBatchItems(t, "node allow list ["+c.allow+"], submit from 127.0.0.1, org allow list [127.0.0.1/32]",
				runBatch(t, g, batchKeyIn, batchOrgIn, ""), c.allowed)
		}
	})

	// Behind a trusted proxy the submitting address is the one the proxy
	// vouches for, as on any request.
	t.Run("forwarded address from a trusted proxy", func(t *testing.T) {
		g := batchGateway(t, "  trusted_proxies: [\"127.0.0.1/32\"]\n", "")
		expectBatchItems(t, "submit forwarded for 10.9.9.9, org allow list [10.9.9.9/32]",
			runBatch(t, g, batchKeyOut, batchOrgOut, "10.9.9.9"), true)
		expectBatchItems(t, "submit forwarded for 10.9.9.9, org allow list [127.0.0.1/32]",
			runBatch(t, g, batchKeyIn, batchOrgIn, "10.9.9.9"), false)
		expectBatchItems(t, "submit from the proxy itself, org allow list [10.9.9.9/32]",
			runBatch(t, g, batchKeyOut, batchOrgOut, ""), false)
	})
}

// The shipped texts say how a batch's items are judged: by the address that
// submitted the batch (config.example.yaml in the org-sections note and in
// the ip_acl block, the Helm chart's values.yaml, the gateway release notes).
func TestBatchIPRuleTextsMatchTheGateway(t *testing.T) {
	contradicting := []*regexp.Regexp{
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
		for _, re := range contradicting {
			if m := re.FindString(text); m != "" {
				t.Errorf("%s says %q: a batch's items carry the submitting address", f.path, m)
			}
		}
		if n := strings.Count(text, want); n < f.min {
			t.Errorf("%s says %q %d times, want at least %d", f.path, want, n, f.min)
		}
	}
}
