package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

// childEnvNames returns the environment variable names the client's child sees.
func childEnvNames(t *testing.T, c *Client) map[string]bool {
	t.Helper()
	text, err := callText(t, c, "env_names", nil)
	if err != nil {
		t.Fatalf("env_names: %v", err)
	}
	var names []string
	if err := json.Unmarshal([]byte(text), &names); err != nil {
		t.Fatalf("env_names output %q: %v", text, err)
	}
	set := make(map[string]bool, len(names))
	for _, n := range names {
		set[n] = true
	}
	return set
}

// TestStdioMinimalEnv: the child sees only the default base_env names that the
// host has set, never the gateway's provider keys or admin token, and a base
// name missing on the host is not set at all.
func TestStdioMinimalEnv(t *testing.T) {
	t.Setenv("OPENAI_API_KEY", "fake-provider-key-for-test")
	t.Setenv("AGENTCC_ADMIN_TOKEN", "fake-admin-token-for-test")
	t.Setenv("DATABASE_URL", "postgres://fake/for-test")
	t.Setenv("LANG", "")
	os.Unsetenv("LANG") // t.Setenv restores the host's value afterwards

	c := connectStdioHelper(t, ClientConfig{ServerID: "minimal-env"})
	names := childEnvNames(t, c)

	allowed := map[string]bool{"PATH": true, "HOME": true, "LANG": true, "TMPDIR": true}
	for name := range names {
		if !allowed[name] {
			t.Errorf("child sees %s", name)
		}
	}
	if names["LANG"] {
		t.Error("LANG is unset on the host but set in the child")
	}
	if os.Getenv("PATH") != "" && !names["PATH"] {
		t.Error("child is missing PATH, a default base_env name")
	}
}

// childEnv returns the values the client's child sees for names.
func childEnv(t *testing.T, c *Client, names ...string) map[string]string {
	t.Helper()
	text, err := callText(t, c, "env", map[string]interface{}{"names": names})
	if err != nil {
		t.Fatalf("env: %v", err)
	}
	values := map[string]string{}
	if err := json.Unmarshal([]byte(text), &values); err != nil {
		t.Fatalf("env output %q: %v", text, err)
	}
	return values
}

// TestStdioEnvFromHostAndValues: the child's environment is BaseEnv, then the
// EnvFromHost names the host has set, then the Env values (which win), and
// nothing else; an empty BaseEnv copies no base name.
func TestStdioEnvFromHostAndValues(t *testing.T) {
	t.Setenv("STDIO_TEST_FROM_HOST", "host-value")
	t.Setenv("STDIO_TEST_OVERRIDDEN", "host-value")
	t.Setenv("STDIO_TEST_NOT_DECLARED", "host-value")
	t.Setenv("STDIO_TEST_ABSENT", "")
	os.Unsetenv("STDIO_TEST_ABSENT")

	c := connectStdioHelper(t, ClientConfig{
		ServerID:    "env-values",
		BaseEnv:     []string{"PATH"},
		EnvFromHost: []string{"STDIO_TEST_FROM_HOST", "STDIO_TEST_OVERRIDDEN", "STDIO_TEST_ABSENT"},
		Env:         map[string]string{"STDIO_TEST_VALUE": "declared", "STDIO_TEST_OVERRIDDEN": "declared"},
	})
	want := map[string]bool{"PATH": true, "STDIO_TEST_FROM_HOST": true, "STDIO_TEST_OVERRIDDEN": true, "STDIO_TEST_VALUE": true}
	names := childEnvNames(t, c)
	for name := range names {
		if !want[name] {
			t.Errorf("child sees undeclared %s", name)
		}
	}
	for name := range want {
		if !names[name] {
			t.Errorf("child is missing %s", name)
		}
	}
	got := childEnv(t, c, "STDIO_TEST_FROM_HOST", "STDIO_TEST_OVERRIDDEN", "STDIO_TEST_VALUE", "PATH")
	if got["STDIO_TEST_FROM_HOST"] != "host-value" || got["STDIO_TEST_OVERRIDDEN"] != "declared" || got["STDIO_TEST_VALUE"] != "declared" {
		t.Errorf("child values = %v", got)
	}
	if got["PATH"] != os.Getenv("PATH") {
		t.Errorf("child PATH = %q, want the host's", got["PATH"])
	}

	bare := connectStdioHelper(t, ClientConfig{ServerID: "env-none", BaseEnv: []string{}})
	if names := childEnvNames(t, bare); len(names) != 0 {
		t.Errorf("empty BaseEnv: child sees %v", names)
	}
}

