package server

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCAT2OperatorReloadRequiresRestart(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		g, _, _ := cat2OperatorGateway(t, enabled)
		before := g.srv.handlers.catalog.Load().ID()
		g.srv.cfg.Admin.Token = strings.Repeat("synthetic-admin-", 4)
		g.srv.configPath = filepath.Join(t.TempDir(), "gateway.yaml")
		families := "[cat2]"
		if enabled {
			families = "[]"
		}
		if err := os.WriteFile(g.srv.configPath, []byte("managed:\n  catalogue_families: "+families+"\n"), 0600); err != nil {
			t.Fatal(err)
		}
		r := httptest.NewRequest(http.MethodPost, "/-/reload", nil)
		r.Header.Set("Authorization", "Bearer "+g.srv.cfg.Admin.Token)
		w := httptest.NewRecorder()
		g.srv.reloadHandler(w, r)
		if w.Code != 400 || !strings.Contains(w.Body.String(), "restart_required") {
			t.Fatalf("reload status=%d body=%s", w.Code, w.Body)
		}
		if g.srv.cfg.Managed.CAT2Enabled() != enabled || g.srv.handlers.catalog.Load().ID() != before {
			t.Fatal("refused reload changed live config or catalogue")
		}
	}
}
