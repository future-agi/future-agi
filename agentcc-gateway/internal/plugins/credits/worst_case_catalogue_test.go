package credits

// This offline exporter deliberately lives beside primaryEstimate: the report
// reads the same seeded catalogue, eligibility rules and held card as admission.
// It can also be overlaid unchanged on a CAT-2 checkout; reflection only handles
// fields absent from the stock binary, never reimplements catalogue policy.
import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type holdRow struct {
	Model        string              `json:"model"`
	Scenario     string              `json:"scenario"`
	Input        int                 `json:"input_tokens"`
	Output       int                 `json:"output_tokens"`
	Price        models.PriceCard    `json:"price"`
	Markup       int                 `json:"markup_bps"`
	Inclusive    map[string]any      `json:"inclusive,omitempty"`
	N            int                 `json:"n"`
	Images       int                 `json:"images"`
	GoMicros     int64               `json:"go_micros"`
	Modalities   []string            `json:"modalities,omitempty"`
	InputImages  int                 `json:"input_images"`
	ProviderOnly string              `json:"provider_only,omitempty"`
	Endpoints    []holdEndpointProof `json:"endpoints,omitempty"`
}

// Trusted card fields intentionally have json:"-" in production. Export them
// explicitly in this TEST-ONLY receipt so parity checks do not lose evidence.
type holdEndpointProof struct {
	Ref          models.EndpointRef `json:"ref"`
	ExplicitFree bool               `json:"explicit_free"`
	Inclusive    any                `json:"inclusive,omitempty"`
}

func holdEndpointProofs(refs []models.EndpointRef) []holdEndpointProof {
	var out []holdEndpointProof
	for _, ref := range refs {
		proof := holdEndpointProof{Ref: ref, ExplicitFree: ref.Price.ExplicitFree}
		if q := reflect.ValueOf(ref.Price).FieldByName("Inclusive"); q.IsValid() && !q.IsNil() {
			proof.Inclusive = q.Interface()
		}
		out = append(out, proof)
	}
	return out
}

// Each real top-five set is evaluated separately so the exported pin is an
// actual request, not a synthetic union. Endpoint IDs are NOT unique prices.
type holdEndpointSet struct {
	Only    string
	Verdict eligibility.Verdict
}

func holdEligibleSets(in eligibility.Input, initial eligibility.Verdict) []holdEndpointSet {
	out := []holdEndpointSet{{Verdict: initial}}
	seen := map[string]bool{"": true}
	for _, e := range in.Entry.Endpoints {
		tag := e.Tag
		if tag == "" {
			tag = e.UpstreamProvider
		}
		if seen[tag] {
			continue
		}
		seen[tag] = true
		in.Prefs = &models.ProviderPrefs{Only: []string{tag}}
		if v := eligibility.Check(&in); v.OK {
			out = append(out, holdEndpointSet{Only: tag, Verdict: v})
		}
	}
	return out
}

// Exact per-object conversion from server/hook_policy.go:applyVerdict,
// including trusted explicit-free handling. Never re-fetch a card by ID.
func holdEndpointRefs(v eligibility.Verdict, snapshot string) []models.EndpointRef {
	var refs []models.EndpointRef
	for _, e := range v.EndpointSet(eligibility.MaxEndpointSet) {
		card := eligibility.PriceCard(e, snapshot)
		if v.Price != nil {
			card.SnapshotID = v.Price.SnapshotID
			card.ExplicitFree = v.Price.ExplicitFree && e.Prices == (catalog.Prices{})
			if q := reflect.ValueOf(&card).Elem().FieldByName("Inclusive"); q.IsValid() {
				q.Set(reflect.ValueOf(*v.Price).FieldByName("Inclusive"))
			}
		}
		refs = append(refs, models.EndpointRef{ID: e.ID, UpstreamProvider: e.UpstreamProvider, ProviderName: e.ProviderName, Price: card})
	}
	return refs
}

func TestWorstCaseEligiblePinsIncludeDegraded(t *testing.T) {
	entry := &catalog.Model{ID: "vendor/model", Prices: catalog.Prices{Prompt: 1e-6, Completion: 1e-6}, Endpoints: []catalog.Endpoint{
		{ID: "up", Tag: "up", Source: catalog.SourceOpenRouter, Prices: catalog.Prices{Prompt: 1e-6, Completion: 1e-6}},
		{ID: "down", Tag: "down", Source: catalog.SourceOpenRouter, Status: -1, Prices: catalog.Prices{Prompt: 2e-6, Completion: 2e-6}},
	}}
	in := eligibility.Input{Model: entry.ID, ProviderID: "openrouter", Managed: true, Aggregator: true, Entry: entry,
		Org: &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true}}, Request: &models.ChatCompletionRequest{Model: entry.ID}}
	v := eligibility.Check(&in)
	if !v.OK || len(v.EndpointSet(5)) != 1 {
		t.Fatalf("initial tier: %+v", v)
	}
	for _, set := range holdEligibleSets(in, v) {
		if set.Only == "down" && len(set.Verdict.EndpointSet(5)) == 1 {
			return
		}
	}
	t.Fatal("degraded caller pin was dropped")
}

