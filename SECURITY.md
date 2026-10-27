# Security Policy

## Reporting a vulnerability

Please do not report a security vulnerability in a public issue. Contact the repository owner privately through GitHub and provide a concise description, impact, and reproduction steps. Do not include real user media or production secrets.

## Sensitive data

Never commit or attach any of the following:

- `.env` files or production environment values
- Google OAuth client secrets, access tokens, or refresh tokens
- `EVERVAULT_SECRET` or database passwords
- SQLite/PostgreSQL dumps containing account data
- Personal media files or upload fragments

If a secret is exposed, revoke or rotate it at the provider immediately, update deployment environment variables, and review repository history. Removing a secret in a later commit does not remove it from previous commits.

## Storage safety

Treat file-migration and cleanup utilities as destructive until proven otherwise. Make a backup first; test with disposable sample files; and never delete a Google Drive fallback copy until the restored destination file has been independently verified.
