package conformance

import (
	"encoding/json"
	"fmt"
	"net/url"
	"path/filepath"
	"sort"
	"strings"
)

// MockBinPlaceholder is the agentcc-mcpmock path in a rendered gateway
// config; lib_mcp.sh's scn_mcp_gateway_template replaces it.
const MockBinPlaceholder = "@@MCPMOCK_BIN@@"

// StdioFiles are where a stdio case's mock writes its control address and
// its graceful-exit marker, under the run's work directory.
func StdioFiles(workDir, serverID string) (addrFile, marker string) {
	dir := filepath.Join(workDir, serverID)
	return filepath.Join(dir, "control.addr"), filepath.Join(dir, "exit.marker")
}

// FixturesDir is where the harness writes a case's tools.json.
func FixturesDir(workDir, serverID string) string { return filepath.Join(workDir, serverID) }

// RenderMCP is the gateway config's top-level mcp block for the run: MCP and
// stdio on, the static org, an egress exception for each http mock (the SSRF
// guard refuses loopback otherwise, decision 39), and an operator YAML stdio
// server for each stdio case, run as agentcc-mcpmock with the provider's
// declared environment; and its connectors block, which loads the run's
// catalogs (CatalogDir) in place of the embedded one. mocks maps a case's
// server id to its mock's base URL.
func RenderMCP(plan *Plan, mocks map[string]string, workDir string) (string, error) {
	var b strings.Builder
	b.WriteString("# The connector conformance harness's catalogs and servers (internal/connectorcatalog/conformance).\n")
	fmt.Fprintf(&b, "connectors:\n  catalog_dir: %s\n", quote(CatalogDir(workDir)))
	b.WriteString("mcp:\n  enabled: true\n  stdio:\n    enabled: true\n")
	fmt.Fprintf(&b, "  static:\n    org_id: %s\n", quote(plan.StaticOrg))
	var allow, stdio []string
	for _, cs := range plan.Cases {
		switch {
		case cs.Skip != "":
		case cs.Transport == "stdio":
			stdio = append(stdio, cs.ServerID)
		default:
			base, ok := mocks[cs.ServerID]
			if !ok {
				return "", fmt.Errorf("case %s: no mock named %s is running", cs.ID, cs.ServerID)
			}
			u, err := url.Parse(base)
			if err != nil || u.Host == "" {
				return "", fmt.Errorf("case %s: mock URL %q has no host", cs.ID, base)
			}
			allow = append(allow, u.Host)
		}
	}
	if len(allow) > 0 {
		sort.Strings(allow)
		b.WriteString("  egress:\n    private_allow:\n")
		for _, h := range allow {
			fmt.Fprintf(&b, "      - %s\n", quote(h))
		}
	}
	if len(stdio) > 0 {
		b.WriteString("  servers:\n")
		for _, cs := range plan.Cases {
			if cs.Skip == "" && cs.Transport == "stdio" {
				writeStdioServer(&b, cs, workDir)
			}
		}
	}
	out := b.String()
	if strings.Contains(out, "$") {
		return "", fmt.Errorf("the rendered mcp block holds a $, which the gateway would expand as a variable")
	}
	return out, nil
}

func writeStdioServer(b *strings.Builder, cs *Case, workDir string) {
	addrFile, marker := StdioFiles(workDir, cs.ServerID)
	args := append([]string{}, cs.MockArgs...)
	args = append(args, "--fixtures", FixturesDir(workDir, cs.ServerID), "--addr", "127.0.0.1:0",
		"--addr-file", addrFile, "--marker", marker, "--name", "mcpmock-"+cs.ServerID)
	fmt.Fprintf(b, "    %s:\n      transport: \"stdio\"\n      command: %s\n      args: [", cs.ServerID, quote(MockBinPlaceholder))
	for i, a := range args {
		if i > 0 {
			b.WriteString(", ")
		}
		b.WriteString(quote(a))
	}
	b.WriteString("]\n")
	if cs.PinEra != "" {
		fmt.Fprintf(b, "      era: %s\n", quote(cs.PinEra))
	}
	if len(cs.Credential.Env) > 0 {
		b.WriteString("      env:\n")
		for _, name := range sortedKeys(cs.Credential.Env) {
			fmt.Fprintf(b, "        %s: %s\n", quote(name), quote(cs.Credential.Env[name]))
		}
	}
}

// quote is a YAML scalar: a JSON string, which YAML reads as the same string.
func quote(s string) string {
	out, _ := json.Marshal(s) // a string always encodes
	return string(out)
}
