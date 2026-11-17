"""The placeholder a read shows in place of a stored secret.

A read never carries a character of the secret, nor its length. A write that
sends the placeholder back means "keep the stored value", where the stored
value may still be used: for the same destination only (an email alert's SMTP
host and port, a webhook's URL). Elsewhere the caller enters the secret again.
"""

MASKED_SECRET = "********"
