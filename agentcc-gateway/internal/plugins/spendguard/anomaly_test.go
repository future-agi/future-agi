package spendguard

import (
	"net/http"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// alerts records the anomaly events a Guard raises.
type alerts struct {
	mu     sync.Mutex
	events []AnomalyEvent
}

func (a *alerts) record(ev AnomalyEvent) {
	a.mu.Lock()
	a.events = append(a.events, ev)
	a.mu.Unlock()
}

func (a *alerts) all() []AnomalyEvent {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]AnomalyEvent(nil), a.events...)
}

// spend admits and settles billed micros for org at the guard's clock.
func spend(t *testing.T, g *Guard, org, hold string, billed int64) {
	t.Helper()
	if apiErr := g.Admit(bg, org, "", billed, hold); apiErr != nil {
		t.Fatalf("admit %s: %v", hold, apiErr)
	}
	g.Settle(bg, org, hold, billed)
}

func anomalyGuard(t *testing.T, cfgs map[string]*tenant.OrgConfig) (*Guard, *clock, *alerts, *redisstate.SpendGuardStore) {
	store, _, _ := testRedis(t)
	g, clk := guardWith(store, config.SpendGuardConfig{}, cfgs)
	a := &alerts{}
	g.SetAlerter(a.record)
	return g, clk, a, store
}

// A spike (5-minute spend over 10 x the trailing hour's 5-minute average and
// over $1) raises one managed_spend.anomaly per org per hour.
func TestSpendGuardAnomalyAlertsOncePerHour(t *testing.T) {
	g, clk, a, _ := anomalyGuard(t, nil)
	clk.Set(t0.Add(-30 * time.Minute))
	spend(t, g, "org-s", "base", 60_000) // $0.06 in the trailing hour: $0.005 per 5 minutes
	clk.Set(t0)
	spend(t, g, "org-s", "small", 900_000) // $0.90: over 10 x the average but under $1
	if n := len(a.all()); n != 0 {
		t.Fatalf("%d alerts under $1", n)
	}
	spend(t, g, "org-s", "spike", 200_000) // $1.10 in 5 minutes
	evs := a.all()
	if len(evs) != 1 {
		t.Fatalf("%d alerts after the spike, want 1", len(evs))
	}
	ev := evs[0]
	if ev.OrgID != "org-s" || ev.Reason != AnomalySpike || ev.Spend5mUSD != 1.10 || ev.TrailingAvg5mUSD != 0.005 ||
		ev.Spend1hUSD != 1.16 || ev.AutoPaused || ev.EventID == "" || ev.TsMs != t0.UnixMilli() {
		t.Fatalf("event %+v", ev)
	}
	clk.Set(t0.Add(2 * time.Minute))
	spend(t, g, "org-s", "spike-2", 500_000)
	if n := len(a.all()); n != 1 {
		t.Fatalf("%d alerts within the hour, want 1", n)
	}
}

// A 5-minute spend that is not 10 x the trailing average is no spike, however
// large.
func TestSpendGuardAnomalyNeedsTenTimesTheTrailingAverage(t *testing.T) {
	g, clk, a, _ := anomalyGuard(t, nil)
	// $0.20 every 5 minutes through the trailing hour: none of these is a spike.
	for k := 12; k >= 1; k-- {
		clk.Set(t0.Add(-time.Duration(5*k) * time.Minute))
		spend(t, g, "org-busy", "base-"+strconv.Itoa(k), 200_000)
	}
	clk.Set(t0)
	spend(t, g, "org-busy", "now", 1_900_000) // $1.90: 9.5 x the $0.20 average
	if n := len(a.all()); n != 0 {
		t.Fatalf("%d alerts at 9.5 x the trailing average", n)
	}
	spend(t, g, "org-busy", "more", 200_000) // $2.10: 10.5 x
	if n := len(a.all()); n != 1 {
		t.Fatalf("%d alerts at 10.5 x the trailing average, want 1", n)
	}
}

