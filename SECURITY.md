# Security Policy

## Self-Hosting Best Practices

KidTune is designed to run on a trusted home LAN for family use:

- **Change the default PIN immediately**: Update `PARENT_PIN` in `.env` or inside **Parent Mode** after initial setup (do not leave it as `1234`).
- **Keep secrets out of Git**: Never commit `.env`, `data/.session_secret`, `PLEX_TOKEN`, or `GOOGLE_KEEP_MASTER_TOKEN`.
- **Keep KidTune off the public internet**: Host KidTune on your local network (or behind a VPN/reverse proxy with authentication) rather than exposing port `8484` directly to the internet.
- **Keep dependencies updated**: Regularly rebuild your Docker image to pick up `yt-dlp`, `fastapi`, and base OS security updates.

## Reporting a Vulnerability

If you discover a security issue in KidTune (such as an authentication bypass, path traversal in filename sanitization, or credential leakage):

1. **Do not** open a public issue with exploit details.
2. Report it privately using GitHub Security Advisories (**Security -> Advisories -> Report a vulnerability**) or contact the repository owner directly.
3. Include steps to reproduce and any relevant environment details so it can be investigated promptly.
