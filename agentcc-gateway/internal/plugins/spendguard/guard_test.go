package spendguard

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func capped(minute, hour float64) *tenant.OrgConfig {
	sg := &tenant.ManagedSpendGuardConfig{}
	if minute > 0 {
		sg.MaxUSDPerMinute = usd(minute)
	}
	if hour > 0 {
		sg.MaxUSDPerHour = usd(hour)
	}
	return &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true, SpendGuard: sg}}
}

// writeErr writes apiErr as the gateway writes it to a client.
func writeErr(apiErr *models.APIError) *httptest.ResponseRecorder {
	w := httptest.NewRecorder()
	models.WriteError(w, apiErr)
	return w
}

type errorBody struct {
	Error struct {
		Type     string         `json:"type"`
		Code     string         `json:"code"`
		Message  string         `json:"message"`
		Param    any            `json:"param"`
		Metadata map[string]any `json:"metadata"`
	} `json:"error"`
}

// checkErrorContract decodes w's body and checks it against
// error-402-v1.schema.json: the code and limit_source are in its enums, the
// metadata names only its properties and every one a limit refusal needs,
// and scope matches its pattern. Reason-only refusals instead validate their
// conditional schema branch. It returns the body.
func checkErrorContract(t *testing.T, w *httptest.ResponseRecorder) errorBody {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "api_contracts", "gateway", "examples", "error-402-v1.schema.json"))
	if err != nil {
		t.Fatal(err)
	}
	var schema struct {
		Properties struct {
			Error struct {
				Properties struct {
					Code struct {
						Enum []string `json:"enum"`
					} `json:"code"`
				} `json:"properties"`
			} `json:"error"`
		} `json:"properties"`
		Defs struct {
			Metadata struct {
				Properties map[string]struct {
					Enum    []string `json:"enum"`
					Pattern string   `json:"pattern"`
				} `json:"properties"`
			} `json:"Metadata"`
		} `json:"$defs"`
	}
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatal(err)
	}
	var body errorBody
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("body %s: %v", w.Body, err)
	}
	e := body.Error
	if !slices.Contains(schema.Properties.Error.Properties.Code.Enum, e.Code) {
		t.Errorf("code %q is not in error-402-v1's enum", e.Code)
	}
	if e.Type == "" || e.Message == "" || e.Param != nil {
		t.Errorf("type %q, message %q, param %v", e.Type, e.Message, e.Param)
	}
	props := schema.Defs.Metadata.Properties
	for k := range e.Metadata {
		if _, ok := props[k]; !ok {
			t.Errorf("metadata.%s is not in error-402-v1", k)
		}
	}
	if reason := e.Metadata["reason"]; reason == "request_exceeds_minute_cap" || reason == "new_org_daily_cap" {
		checkReasonOnlyContract(t, raw, body)
		return body
	}
	for _, k := range []string{"limit_source", "remedy_hint", "scope", "resets_at"} {
		if _, ok := e.Metadata[k]; !ok {
			t.Errorf("metadata.%s missing: %s", k, w.Body)
		}
	}
	if src, _ := e.Metadata["limit_source"].(string); !slices.Contains(props["limit_source"].Enum, src) {
		t.Errorf("limit_source %q is not in error-402-v1's enum", src)
	}
	if scope, _ := e.Metadata["scope"].(string); !regexp.MustCompile(props["scope"].Pattern).MatchString(scope) {
		t.Errorf("scope %q does not match %s", scope, props["scope"].Pattern)
	}
	return body
}

