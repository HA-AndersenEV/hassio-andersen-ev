# Copilot Instructions

## Code quality

After making edits to Python files, run Ruff and pylint to check for lint and formatting issues **only on the files you modified**:

```bash
ruff check --fix <modified_files>
ruff format <modified_files>
```

- Only fix issues introduced by your own edits. Do not modify pre-existing code that you did not change.
- If Ruff reports issues on lines you did not touch, leave them as-is.
- Use `collections.abc` for `Callable`, `Mapping`, `Sequence`, etc. instead of `typing` (Ruff UP035).
- Prefer `X | Y` union syntax over `Optional[X]` or `Union[X, Y]` (Ruff UP007).
- Make sure all imports are sorted and grouped correctly and there are no unused imports (Ruff F401, I001, I002). Always fix imports in the files you modified, even if they were not directly related to your changes.

## Tests

Run pytest to ensure that your changes do not break existing tests
When creating or editing existing code, make sure that it is covered by a test. If you are adding new functionality, add a new test for it. If you are modifying existing functionality, make sure that the existing tests still pass and consider adding new tests if necessary.

