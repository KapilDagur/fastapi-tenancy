## Summary

<!-- One sentence describing what this PR does -->

## Motivation

<!-- Why is this change needed? Link the issue it fixes: Fixes #123 -->

## Changes

<!-- Bullet list of what changed -->

-
-

## Testing

<!-- How did you test this? Which tests were added/modified? -->

```bash
pytest tests/test_<affected_module>.py -v
```

## Checklist

- [ ] Tests pass: `pytest -m "not e2e"`
- [ ] Coverage ≥ 80%: `pytest --cov`
- [ ] `ruff format src tests && ruff check src tests` passes
- [ ] `mypy src` passes
- [ ] CHANGELOG.md updated under `[Unreleased]`
- [ ] Public API changes documented in docstrings and `docs/`

## Breaking changes

<!-- If this PR has breaking changes, describe the migration path here. -->
<!-- Otherwise: "None" -->

None
