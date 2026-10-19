# Security

## Report a problem

Do not open a public issue for a security problem. Use the private vulnerability report of GitHub:
the **Security** tab of this repository, then **Report a vulnerability**.

## Scope

VISR is a research prototype. The plant is a simulation. Do not connect VISR to the network of a
live plant.

## What never goes into the repository

Credentials, data and material from partners stay out of git. `CONTRIBUTING.md` gives the full list,
and `scripts/check_commit.py` checks it on every push. If a credential gets into a commit, rotate it:
the history keeps the old commit after the file changes.
