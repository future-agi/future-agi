package main

import (
	"bytes"
	"log/slog"
	"os"
	"path/filepath"
	"runtime/debug"
	"testing"
)

func TestContainerMemoryLimit(t *testing.T) {
	write := func(t *testing.T, root, rel, content string) {
		t.Helper()
		p := filepath.Join(root, rel)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	cases := []struct {
		name  string
		files map[string]string
		want  int64
	}{
		{"cgroup v2 with a limit", map[string]string{"memory.max": "1073741824\n"}, 1 << 30},
		{"cgroup v2 without one", map[string]string{"memory.max": "max\n"}, 0},
		{"cgroup v1 with a limit", map[string]string{"memory/memory.limit_in_bytes": "536870912\n"}, 512 << 20},
		{"cgroup v1 without one", map[string]string{"memory/memory.limit_in_bytes": "9223372036854771712\n"}, 0},
		{"no cgroup", nil, 0},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			root := t.TempDir()
			for rel, content := range tc.files {
				write(t, root, rel, content)
			}
			if got, _ := containerMemoryLimit(root); got != tc.want {
				t.Errorf("limit %d, want %d", got, tc.want)
			}
		})
	}
}

// Without GOMEMLIMIT, the Go memory limit becomes 90% of the container's;
// with it, it is left alone.
func TestApplyContainerMemoryLimit(t *testing.T) {
	prev := debug.SetMemoryLimit(-1)
	t.Cleanup(func() { debug.SetMemoryLimit(prev) })
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "memory.max"), []byte("1073741824"), 0o644); err != nil {
		t.Fatal(err)
	}
	var logs bytes.Buffer
	log := slog.New(slog.NewTextHandler(&logs, nil))

	debug.SetMemoryLimit(1 << 62)
	applyContainerMemoryLimit(log, "", root)
	if got, want := debug.SetMemoryLimit(-1), int64(1<<30)/10*9; got != want {
		t.Errorf("Go memory limit %d, want 90%% of the container's, %d", got, want)
	}
	debug.SetMemoryLimit(1 << 62)
	applyContainerMemoryLimit(log, "700MiB", root)
	if got := debug.SetMemoryLimit(-1); got != 1<<62 {
		t.Errorf("Go memory limit changed to %d although GOMEMLIMIT is set", got)
	}
}
