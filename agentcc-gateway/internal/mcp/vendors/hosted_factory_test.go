package vendors_test

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vendors" // K2_FACTORY_IMPORT
)

// K2_FACTORY_BEGIN

// hostedStep is the credential step under test: the K2 hosted runtime
// wiring. The red receipt's base-side copy of this file returns f.resolver,
// the credential resolver that is Deps.CredentialResolver at base; the fixtures and
// every behavioral assertion stay identical.
func hostedStep(t *testing.T, f *hostedFixture) mcp.CredentialResolver {
	t.Helper()
	h, err := vendors.NewHosted(vendors.HostedOptions{Resolver: f.resolver, Catalog: f.catalog, Configs: f.store, RuntimeURL: f.runtime})
	if err != nil {
		t.Fatal(err)
	}
	return h
}

// K2_FACTORY_END

// TestK2HostedConstruction: the wiring needs every dependency and a usable
// runtime URL, and constructing it changes nothing global.
func TestK2HostedConstruction(t *testing.T) {
	f := newHostedFixture(t, func(f *hostedFixture) map[string]any { return map[string]any{} })
	ok := vendors.HostedOptions{Resolver: f.resolver, Catalog: f.catalog, Configs: f.store, RuntimeURL: f.runtime}
	for name, change := range map[string]func(*vendors.HostedOptions){
		"no runtime":       func(o *vendors.HostedOptions) { o.RuntimeURL = "" },
		"relative runtime": func(o *vendors.HostedOptions) { o.RuntimeURL = "/k2" },
		"runtime query":    func(o *vendors.HostedOptions) { o.RuntimeURL = f.runtime + "?x=1" },
		"runtime user":     func(o *vendors.HostedOptions) { o.RuntimeURL = "http://someone@127.0.0.1:9" },
		"no resolver":      func(o *vendors.HostedOptions) { o.Resolver = nil },
		"no catalog":       func(o *vendors.HostedOptions) { o.Catalog = nil },
		"no configs":       func(o *vendors.HostedOptions) { o.Configs = nil },
	} {
		t.Run(name, func(t *testing.T) {
			o := ok
			change(&o)
			if _, err := vendors.NewHosted(o); err == nil {
				t.Fatal("constructed without a required dependency")
			}
		})
	}
	t.Run("trailing slash is the same runtime", func(t *testing.T) {
		o := ok
		o.RuntimeURL += "/"
		if _, err := vendors.NewHosted(o); err != nil {
			t.Fatal(err)
		}
	})
	t.Run("inert: the default resolver and the step are unchanged", func(t *testing.T) {
		if _, err := vendors.NewHosted(ok); err != nil {
			t.Fatal(err)
		}
		if f.srv.Deps().CredentialResolver != f.step {
			t.Fatal("constructing the wiring replaced the server's credential step")
		}
		var _ mcp.CredentialPurger = f.step.(mcp.CredentialPurger)
	})
}
