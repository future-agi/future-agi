package cassette

import (
	"errors"
	"fmt"
	"strings"
)

// ScrubConfig adds to the built-in scrubbing rules.
type ScrubConfig struct {
	// CredentialHeaders are more header names whose values are credentials.
	CredentialHeaders []string
	// IDKeys are more JSON keys whose values are ids (Slack's "user", say).
	IDKeys []string
	// Literals are values scrubbed wherever they appear: the test account's
	// login, workspace or tenant name. Each is at least 4 characters.
	Literals []string
	// KnownSecrets are the credentials the recording used. They are
	// scrubbed wherever they appear, even where no rule would look; one
	// shorter than MinKnownSecret only where a rule finds it.
	KnownSecrets []string
	// SecretParams are more query or form parameter names, and SecretKeys
	// more JSON member names, whose values are credentials. The cassette
	// keeps them (its Rules), so replay blinds them too.
	SecretParams []string
	SecretKeys   []string
}

// MinKnownSecret is the shortest known secret replaced wherever it appears:
// a shorter one would match ordinary words and numbers.
const MinKnownSecret = minGlobalToken

// Validate refuses a configuration the scrubber cannot honour: a name that
// matches nothing, a literal too short to replace safely, a header name
// that is not one, or a literal spelled in a credential name the cassette
// keeps where the scrubber would rewrite that name in the recording
// (anywhere in a parameter name, since query and form text is scrubbed as
// text; standing alone in a member name): the rule would then match
// nothing, and the literal would reach the file through the rules. Save
// calls it; record calls it before any network call.
func (cfg ScrubConfig) Validate() error {
	if err := (&Rules{SecretParams: cfg.SecretParams, SecretKeys: cfg.SecretKeys}).Validate(); err != nil {
		return err
	}
	for _, l := range cfg.Literals {
		if len(l) < 4 || strings.ContainsAny(l, "\r\n") {
			return errors.New("a literal to scrub is at least 4 characters on one line")
		}
	}
	for _, h := range cfg.CredentialHeaders {
		if h == "" || strings.ContainsAny(h, " :\r\n") {
			return fmt.Errorf("credential header %q is not a header name", h)
		}
	}
	if len(cfg.Literals) == 0 {
		return nil
	}
	ph := newPlaceholders()
	for _, l := range cfg.Literals {
		ph.add(classLiteral, l)
	}
	ph.assign()
	for _, list := range []struct {
		names  []string
		inName bool
	}{{cfg.SecretParams, false}, {cfg.SecretKeys, true}} {
		for _, name := range list.names {
			if len(ph.survivors(name, list.inName)) > 0 {
				return fmt.Errorf("secret name %q spells a literal to scrub: the scrubber would rewrite that name in the recording, "+
					"and the cassette keeps the names it adds, so the literal would reach the file", name)
			}
		}
	}
	return nil
}
