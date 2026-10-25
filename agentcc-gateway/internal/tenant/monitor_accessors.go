package tenant

import "time"

// Alert rule severities an org rule may carry (contract AlertRuleConfig.severity).
const (
	AlertSeverityInfo     = "info"
	AlertSeverityWarning  = "warning"
	AlertSeverityCritical = "critical"

	// DefaultAlertSeverity is the severity of a rule saved without one.
	DefaultAlertSeverity = AlertSeverityWarning

	// DefaultAlertCooldown is how long a rule saved without a cooldown (or
	// with one that is not a Go duration) waits after it fires before it can
	// fire again.
	DefaultAlertCooldown = 15 * time.Minute
)

// AlertSeverity returns the rule's severity: info, warning or critical. An
// empty severity is DefaultAlertSeverity. Any other value is also read as
// DefaultAlertSeverity, and known is false so the caller can log it (the
// platform's contract refuses such a value; a direct admin API caller may
// still send one).
func (r *AlertRuleConfig) AlertSeverity() (severity string, known bool) {
	if r == nil || r.Severity == "" {
		return DefaultAlertSeverity, true
	}
	switch r.Severity {
	case AlertSeverityInfo, AlertSeverityWarning, AlertSeverityCritical:
		return r.Severity, true
	}
	return DefaultAlertSeverity, false
}

// AlertCooldown returns how long the rule waits after it fires before it can
// fire again. An empty, unparseable or negative cooldown is
// DefaultAlertCooldown. An explicit zero ("0s") is honoured: the rule fires on
// every evaluation past its threshold.
func (r *AlertRuleConfig) AlertCooldown() time.Duration {
	if r == nil || r.Cooldown == "" {
		return DefaultAlertCooldown
	}
	d, err := time.ParseDuration(r.Cooldown)
	if err != nil || d < 0 {
		return DefaultAlertCooldown
	}
	return d
}
