# Changelog

Notable changes to `tare.tools.specgraph` are recorded here, newest first.

## Unreleased

- CI runs on GitHub-hosted Linux and Windows runners (`Tests (linux)`, `Tests (windows)`) with the README's full-suite commands: the kernel and runtime install from their public repositories at the pinned commits, Rust comes from the runner image with a build cache, and the deploy-key clones and the hand-installed MinGW and rustup are gone. The repository variables `CI_RUNNER_LINUX` and `CI_RUNNER_WINDOWS` move it to your own runners, and pull requests from forks then skip it.
- The source-identity pilot test expects the pinned kernel's `read` line format, `N|text` (`1|# source`).
- CI pins the agent runtime and the kernel at the first commits of their public repositories; the commits it pinned before are not published.
- First public release. The repository starts from a single commit; earlier history is not published. It contains
  SpecGraph as the README describes it: the indexer of a repository's code, specifications, ADRs and declared test
  links into a local SQLite graph, its CLI, Python SDK and read-only MCP server, the documentation and the test suite.