func TestWorstCaseCatalogueExport(t *testing.T) {
	cfg := config.DefaultConfig()
	variant := "stock"
	if os.Getenv("HOLD_CAT2") == "1" {
		v := reflect.ValueOf(&cfg.Managed).Elem().FieldByName("CatalogueFamilies")
		if !v.IsValid() {
			t.Fatal("this checkout does not implement CAT-2")
		}
		v.Set(reflect.ValueOf([]string{"cat2"}))
		variant = "cat2-on"
	}
	at := "2026-11-03"
	if v := os.Getenv("HOLD_AS_OF"); v != "" {
		at = v
	}
	now, err := time.Parse(time.DateOnly, at)
	if err != nil {
		t.Fatal(err)
	}
	sources := []catalog.Source{{ProviderID: "openrouter", Type: "openrouter"}}
	store := catalog.NewStore()
	opts := catalog.OptionsFromConfig(cfg, sources)
	opts.SnapshotDir = ""
	opts.Now = func() time.Time { return now }
	if err := store.Configure(opts); err != nil {
		t.Fatal(err)
	}
	catalog.Seed(store, sources) // embedded snapshots only; no fetch or refresh
	cat := store.Load()
	calendarBytes, err := os.ReadFile("../../../catalog/deprecations.json")
	if err != nil {
		t.Fatal(err)
	}
	shutdowns, err := modeldb.ParseDeprecations(calendarBytes)
	if err != nil {
		t.Fatal(err)
	}
	p := New(true, nil)
	settings := DefaultSettings()
	settings.MarkupPercent = cfg.Managed.MarkupPercent
	settings.ReserveDefaultOutputTokens = cfg.Managed.ReserveDefaultOutputTokens
	p.SetSettings(settings)
	p.SetPrices(store.PriceCard)
	p.SetMaxOutput(func(provider, id string) int { m, _ := cat.Get(provider, id); return m.MaxOutputTokens })
	org := &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true}}
	var rows []holdRow
	blocked := map[string]string{}
	for _, snap := range cat.Snapshots() {
		ids := make([]string, 0, len(snap.Models))
		for id := range snap.Models {
			ids = append(ids, id)
		}
		sort.Strings(ids)
		for _, id := range ids {
			m := snap.Models[id]
			if !cat.ServesManaged(snap.ProviderID, id) || !m.Servable(now) {
				blocked[id] = "blocked/expired/unpriced/unsupported (ServesManaged + Servable)"
				continue
			}
			card, ok := cat.PriceCard(snap.ProviderID, id, "")
			if !ok {
				t.Fatalf("eligible listing has no admission card: %s", id)
			}
			var q map[string]any
			iv := reflect.ValueOf(card).FieldByName("Inclusive")
			if iv.IsValid() && !iv.IsNil() {
				q = map[string]any{}
				v := iv.Elem()
				for i := 0; i < v.NumField(); i++ {
					q[v.Type().Field(i).Name] = v.Field(i).Interface()
				}
			}
			for _, scenario := range []string{"default-output", "max-output"} {
				out := billing.DefaultMaxOut(m.MaxOutputTokens, cfg.Managed.ReserveDefaultOutputTokens)
				if scenario == "max-output" && m.MaxOutputTokens > 0 {
					out = m.MaxOutputTokens
				}
				input := m.ContextLength
				if input < 7 {
					t.Fatalf("no declared input envelope: %s", id)
				}
				req := &models.ChatCompletionRequest{Model: id}
				if scenario == "max-output" {
					req.MaxTokens = &out
				}
				content, _ := json.Marshal(strings.Repeat("x", (input-7)*4))
				req.Messages = []models.Message{{Role: "user", Content: content}}
				if q != nil {
					if scenario == "default-output" {
						continue
					} // inclusive admission requires an explicit cap
					out = 65536
					if v, ok := q["OutputLimit"].(int64); ok && v > 0 && int(v) < out {
						out = int(v)
					}
					req.MaxTokens = &out
				}
				policy := eligibility.Input{Model: id, ProviderID: snap.ProviderID,
					Managed: true, Aggregator: true, Entry: m, Org: org, Request: req, Now: now, Shutdowns: shutdowns, Host: "openrouter"}
				verdict := eligibility.Check(&policy)
				if !verdict.OK {
					blocked[id] = verdict.Code + ": " + verdict.Reason
					continue
				}
				rc := &models.RequestContext{Provider: snap.ProviderID, Model: id, Request: req, Metadata: map[string]string{"org_id": "synthetic-audit"}}
				rc.Billing.ReservePrice = &card
				if q == nil {
					if err := reqopts.CheckManaged(req, &models.RoutingOptions{}, nil); err != nil {
						t.Fatal(err)
					}
					var best holdRow
					for _, set := range holdEligibleSets(policy, verdict) {
						// server/hook_policy.go:judge attaches the trusted model card
						// AFTER eligibility.Check, including its explicit-free marker.
						set.Verdict.Price = &card
						rc.Billing.ReservePrice = set.Verdict.Price
						rc.Billing.EndpointSet = holdEndpointRefs(set.Verdict, cat.ID())
						micros, admitted := p.primaryEstimate(rc)
						if !admitted {
							t.Fatalf("eligible token model refused: %s pin=%s", id, set.Only)
						}
						if best.Model == "" || micros > best.GoMicros {
							best = holdRow{Model: id, Scenario: scenario, Input: billing.EstimateInputTokens(req), Output: billing.OutputCap(req, out), Price: *rc.Billing.ReservePrice, Markup: settings.markupBps(), N: 1, GoMicros: micros, ProviderOnly: set.Only, Endpoints: holdEndpointProofs(rc.Billing.EndpointSet)}
						}
					}
					rows = append(rows, best)
					continue
				}
				// reqopts/managed_allowlist.go:78-95 is stricter than inclusive.Bound:
				// n must be 1 and modalities is absent from the allowlist. Input
				// image parts remain allowed; inclusive.Bound caps those at 64.
				verdict.Price = &card
				rc.Billing.EndpointSet = holdEndpointRefs(verdict, cat.ID())
				var best holdRow
				maxOutput := out
				if context, ok := q["ContextLimit"].(int64); ok {
					input = int(context)
				}
				n := 1
				req.N = &n
				images := 0
				if generation, _ := q["Generation"].(string); generation == "" {
					parts := make([]map[string]any, 64)
					for i := range parts {
						parts[i] = map[string]any{"type": "image_url", "image_url": map[string]string{"url": "http://127.0.0.1/never-fetched"}}
					}
					req.Messages[0].Content, _ = json.Marshal(parts)
					images = 64
				}
				if err := reqopts.CheckManaged(req, &models.RoutingOptions{}, nil); err != nil {
					t.Fatal(err)
				}
				// Search the greatest admitted output within the model/65536 cap.
				// This retains the inclusive router's independent refusal bounds.
				low, high := 1, maxOutput
				for low <= high {
					out = low + (high-low)/2
					micros, admitted := p.primaryEstimate(rc)
					if admitted {
						if best.Model == "" || micros > best.GoMicros || (micros == best.GoMicros && out > best.Output) {
							best = holdRow{Model: id, Scenario: scenario, Input: input, Output: out, Price: card, Markup: settings.markupBps(), Inclusive: q, N: n, Images: images, GoMicros: micros, InputImages: images, Endpoints: holdEndpointProofs(rc.Billing.EndpointSet)}
						}
						low = out + 1
					} else {
						high = out - 1
					}
				}
				if best.Model == "" {
					blocked[id] = "inclusive envelope refused"
				} else {
					rows = append(rows, best)
				}
			}
		}
	}
	if len(rows) == 0 {
		t.Fatal("empty enabled catalogue")
	}
	revisionCmd := exec.Command("git", "rev-parse", "HEAD")
	revisionCmd.Env = append(os.Environ(), "GIT_NO_LAZY_FETCH=1")
	revision, err := revisionCmd.Output()
	if err != nil {
		t.Fatal(err)
	}
	doc := map[string]any{"variant": variant, "as_of": at, "source_revision": strings.TrimSpace(string(revision)), "snapshot_id": cat.ID(), "rows": rows, "excluded": blocked,
		"reserve_default_output_tokens": cfg.Managed.ReserveDefaultOutputTokens, "markup_percent": cfg.Managed.MarkupPercent,
		"unpriced_reserve_usd": cfg.Managed.UnpricedReserveUSD}
	data, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	path := os.Getenv("HOLD_EXPORT")
	if path == "" {
		path = filepath.Join(t.TempDir(), "catalogue.json")
	}
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	script := filepath.Join("..", "..", "..", "scripts", "worst_case_hold.py")
	if v := os.Getenv("HOLD_SCRIPT"); v != "" {
		script = v
	}
	cmd := exec.Command("python3", "-I", script, "--catalogue", path, "--check")
	cmd.Stdin = bytes.NewReader(data)
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("catalogue Python/Go parity: %v\n%s", err, output)
	}
	t.Log(string(output))
}