// Spend over alert_usd_per_hour in the last hour raises the alert too.
func TestSpendGuardAnomalyHourlyThreshold(t *testing.T) {
	cfg := &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true,
		SpendGuard: &tenant.ManagedSpendGuardConfig{AlertUSDPerHour: usd(0.5)}}}
	g, clk, a, _ := anomalyGuard(t, map[string]*tenant.OrgConfig{"org-h": cfg})
	clk.Set(t0.Add(-50 * time.Minute))
	spend(t, g, "org-h", "early", 300_000)
	clk.Set(t0)
	spend(t, g, "org-h", "at-threshold", 200_000) // exactly $0.50: not over
	if n := len(a.all()); n != 0 {
		t.Fatalf("%d alerts at the threshold", n)
	}
	spend(t, g, "org-h", "over", 1)
	evs := a.all()
	if len(evs) != 1 || evs[0].Reason != AnomalyHourlyThreshold || evs[0].AlertUSDPerHour != 0.5 {
		t.Fatalf("events %+v, want one hourly_threshold alert", evs)
	}
}

// With auto_pause, the anomaly also pauses the org's managed requests (402
// managed_spend_paused) until an admin unpauses it.
func TestSpendGuardAutoPauseAndUnpause(t *testing.T) {
	cfg := &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true,
		SpendGuard: &tenant.ManagedSpendGuardConfig{AutoPause: true}}}
	g, _, a, _ := anomalyGuard(t, map[string]*tenant.OrgConfig{"org-ap": cfg})
	spend(t, g, "org-ap", "spike", 1_500_000)
	if evs := a.all(); len(evs) != 1 || !evs[0].AutoPaused {
		t.Fatalf("events %+v, want one auto-paused alert", evs)
	}
	apiErr := g.Admit(bg, "org-ap", "key_a", 10, "after")
	if apiErr == nil || apiErr.Status != http.StatusPaymentRequired || apiErr.Code != "managed_spend_paused" {
		t.Fatalf("after the auto-pause: %+v", apiErr)
	}
	if w := writeErr(apiErr); checkErrorContract(t, w).Error.Metadata["limit_source"] != "agentcc_budget" {
		t.Fatalf("auto-pause refusal %s", w.Body)
	}
	cleared, err := g.Unpause(bg, "org-ap")
	if err != nil || !cleared {
		t.Fatalf("unpause: %v %v", cleared, err)
	}
	if apiErr := g.Admit(bg, "org-ap", "key_a", 10, "resumed"); apiErr != nil {
		t.Fatalf("after unpause: %v", apiErr)
	}
	// Without auto_pause the same spike only alerts.
	g2, _, a2, _ := anomalyGuard(t, nil)
	spend(t, g2, "org-np", "spike", 1_500_000)
	if evs := a2.all(); len(evs) != 1 || evs[0].AutoPaused {
		t.Fatalf("events %+v", evs)
	}
	if apiErr := g2.Admit(bg, "org-np", "", 10, "still-open"); apiErr != nil {
		t.Fatalf("an alert without auto_pause paused the org: %v", apiErr)
	}
}

// Only the first of many replicas that see the same anomaly raises it.
func TestSpendGuardAnomalyOneAlertAcrossReplicas(t *testing.T) {
	store, _, _ := testRedis(t)
	a := &alerts{}
	var guards []*Guard
	for range 4 {
		g, _ := guardWith(store, config.SpendGuardConfig{}, nil)
		g.SetAlerter(a.record)
		guards = append(guards, g)
	}
	var wg sync.WaitGroup
	for i, g := range guards {
		wg.Add(1)
		go func(i int, g *Guard) {
			defer wg.Done()
			spend(t, g, "org-r", "r"+strconv.Itoa(i), 1_100_000)
		}(i, g)
	}
	wg.Wait()
	if n := len(a.all()); n != 1 {
		t.Fatalf("%d alerts from 4 replicas, want 1", n)
	}
}
