from dataclasses import asdict
import ast

from specgraph.api_parser import ApiAndMcpParser, ASTApiVisitor, ApiEndpoint, McpToolContract


SOURCE = '''raise RuntimeError("FORBIDDEN: analyzed source must never execute")
router = APIRouter(prefix='/v1')
app.include_router(router, prefix='/api')
bp = Blueprint('bp', __name__, url_prefix='/local')
app.register_blueprint(bp, url_prefix='/web')

@router.get('/items')
async def items(q):
    """Items.

    @spec spec-demo [ac-01, AC-02]
    @spec SPEC-DEMO [AC-01]
    """
    pass

@bp.route(rule='/sync', methods=('post', 'PUT'))
def sync(): pass

@router.api_route(path='/default')
def default(): pass

@router.api_route('/many', methods={'patch', 'delete'})
def many(): pass

class Service:
    @mcp.tool(name='lookup')
    def lookup(self, value, optional=None, *, ignored=None):
        """Lookup description.

        @spec SPEC-DEMO [AC-01]
        @spec SPEC-DEMO [AC-01]
        """
        pass

    @app.head('/class')
    def handle(self): pass

def outer():
    @mcp.tool
    async def nested(value):
        """Nested description."""
        pass
    @app.options('/nested')
    def handler(): pass
    app.add_api_route('/imperative', handler, methods=['post', 'GET'])

def dynamic(handler):
    app.add_api_route(path='/dynamic', endpoint=handler)

router.add_api_route('/direct', items, methods=('put',))
'''


def test_routes_tools_identity_and_fields(tmp_path):
    """@verifies SPEC-SPECGRAPH-GOVERNANCE-001 [AC-01]"""
    path = tmp_path / 'routes.py'
    path.write_text(SOURCE, encoding='utf-8')
    endpoints, tools, errors = ApiAndMcpParser.parse_file(path, tmp_path)
    assert errors == []
    expected = {
        'api:GET:/api/v1/items': 'items',
        'api:POST:/web/local/sync': 'sync',
        'api:PUT:/web/local/sync': 'sync',
        'api:GET:/api/v1/default': 'default',
        'api:PATCH:/api/v1/many': 'many',
        'api:DELETE:/api/v1/many': 'many',
        'api:HEAD:/class': 'Service.handle',
        'api:OPTIONS:/nested': 'outer.handler',
        'api:POST:/imperative': 'outer.handler',
        'api:GET:/imperative': 'outer.handler',
        'api:GET:/dynamic': 'dynamic_handler',
        'api:PUT:/api/v1/direct': 'items',
    }
    assert len(endpoints) == len(expected)
    assert {e.endpoint_id: e.handler_symbol for e in endpoints} == {
        key: 'routes.py:' + value for key, value in expected.items()
    }
    tree = ast.parse(SOURCE)
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    item = next(e for e in endpoints if e.endpoint_id == 'api:GET:/api/v1/items')
    assert asdict(item) == dict(
        endpoint_id='api:GET:/api/v1/items', name='items', method='GET',
        path='/api/v1/items', handler_symbol='routes.py:items', file_path='routes.py',
        start_line=functions['items'].lineno, end_line=functions['items'].end_lineno,
        linked_specs=['SPEC-DEMO:AC-01', 'SPEC-DEMO:AC-02'],
        docstring=ast.get_docstring(functions['items']),
    )
    assert len(tools) == 2
    lookup, nested = tools
    assert asdict(lookup) == dict(
        tool_id='mcp:lookup', name='lookup', description='Lookup description.',
        handler_symbol='routes.py:Service.lookup', file_path='routes.py',
        start_line=functions['lookup'].lineno, end_line=functions['lookup'].end_lineno,
        parameters=['value', 'optional'], linked_specs=['SPEC-DEMO:AC-01'],
    )
    assert asdict(nested) == dict(
        tool_id='mcp:nested', name='nested', description='Nested description.',
        handler_symbol='routes.py:outer.nested', file_path='routes.py',
        start_line=functions['nested'].lineno, end_line=functions['nested'].end_lineno,
        parameters=['value'], linked_specs=[],
    )
    visitor = ASTApiVisitor('routes.py', tree)
    visitor.visit(tree)
    assert visitor.scope_stack == visitor.class_stack == []
    assert visitor.endpoints == endpoints and visitor.mcp_tools == tools
    fallback = ASTApiVisitor('pkg\\routes.py')
    fallback.visit(ast.parse('@app.get("/x")\ndef f(): pass'))
    assert fallback.endpoints[0].handler_symbol == 'pkg/routes.py:f'
    assert fallback._extract_spec_links(None) == []
    assert fallback._extract_spec_links('@spec spec-demo\n@spec SPEC-DEMO') == ['SPEC-DEMO']


