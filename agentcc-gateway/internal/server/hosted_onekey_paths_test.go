package server

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// 708 add.40 (b), admin API side: with AGENTCC_HOSTED_ONEKEY_ACTIVATION
// unset, POST /-/reload cannot bring a managed provider onto a hosted node;
// config.Load refuses it before the restart-only check, so the operator sees
// the preparation refusal. The running configuration stays.
func TestHostedPreparationReloadCannotAddManagedProvider(t *testing.T) {
	admin := activatedHostedEnv(t)
	t.Setenv("AGENTCC_HOSTED_ONEKEY_ACTIVATION", "")
	const paid = "providers: {paid: {base_url: 'https://upstream.synthetic.invalid', api_format: openai, managed: true, auto_discover: false}}\n"
	if !strings.Contains(activatedHostedBase, paid) {
		t.Fatal("fixture drifted from activatedHostedBase")
	}
	preparation := strings.Replace(activatedHostedBase, paid, "", 1)
	path := filepath.Join(t.TempDir(), "gateway.yaml")
	if err := os.WriteFile(path, []byte(preparation), 0o600); err != nil {
		t.Fatal(err)
	}
	start, err := config.Load(path)
	if err != nil {
		t.Fatalf("the hosted preparation config was refused: %v", err)
	}
	registry, err := providers.NewRegistry(config.DefaultConfig())
	if err != nil {
		t.Fatal(err)
	}
	defer registry.Close()
	s := &Server{cfg: start, configPath: path, hosted: true, registry: registry, handlers: &Handlers{}}
	for _, tc := range []struct{ name, text, want string }{
		{"managed provider", preparation + paid, "hosted preparation refuses providers.*.managed=true"},
		{"provider kept off credits", preparation + strings.Replace(paid, "managed: true", "managed: false", 1), "hosted infrastructure changes require restart"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if err := os.WriteFile(path, []byte(tc.text), 0o600); err != nil {
				t.Fatal(err)
			}
			r := httptest.NewRequest(http.MethodPost, "/-/reload", nil)
			r.Header.Set("Authorization", "Bearer "+admin)
			w := httptest.NewRecorder()
			s.reloadHandler(w, r)
			if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), `"validation_failed"`) || !strings.Contains(w.Body.String(), tc.want) {
				t.Fatalf("reload answered %d %s; want 400 validation_failed naming %q", w.Code, w.Body.String(), tc.want)
			}
			if s.cfg != start || len(s.cfg.Providers) != 0 {
				t.Fatal("a refused reload changed the running configuration")
			}
		})
	}
}
