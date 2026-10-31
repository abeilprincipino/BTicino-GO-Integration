# Contributing

## Branches

| Branch | Purpose |
| --- | --- |
| `develop` | Default branch. Every pull request targets it. Beta releases are cut from it. |
| `main` | What official releases are built from. It only receives changes that have already shipped in a beta. |

Feature branches are created from `develop`, for example `feat/ring-clip` or `fix/card-answer`.

## Pull requests

1. Branch from `develop` and open the pull request against `develop`.
2. The **CI** workflow runs the HACS and hassfest validations and the tests in the Home Assistant
   image (`docker build --target test .`). They must pass before the pull request can be merged.
3. Say in the description whether the change was tried in a real Home Assistant with a companion,
   and which companion version.
4. Changes that need a new companion API must say which companion version provides it.

## Releases

Releases are published by the **Release** workflow. It validates, tests, sets the tag as
`version` in `manifest.json` and attaches `bticino_companion.zip`, which HACS installs. Versions
follow Home Assistant: `2026.11.1` (or `2026.11.1.2`) for a release, `2026.11.1b1` for a beta.

There are two ways to start it:

- **From GitHub:** open **Actions > Release > Run workflow**, pick the branch in **Use workflow from**
  and type the new tag. The release and its tag are created on the head of that branch once the
  validations and tests pass. A tag that already exists is refused.
- **From a clone:** push the tag yourself, as shown below.

### Beta

A beta is a tag ending in `b<N>` (or `a<N>`, `rc<N>`) on a commit of `develop`. From GitHub, run the
workflow from `develop` with a tag such as `2026.11.1b1`. From a clone:

```sh
git checkout develop && git pull
git tag -a 2026.11.1b1 -m "2026.11.1b1"
git push origin 2026.11.1b1
```

It is published as a GitHub pre-release. HACS offers it only to users who enable **Show beta
versions** on this repository.

### Official release

Once a beta has been validated:

1. Open a pull request from `develop` to `main` and merge it with **Create a merge commit**, so the
   two branches keep a shared history.
2. Tag the merge commit on `main`: run the workflow from `main` with a tag such as `2026.11.1`, or
   from a clone:

   ```sh
   git checkout main && git pull
   git tag -a 2026.11.1 -m "2026.11.1"
   git push origin 2026.11.1
   ```

The Release workflow refuses a release tag that does not point to a commit on `main`, and a beta
tag that points to neither `develop` nor `main`.

### Hotfix

A fix that cannot wait for `develop` goes to `main` through a pull request from a branch created
from `main`. Release it from `main` (optionally as a beta first), then merge `main` back into
`develop`.
