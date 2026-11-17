package budget

// R7-BUDGET test helpers: the budgets section exactly as the console's Set
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

const r7Org = "org-r7-budget"

// r7Now is a Tuesday, ten seconds into a minute.
var r7Now = time.Date(2026, time.October, 21, 10, 0, 10, 0, time.UTC)

type r7Clock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *r7Clock) now() time.Time { c.mu.Lock(); defer c.mu.Unlock(); return c.t }
func (c *r7Clock) add(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

// r7Fixture returns testdata/r7_console_push.json's cases (keys starting with
// "_" are notes).
func r7Fixture(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	body, err := os.ReadFile(filepath.Join("testdata", "r7_console_push.json"))
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
	r7DjangoOnce sync.Once
	r7DjangoOut  map[string]json.RawMessage
	r7DjangoErr  string
)

// r7DjangoPush runs Django's own set-budget and push functions (extracted from
// the repository's source by testdata/r7_django_budget_push.py) on the Set
// Budget dialog's bodies and returns {case: pushed budgets}.
func r7DjangoPush(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	r7DjangoOnce.Do(func() {
		py, err := exec.LookPath("python3")
		if err != nil {
			r7DjangoErr = "python3 not found: " + err.Error()
			return
		}
		root, _ := filepath.Abs(filepath.Join("..", "..", "..", ".."))
		if _, err := os.Stat(filepath.Join(root, "futureagi", "agentcc", "services", "config_push.py")); err != nil {
			r7DjangoErr = "no futureagi/ beside agentcc-gateway/: " + err.Error()
			return
		}
		var stdout, stderr bytes.Buffer
		cmd := exec.Command(py, "-I", filepath.Join("testdata", "r7_django_budget_push.py"), root)
		cmd.Stdout, cmd.Stderr = &stdout, &stderr
		if err := cmd.Run(); err != nil {
			r7DjangoErr = "django transform failed: " + err.Error() + ": " + stderr.String()
			return
		}
		if err := json.Unmarshal(stdout.Bytes(), &r7DjangoOut); err != nil {
			r7DjangoErr = "django transform output: " + err.Error()
		}
	})
	if r7DjangoErr != "" {
		t.Skip(r7DjangoErr)
	}
	return r7DjangoOut
}

// r7Case returns the fixture case after checking Django's own transform
// pushes exactly that for the case's dialog saves.
func r7Case(t *testing.T, name string) json.RawMessage {
	t.Helper()
	want, ok := r7Fixture(t)[name]
	if !ok {
		t.Fatalf("fixture has no case %q", name)
	}
	t.Run("django pushes the fixture", func(t *testing.T) {
		got, ok := r7DjangoPush(t)[name]
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

// r7Decode reads a pushed budgets section the way PUT /-/orgs/{org}/config
// does: the generated admin contract with unknown fields refused, then the
// contract -> tenant JSON conversion.
func r7Decode(t *testing.T, budgets json.RawMessage) *tenant.BudgetsConfig {
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

// r7Plugin is the budget plugin over one org's budgets with a test clock.
func r7Plugin(t *testing.T, b *tenant.BudgetsConfig) (*Plugin, *r7Clock) {
	t.Helper()
	clock := &r7Clock{t: r7Now}
	p := New(nil, testPricing(), false, testTenantStore(r7Org, b))
	p.now = clock.now
	return p, clock
}

func r7RC(meta map[string]string) *models.RequestContext {
	rc := makeRC("gpt-4o", "openai", map[string]string{tenant.MetadataKeyOrgID: r7Org})
	for k, v := range meta {
		rc.Metadata[k] = v
	}
	return rc
}

// r7Spend records cost dollars against every level the request names.
func r7Spend(t *testing.T, p *Plugin, cost string, meta map[string]string) {
	t.Helper()
	rc := r7RC(meta)
	rc.Metadata["cost"] = cost
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)
}

// r7Ask runs one request's budget check and returns its result and request.
func r7Ask(p *Plugin, meta map[string]string) (pipeline.PluginResult, *models.RequestContext) {
	rc := r7RC(meta)
	return p.ProcessRequest(context.Background(), rc), rc
}

func r7Code(res pipeline.PluginResult) string {
	if res.Error == nil {
		return "allowed"
	}
	return res.Error.Code
}