// The 429 names the window that tripped (the org's, or the one key's that
// alone broke the cap) and resets_at when its oldest counted bucket leaves
// it, within the window, with Retry-After the seconds until then.
func TestLimitErrorCarriesScopeAndReset(t *testing.T) {
	store, _, _ := testRedis(t)
	g, clk := guardWith(store, config.SpendGuardConfig{}, map[string]*tenant.OrgConfig{"org-l": capped(0.01, 0)})
	clk.Set(t0) // 10 s into the minute

	if apiErr := g.Admit(bg, "org-l", "key_a", 8_000, "a1"); apiErr != nil {
		t.Fatalf("a1: %v", apiErr)
	}
	// key_a alone would break the cap: scope key:key_a.
	w := writeErr(g.Admit(bg, "org-l", "key_a", 3_000, "a2"))
	if w.Code != http.StatusTooManyRequests {
		t.Fatalf("status %d: %s", w.Code, w.Body)
	}
	body := checkErrorContract(t, w)
	meta := body.Error.Metadata
	if body.Error.Code != "managed_spend_velocity" || body.Error.Type != models.ErrTypeRateLimit ||
		meta["limit_source"] != models.LimitSourceSpendGuard || meta["scope"] != "key:key_a" {
		t.Fatalf("key refusal: %s", w.Body)
	}
	resets, err := time.Parse(time.RFC3339, meta["resets_at"].(string))
	if err != nil {
		t.Fatalf("resets_at %v: %v", meta["resets_at"], err)
	}
	if want := t0.Truncate(time.Minute).Add(time.Minute); !resets.Equal(want) {
		t.Fatalf("resets_at %v, want the end of the minute %v", resets, want)
	}
	if got := w.Header().Get("Retry-After"); got != "50" {
		t.Fatalf("Retry-After %q, want 50 (seconds to the end of the minute)", got)
	}

	// Another key under its own cap, but the org's combined spend breaks it.
	clk.Set(t0.Add(45 * time.Second))
	w = writeErr(g.Admit(bg, "org-l", "key_b", 3_000, "b1"))
	body = checkErrorContract(t, w)
	if w.Code != http.StatusTooManyRequests || body.Error.Metadata["scope"] != "org" {
		t.Fatalf("org refusal: %d %s", w.Code, w.Body)
	}
	if got := w.Header().Get("Retry-After"); got != "5" {
		t.Fatalf("Retry-After %q, want 5", got)
	}

	// The hour window resets when its oldest counted bucket leaves the hour.
	g.SetOrgConfig(orgs(map[string]*tenant.OrgConfig{"org-l": capped(0, 0.01)}))
	clk.Set(t0.Add(30 * time.Minute))
	w = writeErr(g.Admit(bg, "org-l", "", 3_000, "h1"))
	body = checkErrorContract(t, w)
	resets, _ = time.Parse(time.RFC3339, body.Error.Metadata["resets_at"].(string))
	if want := t0.Truncate(time.Minute).Add(time.Hour); body.Error.Metadata["scope"] != "org" || !resets.Equal(want) {
		t.Fatalf("hour refusal: %s, want resets_at %v", w.Body, want)
	}
	if got, _ := strconv.Atoi(w.Header().Get("Retry-After")); got != int(t0.Truncate(time.Minute).Add(time.Hour).Sub(t0.Add(30*time.Minute)).Seconds()) {
		t.Fatalf("Retry-After %d", got)
	}
}

// Every refusal is the error-402-v1 object: the pauses are 402
// managed_spend_paused naming agentcc_budget, scope org and resets_at null.
func TestSpendGuardPausesAre402(t *testing.T) {
	store, rdb, prefix := testRedis(t)
	g, _ := guardWith(store, config.SpendGuardConfig{}, nil)

	rdb.Set(bg, prefix+"pause:org-p:managed_spend", "1", 0)
	w := writeErr(g.Admit(bg, "org-p", "key_a", 10, "p1"))
	body := checkErrorContract(t, w)
	meta := body.Error.Metadata
	if w.Code != http.StatusPaymentRequired || body.Error.Code != "managed_spend_paused" ||
		meta["limit_source"] != models.LimitSourceBudget || meta["scope"] != "org" || meta["resets_at"] != nil {
		t.Fatalf("EE budget pause: %d %s", w.Code, w.Body)
	}
	if w.Header().Get("Retry-After") != "" {
		t.Fatal("a pause carries Retry-After")
	}
	rdb.Del(bg, prefix+"pause:org-p:managed_spend")

	rdb.Set(bg, prefix+"mc:{org-p}:paused", "1", 0)
	w = writeErr(g.Admit(bg, "org-p", "key_a", 10, "p2"))
	body = checkErrorContract(t, w)
	if w.Code != http.StatusPaymentRequired || body.Error.Code != "managed_spend_paused" || body.Error.Metadata["limit_source"] != models.LimitSourceBudget {
		t.Fatalf("auto-pause: %d %s", w.Code, w.Body)
	}
	cleared, err := g.Unpause(bg, "org-p")
	if err != nil || !cleared {
		t.Fatalf("unpause: %v %v", cleared, err)
	}
	if apiErr := g.Admit(bg, "org-p", "key_a", 10, "p3"); apiErr != nil {
		t.Fatalf("after unpause: %v", apiErr)
	}
}

