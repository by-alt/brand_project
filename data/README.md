# data/

This folder holds the collected data — the SQLite database (`brand_health.db`)
and any raw exports.

These files are **intentionally not committed** to git (see `.gitignore`): the
data is large, regenerates itself every run, and shouldn't live in version control.
The folder is kept in the repo by this README so the path always exists.
