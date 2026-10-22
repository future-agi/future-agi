package budget

// Budget test helpers: the budgets section exactly as the console's Set
// Budget save pushes it, decoded the way the gateway decodes a pushed org
// config, and a plugin with a test clock.

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sync"
	"testing"
	"time"

	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const btOrg = "org-budget-test"

// btNow is a Tuesday, ten seconds into a minute.
var btNow = time.Date(2026, time.October, 21, 10, 0, 10, 0, time.UTC)

type btClock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *btClock) now() time.Time { c.mu.Lock(); defer c.mu.Unlock(); return c.t }
func (c *btClock) add(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

// btFixture returns testdata/console_push.json's cases (keys starting with
// "_" are notes).
func btFixture(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	body, err := os.ReadFile(filepath.Join("testdata", "console_push.json"))
	if err != nil {
		t.Fatal(err)
	}
	var all map[string]json.RawMessage
	if err := json.Unmarshal(body, &all); err != nil {
		t.Fatal(err)
	}
	for k := range all {
		if k != "" && k[0] == '_' {
			delete(all, k)
		}
	}
	return all
}

var (
	btDjangoOnce sync.Once
	btDjangoOut  map[string]json.RawMessage
	btDjangoErr  string
)

// btDjangoPush runs Django's own set-budget and push functions (extracted from
// the repository's source by testdata/django_budget_push.py) on the Set
// Budget dialog's bodies and returns {case: pushed budgets}.
func btDjangoPush(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	btDjangoOnce.Do(func() {
		py, err := exec.LookPath("python3")
		if err != nil {
			btDjangoErr = "python3 not found: " + err.Error()
			return
		}
		root, _ := filepath.Abs(filepath.Join("..", "..", "..", ".."))
		if _, err := os.Stat(filepath.Join(root, "futureagi", "agentcc", "services", "config_push.py")); err != nil {
			btDjangoErr = "no futureagi/ beside agentcc-gateway/: " + err.Error()
			return
		}
		var stdout, stderr bytes.Buffer
		cmd := exec.Command(py, "-I", filepath.Join("testdata", "django_budget_push.py"), root)
		cmd.Stdout, cmd.Stderr = &stdout, &stderr
		if err := cmd.Run(); err != nil {
			btDjangoErr = "django transform failed: " + err.Error() + ": " + stderr.String()
			return
		}
		if err := json.Unmarshal(stdout.Bytes(), &btDjangoOut); err != nil {
			btDjangoErr = "django transform output: " + err.Error()
		}
	})
	if btDjangoErr != "" {
		t.Skip(btDjangoErr)
	}
	return btDjangoOut
}

// btCase returns the fixture case after checking Django's own transform
// pushes exactly that for the case's dialog saves.
func btCase(t *testing.T, name string) json.RawMessage {
	t.Helper()
	want, ok := btFixture(t)[name]
	if !ok {
		t.Fatalf("fixture has no case %q", name)
	}
	t.Run("django pushes the fixture", func(t *testing.T) {
		got, ok := btDjangoPush(t)[name]
		if !ok {
			t.Fatalf("django run has no case %q", name)
		}
		var g, w any
		_ = json.Unmarshal(got, &g)
		_ = json.Unmarshal(want, &w)
		if !reflect.DeepEqual(g, w) {
			t.Fatalf("Django's push for %s:\n  got  %s\n  want %s", name, got, want)
		}
	})
	return want
}

// btDecode reads a pushed budgets section the way PUT /-/orgs/{org}/config
// does: the generated admin contract with unknown fields refused, then the
// contract -> tenant JSON conversion.
func btDecode(t *testing.T, budgets json.RawMessage) *tenant.BudgetsConfig {
	t.Helper()
	dec := json.NewDecoder(bytes.NewReader([]byte(`{"budgets":` + string(budgets) + `}`)))
	dec.DisallowUnknownFields()
	var contract gatewayadmin.OrgConfig
	if err := dec.Decode(&contract); err != nil {
		t.Fatalf("admin contract refuses the pushed budgets %s: %v", budgets, err)
	}
	body, err := json.Marshal(&contract)
	if err != nil {
		t.Fatal(err)
	}
	var cfg tenant.OrgConfig
	if err := json.Unmarshal(body, &cfg); err != nil {
		t.Fatal(err)
	}
	if cfg.Budgets == nil {
		t.Fatalf("no budgets after decode of %s", budgets)
	}
	return cfg.Budgets
}

// btPlugin is the budget plugin over one org's budgets with a test clock.
func btPlugin(t *testing.T, b *tenant.BudgetsConfig) (*Plugin, *btClock) {
	t.Helper()
	clock := &btClock{t: btNow}
	p := New(nil, testPricing(), false, testTenantStore(btOrg, b))
	p.now = clock.now
	return p, clock
}

func btRC(meta map[string]string) *models.RequestContext {
	rc := makeRC("gpt-4o", "openai", map[string]string{tenant.MetadataKeyOrgID: btOrg})
	for k, v := range meta {
		rc.Metadata[k] = v
	}
	return rc
}

// btSpend records cost dollars against every level the request names.
func btSpend(t *testing.T, p *Plugin, cost string, meta map[string]string) {
	t.Helper()
	rc := btRC(meta)
	rc.Metadata["cost"] = cost
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)
}

// btAsk runs one request's budget check and returns its result and request.
func btAsk(p *Plugin, meta map[string]string) (pipeline.PluginResult, *models.RequestContext) {
	rc := btRC(meta)
	return p.ProcessRequest(context.Background(), rc), rc
}

func btCode(res pipeline.PluginResult) string {
	if res.Error == nil {
		return "allowed"
	}
	return res.Error.Code
}
