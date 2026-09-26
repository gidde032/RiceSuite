# Branch protection ruleset (pending)

`main.json` is the `main`-branch protection ruleset for RiceClipper, mirroring
the RicePoster sibling repo. It:

- blocks branch **deletion** and **non-fast-forward** (force) pushes on `main`,
- requires changes to land through a **pull request** (0 required approvals for
  a solo repo, but requires extra approval for unattributed changes), and
- requires the **`Python 3.12 tests and coverage`** GitHub Actions check to pass
  (`integration_id: 15368` is the GitHub Actions app).

## Why it is not applied yet

Repository rulesets (and classic branch protection) require **GitHub Pro** for a
**private** repository. RiceClipper is private on the Free plan, so the API
returns HTTP 403 (`Upgrade to GitHub Pro or make this repository public`). CI
still runs and reports on every PR; it is simply not enforced as a hard merge
block until protection can be created.

## How to apply it

Once the repo is on GitHub Pro (or made public):

```sh
gh api repos/gidde032/RiceClipper/rulesets -X POST \
  --input .github/rulesets/main.json
```

Verify:

```sh
gh api repos/gidde032/RiceClipper/rulesets \
  --jq '.[] | {id, name, enforcement}'
```
