package main

import (
	"flag"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

// Exercise startup, not just YAML decoding: a parsed address has no effect
// if main still passes a constant to the HTTP listener.
func TestConfiguredAdminListener(t *testing.T) {
	if path := os.Getenv("TEST_COLLECTOR_ADMIN_CONFIG"); path != "" {
		flag.CommandLine = flag.NewFlagSet("fi-collector", flag.ExitOnError)
		os.Args = []string{"fi-collector", "-config", path}
		main()
		return
	}

	for _, source := range []string{"yaml", "env_over_yaml"} {
		t.Run(source, func(t *testing.T) {
			ln, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			addr := ln.Addr().String()
			if err := ln.Close(); err != nil {
				t.Fatal(err)
			}

			dir := t.TempDir()
			path := filepath.Join(dir, "collector.yaml")
			cmd := exec.Command(os.Args[0], "-test.run=^TestConfiguredAdminListener$")
			cmd.Env = []string{
				"TEST_COLLECTOR_ADMIN_CONFIG=" + path,
				"FI_CH_URL=http://127.0.0.1:1", "FI_PG_WRITE=postgres://test@127.0.0.1:1/test",
				"FI_GRPC_ADDR=127.0.0.1:0", "FI_HTTP_ADDR=disable", "FI_PRICING_CATALOGUE_URL=",
				"FI_DEAD_LETTER_FILE=" + filepath.Join(dir, "dead-letter.jsonl"),
			}
			yamlAddr := addr
			if source == "env_over_yaml" {
				yamlAddr = "127.0.0.1:0"
				cmd.Env = append(cmd.Env, "FI_ADMIN_ADDR="+addr)
			}
			if err := os.WriteFile(path, []byte(fmt.Sprintf("admin:\n  addr: %q\n", yamlAddr)), 0o600); err != nil {
				t.Fatal(err)
			}
			cmd.Stderr = os.Stderr
			if err := cmd.Start(); err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = cmd.Process.Kill(); _ = cmd.Wait() })

			client := http.Client{Timeout: time.Second}
			defer client.CloseIdleConnections()
			deadline := time.Now().Add(5 * time.Second)
			for {
				resp, err := client.Get("http://" + addr + "/healthz")
				if err == nil {
					resp.Body.Close()
					if resp.StatusCode != http.StatusOK {
						t.Fatalf("/healthz returned %s", resp.Status)
					}
					return
				}
				if time.Now().After(deadline) {
					t.Fatalf("/healthz did not answer on the configured address %s: %v", addr, err)
				}
				time.Sleep(20 * time.Millisecond)
			}
		})
	}
}
