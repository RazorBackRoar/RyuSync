# CI vendor directory

Standalone GitHub Actions for this public repo cannot read the private
`RazorBackRoar/razorcore` repository. CI installs razorcore from the pinned
wheel in this directory via `UV_FIND_LINKS`.

Local development in the Apps workspace can overlay the editable sibling:

```bash
uv sync
uv pip install -e ../.razorcore --no-deps
```

When bumping the `razorcore>=…` requirement in `pyproject.toml`, rebuild and
refresh the wheel:

```bash
cd ../.razorcore
uv build
cp dist/razorcore-<version>-py3-none-any.whl ../RyuSync/ci/vendor/
```
