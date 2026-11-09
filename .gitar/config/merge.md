Arm auto-merge only when all of these are true:

- the pull request's base branch is `main` (never auto-merge one layer of a stack into another);
- the risk is low or medium;
- the pull request doesn't carry the `do-not-merge` label.

High-risk pull requests wait for the builder to merge them.