// The new-org daily cap is a value-free 429 and stops applying 7 days after
// the org's first request. It must not disclose the org's counted spend.
func TestSpendGuardNewOrgCapExpiresAfter7Days(t *testing.T) {
	store, _, _ := testRedis(t)
	g, clk := guardWith(store, config.SpendGuardConfig{NewOrgDailyCapUSD: 0.05}, nil)

	if apiErr := g.Admit(bg, "org-n", "", 40_000, "n1"); apiErr != nil {
		t.Fatalf("first request: %v", apiErr)
	}
	clk.Set(t0.Add(2 * time.Hour))
	w := writeErr(g.Admit(bg, "org-n", "", 20_000, "n2"))
	// Preserve the pre-union JSON assertion as well as SG's schema checks.
	var body errorBody
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	body = checkErrorContract(t, w)
	if w.Code != http.StatusTooManyRequests || body.Error.Code != "managed_spend_velocity" ||
		len(body.Error.Metadata) != 1 || body.Error.Metadata["reason"] != "new_org_daily_cap" || w.Header().Get("Retry-After") != "" {
		t.Fatalf("new-org cap: %d %s", w.Code, w.Body)
	}
	clk.Set(t0.Add(7*24*time.Hour - time.Second))
	if apiErr := g.Admit(bg, "org-n", "", 60_000, "n3"); apiErr == nil {
		t.Fatal("the cap stopped applying before 7 days")
	}
	clk.Set(t0.Add(7 * 24 * time.Hour))
	if apiErr := g.Admit(bg, "org-n", "", 60_000, "n4"); apiErr != nil {
		t.Fatalf("7 days after the first request: %v", apiErr)
	}
}

// An org's spend guard can only tighten the gateway's: the stricter non-zero
// cap of the two applies, so a customer cannot lift FutureAGI's own.
func TestSpendGuardOrgConfigTightensGatewayDefaults(t *testing.T) {
	store, _, _ := testRedis(t)
	defaults := config.SpendGuardConfig{MaxUSDPerMinute: 0.01}
	g, _ := guardWith(store, defaults, map[string]*tenant.OrgConfig{
		"org-loose": capped(1.00, 0), // tries to lift the gateway's $0.01
		"org-tight": capped(0.005, 0),
	})
	if apiErr := g.Admit(bg, "org-loose", "", 11_000, "l1"); apiErr == nil {
		t.Fatal("an org config lifted the gateway's per-minute cap")
	}
	if apiErr := g.Admit(bg, "org-tight", "", 6_000, "t1"); apiErr == nil {
		t.Fatal("the org's tighter cap did not apply")
	}
	if apiErr := g.Admit(bg, "org-unset", "", 9_000, "u1"); apiErr != nil {
		t.Fatalf("an org without a spend guard gets the gateway's: %v", apiErr)
	}
}

// Redis unreachable: managed admission fails closed with 503
// managed_billing_unavailable.
func TestSpendGuardFailsClosedWithoutRedis(t *testing.T) {
	g := NewGuard(redisstate.NewSpendGuardStore(nil, ""), config.SpendGuardConfig{})
	apiErr := g.Admit(bg, "org-x", "key_a", 10, "h")
	if apiErr == nil || apiErr.Status != http.StatusServiceUnavailable || apiErr.Code != "managed_billing_unavailable" {
		t.Fatalf("admission without Redis: %+v", apiErr)
	}
}

