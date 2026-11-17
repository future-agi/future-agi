package main

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"
)

// TestRefusesAToolOutsideVendorDomains: the repro, run as an
// operator would. In a copy of the example Gmail app every tool's x-origin is set
// to https://attacker.example; `agentcc-catalog check` accepts such a
// catalog, because it does not read x-origin. Next to it is a good app
// (Slack). The runtime re-checks every tool's origin against its app's
// vendor.domains when it loads the catalog: it never listens (so no tool,
// and not the good app either, is served), and it logs one record per
// refused tool naming the app, the provider and the tool.
func TestRefusesAToolOutsideVendorDomains(t *testing.T) {
	dir := t.TempDir()
	for _, app := range []string{"gmail", "slack"} {
		copyDir(t, filepath.Join(cn4Catalog, "apps", app), filepath.Join(dir, "apps", app))
	}
	gen := filepath.Join(dir, "apps", "gmail", "providers", "futureagi-rest", "tools.generated.json")
	data, err := os.ReadFile(gen)
	if err != nil {
		t.Fatal(err)
	}
	var generated struct {
		Tools []struct {
			Name string `json:"name"`
		} `json:"tools"`
	}
	if err := json.Unmarshal(data, &generated); err != nil || len(generated.Tools) == 0 {
		t.Fatalf("example Gmail tools.generated.json: %v", err)
	}
	from := []byte(`"x-origin": "` + gmailOrigin + `"`)
	if n := bytes.Count(data, from); n != len(generated.Tools) {
		t.Fatalf("%d of %d Gmail tools name %s", n, len(generated.Tools), gmailOrigin)
	}
	if err := os.WriteFile(gen, bytes.ReplaceAll(data, from, []byte(`"x-origin": "https://attacker.example"`)), 0o644); err != nil {
		t.Fatal(err)
	}

	addrFile := filepath.Join(t.TempDir(), "addr")
	var stderr syncBuffer
	// Bounded: a runtime that wrongly serves stops when ctx ends (exit 0)
	// and fails below, instead of hanging the test.
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	code := run(ctx, []string{"--addr", "127.0.0.1:0", "--addr-file", addrFile, "--catalog-dir", dir}, &stderr)
	if code != exitFail {
		t.Errorf("exit %d, want %d", code, exitFail)
	}
	if _, err := os.Stat(addrFile); !os.IsNotExist(err) {
		t.Errorf("the runtime wrote --addr-file (it listened): %v", err)
	}
	logs := stderr.String()
	if strings.Contains(logs, `"msg":"agentcc-connectors serving"`) {
		t.Errorf("the runtime served the catalog:\n%s", logs)
	}

	var refused []string
	for _, line := range strings.Split(strings.TrimSpace(logs), "\n") {
		var rec map[string]any
		if err := json.Unmarshal([]byte(line), &rec); err != nil {
			t.Fatalf("a log line is not JSON: %q", line)
		}
		if rec["msg"] != "agentcc-connectors: tool refused" {
			continue
		}
		if rec["level"] != "ERROR" || rec["app"] != "gmail" || rec["provider"] != "futureagi-rest" {
			t.Errorf("a refusal record does not name Gmail's provider at level ERROR: %s", line)
		}
		reason, _ := rec["reason"].(string)
		if !strings.Contains(reason, "attacker.example is not under the app's vendor.domains") {
			t.Errorf("a refusal record does not say why: %s", line)
		}
		tool, _ := rec["tool"].(string)
		refused = append(refused, tool)
	}
	var want []string
	for _, tl := range generated.Tools {
		want = append(want, tl.Name)
	}
	sort.Strings(want)
	sort.Strings(refused)
	if !reflect.DeepEqual(refused, want) {
		t.Errorf("tool refusal records name %v, want one per Gmail tool %v; log:\n%s", refused, want, logs)
	}
	if !strings.Contains(logs, `"msg":"agentcc-connectors: the catalog cannot be served"`) {
		t.Errorf("no summary record:\n%s", logs)
	}
}
