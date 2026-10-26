# Contributing

Thanks for helping improve Evervault.

## Before opening an issue or pull request

- Search existing issues first.
- Describe the problem, expected behavior, and reproducible steps.
- Use only disposable test files when testing uploads or migration tools.
- Never attach passwords, OAuth tokens, `.env` files, SQLite dumps, or private media.

## Development

1. Create a branch for your change.
2. Set up Python and install dependencies using `requirements.txt`.
3. Test the relevant path locally before submitting.
4. For Python changes, run `python -m py_compile` on the changed files and include test steps in the pull request.
5. Explain any change affecting storage location, upload behavior, authentication, migrations, or deletion in the pull request description.

## Pull request checklist

- [ ] The change has a clear purpose and focused scope.
- [ ] No secrets, personal data, or generated databases are included.
- [ ] Storage and deletion behavior is explained.
- [ ] Tests or manual verification steps are listed.
- [ ] Documentation is updated where necessary.
