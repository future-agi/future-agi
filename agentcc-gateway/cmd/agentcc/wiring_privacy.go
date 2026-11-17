package main

import "github.com/futureagi/agentcc-gateway/internal/privacy"

// initPrivacy installs privacy.default_content_mode as the global default
// content mode, the least restrictive of the four sources step 5 and the
// logging plugin resolve (D7, plan section 6.13). Config validation already
// refuses any value but full, metadata or none. Integrator step for D7:
// main.go is frozen, so the call lives here.
func initPrivacy(d *wiringDeps) error {
	_, err := privacy.SetDefaultContentMode(d.cfg.Privacy.DefaultContentMode)
	return err
}
