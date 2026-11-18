package main

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// The README's source quickstart is executable user input. Keep its entrypoint,
// explicit config selection, and toolchain requirement aligned with this module.
func TestSourceQuickstart(t *testing.T) {
	root := filepath.Join("..", "..")
	readmeBytes, err := os.ReadFile(filepath.Join(root, "README.md"))
	if err != nil {
		t.Fatal(err)
	}
	readme := string(readmeBytes)
	start := strings.Index(readme, "<details><summary><b>Run from source")
	if start < 0 {
		t.Fatal("README source quickstart is missing")
	}
	end := strings.Index(readme[start:], "</details>")
	if end < 0 {
		t.Fatal("README source quickstart is not closed")
	}
	quickstart := readme[start : start+end]

	t.Run("entrypoint", func(t *testing.T) {
		match := regexp.MustCompile(`(?m)^go run (\S+)(.*)$`).FindStringSubmatch(quickstart)
		if match == nil {
			t.Fatal("source quickstart has no go run command")
		}
		if _, err := os.Stat(filepath.Join(root, match[1], "main.go")); err != nil {
			t.Fatalf("documented entrypoint %q has no main.go: %v", match[1], err)
		}
	})
	t.Run("copied-config", func(t *testing.T) {
		copyCommand := regexp.MustCompile(`(?m)^cp config\.example\.yaml (\S+)$`).FindStringSubmatch(quickstart)
		if copyCommand == nil {
			t.Fatal("source quickstart must copy the example config")
		}
		command := regexp.MustCompile(`(?m)^go run \S+ --config (\S+)$`).FindStringSubmatch(quickstart)
		if command == nil || command[1] != copyCommand[1] {
			t.Fatal("go run must explicitly load the copied file with --config")
		}
		if !strings.Contains(quickstart, "AGENTCC_CONFIG_FILE") {
			t.Fatal("source quickstart must name the alternate config environment variable")
		}
	})
	t.Run("go-version", func(t *testing.T) {
		module, err := os.ReadFile(filepath.Join(root, "go.mod"))
		if err != nil {
			t.Fatal(err)
		}
		version := regexp.MustCompile(`(?m)^go (\d+\.\d+)(?:\.\d+)?$`).FindSubmatch(module)
		if version == nil {
			t.Fatal("go.mod has no Go version")
		}
		if !strings.Contains(quickstart, "Go "+string(version[1])+"+") ||
			!strings.Contains(readme, `alt="Go `+string(version[1])+`"`) {
			t.Fatalf("source quickstart and badge must match go.mod Go %s", version[1])
		}
	})
	t.Run("local-health-check", func(t *testing.T) {
		for _, text := range []string{"0.0.0.0:8080", "all network interfaces", "server.host", "127.0.0.1", "curl --fail http://localhost:8080/healthz"} {
			if !strings.Contains(quickstart, text) {
				t.Errorf("source quickstart is missing %q", text)
			}
		}
	})
	t.Run("contribution-links", func(t *testing.T) {
		for _, target := range []string{"../README.md", "../CONTRIBUTING.md", "./cmd/agentcc/main.go"} {
			if !strings.Contains(readme, "]("+target+")") {
				t.Errorf("README is missing link to %s", target)
			}
			if _, err := os.Stat(filepath.Join(root, target)); err != nil {
				t.Errorf("README link %s does not resolve: %v", target, err)
			}
		}
	})
}
