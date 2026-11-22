package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func collectorReadme(t *testing.T) string {
	t.Helper()
	data, err := os.ReadFile("../../README.md")
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func TestReadmePricingRefreshWorkingDirectory(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("README commands target a POSIX shell")
	}
	readme := collectorReadme(t)
	_, section, ok := strings.Cut(readme, "Refresh the embedded snapshot")
	if !ok {
		t.Fatal("missing pricing refresh instructions")
	}
	_, section, ok = strings.Cut(section, "```bash\n")
	if !ok {
		t.Fatal("missing pricing refresh command block")
	}
	commands, _, ok := strings.Cut(section, "```")
	if !ok {
		t.Fatal("unterminated pricing refresh command block")
	}

	root := t.TempDir()
	for _, dir := range []string{"tools", "fi-collector/pkg/pricing", "fi-collector/cmd/fi-collector"} {
		if err := os.MkdirAll(filepath.Join(root, dir), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	// Offline command doubles validate the documented paths and shell cwd.
	// A clean real Go build is a separate acceptance check, not simulated here.
	files := map[string]string{
		"fi-collector/go.mod": "module example.test/collector\n",
		"tools/curl":          "#!/bin/sh\nset -eu\nwhile [ \"$#\" -gt 0 ]; do\n  if [ \"$1\" = -o ]; then shift; printf '{}' > \"$1\"; exit 0; fi\n  shift\ndone\nexit 1\n",
		"tools/go":            "#!/bin/sh\nset -eu\ntest -f go.mod || { printf 'go.mod missing from documented build directory\\n' >&2; exit 1; }\ntest -d cmd/fi-collector\ntest \"$*\" = 'build -o bin/fi-collector ./cmd/fi-collector'\n",
	}
	for name, content := range files {
		if err := os.WriteFile(filepath.Join(root, name), []byte(content), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	cmd := exec.Command("sh", "-eu", "-c", commands)
	cmd.Dir = root
	cmd.Env = append(os.Environ(), "PATH="+filepath.Join(root, "tools")+string(os.PathListSeparator)+os.Getenv("PATH"))
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("pricing refresh commands fail from repository root: %v\n%s", err, output)
	}
	if _, err := os.Stat(filepath.Join(root, "fi-collector/pkg/pricing/model_prices.json")); err != nil {
		t.Fatalf("pricing snapshot not written to documented path: %v", err)
	}
}

func TestReadmePricingOverrideWarning(t *testing.T) {
	readme := collectorReadme(t)
	if !strings.Contains(readme, "can't be read or parsed, the collector logs a warning and falls back") {
		t.Fatal("README must describe the warning-level override fallback, not an error")
	}
}