def test_errors_discovery_and_exclusions(tmp_path):
    """@verifies SPEC-SPECGRAPH-GOVERNANCE-001 [AC-01]"""
    root = tmp_path / 'project'
    src = root / 'src'
    src.mkdir(parents=True)
    good = src / 'routes.py'
    good.write_text(SOURCE, encoding='utf-8')
    (src / 'bad.py').write_text('raise RuntimeError("FORBIDDEN")\ndef (', encoding='utf-8')
    (src / 'utf.py').write_bytes(b'raise RuntimeError("FORBIDDEN")\n#\xff')
    excluded = src / 'skip'
    excluded.mkdir()
    (excluded / 'bad.py').write_text('def (', encoding='utf-8')
    outside = tmp_path / 'outside.py'
    outside.write_text(SOURCE, encoding='utf-8')
    for filename, diagnostic in [('bad.py', 'AST SyntaxError'), ('utf.py', 'UTF-8 Decoding Error')]:
        eps, tools, errors = ApiAndMcpParser.parse_file(src / filename, root)
        assert eps == tools == []
        assert len(errors) == 1 and diagnostic in errors[0]
    eps, tools, errors = ApiAndMcpParser.parse_file(outside, root)
    assert eps == tools == [] and len(errors) == 1 and 'Security Violation' in errors[0]
    expected_eps, expected_tools, _ = ApiAndMcpParser.parse_file(good, root)
    eps, tools, errors = ApiAndMcpParser.discover_apis_and_tools(['src', '../outside.py'], ['skip'], root)
    assert eps == expected_eps and tools == expected_tools
    assert len(errors) == 3
    assert sum('AST SyntaxError' in e for e in errors) == 1
    assert sum('UTF-8 Decoding Error' in e for e in errors) == 1
    assert sum('Security Violation' in e for e in errors) == 1
    assert ApiAndMcpParser.discover_apis_and_tools(['src/routes.py', 'missing'], [], root) == (expected_eps, expected_tools, [])
    assert ApiAndMcpParser.parse_file(root / 'missing.py', root) == ([], [], [])
    ignored = root / 'ignored.txt'
    ignored.write_text('def (', encoding='utf-8')
    assert ApiAndMcpParser.parse_file(ignored, root) == ([], [], [])


def test_contract_models_have_independent_defaults():
    """@verifies SPEC-SPECGRAPH-GOVERNANCE-001 [AC-01]"""
    endpoint = ApiEndpoint('api:GET:/', 'f', 'GET', '/', 'a.py:f', 'a.py')
    other = ApiEndpoint('api:GET:/', 'f', 'GET', '/', 'a.py:f', 'a.py')
    assert asdict(endpoint) == dict(endpoint_id='api:GET:/', name='f', method='GET', path='/',
        handler_symbol='a.py:f', file_path='a.py', start_line=1, end_line=1, linked_specs=[], docstring=None)
    endpoint.linked_specs.append('SPEC-DEMO')
    assert other.linked_specs == []
    tool = McpToolContract('mcp:f', 'f', 'description', 'a.py:f', 'a.py')
    second = McpToolContract('mcp:f', 'f', 'description', 'a.py:f', 'a.py')
    assert asdict(tool) == dict(tool_id='mcp:f', name='f', description='description',
        handler_symbol='a.py:f', file_path='a.py', start_line=1, end_line=1, parameters=[], linked_specs=[])
    tool.parameters.append('x')
    tool.linked_specs.append('SPEC-DEMO')
    assert second.parameters == second.linked_specs == []
