# Agent instructions for this repository

## Commit rules

- Write commit messages in the imperative mood ("Add filters", not "Added filters" or "Adds filters").
- Subject line under ~70 characters, no trailing period. Add a body (wrapped around 72 columns) when the change needs explaining — focus the body on *why*, not a restatement of the diff.
- Keep commits scoped to one logical change. Split unrelated changes into separate commits rather than bundling them.
- Do not add any AI/assistant attribution, co-author trailers, or tool names in commit messages or PR descriptions. Commits should read as if written by the person authoring the change.
- Never commit generated runtime output (search exports, query logs, caches) — see `.gitignore`.

## DRY (Don't Repeat Yourself)

- Before adding new code, check whether equivalent logic already exists elsewhere in the file/module and reuse or extract it instead of duplicating it.
- When the same value, path, or string literal is used in more than one place (e.g. `DEFAULT_QUERY_LOG_PATH`, CSS selectors shared across widgets), define it once as a constant/shared rule and reference it, rather than repeating the literal.
- Prefer extracting a small helper function over copy-pasting a block with minor tweaks.
- Don't over-apply this: duplicating two or three similar lines is better than a premature abstraction that only has one real caller.
