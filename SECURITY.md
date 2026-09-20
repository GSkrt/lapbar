# Security

LapBar stores a Strava Client Secret and sign-in tokens, so credential handling matters here.

If you find a way that a secret could leak (to a file, the cache, logs, the terminal or another user), please
report it privately through GitHub's "Report a vulnerability" on this repository rather than in a public issue.
Include steps to reproduce; I will acknowledge it as soon as I can.

How secrets are meant to be handled is described in the README under "Where your data and secrets live".
