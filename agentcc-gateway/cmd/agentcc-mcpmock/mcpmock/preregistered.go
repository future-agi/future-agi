package mcpmock

import (
	"errors"
	"slices"
)

// OAuthClient is explicit mock registration metadata, not an implicit-public
// fallback. Static fixtures and dynamically registered clients share one store
// and the unchanged redirect, grant, PKCE and authentication guards.
type OAuthClient = registration

func preRegisteredClient(id string, c OAuthClient) (registration, error) {
	invalid := errors.New("pre-registered OAuth client requires explicit valid id, redirects, auth method and grants")
	if id == "" || (c.ClientID != "" && c.ClientID != id) || len(c.RedirectURIs) == 0 || len(c.RedirectURIs) > 16 {
		return registration{}, invalid
	}
	for _, uri := range c.RedirectURIs {
		if !redirectAllowed(uri) {
			return registration{}, invalid
		}
	}
	if !slices.Contains([]string{"none", "client_secret_post", "client_secret_basic"}, c.AuthMethod) ||
		(c.AuthMethod == "none") != (c.Secret == "") || len(c.GrantTypes) == 0 ||
		len(c.ResponseTypes) != 1 || c.ResponseTypes[0] != "code" {
		return registration{}, invalid
	}
	for _, grant := range c.GrantTypes {
		if grant != "authorization_code" && grant != "refresh_token" {
			return registration{}, invalid
		}
	}
	c.ClientID = id
	c.RedirectURIs = slices.Clone(c.RedirectURIs)
	c.GrantTypes = slices.Clone(c.GrantTypes)
	c.ResponseTypes = slices.Clone(c.ResponseTypes)
	return c, nil
}
