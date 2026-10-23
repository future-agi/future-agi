package main

// The alerting plugin
// main.go builds (newAlertingPlugin) sends an organization's own alert channel
// through the guarded egress dialer, built from mcp.egress.private_allow. The
// channel is an httptest server on 127.0.0.1.

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// fireOrgAlert sends one request from org-a through the alerting plugin main
// builds for cfg; the org's rule fires on any request and posts to its webhook
// channel at url.
func fireOrgAlert(t *testing.T, cfg *config.Config, url string) {
	t.Helper()
	store := tenant.NewStore()
	store.Set("org-a", &tenant.OrgConfig{Alerting: &tenant.AlertingConfig{
		Enabled: true,
		Rules: []*tenant.AlertRuleConfig{{Name: "any-request", Metric: "request_count", Condition: ">=",
			Threshold: 1, Channels: []string{"ch"}}},
		Channels: []*tenant.AlertChannelConfig{{Name: "ch", Type: "webhook", URL: url}},
	}})
	plugin, _, err := newAlertingPlugin(cfg, store)
	if err != nil {
		t.Fatalf("newAlertingPlugin: %v", err)
	}
	plugin.ProcessResponse(context.Background(), &models.RequestContext{
		RequestID: "req-alert",
		StartTime: time.Now(),
		Metadata:  map[string]string{tenant.MetadataKeyOrgID: "org-a"},
		Timings:   map[string]time.Duration{},
	})
}

func TestOrgAlertChannel_DialsThroughTheOperatorList(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hits.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	unlisted := config.DefaultConfig()
	unlisted.Alerting.Enabled = true
	fireOrgAlert(t, unlisted, srv.URL+"/hook")
	if n := hits.Load(); n != 0 {
		t.Fatalf("an org alert webhook at an unlisted loopback address was delivered %d time(s)", n)
	}

	listed := config.DefaultConfig()
	listed.Alerting.Enabled = true
	listed.MCP.Egress.PrivateAllow = []string{strings.TrimPrefix(srv.URL, "http://")}
	fireOrgAlert(t, listed, srv.URL+"/hook")
	deadline := time.Now().Add(5 * time.Second)
	for hits.Load() == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if n := hits.Load(); n != 1 {
		t.Fatalf("the org alert webhook the operator listed was delivered %d time(s), want 1", n)
	}
}

func TestNewAlertingPlugin_RefusesABadEntry(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.MCP.Egress.PrivateAllow = []string{"host:0"}
	if _, _, err := newAlertingPlugin(cfg, tenant.NewStore()); err == nil {
		t.Fatal("newAlertingPlugin accepted a malformed private_allow entry")
	}
}