// The admission check exported for callers outside the pipeline (MCP
// managed tool calls) refuses and corrects exactly as the plugin path does,
// with no RequestContext: driven through the plugin for one org and through
// Guard directly for another, every step gives the same refusal and the
// same window.
func TestAdmissionCheckUsableOutsidePipeline(t *testing.T) {
	store, rdb, prefix := testRedis(t)
	g, _ := guardWith(store, config.SpendGuardConfig{}, map[string]*tenant.OrgConfig{
		"org-plugin": capped(0.01, 0), "org-direct": capped(0.01, 0)})
	p := New(g, fixedEstimate(4_000))

	type step struct {
		hold   string
		billed int64 // settled at; 0 voids
	}
	admitPlugin := func(hold string) (*models.APIError, *models.RequestContext) {
		rc := managedRC("org-plugin", "key_a", hold, 10)
		res := p.ProcessRequest(bg, rc)
		return res.Error, rc
	}
	admitDirect := func(hold string) *models.APIError {
		return g.Admit(bg, "org-direct", "key_a", 4_000, hold)
	}
	same := func(name string, a, b *models.APIError) {
		t.Helper()
		if (a == nil) != (b == nil) {
			t.Fatalf("%s: plugin %v, direct %v", name, a, b)
		}
		if a != nil {
			wa, wb := writeErr(a), writeErr(b)
			if wa.Code != wb.Code || wa.Body.String() != wb.Body.String() || wa.Header().Get("Retry-After") != wb.Header().Get("Retry-After") {
				t.Fatalf("%s: plugin %d %s, direct %d %s", name, wa.Code, wa.Body, wb.Code, wb.Body)
			}
		}
	}
	windows := func(name string) {
		t.Helper()
		for _, k := range [][2]string{
			{orgMinute(prefix, "org-plugin", t0), orgMinute(prefix, "org-direct", t0)},
			{keyMinute(prefix, "org-plugin", "key_a", t0), keyMinute(prefix, "org-direct", "key_a", t0)},
		} {
			if a, b := bucket(t, rdb, k[0]), bucket(t, rdb, k[1]); a != b {
				t.Fatalf("%s: plugin window %d, direct window %d", name, a, b)
			}
		}
	}

	errP1, rc1 := admitPlugin("r1")
	same("admit 1", errP1, admitDirect("r1"))
	errP2, rc2 := admitPlugin("r2")
	same("admit 2", errP2, admitDirect("r2"))
	errP3, rc3 := admitPlugin("r3")
	same("admit 3 (over the cap)", errP3, admitDirect("r3"))
	if errP3 == nil {
		t.Fatal("the third $0.004 admission fit a $0.01 cap")
	}
	windows("after admissions")

	rc1.Billing.BilledMicros = 1_500
	p.ProcessResponse(bg, rc1)
	g.Settle(bg, "org-direct", "r1", 1_500)
	windows("after a settle")

	p.ProcessResponse(bg, rc2) // nothing billed: void
	g.Void(bg, "org-direct", "r2")
	windows("after a void")
	if got := bucket(t, rdb, orgMinute(prefix, "org-direct", t0)); got != 1_500 {
		t.Fatalf("direct window %d, want 1500 (r1 settled, r2 voided)", got)
	}
	p.ProcessResponse(bg, rc3) // refused at admission: nothing to correct
	windows("after the refused request's post-plugin")
	for _, rc := range []*models.RequestContext{rc1, rc2, rc3} {
		rc.Release()
	}
	windows("after the finalizers")
}

// Held micros: ten parallel managed admissions held at $0.004 against a $0.01
// per-minute cap refuse the third before any settles, and a void gives the
// estimate back to the window.
func TestSpendGuardPluginCountsHeldMicros(t *testing.T) {
	store, rdb, prefix := testRedis(t)
	g, _ := guardWith(store, config.SpendGuardConfig{}, map[string]*tenant.OrgConfig{"org-h": capped(0.01, 0)})
	p := New(g, fixedEstimate(4_000))

	var wg sync.WaitGroup
	results := make([]*models.APIError, 10)
	rcs := make([]*models.RequestContext, 10)
	for i := range 10 {
		rcs[i] = managedRC("org-h", "key_a", "par-"+strconv.Itoa(i), 10)
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			results[i] = p.ProcessRequest(bg, rcs[i]).Error
		}(i)
	}
	wg.Wait()
	var admitted []int
	for i, apiErr := range results {
		switch {
		case apiErr == nil:
			admitted = append(admitted, i)
		case apiErr.Code != "managed_spend_velocity":
			t.Fatalf("request %d: %v", i, apiErr)
		}
	}
	if len(admitted) != 2 {
		t.Fatalf("%d of 10 admitted, want 2: the third $0.004 hold does not fit $0.01", len(admitted))
	}
	if got := bucket(t, rdb, orgMinute(prefix, "org-h", t0)); got != 8_000 {
		t.Fatalf("window %d before any settle, want the two held estimates (8000)", got)
	}
	p.ProcessResponse(bg, rcs[admitted[0]]) // billed nothing: void
	if got := bucket(t, rdb, orgMinute(prefix, "org-h", t0)); got != 4_000 {
		t.Fatalf("window %d after a void, want 4000", got)
	}
	if res := p.ProcessRequest(bg, managedRC("org-h", "key_a", "par-next", 10)); res.Error != nil {
		t.Fatalf("the voided estimate's room: %v", res.Error)
	}
}

