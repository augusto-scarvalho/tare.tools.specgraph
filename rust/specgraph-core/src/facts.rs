//! Per-file facts extracted from Python source with tree-sitter.
//!
//! Facts are purely syntactic and depend only on one file's bytes (plus its module
//! name), so they can be cached by file stamp and re-extracted in parallel.

use serde::{Deserialize, Serialize};
use tree_sitter::{Node, Parser};

/// A value expression reduced to what name binding needs.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub enum Expr {
    Name(String),
    Attr(Box<Expr>, String),
    Call(Box<Expr>),
    Other,
}

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
pub enum DefKind {
    Function,
    Class,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Param {
    pub name: String,
    pub annotation: Option<Expr>,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Def {
    /// Qualified name inside the module, e.g. `Engine.sync` or `outer.inner`.
    pub qual: String,
    pub kind: DefKind,
    /// 1-based inclusive lines; decorators belong to the definition.
    pub start: u32,
    pub end: u32,
    pub bases: Vec<Expr>,
    pub params: Vec<Param>,
    pub returns: Option<Expr>,
    pub decorators: Vec<Expr>,
    /// Directly inside a class body.
    pub is_method: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Import {
    /// Enclosing definition (`""` for module level).
    pub scope: String,
    /// Name bound in the scope (`*` for a wildcard import).
    pub local: String,
    /// Absolute dotted target the name refers to (module or module attribute).
    pub target: String,
    /// Absolute module whose import runs at this statement.
    pub loads: String,
    pub line: u32,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Use {
    pub scope: String,
    pub expr: Expr,
    pub line: u32,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Assign {
    pub scope: String,
    pub target: Expr,
    pub value: Expr,
    pub annotation: Option<Expr>,
    pub line: u32,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Facts {
    pub path: String,
    pub module: String,
    pub is_package: bool,
    pub defs: Vec<Def>,
    pub imports: Vec<Import>,
    pub uses: Vec<Use>,
    pub assigns: Vec<Assign>,
    pub strings: Vec<String>,
    pub parse_errors: bool,
}

/// Module name for `path` (posix, repository-relative) under the first matching root.
pub fn module_name(path: &str, roots: &[String]) -> (String, bool) {
    let stem = path.strip_suffix(".py").unwrap_or(path);
    let relative = roots
        .iter()
        .find_map(|root| stem.strip_prefix(&format!("{}/", root.trim_end_matches('/'))))
        .unwrap_or(stem);
    let mut parts: Vec<&str> = relative.split('/').collect();
    let is_package = parts.last() == Some(&"__init__");
    if is_package {
        parts.pop();
    }
    (parts.join("."), is_package)
}

pub fn extract(path: &str, source: &[u8], roots: &[String]) -> Facts {
    let (module, is_package) = module_name(path, roots);
    let mut parser = Parser::new();
    parser
        .set_language(&tree_sitter_python::LANGUAGE.into())
        .expect("tree-sitter-python grammar");
    let mut facts = Facts { path: path.to_string(), module, is_package, ..Default::default() };
    let Some(tree) = parser.parse(source, None) else {
        facts.parse_errors = true;
        return facts;
    };
    facts.parse_errors = tree.root_node().has_error();
    let mut walker = Walker { src: source, facts: &mut facts };
    walker.block(tree.root_node(), &Scope::module());
    facts
}

#[derive(Clone)]
struct Scope {
    qual: String,
    /// Innermost definition is a class (its body is a class scope).
    in_class: bool,
}

impl Scope {
    fn module() -> Self {
        Scope { qual: String::new(), in_class: false }
    }
    fn child(&self, name: &str, in_class: bool) -> Self {
        let qual = if self.qual.is_empty() { name.to_string() } else { format!("{}.{}", self.qual, name) };
        Scope { qual, in_class }
    }
}

struct Walker<'a> {
    src: &'a [u8],
    facts: &'a mut Facts,
}

fn line(node: Node) -> u32 {
    node.start_position().row as u32 + 1
}

impl<'a> Walker<'a> {
    fn text(&self, node: Node) -> String {
        node.utf8_text(self.src).unwrap_or_default().to_string()
    }

    fn block(&mut self, node: Node, scope: &Scope) {
        let mut cursor = node.walk();
        let children: Vec<Node> = node.named_children(&mut cursor).collect();
        for child in children {
            self.stmt(child, scope);
        }
    }

    fn stmt(&mut self, node: Node, scope: &Scope) {
        match node.kind() {
            "decorated_definition" => {
                let mut decorators = Vec::new();
                let mut cursor = node.walk();
                for child in node.named_children(&mut cursor) {
                    if child.kind() == "decorator" {
                        if let Some(expr) = child.named_child(0) {
                            decorators.push(expr);
                        }
                    }
                }
                if let Some(definition) = node.child_by_field_name("definition") {
                    self.definition(definition, scope, line(node), decorators);
                }
            }
            "function_definition" | "class_definition" => self.definition(node, scope, line(node), Vec::new()),
            "import_statement" => self.import(node, scope),
            "import_from_statement" => self.import_from(node, scope),
            "future_import_statement" => {}
            "if_statement" if scope.qual.is_empty() && self.is_main_guard(node) => {
                // `if __name__ == "__main__":` runs as a script, never at import time.
                let main = Scope { qual: "__main__".into(), in_class: false };
                if let Some(body) = node.child_by_field_name("consequence") {
                    self.block(body, &main);
                }
                let mut cursor = node.walk();
                let alternatives: Vec<Node> = node.children_by_field_name("alternative", &mut cursor).collect();
                for alternative in alternatives {
                    self.stmt(alternative, scope);
                }
            }
            "expression_statement" => {
                let mut cursor = node.walk();
                let children: Vec<Node> = node.named_children(&mut cursor).collect();
                for child in children {
                    match child.kind() {
                        "assignment" => self.assignment(child, scope),
                        "augmented_assignment" => {
                            self.visit(child.child_by_field_name("left"), scope);
                            self.visit(child.child_by_field_name("right"), scope);
                        }
                        _ => self.visit(Some(child), scope),
                    }
                }
            }
            _ => {
                // Compound statements: bodies are blocks, headers are expressions.
                let mut cursor = node.walk();
                let children: Vec<Node> = node.named_children(&mut cursor).collect();
                for child in children {
                    match child.kind() {
                        "block" => self.block(child, scope),
                        k if k.ends_with("_clause") || k == "else_clause" || k == "finally_clause" => {
                            self.stmt(child, scope)
                        }
                        k if is_statement(k) => self.stmt(child, scope),
                        _ => self.visit(Some(child), scope),
                    }
                }
            }
        }
    }

    fn is_main_guard(&self, node: Node) -> bool {
        node.child_by_field_name("condition")
            .map(|c| {
                let text: String = self.text(c).chars().filter(|ch| !ch.is_whitespace()).collect();
                text == "__name__==\"__main__\"" || text == "__name__=='__main__'"
                    || text == "\"__main__\"==__name__" || text == "'__main__'==__name__"
            })
            .unwrap_or(false)
    }

    fn definition(&mut self, node: Node, scope: &Scope, start: u32, decorators: Vec<Node>) {
        let Some(name) = node.child_by_field_name("name") else { return };
        let name = self.text(name);
        let is_class = node.kind() == "class_definition";
        let inner = scope.child(&name, is_class);
        let mut def = Def {
            qual: inner.qual.clone(),
            kind: if is_class { DefKind::Class } else { DefKind::Function },
            start,
            end: node.end_position().row as u32 + 1,
            bases: Vec::new(),
            params: Vec::new(),
            returns: None,
            decorators: decorators.iter().map(|d| self.expr(*d)).collect(),
            is_method: scope.in_class && !is_class,
        };
        // Decorators, bases, annotations and defaults are evaluated for the definition.
        for decorator in &decorators {
            self.visit(Some(*decorator), &inner);
        }
        if is_class {
            if let Some(args) = node.child_by_field_name("superclasses") {
                let mut cursor = args.walk();
                let bases: Vec<Node> = args.named_children(&mut cursor).collect();
                for base in bases {
                    if base.kind() != "keyword_argument" {
                        def.bases.push(self.expr(base));
                    }
                    self.visit(Some(base), &inner);
                }
            }
        } else {
            if let Some(params) = node.child_by_field_name("parameters") {
                let mut cursor = params.walk();
                let children: Vec<Node> = params.named_children(&mut cursor).collect();
                for param in children {
                    let (name, annotation, default) = match param.kind() {
                        "identifier" => (Some(self.text(param)), None, None),
                        "typed_parameter" => (
                            param.named_child(0).map(|n| self.text(n)),
                            param.child_by_field_name("type"),
                            None,
                        ),
                        "default_parameter" | "typed_default_parameter" => (
                            param.child_by_field_name("name").map(|n| self.text(n)),
                            param.child_by_field_name("type"),
                            param.child_by_field_name("value"),
                        ),
                        _ => (None, None, None),
                    };
                    if let Some(name) = name {
                        def.params.push(Param {
                            name: name.trim_start_matches('*').to_string(),
                            annotation: annotation.map(|a| self.type_expr(a)),
                        });
                    }
                    self.visit(annotation, &inner);
                    self.visit(default, &inner);
                }
            }
            if let Some(returns) = node.child_by_field_name("return_type") {
                def.returns = Some(self.type_expr(returns));
                self.visit(Some(returns), &inner);
            }
        }
        self.facts.defs.push(def);
        if let Some(body) = node.child_by_field_name("body") {
            self.block(body, &inner);
        }
    }

    fn import(&mut self, node: Node, scope: &Scope) {
        let mut cursor = node.walk();
        let names: Vec<Node> = node.children_by_field_name("name", &mut cursor).collect();
        for name in names {
            let (dotted, alias) = match name.kind() {
                "aliased_import" => (
                    name.child_by_field_name("name").map(|n| self.text(n)).unwrap_or_default(),
                    name.child_by_field_name("alias").map(|n| self.text(n)),
                ),
                _ => (self.text(name), None),
            };
            // `import a.b` binds `a`; `import a.b as c` binds `c` to `a.b`.
            let (local, target) = match alias {
                Some(alias) => (alias, dotted.clone()),
                None => {
                    let head = dotted.split('.').next().unwrap_or_default().to_string();
                    (head.clone(), head)
                }
            };
            self.facts.imports.push(Import {
                scope: scope.qual.clone(),
                local,
                target,
                loads: dotted,
                line: line(node),
            });
        }
    }

    fn import_from(&mut self, node: Node, scope: &Scope) {
        let Some(module_node) = node.child_by_field_name("module_name") else { return };
        let base = self.absolute_module(module_node);
        let mut cursor = node.walk();
        let names: Vec<Node> = node.children_by_field_name("name", &mut cursor).collect();
        if names.is_empty() {
            // `from m import *`
            self.facts.imports.push(Import {
                scope: scope.qual.clone(),
                local: "*".into(),
                target: base.clone(),
                loads: base,
                line: line(node),
            });
            return;
        }
        for name in names {
            let (imported, alias) = match name.kind() {
                "aliased_import" => (
                    name.child_by_field_name("name").map(|n| self.text(n)).unwrap_or_default(),
                    name.child_by_field_name("alias").map(|n| self.text(n)),
                ),
                _ => (self.text(name), None),
            };
            let target = if base.is_empty() { imported.clone() } else { format!("{base}.{imported}") };
            self.facts.imports.push(Import {
                scope: scope.qual.clone(),
                local: alias.unwrap_or_else(|| imported.rsplit('.').next().unwrap_or_default().to_string()),
                target,
                loads: base.clone(),
                line: line(node),
            });
        }
    }

    /// Resolve `from ..pkg import x` against this file's module.
    fn absolute_module(&self, node: Node) -> String {
        if node.kind() != "relative_import" {
            return self.text(node);
        }
        let mut level = 0;
        let mut rest = String::new();
        let mut cursor = node.walk();
        for child in node.named_children(&mut cursor) {
            match child.kind() {
                "import_prefix" => level = self.text(child).chars().filter(|c| *c == '.').count(),
                _ => rest = self.text(child),
            }
        }
        let mut package: Vec<&str> = self.facts.module.split('.').filter(|p| !p.is_empty()).collect();
        if !self.facts.is_package {
            package.pop();
        }
        for _ in 1..level {
            package.pop();
        }
        let mut parts: Vec<String> = package.iter().map(|p| p.to_string()).collect();
        if !rest.is_empty() {
            parts.push(rest);
        }
        parts.join(".")
    }

    fn assignment(&mut self, node: Node, scope: &Scope) {
        let left = node.child_by_field_name("left");
        let right = node.child_by_field_name("right");
        let annotation = node.child_by_field_name("type");
        if let Some(left) = left {
            if matches!(left.kind(), "identifier" | "attribute") {
                self.facts.assigns.push(Assign {
                    scope: scope.qual.clone(),
                    target: self.expr(left),
                    value: right.map(|r| self.expr(r)).unwrap_or(Expr::Other),
                    annotation: annotation.map(|a| self.type_expr(a)),
                    line: line(node),
                });
                // `obj.attr = v` reads `obj`; a plain name target is a binding, not a use.
                if left.kind() == "attribute" {
                    if let Some(object) = left.child_by_field_name("object") {
                        self.visit(Some(object), scope);
                    }
                }
            } else {
                self.visit(Some(left), scope);
            }
        }
        self.visit(annotation, scope);
        match right {
            Some(r) if r.kind() == "assignment" => self.assignment(r, scope),
            other => self.visit(other, scope),
        }
    }

    /// Annotation expression: strings name types too (`"Engine"`, `Optional[Engine]`).
    fn type_expr(&self, node: Node) -> Expr {
        let node = if node.kind() == "type" { node.named_child(0).unwrap_or(node) } else { node };
        match node.kind() {
            "string" => {
                let text = self.text(node);
                let inner = text.trim_matches(|c| c == '"' || c == '\'');
                dotted_expr(inner)
            }
            "subscript" | "generic_type" => {
                // Optional[X] / list[X]: the first type argument is the useful one for binding.
                let mut cursor = node.walk();
                let args: Vec<Node> = node.named_children(&mut cursor).collect();
                args.get(1).map(|a| self.type_expr(*a)).unwrap_or(Expr::Other)
            }
            "type_parameter" => node.named_child(0).map(|n| self.type_expr(n)).unwrap_or(Expr::Other),
            "binary_operator" => {
                // X | None
                let left = node.child_by_field_name("left");
                left.map(|l| self.type_expr(l)).unwrap_or(Expr::Other)
            }
            _ => self.expr(node),
        }
    }

    fn expr(&self, node: Node) -> Expr {
        match node.kind() {
            "identifier" => Expr::Name(self.text(node)),
            "attribute" => {
                let object = node.child_by_field_name("object");
                let attribute = node.child_by_field_name("attribute");
                match (object, attribute) {
                    (Some(o), Some(a)) => Expr::Attr(Box::new(self.expr(o)), self.text(a)),
                    _ => Expr::Other,
                }
            }
            "call" => node
                .child_by_field_name("function")
                .map(|f| Expr::Call(Box::new(self.expr(f))))
                .unwrap_or(Expr::Other),
            "parenthesized_expression" | "await" => {
                node.named_child(0).map(|n| self.expr(n)).unwrap_or(Expr::Other)
            }
            _ => Expr::Other,
        }
    }

    /// Record every maximal reference chain inside an expression subtree.
    fn visit(&mut self, node: Option<Node>, scope: &Scope) {
        let Some(node) = node else { return };
        match node.kind() {
            "identifier" | "attribute" | "call" => {
                self.facts.uses.push(Use { scope: scope.qual.clone(), expr: self.expr(node), line: line(node) });
                self.chain_parts(node, scope);
            }
            "string" | "concatenated_string" => {
                let text = self.text(node);
                self.facts.strings.push(text);
                // f-string interpolations still hold expressions.
                let mut cursor = node.walk();
                let children: Vec<Node> = node.named_children(&mut cursor).collect();
                for child in children {
                    if child.kind() == "interpolation" {
                        self.visit(child.named_child(0), scope);
                    }
                }
            }
            "keyword_argument" => self.visit(node.child_by_field_name("value"), scope),
            "lambda" => self.visit(node.child_by_field_name("body"), scope),
            "function_definition" | "class_definition" | "decorated_definition" => self.stmt(node, scope),
            _ => {
                let mut cursor = node.walk();
                let children: Vec<Node> = node.named_children(&mut cursor).collect();
                for child in children {
                    if is_statement(child.kind()) || child.kind() == "block" {
                        self.stmt(child, scope);
                    } else {
                        self.visit(Some(child), scope);
                    }
                }
            }
        }
    }

    /// Descend a recorded chain: its objects/callees are part of it, arguments are new uses.
    fn chain_parts(&mut self, node: Node, scope: &Scope) {
        match node.kind() {
            "attribute" => {
                if let Some(object) = node.child_by_field_name("object") {
                    self.chain_parts_or_visit(object, scope);
                }
            }
            "call" => {
                if let Some(function) = node.child_by_field_name("function") {
                    self.chain_parts_or_visit(function, scope);
                }
                self.visit(node.child_by_field_name("arguments"), scope);
            }
            _ => {}
        }
    }

    fn chain_parts_or_visit(&mut self, node: Node, scope: &Scope) {
        match node.kind() {
            "identifier" => {}
            "attribute" | "call" => self.chain_parts(node, scope),
            "parenthesized_expression" | "await" => {
                if let Some(inner) = node.named_child(0) {
                    self.chain_parts_or_visit(inner, scope)
                }
            }
            _ => self.visit(Some(node), scope),
        }
    }
}

fn is_statement(kind: &str) -> bool {
    kind.ends_with("_statement") || kind.ends_with("_definition") || kind == "decorated_definition"
}

fn dotted_expr(text: &str) -> Expr {
    let mut parts = text.split('.').filter(|p| !p.is_empty());
    let Some(first) = parts.next() else { return Expr::Other };
    if !first.chars().all(|c| c.is_alphanumeric() || c == '_') {
        return Expr::Other;
    }
    parts.fold(Expr::Name(first.to_string()), |acc, part| Expr::Attr(Box::new(acc), part.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn facts(src: &str) -> Facts {
        extract("src/pkg/mod.py", src.as_bytes(), &["src".to_string()])
    }

    #[test]
    fn module_names_follow_roots_and_packages() {
        let roots = vec!["src".to_string(), "tests".to_string()];
        assert_eq!(module_name("src/pkg/a.py", &roots), ("pkg.a".into(), false));
        assert_eq!(module_name("src/pkg/__init__.py", &roots), ("pkg".into(), true));
        assert_eq!(module_name("tests/test_x.py", &roots), ("test_x".into(), false));
    }

    #[test]
    fn definitions_imports_and_chains() {
        let f = facts(
            "from .engine import Engine as E\nimport os.path\n\n@decorate\nclass Service(Base):\n    \
             def run(self, e: E) -> 'Result':\n        self.cache = E()\n        return e.sync(x=helper())\n",
        );
        assert_eq!(f.imports[0].target, "pkg.engine.Engine");
        assert_eq!(f.imports[0].local, "E");
        assert_eq!(f.imports[1].local, "os");
        assert_eq!(f.imports[1].loads, "os.path");
        let class = f.defs.iter().find(|d| d.qual == "Service").unwrap();
        assert_eq!(class.start, 4, "decorators belong to the definition");
        assert_eq!(class.bases, vec![Expr::Name("Base".into())]);
        let run = f.defs.iter().find(|d| d.qual == "Service.run").unwrap();
        assert!(run.is_method);
        assert_eq!(run.params[1].annotation, Some(Expr::Name("E".into())));
        assert_eq!(run.returns, Some(Expr::Name("Result".into())));
        let sync = Expr::Call(Box::new(Expr::Attr(Box::new(Expr::Name("e".into())), "sync".into())));
        assert!(f.uses.iter().any(|u| u.scope == "Service.run" && u.expr == sync));
        assert!(f.uses.iter().any(|u| u.expr == Expr::Call(Box::new(Expr::Name("helper".into())))));
        assert!(f.assigns.iter().any(|a| a.scope == "Service.run"
            && a.target == Expr::Attr(Box::new(Expr::Name("self".into())), "cache".into())));
    }

    #[test]
    fn main_guard_is_not_module_level() {
        let f = facts("def main():
    pass

if __name__ == '__main__':
    main()
");
        assert!(f.uses.iter().any(|u| u.scope == "__main__" && u.expr == Expr::Call(Box::new(Expr::Name("main".into())))));
        assert!(!f.uses.iter().any(|u| u.scope.is_empty() && matches!(&u.expr, Expr::Call(_))));
    }

    #[test]
    fn relative_imports_resolve_from_packages() {
        let f = extract("src/pkg/sub/__init__.py", b"from ..core import thing\n", &["src".to_string()]);
        assert_eq!(f.imports[0].target, "pkg.core.thing");
        assert_eq!(f.imports[0].loads, "pkg.core");
    }
}
