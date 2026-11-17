package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	alertpkg "github.com/futureagi/agentcc-gateway/internal/alerting"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
	"github.com/futureagi/agentcc-gateway/internal/plugins/spendguard"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An organization's own alert channels receive its managed spend anomaly
// alerts through the same guarded egress dialer as its other alerts:
// a loopback, private or metadata
// URL is refused unless the operator listed it in mcp.egress.private_allow.
// The operator's own config.yaml channels are trusted and keep a plain
// client.

// anomalyRecorder is an HTTP listener on loopback that counts the alerts it
// receives and keeps the last one's org header and alert name.
type anomalyRecorder struct {
	srv       *httptest.Server
	mu        sync.Mutex
	hits      int
	orgHeader string
	alertName string
}

func newAnomalyRecorder(t *testing.T) *anomalyRecorder {
	t.Helper()
	r := &anomalyRecorder{}
	r.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		var body struct {
			Alert string `json:"alert"`
		}
		_ = json.NewDecoder(req.Body).Decode(&body)
		r.mu.Lock()
		r.hits++
		r.orgHeader = req.Header.Get("X-Org-Chosen")
		r.alertName = body.Alert
		r.mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(r.srv.Close)
	return r
}

func (r *anomalyRecorder) seen() (hits int, orgHeader, alertName string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.hits, r.orgHeader, r.alertName
}

func anomalyEgress(t *testing.T, allow ...string) *egress.Dialer {
	t.Helper()
	d, err := egress.New(egress.Options{PrivateAllow: allow})
	if err != nil {
		t.Fatalf("egress.New(%v): %v", allow, err)
	}
	return d
}

// anomalyTenants is a tenant store holding org-a with alerting on and the
// given channels, each carrying a header the org chose.
func anomalyTenants(channels ...*tenant.AlertChannelConfig) *tenant.Store {
	for _, ch := range channels {
		ch.Headers = map[string]string{"X-Org-Chosen": "org-a"}
	}
	store := tenant.NewStore()
	store.Set("org-a", &tenant.OrgConfig{Alerting: &tenant.AlertingConfig{Enabled: true, Channels: channels}})
	return store
}

// operatorAlerting is the operator's config.yaml alerting with one webhook
// channel, "ops", at url.
func operatorAlerting(url string) config.AlertingConfig {
	return config.AlertingConfig{Enabled: true, Channels: []config.AlertChannelConfig{{Name: "ops", Type: "webhook", URL: url}}}
}

func testAnomalyAlert() alertpkg.Alert {
	return alertpkg.Alert{Name: spendguard.EventKindAnomaly, Metric: "managed_spend_usd_1h", Value: 2, Threshold: 1,
		Condition: ">", Window: "5m0s", FiredAt: time.Now(), Service: "agentcc-gateway"}
}

func hourlyAnomaly() spendguard.AnomalyEvent {
	return spendguard.AnomalyEvent{OrgID: "org-a", Reason: spendguard.AnomalyHourlyThreshold, Spend1hUSD: 2, AlertUSDPerHour: 0.01}
}

// TestSpendAnomalyOrgChannelsRefuseInternalAddresses: with no operator
// exception, the org's webhook and slack channels on loopback and its webhook
// on the cloud metadata address are refused by the egress dialer before any
// connection, while the operator's channel on loopback still receives the
// alert.
func TestSpendAnomalyOrgChannelsRefuseInternalAddresses(t *testing.T) {
	ops := newAnomalyRecorder(t)
	internal := newAnomalyRecorder(t)
	store := anomalyTenants(
		&tenant.AlertChannelConfig{Name: "internal", Type: "webhook", URL: internal.srv.URL + "/internal-only"},
		&tenant.AlertChannelConfig{Name: "internal-slack", Type: "slack", URL: internal.srv.URL + "/internal-slack"},
		&tenant.AlertChannelConfig{Name: "metadata", Type: "webhook", URL: "http://169.254.169.254/hook"},
	)

	channels, names := anomalyChannels(operatorAlerting(ops.srv.URL+"/ops"), store, anomalyEgress(t), "org-a")
	for _, want := range []string{"ops", "org:internal", "org:internal-slack", "org:metadata"} {
		if !slices.Contains(names, want) || channels[want] == nil {
			t.Fatalf("anomaly channels %v have no %q", names, want)
		}
	}

	for _, c := range []struct {
		name   string
		reason egress.Reason
	}{
		{"org:internal", egress.ReasonNotAllowed},
		{"org:internal-slack", egress.ReasonNotAllowed},
		{"org:metadata", egress.ReasonForbidden},
	} {
		err := channels[c.name].Send(testAnomalyAlert())
		var refused *egress.RefusedError
		if !errors.As(err, &refused) || refused.Reason != c.reason {
			t.Errorf("%s: Send error %v, want the egress dialer's refusal (%s)", c.name, err, c.reason)
		}
	}
	if hits, _, _ := internal.seen(); hits != 0 {
		t.Fatalf("the org's loopback channels were reached %d times; the egress dialer did not refuse them", hits)
	}

	if err := channels["ops"].Send(testAnomalyAlert()); err != nil {
		t.Fatalf("the operator's channel: %v", err)
	}
	if hits, _, _ := ops.seen(); hits != 1 {
		t.Fatalf("the operator's channel was reached %d times, want 1", hits)
	}
}

