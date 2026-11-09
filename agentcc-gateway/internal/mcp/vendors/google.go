package vendors

import "github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"

// googleProvider admits only K2's two pinned pilots. Endpoint, scope, client
// group and refresh/revocation semantics stay in the loaded manifests and
// W4's control plane. In particular, a local purge must not issue Google's
// broad project-grant revocation or disconnect another Google app.
func googleProvider(p *manifest.Provider) bool {
	if p.App != "gmail" && p.App != "google-calendar" {
		return false
	}
	return nativeProvider(p, "google")
}
