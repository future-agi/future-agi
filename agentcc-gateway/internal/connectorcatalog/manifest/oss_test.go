package manifest

import (
	"reflect"
	"testing"
)

func TestOSSIdsPreservedAndUnique(t *testing.T) {
	ossIDs := func(cat *Catalog, app string) []string {
		var out []string
		for _, s := range EligibleForOSS(cat.App(app)) {
			out = append(out, s.ID+"="+s.Provider.ID)
		}
		return out
	}
	t.Run("emitted ids", func(t *testing.T) {
		cat := mustLoad(t, fixture(t))
		want := map[string][]string{
			"github":    {"github-remote=remote", "github=local"},
			"linear":    {"linear=linear-mcp"},
			"stripe":    {"stripe=stripe-mcp"},
			"close":     {"close=close-mcp"},
			"slack":     {"slack=slack-mcp"},
			"gmail":     nil, // rest and partner providers are not emitted to OSS
			"snowflake": nil, // excluded: its URL has variables
		}
		for app, ids := range want {
			if got := ossIDs(cat, app); !reflect.DeepEqual(got, ids) {
				t.Errorf("EligibleForOSS(%s) = %v, want %v", app, got, ids)
			}
		}
	})
	t.Run("default ids: the app id first, then <app>-<provider>", func(t *testing.T) {
		fsys := fixture(t)
		orderApp(t, fsys, "beta")
		for _, id := range []string{"alpha", "beta", "gamma"} {
			edit(t, fsys, "apps/orderly/providers/"+id+".yaml", "x-omega: {ossId: orderly-"+id+"}\n", "")
		}
		if got, want := ossIDs(mustLoad(t, fsys), "orderly"), []string{"orderly=beta", "orderly-alpha=alpha", "orderly-gamma=gamma"}; !reflect.DeepEqual(got, want) {
			t.Fatalf("ids = %v, want %v", got, want)
		}
	})

	refusals := []struct {
		name, path, old, new string
		want                 []string
	}{
		{"the hosted GitHub server keeps github-remote", "apps/github/providers/remote.yaml", "x-omega: { ossId: github-remote }\n", "",
			[]string{"OSS id github belongs to OSS's stdio server for app github", "OSS calls https://api.githubcopilot.com/mcp/ github-remote"}},
		{"the local GitHub server keeps github", "apps/github/providers/local.yaml", "x-omega: { ossId: github }\n", "x-omega: {ossId: github-docker}\n",
			[]string{"OSS's stdio server for app github is github; no provider keeps that id"}},
		{"a preserved id cannot move to another app", "apps/close/providers/close-mcp.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {ossId: linear}\n",
			[]string{"OSS id linear belongs to OSS's http server for app linear"}},
		{"ids are unique", "apps/stripe/providers/stripe-mcp.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {ossId: close}\n",
			[]string{"OSS id close is emitted twice"}},
		{"an ossId on a provider OSS never receives", "apps/gmail/providers/futureagi-rest.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {ossId: gmail-rest}\n",
			[]string{"x-omega.ossId is set but OSS never receives this provider"}},
		{"an OSS server needs a fixed URL", "apps/snowflake/app.yaml", "x-omega: { ee: exclude, oss: exclude }\n", "x-omega: {ee: exclude}\n",
			[]string{"OSS needs a fixed URL; its url has variables; set x-omega.oss: exclude"}},
	}
	for _, tc := range refusals {
		t.Run(tc.name, func(t *testing.T) {
			fsys := fixture(t)
			edit(t, fsys, tc.path, tc.old, tc.new)
			wantLoadError(t, fsys, tc.want...)
		})
	}
	t.Run("the hosted Sentry server keeps sentry-remote by its URL", func(t *testing.T) {
		fsys := fixture(t)
		cloneApp(t, fsys, "close", "sentry")
		edit(t, fsys, "apps/sentry/providers/close-mcp.yaml", "url: https://mcp.close.com/mcp", "url: https://mcp.sentry.dev/mcp")
		wantLoadError(t, fsys, "OSS calls https://mcp.sentry.dev/mcp sentry-remote")
		edit(t, fsys, "apps/sentry/providers/close-mcp.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {ossId: sentry-remote}\n")
		mustLoad(t, fsys)
	})
	t.Run("a derived id longer than 40 characters", func(t *testing.T) {
		fsys := fixture(t)
		long := "a-thirty-five-character-app-id-here" // 35 characters
		cloneApp(t, fsys, "github", long)
		edit(t, fsys, "apps/"+long+"/providers/remote.yaml", "x-omega: { ossId: github-remote }\n", "")
		edit(t, fsys, "apps/"+long+"/providers/remote.yaml", "url: https://api.githubcopilot.com/mcp/", "url: https://mcp.example.com/long/mcp")
		edit(t, fsys, "apps/"+long+"/providers/local.yaml", "x-omega: { ossId: github }\n", "")
		edit(t, fsys, "apps/"+long+"/app.yaml", "defaultProvider: remote", "defaultProvider: local")
		// local comes first and takes the app id; remote derives <app>-remote, 42 characters.
		wantLoadError(t, fsys, "OSS id "+long+"-remote is longer than 40 characters; set x-omega.ossId")
		edit(t, fsys, "apps/"+long+"/providers/remote.yaml", "lifecycle: { status: active }\n", "lifecycle: { status: active }\nx-omega: {ossId: long-remote}\n")
		if got := ossIDs(mustLoad(t, fsys), long); !reflect.DeepEqual(got, []string{long + "=local", "long-remote=remote"}) {
			t.Fatalf("ids = %v", got)
		}
	})
}
