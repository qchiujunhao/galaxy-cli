# Release Process

This project publishes to PyPI from GitHub Releases using PyPI Trusted
Publishing. No long-lived PyPI API token should be stored in GitHub secrets.

## One-Time Setup

Configure PyPI to trust this repository and workflow.

For an existing PyPI project:

1. Open the `galaxy-cli` project on PyPI.
2. Go to `Settings` -> `Publishing`.
3. Add a GitHub Actions trusted publisher:
   - Owner: `qchiujunhao`
   - Repository name: `galaxy-cli`
   - Workflow name: `release.yml`
   - Environment name: `pypi`

For a first release before the PyPI project exists, create a pending trusted
publisher on PyPI with the same values and project name `galaxy-cli`.

In GitHub, create an environment named `pypi` under repository settings. Use
environment protection rules if you want a manual approval gate before upload.

## Release Checklist

1. Sync the development environment and run all local checks:

   ```bash
   uv sync --group dev
   source .venv/bin/activate
   ruff check .
   pytest -q
   python -m build
   git diff --check
   ```

2. Commit and push the release changes.

3. In GitHub, open `Releases` -> `Draft a new release`.

4. Create/select tag `vX.Y.Z`, targeting the commit to release.

5. Publish the release.

Publishing the GitHub Release triggers `.github/workflows/release.yml`. This
uses the same release shape as pandas: the GitHub Release is the release event,
the built distributions are attached to the GitHub Release, and PyPI upload is
handled by GitHub Actions through Trusted Publishing. The workflow derives the
package version from the Git tag with `setuptools-scm`, checks that the derived
version exactly matches `vX.Y.Z`, runs tests, builds the source distribution and
wheel, checks the distributions, attaches them to the GitHub Release, and
uploads them to PyPI.

The tag is the source of truth for the package version. Do not manually edit a
version constant for releases.

## Recovery Notes

- PyPI does not allow re-uploading the same filename/version. If a release
  upload partially succeeds, do not rerun with modified artifacts for the same
  version. Bump the version and publish a new release.
- If publishing fails with a trusted publisher error, check that the PyPI
  publisher values exactly match owner `qchiujunhao`, repository `galaxy-cli`,
  workflow `release.yml`, and environment `pypi`.
- Draft GitHub Releases do not publish to PyPI. Publishing the release is the
  trigger.
- Do not run `twine upload` manually. PyPI upload should happen only through
  the Trusted Publishing workflow.
