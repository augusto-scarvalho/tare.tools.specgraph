"""SpecGraph public SDK; load analysis subsystems only when requested."""
from importlib import import_module

__version__ = "0.7.9"

_EXPORTS = {'SpecGraphConfig': ('config', 'SpecGraphConfig'), 'CausalTraceabilityMatrix': ('graph', 'CausalTraceabilityMatrix'), 'SpecNode': ('spec_parser', 'SpecNode'), 'SpecParser': ('spec_parser', 'SpecParser'), 'CodeSymbol': ('code_parser', 'CodeSymbol'), 'CodeParser': ('code_parser', 'CodeParser'), 'TestSymbol': ('test_parser', 'TestSymbol'), 'TestParser': ('test_parser', 'TestParser'), 'SchemaContract': ('schema_parser', 'SchemaContract'), 'SchemaParser': ('schema_parser', 'SchemaParser'), 'SQLSchemaParser': ('schema_parser', 'SQLSchemaParser'), 'JSONSchemaParser': ('schema_parser', 'JSONSchemaParser'), 'ApiEndpoint': ('api_parser', 'ApiEndpoint'), 'McpToolContract': ('api_parser', 'McpToolContract'), 'ApiAndMcpParser': ('api_parser', 'ApiAndMcpParser'), 'AdrNode': ('adr_parser', 'AdrNode'), 'AdrParser': ('adr_parser', 'AdrParser'), 'ContextPackager': ('context', 'ContextPackager'), 'IncrementalCacheEngine': ('cache', 'IncrementalCacheEngine'), 'CacheManifest': ('cache', 'CacheManifest'), 'FileFingerprint': ('cache', 'FileFingerprint'), 'StalenessReport': ('cache', 'StalenessReport'), 'ResolveReport': ('resolve', 'ResolveReport'), 'TraditionalMatch': ('resolve', 'TraditionalMatch'), 'resolve_repository': ('resolve', 'resolve_repository'), 'runtime_directory': ('resolve', 'runtime_directory'), 'telemetry_summary': ('resolve', 'telemetry_summary'), 'GroundingEntry': ('ground', 'GroundingEntry'), 'GroundingReport': ('ground', 'GroundingReport'), 'ground_repository': ('ground', 'ground_repository'), 'OntologyFederationError': ('ontology', 'OntologyFederationError'), 'load_federated_ontologies': ('ontology', 'load_federated_ontologies')}

_EXPORTS.update({
    "RepositoryIndex": ("storage", "RepositoryIndex"),
    "IndexUnavailableError": ("storage", "IndexUnavailableError"),
    "index_repository": ("indexer", "index_repository"),
    "record_changes": ("lifecycle", "record_changes"),
    "lifecycle_status": ("lifecycle", "lifecycle_status"),
    "reconcile_repository": ("lifecycle", "reconcile_repository"),
    "watch_repository": ("lifecycle", "watch_repository"),
    "project_query": ("query", "project_query"),
    "architecture_witness": ("query", "architecture_witness"),
    "serialize_query": ("query", "serialize_query"),
    "report_html": ("report", "report_html"),
    "import_scip_json": ("scip_adapter", "import_scip_json"),
    "TreeSitterCodeParser": ("extractors", "TreeSitterCodeParser"),
    "language_capabilities": ("extractors", "language_capabilities"),
})

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(f".{module}", __name__), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))