// TestSpendAnomalyAlertReachesOperatorNotOrgLoopback: a whole hourly
// anomaly alert goes to the operator's channel and never to the org's
// loopback one.
func TestSpendAnomalyAlertReachesOperatorNotOrgLoopback(t *testing.T) {
	ops := newAnomalyRecorder(t)
	internal := newAnomalyRecorder(t)
	store := anomalyTenants(&tenant.AlertChannelConfig{Name: "internal", Type: "webhook", URL: internal.srv.URL + "/internal-only"})

	channels, names := anomalyChannels(operatorAlerting(ops.srv.URL+"/ops"), store, anomalyEgress(t), "org-a")
	sendAnomalyAlert(channels, names, hourlyAnomaly())

	if hits, _, name := ops.seen(); hits != 1 || name != spendguard.EventKindAnomaly {
		t.Fatalf("the operator's channel got %d alerts (last %q), want 1 %s", hits, name, spendguard.EventKindAnomaly)
	}
	if hits, _, _ := internal.seen(); hits != 0 {
		t.Fatalf("the org's loopback channel was reached %d times", hits)
	}
}

// TestSpendAnomalyOrgChannelListedTargetDelivers: once the operator lists the
// address in mcp.egress.private_allow, the org's channel there receives the
// anomaly alert with the org's own header.
func TestSpendAnomalyOrgChannelListedTargetDelivers(t *testing.T) {
	listed := newAnomalyRecorder(t)
	store := anomalyTenants(&tenant.AlertChannelConfig{Name: "hook", Type: "webhook", URL: listed.srv.URL + "/hook"})
	dialer := anomalyEgress(t, strings.TrimPrefix(listed.srv.URL, "http://"))

	channels, names := anomalyChannels(config.AlertingConfig{}, store, dialer, "org-a")
	sendAnomalyAlert(channels, names, hourlyAnomaly())

	hits, header, name := listed.seen()
	if hits != 1 || header != "org-a" || name != spendguard.EventKindAnomaly {
		t.Fatalf("the listed org channel got %d alerts (header %q, alert %q), want 1 with the org's header", hits, header, name)
	}
}

// TestSpendAnomalyNilEgressRefusesLoopback: without a dialer the org's
// channels still get the guard with no exceptions.
func TestSpendAnomalyNilEgressRefusesLoopback(t *testing.T) {
	internal := newAnomalyRecorder(t)
	store := anomalyTenants(&tenant.AlertChannelConfig{Name: "internal", Type: "webhook", URL: internal.srv.URL + "/internal-only"})

	channels, _ := anomalyChannels(config.AlertingConfig{}, store, nil, "org-a")
	err := channels["org:internal"].Send(testAnomalyAlert())
	var refused *egress.RefusedError
	if !errors.As(err, &refused) {
		t.Fatalf("Send error %v, want the egress dialer's refusal", err)
	}
	if hits, _, _ := internal.seen(); hits != 0 {
		t.Fatalf("the org's loopback channel was reached %d times without a dialer", hits)
	}
}

// TestSpendAnomalyOrgAlertingOffSendsNothingToOrg: an org whose alerting is
// off gets no anomaly channel of its own.
func TestSpendAnomalyOrgAlertingOffSendsNothingToOrg(t *testing.T) {
	store := tenant.NewStore()
	store.Set("org-a", &tenant.OrgConfig{Alerting: &tenant.AlertingConfig{Enabled: false, Channels: []*tenant.AlertChannelConfig{
		{Name: "hook", Type: "webhook", URL: "https://hooks.example.com/a"},
	}}})
	channels, names := anomalyChannels(config.AlertingConfig{}, store, anomalyEgress(t), "org-a")
	if len(channels) != 0 || len(names) != 0 {
		t.Fatalf("an org with alerting off has anomaly channels %v", names)
	}
}
