package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

func serverWithStdioFixture(t *testing.T, cfg *config.Config) (*Server, int) {
	t.Helper()
	pidFile := filepath.Join(t.TempDir(), "fixture.pid")
	cfg.MCP.Enabled = true
	cfg.MCP.Servers = map[string]config.MCPServerConfig{"fixture": {
		Transport: "stdio", Command: os.Args[0], Args: []string{"-test.run=^TestShutdownStdioFixture$", "--", pidFile},
	}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	data, err := os.ReadFile(pidFile)
	if err != nil {
		t.Fatalf("fixture did not finish discovery: %v", err)
	}
	pid, err := strconv.Atoi(string(data))
	if err != nil {
		t.Fatal(err)
	}
	return srv, pid
}

func TestFailedListenerReapsConnectedStdioChild(t *testing.T) {
	occupied, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer occupied.Close()
	cfg := config.DefaultConfig()
	cfg.Server.Host = "127.0.0.1"
	cfg.Server.Port = occupied.Addr().(*net.TCPAddr).Port
	srv, pid := serverWithStdioFixture(t, cfg)
	t.Cleanup(func() { _ = srv.Shutdown(context.Background()) })
	if err := srv.Start(); err == nil {
		t.Fatal("Start unexpectedly acquired the occupied listener")
	}
	if _, err := os.Stat(fmt.Sprintf("/proc/%d", pid)); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("Start returned a listener error before reaping its connected stdio child")
	}
}

func TestShutdownReapsConnectedStdioChild(t *testing.T) {
	srv, pid := serverWithStdioFixture(t, config.DefaultConfig())
	child, err := os.FindProcess(pid)
	if err != nil {
		t.Fatal(err)
	}
	defer child.Release()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		_ = child.Kill()
		t.Fatal(err)
	}
	// An exited but uncollected child still has a proc entry.
	if _, err := os.Stat(fmt.Sprintf("/proc/%d", pid)); !errors.Is(err, os.ErrNotExist) {
		_ = child.Kill()
		_, _ = child.Wait()
		t.Fatal("gateway shutdown returned without reaping the connected stdio child")
	}
}

func TestShutdownStdioFixture(t *testing.T) {
	if len(os.Args) < 2 || os.Args[len(os.Args)-2] != "--" {
		return
	}
	in := json.NewDecoder(os.Stdin)
	out := json.NewEncoder(os.Stdout)
	for {
		var req mcp.Message
		if in.Decode(&req) != nil {
			// Closing stdin alone doesn't terminate this fixture.
			time.Sleep(10 * time.Second)
			os.Exit(0)
		}
		if req.IsNotification() {
			continue
		}
		if req.Method == mcp.MethodPromptsList {
			if os.WriteFile(os.Args[len(os.Args)-1], []byte(strconv.Itoa(os.Getpid())), 0600) != nil {
				os.Exit(2)
			}
		}
		response, _ := mcp.NewResponse(req.ID, struct{}{})
		if out.Encode(response) != nil {
			os.Exit(2)
		}
	}
}
