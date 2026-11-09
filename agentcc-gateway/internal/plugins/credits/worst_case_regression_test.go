package credits

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func holdExportRows(t *testing.T) []holdRow {
	t.Helper()
	path := filepath.Join(t.TempDir(), "catalogue.json")
	t.Setenv("HOLD_EXPORT", path)
	TestWorstCaseCatalogueExport(t)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Rows []holdRow `json:"rows"`
	}
	if err := json.Unmarshal(data, &doc); err != nil {
		t.Fatal(err)
	}
	return doc.Rows
}

// Check the ACTUAL emitted shape, not just the inclusive estimator's wider
// bounds. The separate server parity gate also replays the selected endpoint
// objects through applyVerdict and the credits admission plugin.
func TestWorstCaseReportedRequestsAdmitted(t *testing.T) {
	for _, row := range holdExportRows(t) {
		req := &models.ChatCompletionRequest{Model: row.Model, N: &row.N, Modalities: row.Modalities, MaxTokens: &row.Output}
		if err := reqopts.CheckManaged(req, &models.RoutingOptions{}, nil); err != nil {
			t.Errorf("reported maximizer is not admitted: %s n=%d modalities=%v: %v", row.Model, row.N, row.Modalities, err)
		}
	}
}

func TestWorstCaseDuplicateEndpointParity(t *testing.T) {
	const id = "qwen/qwen3-235b-a22b-2507"
	var reported int64
	for _, r := range holdExportRows(t) {
		if r.Model == id && r.Scenario == "max-output" {
			reported = r.GoMicros
		}
	}
	cfg := config.DefaultConfig()
	sources := []catalog.Source{{ProviderID: "openrouter", Type: "openrouter"}}
	store := catalog.NewStore()
	opts := catalog.OptionsFromConfig(cfg, sources)
	opts.SnapshotDir = ""
	now, _ := time.Parse(time.DateOnly, "2026-11-03")
	opts.Now = func() time.Time { return now }
	if err := store.Configure(opts); err != nil {
		t.Fatal(err)
	}
	catalog.Seed(store, sources)
	cat := store.Load()
	m, ok := cat.Get("openrouter", id)
	if !ok {
		t.Fatal("model absent")
	}
	out := m.MaxOutputTokens
	content, _ := json.Marshal(strings.Repeat("x", (m.ContextLength-7)*4))
	req := &models.ChatCompletionRequest{Model: id, MaxTokens: &out, Messages: []models.Message{{Role: "user", Content: content}}}
	policy := eligibility.Input{Model: id, ProviderID: "openrouter", Managed: true, Aggregator: true, Entry: m,
		Org: &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true}}, Request: req, Now: now,
		Prefs: &models.ProviderPrefs{Only: []string{"google-vertex/us-south1"}}}
	verdict := eligibility.Check(&policy)
	if !verdict.OK {
		t.Fatalf("pin ineligible: %+v", verdict)
	}
	var refs []models.EndpointRef
	for _, e := range verdict.EndpointSet(eligibility.MaxEndpointSet) {
		card := eligibility.PriceCard(e, cat.ID())
		if verdict.Price != nil {
			card.SnapshotID = verdict.Price.SnapshotID
			card.ExplicitFree = verdict.Price.ExplicitFree && e.Prices == (catalog.Prices{})
		}
		refs = append(refs, models.EndpointRef{ID: e.ID, Price: card})
	}
	_, actual, ok := billing.HighestCard(refs, req, 500, billing.DefaultMaxOut(m.MaxOutputTokens, 4096))
	if !ok {
		t.Fatal("no actual admitted card")
	}
	if actual != 86016 || actual != reported {
		t.Fatalf("duplicate-ID endpoint object priced incorrectly: actual=%d report=%d want=86016", actual, reported)
	}
}
