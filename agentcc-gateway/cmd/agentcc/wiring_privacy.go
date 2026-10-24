package main

import "github.com/futureagi/agentcc-gateway/internal/privacy"

// initPrivacy installs privacy.default_content_mode as the global default
// content mode, the least restrictive of the four sources step 5 and the
// logging plugin resolve. Config validation refuses any value but full,
// metadata or none.
func initPrivacy(d *wiringDeps) error {
	_, err := privacy.SetDefaultContentMode(d.cfg.Privacy.DefaultContentMode)
	return err
}
