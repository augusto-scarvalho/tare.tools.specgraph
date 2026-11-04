# tare.tools.specgraph

O SpecGraph indexa o código, as especificações, as decisões de arquitetura (ADRs) e os vínculos de teste declarados de um repositório em um grafo SQLite local. Pessoas e agentes de programação consultam esse grafo por uma CLI, um SDK Python e um servidor MCP somente leitura, e recebem respostas limitadas que informam qual snapshot e qual escopo cobrem.

Python é analisado com o módulo `ast` da biblioteca padrão. TypeScript e Rust são suportados por gramáticas tree-sitter opcionais. A [North Star](docs/ADR-001_SPECGRAPH_NORTH_STAR.md) descreve o produto pretendido; ela não afirma que toda capacidade ou meta de desempenho já foi entregue. `specgraph capabilities` informa o que está implementado e seus limites.

[Guia de inteligência de projeto](docs/PROJECT_INTELLIGENCE.md) · [Índice da documentação](docs/INDEX.md) · [Especificação canônica](docs/SPEC-SPECGRAPH-001.md) · [English](README.md)

## Relação com o tare.tools.os

O SpecGraph é um dos repositórios satélites do [`tare.tools.os`](https://github.com/augusto-scarvalho/tare.tools.os). O OS o fixa como submódulo Git em `satellites/tare.tools.specgraph`, instala-o no mesmo ambiente e chama sua API Python e sua CLI para dar contexto de projeto aos agentes (`specgraph index`, `specgraph ground`, contexto por papel para hooks). O SpecGraph não importa o OS e pode ser usado sozinho. Seus testes de integração opcionais usam dois outros satélites: `tare.tools.kernel` (portas de provedor e de processo) e `tare.tools.agent-runtime` (o host dos agentes).

## Requisitos

- Python 3.10 ou mais recente, e Git.
- Opcional: o extra `multilanguage` (gramáticas tree-sitter para TypeScript e Rust) e o extra `fast` (`ruff`, usado no grafo de imports do impacto em testes).
- Opcional: um toolchain Rust para compilar `rust/specgraph-core`, o motor nativo de impacto por símbolo usado na seleção de testes ([ADR-SPECGRAPH-015](docs/adr/ADR-SPECGRAPH-015.md)). Sem ele, o SpecGraph recorre ao impacto por import.

## Instalar a partir de um clone

```bash
git clone https://github.com/augusto-scarvalho/tare.tools.specgraph.git
cd tare.tools.specgraph
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
specgraph --version
```

Acrescente os extras opcionais com `python -m pip install -e ".[dev,multilanguage,fast]"`.

## Indexar um projeto

```bash
specgraph --root /caminho/do/projeto init --name meu-projeto
# Edite specgraph.yaml para listar os caminhos reais de código, testes e documentos do projeto.
specgraph --root /caminho/do/projeto index
specgraph --root /caminho/do/projeto query parse_file --format json
specgraph --root /caminho/do/projeto symbol 'src/parser.py:parse_file'
specgraph --root /caminho/do/projeto refs 'src/parser.py:parse_file'
specgraph --root /caminho/do/projeto code-context 'src/parser.py:parse_file' --max-bytes 8192
specgraph --root /caminho/do/projeto doctor
specgraph --root /caminho/do/projeto serve
```

`init` grava `specgraph.yaml` e uma especificação de exemplo. Rode `index` depois de editar. A extração só é reaproveitada quando as identidades de fonte, configuração e parser coincidem; `index --full` extrai tudo de novo. A indexação ainda descobre e calcula o hash das fontes e monta as relações globalmente. As consultas leem o snapshot publicado sem percorrer a árvore de fontes. As respostas identificam revisão, conteúdo e cobertura, e informam que a worktree atual não foi verificada no caminho de leitura.

`query`, `symbol`, `refs`, `neighbors`, `path`, `explain` e `code-context` exigem um índice publicado. `search` e `resolve` mantêm um caminho de compatibilidade identificado para repositórios sem índice. `doctor` lê metadados; `doctor --deep` executa a validação de governança ao vivo. `sync-cache`, `impact` e `staleness` mantêm o contrato separado do cache JSON legado, incluindo seus custos de validação das fontes.

## Manter o contexto útil depois de edições

`specgraph reconcile` observa mudanças nas fontes e confirma as dicas de edição entregues. `specgraph watch` executa o reconciliador periódico em primeiro plano; ele não é instalado como serviço. Hooks registram edições no journal sem indexar de forma síncrona. Este repositório define `hook_context.automatic_roles: []`, então o contexto por papel é pedido explicitamente em vez de se repetir em leituras e edições comuns.

```bash
specgraph reconcile --format json
specgraph hook-context --role implementer --symbol 'src/parser.py:parse_file'
specgraph doctor --effectiveness-report caminho/para/avaliacao-pareada.json
```

Vínculos declarativos exatos em `intent_mapping_paths` exigem a fonte do dono, seu SHA-256, os critérios de aceitação e o ID exato do alvo. São declarações de rastreabilidade, não cobertura executada. Veja o [guia de inteligência de projeto](docs/PROJECT_INTELLIGENCE.md).

## O que o grafo significa

Símbolos Python têm identidades de arquivo e léxicas. A resolução estática preserva aliases e referências locais suportadas; ocorrências ambíguas, externas e dinâmicas continuam explícitas. Uma dependência estática observada não prova uma chamada em tempo de execução. A análise AST nativa não é um grafo de propriedades de código completo, uma análise de fluxo de dados nem um resolvedor universal de despacho dinâmico.

Especificações usam frontmatter YAML (ou JSON) com `id`, `acceptance_criteria` e `target_symbols` opcional. O código pode declarar `@spec SPEC-ID`; testes podem declarar `@pytest.mark.verifies("SPEC-ID", "AC-01")`. ADRs declaram `governed_specs`. Essas declarações estabelecem rastreabilidade, não provam que um teste rodou ou passou.

`indexed_paths` controla a cobertura de recuperação. `governed_paths` controla a verificação de código órfão. Exclusões e linguagens suportadas limitam respostas negativas: nenhum resultado significa nenhuma correspondência no snapshot e no escopo informados, não prova de que um comportamento não possa existir.

## Contexto limitado e federação

`context SPEC-ID` e `bundle --spec SPEC-ID` devolvem os critérios de aceitação selecionados completos e os trechos de código, ou recusam quando o orçamento de bytes é pequeno demais. `code-context` recupera vizinhanças de código a partir dos intervalos de fonte persistidos. [O contrato canônico de contexto](docs/SPEC-SPECGRAPH-001.md) documenta o renderizador ao vivo.

`ground` projeta metadados selecionados de um manifesto de fontes que o host gera a partir do Git ([contrato](docs/LIBRARY_GROUNDING_CONTRACT.md)); metadados sozinhos não significam que os corpos das fontes foram obtidos nem que um item de Work pode ser executado. Os donos dos repositórios continuam sendo a autoridade sobre seus documentos, e sugestões inferidas nunca substituem especificações aceitas.

O servidor MCP via stdio implementa o protocolo 2025-06-18 com as ferramentas somente leitura `query_causal_graph`, `get_context_envelope` e `check_drift`. O frescor das fontes é informado separadamente da consistência do snapshot.

## Rodar os testes

Na raiz do repositório, com o ambiente virtual ativo:

```bash
python tools/check_completion_contracts.py
python -m pytest tests tools/tests
```

Só com o extra `dev`, os testes de TypeScript/Rust, do Kernel e do núcleo nativo são pulados. A CI roda a suíte completa no Windows e no Linux com todos os extras, os dois satélites nas revisões fixadas em [`.github/workflows/ci.yml`](.github/workflows/ci.yml) (`KERNEL_REVISION`, `RUNTIME_REVISION`) e o núcleo nativo compilado:

```bash
python -m pip install -e ".[dev,multilanguage,fast]" pydantic \
  "git+https://github.com/augusto-scarvalho/tare.tools.kernel@<KERNEL_REVISION>" \
  "git+https://github.com/augusto-scarvalho/tare.tools.agent-runtime@<RUNTIME_REVISION>"
cargo test --release --locked --manifest-path rust/specgraph-core/Cargo.toml
cargo build --release --locked --manifest-path rust/specgraph-core/Cargo.toml
python -m pytest tests tools/tests
```

Mudanças neste repositório acrescentam um item em `## Unreleased` no [CHANGELOG.md](CHANGELOG.md); `python tools/install_git_hooks.py` instala a verificação de pre-push ([política](docs/CHANGELOG_POLICY.md)).

## Medir

```bash
specgraph --root /caminho/do/projeto bench parse_file RepositoryIndex --samples 100 --rounds 2
```

O benchmark mede chamadas completas de consulta pelo SDK, incluindo abrir o SQLite e serializar a saída, e informa o repositório e o ambiente. Inicialização do interpretador, indexação, transporte MCP e raciocínio do agente são custos separados. Recuperação abaixo de 50 ms e economia de 65% de contexto continuam sendo metas até serem medidas em uma carga de trabalho acordada.

O [programa de conclusão](planning/README.md) lista os contratos (C0–C8, X1–X5), suas especificações e decisões. Os registros de qualificação anteriores não estão mais na árvore; [docs/evidence](docs/evidence/README.md) explica como lê-los no histórico do Git.

## Licença

Apache-2.0, conforme declarado em [pyproject.toml](pyproject.toml).