// TestSandboxLauncherUnavailable: the sandbox launcher refuses every launch
// with stdio_sandbox_unavailable and starts no process; StdioLauncherFor maps
// the config values.
func TestSandboxLauncherUnavailable(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "marker")
	command, args := stdioHelperCommand("--marker", marker)

	l, err := StdioLauncherFor("sandbox")
	if err != nil {
		t.Fatalf("StdioLauncherFor(sandbox): %v", err)
	}
	if _, err := l.Launch(StdioSpec{ServerID: "s", Command: command, Args: args}); !errors.Is(err, ErrStdioSandboxUnavailable) {
		t.Fatalf("sandbox Launch error = %v", err)
	}

	c := NewClient(ClientConfig{ServerID: "sandboxed", TransportType: "stdio", Command: command, Args: args, Launcher: l})
	defer c.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	err = c.Connect(ctx)
	if !errors.Is(err, ErrStdioSandboxUnavailable) || !strings.Contains(err.Error(), "stdio_sandbox_unavailable") {
		t.Fatalf("Connect error = %v, want stdio_sandbox_unavailable", err)
	}
	if c.Healthy() {
		t.Fatal("a client whose launch was refused is healthy")
	}
	time.Sleep(200 * time.Millisecond)
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		t.Fatalf("a process started under the sandbox launcher (marker: %v)", readMarker(marker))
	}

	for name, want := range map[string]string{"": "local", "local": "local", "sandbox": "sandbox"} {
		got, err := StdioLauncherFor(name)
		if err != nil || got.Name() != want {
			t.Errorf("StdioLauncherFor(%q) = %v, %v; want %s", name, got, err, want)
		}
	}
	if _, err := StdioLauncherFor("docker"); err == nil {
		t.Error("StdioLauncherFor(docker) accepted an unknown launcher")
	}
}

// TestLocalLauncherGroupAndWorkDir: the local launcher puts the child in its
// own process group, in a fresh directory under the temp dir that Close
// removes.
func TestLocalLauncherGroupAndWorkDir(t *testing.T) {
	c := connectStdioHelper(t, ClientConfig{ServerID: "work/dir"})
	pid := stdioPid(t, c)
	pgid, err := syscall.Getpgid(pid)
	if err != nil {
		t.Fatalf("getpgid: %v", err)
	}
	if pgid != pid || pgid == syscall.Getpgrp() {
		t.Fatalf("child pgid %d (pid %d, ours %d): want its own group", pgid, pid, syscall.Getpgrp())
	}

	dir, err := callText(t, c, "cwd", nil)
	if err != nil {
		t.Fatalf("cwd: %v", err)
	}
	tmp, _ := filepath.EvalSymlinks(os.TempDir())
	realDir, _ := filepath.EvalSymlinks(dir)
	if !strings.HasPrefix(realDir, tmp+string(filepath.Separator)) || !strings.HasPrefix(filepath.Base(realDir), "agentcc-mcp-work_dir-") {
		t.Fatalf("child cwd %q, want a fresh agentcc-mcp-work_dir-* dir under %q", realDir, tmp)
	}
	c.Close()
	if _, err := os.Stat(realDir); !os.IsNotExist(err) {
		t.Fatalf("work dir %q left after Close (err %v)", realDir, err)
	}
}

// TestLocalLauncherRelativeCommand: a relative command resolves against the
// gateway's directory, not the child's new working directory.
func TestLocalLauncherRelativeCommand(t *testing.T) {
	wd, _ := os.Getwd()
	rel, err := filepath.Rel(wd, os.Args[0])
	if err != nil {
		t.Skipf("test binary %s has no path relative to %s", os.Args[0], wd)
	}
	if !strings.Contains(rel, string(filepath.Separator)) {
		rel = "." + string(filepath.Separator) + rel
	}
	_, args := stdioHelperCommand()
	c := NewClient(ClientConfig{ServerID: "relative", TransportType: "stdio", Command: rel, Args: args})
	defer c.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := c.Connect(ctx); err != nil {
		t.Fatalf("connect with relative command %q: %v", rel, err)
	}
}

// TestBuildStdioEnv: base names, then host names, then values, with a later
// step winning on a repeated name; unset host names and names that cannot be
// variable names are dropped.
func TestBuildStdioEnv(t *testing.T) {
	t.Setenv("STDIO_TEST_A", "host-a")
	t.Setenv("STDIO_TEST_B", "host-b")
	t.Setenv("STDIO_TEST_UNSET", "")
	os.Unsetenv("STDIO_TEST_UNSET")
	got := buildStdioEnv(
		[]string{"STDIO_TEST_A", "STDIO_TEST_UNSET"},
		[]string{"STDIO_TEST_B", "STDIO_TEST_A", "BAD=NAME"},
		map[string]string{"STDIO_TEST_B": "value-b", "STDIO_TEST_C": "value-c", "PATH=/evil": "x", "": "empty"},
	)
	want := []string{"STDIO_TEST_A=host-a", "STDIO_TEST_B=value-b", "STDIO_TEST_C=value-c"}
	if strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Fatalf("buildStdioEnv = %q, want %q", got, want)
	}
	if env := buildStdioEnv(nil, nil, nil); env == nil || len(env) != 0 {
		t.Fatalf("an empty declaration gives %#v, want an empty, non-nil environment", env)
	}
}