// Only managed requests are guarded: a BYOK request is neither refused nor
// counted.
func TestSpendGuardSkipsBYOK(t *testing.T) {
	store, rdb, prefix := testRedis(t)
	rdb.Set(bg, prefix+"pause:org-b:managed_spend", "1", 0)
	g, _ := guardWith(store, config.SpendGuardConfig{}, nil)
	p := New(g, fixedEstimate(4_000))
	rc := managedRC("org-b", "key_a", "byok-1", 10)
	rc.Metadata["billing_mode"] = models.BillingModeBYOK
	if res := p.ProcessRequest(bg, rc); res.Error != nil {
		t.Fatalf("a BYOK request was refused: %v", res.Error)
	}
	if n := rdb.Exists(bg, orgMinute(prefix, "org-b", t0)).Val(); n != 0 {
		t.Fatal("a BYOK request was counted")
	}
}

// The engine runs post-plugins after a later pre-plugin refuses: an admitted
// request the credits reserve (510) then refuses gives its estimate back.
func TestSpendGuardLaterRefusalVoidsEstimate(t *testing.T) {
	store, rdb, prefix := testRedis(t)
	g, _ := guardWith(store, config.SpendGuardConfig{}, nil)
	p := New(g, fixedEstimate(4_000))
	engine := newEngine(p, refuseAt(510))
	rc := managedRC("org-r", "key_a", "refused-later", 10)
	if err := engine.Process(bg, rc, nil); err == nil {
		t.Fatal("the 510 refusal did not stop the request")
	}
	if got := bucket(t, rdb, orgMinute(prefix, "org-r", t0)); got != 0 {
		t.Fatalf("window %d after a later refusal, want 0", got)
	}
	rc.Release()
}

// A request whose post-plugins never ran (a client abort mid-stream, a
// panic) is corrected by its finalizer: settled at what was billed so far,
// or voided.
func TestSpendGuardFinalizerCorrectsAbortedRequests(t *testing.T) {
	store, rdb, prefix := testRedis(t)
	g, _ := guardWith(store, config.SpendGuardConfig{}, nil)
	p := New(g, fixedEstimate(4_000))

	billed := managedRC("org-f", "key_a", "abort-billed", 10)
	voided := managedRC("org-f", "key_a", "abort-void", 10)
	for _, rc := range []*models.RequestContext{billed, voided} {
		if res := p.ProcessRequest(bg, rc); res.Error != nil {
			t.Fatal(res.Error)
		}
	}
	if got := bucket(t, rdb, orgMinute(prefix, "org-f", t0)); got != 8_000 {
		t.Fatalf("window %d, want 8000 held", got)
	}
	billed.Billing.BilledMicros = 2_500 // an aborted stream billed for what it streamed
	billed.RunFinalizers()
	voided.RunFinalizers()
	if got := bucket(t, rdb, orgMinute(prefix, "org-f", t0)); got != 2_500 {
		t.Fatalf("window %d after the finalizers, want the 2500 billed", got)
	}
	// The post-plugin after the finalizer (or a second finalizer run) changes nothing.
	p.ProcessResponse(bg, billed)
	if got := bucket(t, rdb, orgMinute(prefix, "org-f", t0)); got != 2_500 {
		t.Fatalf("window %d after a late post-plugin, want 2500", got)
	}
	billed.Release()
	voided.Release()
}

// A request a cache answered after admission is voided, like the credits
// hold, whatever its cost fields say.
func TestSpendGuardCacheHitVoids(t *testing.T) {
	store, rdb, prefix := testRedis(t)
	g, _ := guardWith(store, config.SpendGuardConfig{}, nil)
	p := New(g, fixedEstimate(4_000))
	rc := managedRC("org-c", "key_a", "cached", 10)
	if res := p.ProcessRequest(bg, rc); res.Error != nil {
		t.Fatal(res.Error)
	}
	rc.Flags.CacheHit = true
	rc.Billing.BilledMicros = 3_000
	p.ProcessResponse(bg, rc)
	if got := bucket(t, rdb, orgMinute(prefix, "org-c", t0)); got != 0 {
		t.Fatalf("window %d after a cache hit, want 0", got)
	}
	rc.Release()
}
