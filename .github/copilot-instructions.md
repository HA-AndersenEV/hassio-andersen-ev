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
- Keep code compact without unnecessary new or blank lines, but do not sacrifice readability. Keep lines to 120 chars
- Match the existing code style and conventions used throughout the project.

## Tests

Run pytest to ensure that your changes do not break existing tests
When creating or editing existing code, make sure that it is covered by a test. If you are adding new functionality, add a new test for it. If you are modifying existing functionality, make sure that the existing tests still pass and consider adding new tests if necessary.

# Keep it simple

When writing code, try to keep it simple and easy to understand. Avoid unnecessary complexity and strive for clarity. If you find yourself writing complex code, consider refactoring it to make it simpler.

# Follow home assistance guidelines

When contributing to the Andersen EV integration, always adhere to the Home Assistant development guidelines, including code style, testing, and documentation requirements.

All changes should adhere to at least the gold standard as detailed under https://developers.home-assistant.io/docs/core/integration-quality-scale/rules
Do not make changes to pre-existing code just to meet the standards, unless specifically asked to; just focus on ensuring any new code meets the standards.


## Andersen EV specific
- When working on the Andersen EV integration, make sure to follow the Home Assistant development guidelines and best practices.
- You can make a few assumptions about Andersen usage:
  - Most users will have a single device
  - The device will very rarely if ever be removed, but you can't assume it will never be removed. Don't worry too much around timing or data cleanup etc for removal or adding of devices as it very rarely happens after the initial setup, just keep it simple and handle common cases.
  - API tokens need to be refreshed before their expiry time, but apart from that assume tokens will be generally be valid. If a request fails from a bad token, then try a single refresh of the token and retry the request, but if it fails again then it's likely a real issue and you can log an error and give up on the request.
  - Don't overcomplicate token management with convoluted locks and timeouts etc, just keep it simple and handle the common cases / race conditions.