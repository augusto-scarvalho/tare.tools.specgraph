# Changelog

Notable changes to `tare.tools.specgraph` are recorded here, newest first.

## Unreleased

- The source-identity pilot test expects the pinned kernel's `read` line format, `N|text` (`1|# source`).
- CI pins the agent runtime and the kernel at the first commits of their public repositories; the commits it pinned before are not published.
- First public release. The repository starts from a single commit; earlier history is not published. It contains
  SpecGraph as the README describes it: the indexer of a repository's code, specifications, ADRs and declared test
  links into a local SQLite graph, its CLI, Python SDK and read-only MCP server, the documentation and the test suite.
