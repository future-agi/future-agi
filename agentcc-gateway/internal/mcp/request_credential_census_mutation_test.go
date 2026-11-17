package mcp

import (
	"strings"
	"testing"
)

func TestK2R7CustodySourceMutations(t *testing.T) {
	base, allowed := r7ModuleSources(t), r7AllowedDispatch(t)
	if problems := r7CustodyProblems(base, allowed); len(problems) != 0 {
		t.Fatalf("unmodified module must pass before mutations: %v", problems)
	}
	const installer = "installRequestCredential(c, auth)"
	cases := []struct {
		name, path, old, replacement, want string
	}{
		{
			name: "direct-root-hook-sender", path: "internal/mcp/hook_credential.go",
			old: "resolved.Header = auth.Header.Clone()",
			replacement: `resolved.Header = auth.Header.Clone()
			req, _ := http.NewRequestWithContext(cc.Context(), http.MethodPost, "http://synthetic.invalid/mcp", nil)
			req.Header = resolved.Header.Clone()
			_, _ = http.DefaultClient.Do(req)`,
			want: "dispatch census internal/mcp/hook_credential.go|resolveCredential|Do",
		},
		{
			name: "sender-named-once", path: "internal/mcp/hook_credential.go",
			old: "resolved.Header = auth.Header.Clone()",
			replacement: `resolved.Header = auth.Header.Clone()
			req, _ := http.NewRequestWithContext(cc.Context(), http.MethodPost, "http://synthetic.invalid/mcp", nil)
			req.Header = resolved.Header.Clone()
			once := http.DefaultClient
			_, _ = once.Do(req)`,
			want: "dispatch census internal/mcp/hook_credential.go|resolveCredential|Do",
		},
		{
			name: "sync-once-control", path: "internal/mcp/census_once_control.go",
			replacement: "package mcp\nimport \"sync\"\nfunc censusOnceControl(once *sync.Once) { once.Do(func() {}) }\n",
		},
		{
			name: "removed-installer", path: "internal/mcp/transport.go",
			old: installer, replacement: "", want: "installer CALL",
		},
		{
			name: "commented-installer", path: "internal/mcp/transport.go",
			old: installer, replacement: "// " + installer, want: "installer CALL",
		},
		{
			name: "commented-custody-transfer", path: "internal/mcp/tenancy/manager.go",
			old:         "cfg.Auth, auth = guardedAuth(cfg.Auth, auth, authorize)",
			replacement: "// cfg.Auth, auth = guardedAuth(cfg.Auth, auth, authorize)", want: "custody CALLS",
		},
		{
			name: "outside-mcp-module-sender", path: "cmd/synthetic-r7/sender.go",
			replacement: "package main\nimport \"net/http\"\nfunc send(req *http.Request) { _, _ = http.DefaultClient.Do(req) }\n",
			want:        "dispatch census cmd/synthetic-r7/sender.go|send|Do",
		},
		{
			name: "comment-only-sender-control", path: "internal/mcp/hook_credential.go",
			old:         "resolved.Header = auth.Header.Clone()",
			replacement: "resolved.Header = auth.Header.Clone() // http.DefaultClient.Do(req)",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			sources := make(map[string]string, len(base)+1)
			for path, raw := range base {
				sources[path] = raw
			}
			if tc.old == "" {
				if _, exists := sources[tc.path]; exists {
					t.Fatal("new-source mutation already exists")
				}
				sources[tc.path] = tc.replacement
			} else {
				if strings.Count(sources[tc.path], tc.old) != 1 {
					t.Fatal("mutation anchor is not unique")
				}
				sources[tc.path] = strings.Replace(sources[tc.path], tc.old, tc.replacement, 1)
			}
			if tc.name == "commented-installer" && strings.Count(sources[tc.path], installer) != 1 {
				t.Fatal("comment mutation must preserve the old string-count predicate")
			}
			problems := r7CustodyProblems(sources, allowed)
			for _, problem := range problems {
				if strings.HasPrefix(problem, "parse ") {
					t.Fatalf("mutation must be valid Go syntax, not a parser failure: %s", problem)
				}
			}
			if tc.want == "" {
				if len(problems) != 0 {
					t.Fatalf("control mutation flagged (comments and sync.Once.Do are not sends): %v", problems)
				}
				return
			}
			for _, problem := range problems {
				if strings.Contains(problem, tc.want) {
					return
				}
			}
			t.Fatalf("negative source mutation was not rejected by %q: %v", tc.want, problems)
		})
	}
}
