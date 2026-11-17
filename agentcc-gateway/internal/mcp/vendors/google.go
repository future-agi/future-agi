package vendors

import "github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"

// googleProvider admits declared org-source providers in the Google client
// group. Endpoint, scope and refresh/revocation semantics stay in the catalog and
// the vendor control plane. In particular, a local purge must not issue Google's
// broad project-grant revocation or disconnect another Google app.
func googleProvider(p *manifest.Provider) bool {
	return nativeProvider(p, "google")
}
