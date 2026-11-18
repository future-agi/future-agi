package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"
)

func catalogFixtureClient(t *testing.T, mode, marker string) *Client {
	t.Helper()
	c := NewClient(ClientConfig{ServerID: "local", TransportType: "stdio", Command: os.Args[0],
		Args: []string{"-test.run=^TestStdioCatalogFixture$", "--", mode, marker}, PingInterval: time.Hour})
	t.Cleanup(func() { _ = c.Close() })
	return c
}

func TestKnownStdioCloseReconnectsBeforeNextPing(t *testing.T) {
	c := catalogFixtureClient(t, "serve", filepath.Join(t.TempDir(), "unused"))
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := c.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	first := c.currentTransport()
	firstTool := c.Tools()[0].Name
	// Same terminal transition as an interrupted write. A later failed ping
	// should not be necessary to notice an already-closed local session.
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}
	if c.Healthy() {
		t.Error("client reported healthy after its local transport closed")
	}
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); {
		if c.Healthy() && c.currentTransport() != first && c.Tools()[0].Name != firstTool {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("closed stdio session did not reconnect before the next scheduled ping")
}

func TestRejectedHandshakeDoesNotPublishCatalog(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "reached-prompts")
	c := catalogFixtureClient(t, "incomplete", marker)
	c.tools.Store(&[]Tool{{Name: "accepted-tool"}})
	c.resources.Store(&[]Resource{{Name: "accepted-resource"}})
	c.prompts.Store(&[]Prompt{{Name: "accepted-prompt"}})
	var notifications int
	c.SetOnToolsChanged(func(string, []Tool) { notifications++ })
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := c.Connect(ctx); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected discovery deadline, got %v", err)
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatalf("fixture did not finish tools and resources: %v", err)
	}
	if c.Tools()[0].Name != "accepted-tool" || c.Resources()[0].Name != "accepted-resource" || c.Prompts()[0].Name != "accepted-prompt" || notifications != 0 {
		t.Fatalf("rejected session changed the accepted catalog: tool=%s resource=%s prompt=%s notifications=%d", c.Tools()[0].Name, c.Resources()[0].Name, c.Prompts()[0].Name, notifications)
	}
}

func TestStdioCatalogFixture(t *testing.T) {
	if len(os.Args) < 3 || os.Args[len(os.Args)-3] != "--" {
		return
	}
	mode, marker := os.Args[len(os.Args)-2], os.Args[len(os.Args)-1]
	in, out := json.NewDecoder(os.Stdin), json.NewEncoder(os.Stdout)
	for {
		var req Message
		if in.Decode(&req) != nil {
			os.Exit(0)
		}
		if req.IsNotification() {
			continue
		}
		var result any = struct{}{}
		switch req.Method {
		case MethodToolsList:
			result = ListToolsResult{Tools: []Tool{{Name: "tool-" + strconv.Itoa(os.Getpid())}}}
		case MethodResourcesList:
			result = ListResourcesResult{Resources: []Resource{{Name: "new-resource"}}}
		case MethodPromptsList:
			if mode == "incomplete" {
				if err := os.WriteFile(marker, nil, 0600); err != nil {
					os.Exit(2)
				}
				_, _ = io.Copy(io.Discard, os.Stdin)
				os.Exit(0)
			}
		}
		resp, _ := NewResponse(req.ID, result)
		if err := out.Encode(resp); err != nil {
			os.Exit(2)
		}
	}
}
