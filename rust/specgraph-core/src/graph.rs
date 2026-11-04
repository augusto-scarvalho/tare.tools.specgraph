//! Project-wide binding ("type-lite" name resolution) and reverse test impact.
//!
//! Binding follows Python's scoping (function -> enclosing functions -> module; class
//! bodies are invisible to their methods), imports and re-exports, `self`/`cls`,
//! annotated parameters, constructor-assigned locals, annotated returns, instance
//! attributes and inheritance. What cannot be proven stays unresolved; the impact
//! query compensates with conservative rules rather than guessing.

use crate::facts::{DefKind, Expr, Facts};
use serde::Serialize;
use std::cell::RefCell;
use std::collections::{BTreeSet, HashMap, HashSet};

/// Symbol id: `module:qual`; a module's own node is `module:`.
pub type Sym = String;

#[derive(Clone, Debug, PartialEq)]
enum Target {
    Module(String),
    Sym(Sym),
    Inst(Sym),
    Unknown,
}

#[derive(Clone, Debug)]
enum Bind {
    Def(Sym),
    Import(String),
    Value { expr: Expr, annotation: Option<Expr>, scope: String },
    Param { def: Sym, index: usize, annotation: Option<Expr> },
}

const MAX_DEPTH: u32 = 24;

/// Where an attribute's type comes from: (file, scope, assigned value, annotation).
type AttrSource = (usize, String, Expr, Option<Expr>);

pub struct Project {
    pub files: Vec<Facts>,
    by_module: HashMap<String, usize>,
    by_path: HashMap<String, usize>,
    /// Symbol -> (file, def index).
    defs: HashMap<Sym, (usize, usize)>,
    /// Class -> direct member name -> symbol.
    members: HashMap<Sym, HashMap<String, Sym>>,
    /// (file, scope qual) -> name bindings; `""` is the module scope.
    scopes: HashMap<(usize, String), HashMap<String, Bind>>,
    stars: HashMap<usize, Vec<String>>,
    /// Class -> attribute -> (file, scope, value, annotation) from `self.a = ...` / `a: T`.
    instance_attrs: HashMap<Sym, HashMap<String, AttrSource>>,
    /// Module -> modules it loads at import time (including parent packages).
    module_deps: HashMap<String, HashSet<String>>,
    /// Reverse of `module_deps`: module -> modules that load it directly.
    module_importers: HashMap<String, HashSet<String>>,
    /// Reverse edges: used symbol -> users.
    users: HashMap<Sym, HashSet<Sym>>,
    /// Import target -> files importing it (fixtures shared across test files).
    imported_by: HashMap<String, HashSet<usize>>,
    bases_memo: RefCell<HashMap<Sym, Vec<Sym>>>,
    name_memo: RefCell<HashMap<(usize, String, String), Target>>,
    pub unresolved_attributes: usize,
    pub edges: usize,
}

#[derive(Serialize, Default)]
pub struct Impact {
    /// (path, pytest-style qualname) of test functions reached.
    pub tests: BTreeSet<(String, String)>,
    /// Whole test files to run.
    pub files: BTreeSet<String>,
    /// First reason each whole file was selected.
    pub reasons: std::collections::BTreeMap<String, String>,
    /// One mode per change: symbol, class-body, module, unknown-file.
    pub modes: Vec<String>,
    pub full_suite: bool,
    pub reached_symbols: usize,
    /// Modules whose import-time code is affected.
    pub import_time: BTreeSet<String>,
}

impl Impact {
    fn add_file(&mut self, path: &str, reason: &str) {
        if self.files.insert(path.to_string()) {
            self.reasons.insert(path.to_string(), reason.to_string());
        }
    }
}

fn sym(module: &str, qual: &str) -> Sym {
    format!("{module}:{qual}")
}

fn parent(qual: &str) -> &str {
    qual.rfind('.').map(|i| &qual[..i]).unwrap_or("")
}

fn is_named(expr: &Expr, names: &[&str]) -> bool {
    match expr {
        Expr::Name(n) => names.contains(&n.as_str()),
        Expr::Attr(_, a) => names.contains(&a.as_str()),
        Expr::Call(f) => is_named(f, names),
        Expr::Other => false,
    }
}

impl Project {
    pub fn build(files: Vec<Facts>) -> Project {
        let mut p = Project {
            by_module: HashMap::new(),
            by_path: HashMap::new(),
            defs: HashMap::new(),
            members: HashMap::new(),
            scopes: HashMap::new(),
            stars: HashMap::new(),
            instance_attrs: HashMap::new(),
            module_deps: HashMap::new(),
            module_importers: HashMap::new(),
            users: HashMap::new(),
            imported_by: HashMap::new(),
            bases_memo: RefCell::new(HashMap::new()),
            name_memo: RefCell::new(HashMap::new()),
            unresolved_attributes: 0,
            edges: 0,
            files,
        };
        p.index();
        p.link();
        p
    }

    fn index(&mut self) {
        for (fi, f) in self.files.iter().enumerate() {
            self.by_module.entry(f.module.clone()).or_insert(fi);
            self.by_path.insert(f.path.clone(), fi);
        }
        for (fi, f) in self.files.iter().enumerate() {
            let classes: HashSet<&str> =
                f.defs.iter().filter(|d| d.kind == DefKind::Class).map(|d| d.qual.as_str()).collect();
            for (di, d) in f.defs.iter().enumerate() {
                let id = sym(&f.module, &d.qual);
                self.defs.insert(id.clone(), (fi, di));
                let owner = parent(&d.qual).to_string();
                let name = d.qual.rsplit('.').next().unwrap_or(&d.qual).to_string();
                if classes.contains(owner.as_str()) {
                    self.members.entry(sym(&f.module, &owner)).or_default().insert(name.clone(), id.clone());
                } else {
                    // Module-level or nested in a function: a name in that scope.
                    self.scopes.entry((fi, owner)).or_default().insert(name, Bind::Def(id.clone()));
                }
                if d.kind == DefKind::Function {
                    let scope = self.scopes.entry((fi, d.qual.clone())).or_default();
                    for (index, param) in d.params.iter().enumerate() {
                        scope.insert(
                            param.name.clone(),
                            Bind::Param { def: id.clone(), index, annotation: param.annotation.clone() },
                        );
                    }
                }
            }
            for import in &f.imports {
                if import.local == "*" {
                    if import.scope.is_empty() {
                        self.stars.entry(fi).or_default().push(import.target.clone());
                    }
                } else {
                    self.scopes
                        .entry((fi, import.scope.clone()))
                        .or_default()
                        .insert(import.local.clone(), Bind::Import(import.target.clone()));
                }
                self.imported_by.entry(import.target.clone()).or_default().insert(fi);
                let deps = self.module_deps.entry(f.module.clone()).or_default();
                for loaded in [&import.loads, &import.target] {
                    let parts: Vec<&str> = loaded.split('.').collect();
                    for i in 1..=parts.len() {
                        deps.insert(parts[..i].join("."));
                    }
                }
            }
            for a in &f.assigns {
                match &a.target {
                    Expr::Name(name) => {
                        if classes.contains(a.scope.as_str()) {
                            // Class-body attribute: visible through the class and its instances.
                            self.instance_attrs.entry(sym(&f.module, &a.scope)).or_default().insert(
                                name.clone(),
                                (fi, parent(&a.scope).to_string(), a.value.clone(), a.annotation.clone()),
                            );
                        } else {
                            self.scopes.entry((fi, a.scope.clone())).or_default().insert(
                                name.clone(),
                                Bind::Value { expr: a.value.clone(), annotation: a.annotation.clone(), scope: a.scope.clone() },
                            );
                        }
                    }
                    Expr::Attr(object, attr) if matches!(object.as_ref(), Expr::Name(n) if n == "self") => {
                        let class = parent(&a.scope);
                        if classes.contains(class) {
                            self.instance_attrs.entry(sym(&f.module, class)).or_default().insert(
                                attr.clone(),
                                (fi, a.scope.clone(), a.value.clone(), a.annotation.clone()),
                            );
                        }
                    }
                    _ => {}
                }
            }
        }
        // A module also loads its parent packages.
        let modules: Vec<String> = self.by_module.keys().cloned().collect();
        for module in modules {
            let parts: Vec<&str> = module.split('.').collect();
            let deps = self.module_deps.entry(module.clone()).or_default();
            for i in 1..parts.len() {
                deps.insert(parts[..i].join("."));
            }
        }
        for (module, deps) in &self.module_deps {
            for dep in deps {
                self.module_importers.entry(dep.clone()).or_default().insert(module.clone());
            }
        }
    }

    fn link(&mut self) {
        let mut edges: Vec<(Sym, Sym)> = Vec::new();
        let mut unresolved = 0;
        for (fi, f) in self.files.iter().enumerate() {
            for u in &f.uses {
                let source = sym(&f.module, &u.scope);
                let mut found = Vec::new();
                let mut misses = 0;
                self.resolve_expr(fi, &u.scope, &u.expr, 0, &mut found, &mut misses);
                unresolved += misses;
                for target in found {
                    if target != source {
                        edges.push((target, source.clone()));
                    }
                }
            }
        }
        self.unresolved_attributes = unresolved;
        self.edges = edges.len();
        for (used, user) in edges {
            self.users.entry(used).or_default().insert(user);
        }
    }

    fn def_of(&self, id: &str) -> Option<&crate::facts::Def> {
        self.defs.get(id).map(|(fi, di)| &self.files[*fi].defs[*di])
    }

    fn is_class(&self, id: &str) -> bool {
        self.def_of(id).map(|d| d.kind == DefKind::Class).unwrap_or(false)
    }

    fn resolve_expr(&self, fi: usize, scope: &str, expr: &Expr, depth: u32, found: &mut Vec<Sym>, misses: &mut usize) -> Target {
        if depth > MAX_DEPTH {
            return Target::Unknown;
        }
        let target = match expr {
            Expr::Name(name) => self.resolve_name(fi, scope, name, depth),
            Expr::Attr(object, attr) => {
                let base = self.resolve_expr(fi, scope, object, depth + 1, found, misses);
                let t = self.resolve_attr(&base, attr, depth + 1);
                if t == Target::Unknown && base == Target::Unknown {
                    *misses += 1;
                }
                t
            }
            Expr::Call(function) => {
                let callee = self.resolve_expr(fi, scope, function, depth + 1, found, misses);
                return self.call_result(&callee, depth + 1);
            }
            Expr::Other => Target::Unknown,
        };
        if let Target::Sym(s) = &target {
            found.push(s.clone());
        }
        target
    }

    fn call_result(&self, callee: &Target, depth: u32) -> Target {
        match callee {
            Target::Sym(s) if self.is_class(s) => Target::Inst(s.clone()),
            Target::Sym(s) => {
                let Some((fi, di)) = self.defs.get(s) else { return Target::Unknown };
                let d = &self.files[*fi].defs[*di];
                match &d.returns {
                    Some(r) => self.instance_of(self.resolve_quiet(*fi, parent(&d.qual), r, depth + 1)),
                    None => Target::Unknown,
                }
            }
            _ => Target::Unknown,
        }
    }

    fn resolve_quiet(&self, fi: usize, scope: &str, expr: &Expr, depth: u32) -> Target {
        let mut found = Vec::new();
        let mut misses = 0;
        self.resolve_expr(fi, scope, expr, depth, &mut found, &mut misses)
    }

    fn instance_of(&self, t: Target) -> Target {
        match t {
            Target::Sym(s) if self.is_class(&s) => Target::Inst(s),
            Target::Inst(s) => Target::Inst(s),
            _ => Target::Unknown,
        }
    }

    fn resolve_name(&self, fi: usize, scope: &str, name: &str, depth: u32) -> Target {
        let key = (fi, scope.to_string(), name.to_string());
        if let Some(hit) = self.name_memo.borrow().get(&key) {
            return hit.clone();
        }
        let module = &self.files[fi].module;
        let mut qual = scope;
        let result = loop {
            let own_class_body = qual == scope;
            let visible = own_class_body || qual.is_empty() || !self.is_class(&sym(module, qual));
            if visible {
                if let Some(bind) = self.scopes.get(&(fi, qual.to_string())).and_then(|s| s.get(name)) {
                    break self.resolve_bind(fi, bind, depth + 1);
                }
            }
            if qual.is_empty() {
                let stars = self.stars.get(&fi).cloned().unwrap_or_default();
                break stars
                    .iter()
                    .map(|m| self.resolve_attr(&Target::Module(m.clone()), name, depth + 1))
                    .find(|t| *t != Target::Unknown)
                    .unwrap_or(Target::Unknown);
            }
            qual = parent(qual);
        };
        self.name_memo.borrow_mut().insert(key, result.clone());
        result
    }

    fn resolve_bind(&self, fi: usize, bind: &Bind, depth: u32) -> Target {
        if depth > MAX_DEPTH {
            return Target::Unknown;
        }
        match bind {
            Bind::Def(s) => Target::Sym(s.clone()),
            Bind::Import(target) => self.resolve_dotted(target, depth + 1),
            Bind::Value { expr, annotation, scope } => match annotation {
                Some(a) => self.instance_of(self.resolve_quiet(fi, scope, a, depth + 1)),
                None => self.resolve_quiet(fi, scope, expr, depth + 1),
            },
            Bind::Param { def, index, annotation } => {
                let Some(d) = self.def_of(def) else { return Target::Unknown };
                if d.is_method && *index == 0 && !d.decorators.iter().any(|e| is_named(e, &["staticmethod"])) {
                    let class = sym(&self.files[fi].module, parent(&d.qual));
                    return if d.decorators.iter().any(|e| is_named(e, &["classmethod"])) {
                        Target::Sym(class)
                    } else {
                        Target::Inst(class)
                    };
                }
                match annotation {
                    Some(a) => self.instance_of(self.resolve_quiet(fi, parent(&d.qual), a, depth + 1)),
                    None => Target::Unknown,
                }
            }
        }
    }

    fn resolve_dotted(&self, dotted: &str, depth: u32) -> Target {
        let parts: Vec<&str> = dotted.split('.').collect();
        for i in (1..=parts.len()).rev() {
            let module = parts[..i].join(".");
            if self.by_module.contains_key(&module) {
                let mut t = Target::Module(module);
                for part in &parts[i..] {
                    t = self.resolve_attr(&t, part, depth + 1);
                }
                return t;
            }
        }
        Target::Unknown
    }

    fn resolve_attr(&self, base: &Target, attr: &str, depth: u32) -> Target {
        if depth > MAX_DEPTH {
            return Target::Unknown;
        }
        match base {
            Target::Module(m) => {
                let Some(&fi) = self.by_module.get(m) else { return Target::Unknown };
                if let Some(bind) = self.scopes.get(&(fi, String::new())).and_then(|s| s.get(attr)) {
                    return self.resolve_bind(fi, bind, depth + 1);
                }
                let sub = format!("{m}.{attr}");
                if self.by_module.contains_key(&sub) {
                    return Target::Module(sub);
                }
                self.stars
                    .get(&fi)
                    .into_iter()
                    .flatten()
                    .map(|s| self.resolve_attr(&Target::Module(s.clone()), attr, depth + 1))
                    .find(|t| *t != Target::Unknown)
                    .unwrap_or(Target::Unknown)
            }
            Target::Sym(class) if self.is_class(class) => match self.member(class, attr, depth + 1) {
                Some(m) => Target::Sym(m),
                None => self.attribute_type(class, attr, depth + 1),
            },
            Target::Inst(class) => match self.member(class, attr, depth + 1) {
                Some(m) => Target::Sym(m),
                None => self.attribute_type(class, attr, depth + 1),
            },
            _ => Target::Unknown,
        }
    }

    /// Member lookup through the class and its bases (depth-first, left to right).
    fn member(&self, class: &str, name: &str, depth: u32) -> Option<Sym> {
        let mut seen = HashSet::new();
        let mut stack = vec![class.to_string()];
        while let Some(c) = stack.pop() {
            if !seen.insert(c.clone()) || seen.len() > 64 {
                continue;
            }
            if let Some(m) = self.members.get(&c).and_then(|ms| ms.get(name)) {
                return Some(m.clone());
            }
            let mut bases = self.bases(&c, depth + 1);
            bases.reverse();
            stack.extend(bases);
        }
        None
    }

    fn attribute_type(&self, class: &str, attr: &str, depth: u32) -> Target {
        let mut seen = HashSet::new();
        let mut stack = vec![class.to_string()];
        while let Some(c) = stack.pop() {
            if !seen.insert(c.clone()) || seen.len() > 64 {
                continue;
            }
            if let Some((fi, scope, value, annotation)) = self.instance_attrs.get(&c).and_then(|a| a.get(attr)) {
                let t = match annotation {
                    Some(a) => self.resolve_quiet(*fi, scope, a, depth + 1),
                    None => self.resolve_quiet(*fi, scope, value, depth + 1),
                };
                return self.instance_of(t);
            }
            stack.extend(self.bases(&c, depth + 1));
        }
        Target::Unknown
    }

    fn bases(&self, class: &str, depth: u32) -> Vec<Sym> {
        if let Some(hit) = self.bases_memo.borrow().get(class) {
            return hit.clone();
        }
        let mut result = Vec::new();
        if let Some((fi, di)) = self.defs.get(class) {
            let d = &self.files[*fi].defs[*di];
            for base in &d.bases {
                if let Target::Sym(s) = self.resolve_quiet(*fi, parent(&d.qual), base, depth + 1) {
                    if self.is_class(&s) {
                        result.push(s);
                    }
                }
            }
        }
        self.bases_memo.borrow_mut().insert(class.to_string(), result.clone());
        result
    }

    fn is_test_file(path: &str, test_roots: &[String]) -> bool {
        test_roots.iter().any(|r| path.starts_with(&format!("{}/", r.trim_end_matches('/'))))
    }

    /// Modules whose import (transitively) runs `module`.
    fn importers(&self, module: &str) -> HashSet<String> {
        let mut result: HashSet<String> = HashSet::from([module.to_string()]);
        let mut frontier = vec![module.to_string()];
        while let Some(m) = frontier.pop() {
            for importer in self.module_importers.get(&m).into_iter().flatten() {
                if result.insert(importer.clone()) {
                    frontier.push(importer.clone());
                }
            }
        }
        result
    }

    pub fn impact(&self, changes: &[(String, u32)], test_roots: &[String]) -> Impact {
        let mut impact = Impact::default();
        let mut seeds: Vec<Sym> = Vec::new();
        let mut import_time: HashSet<String> = HashSet::new();
        for (path, line) in changes {
            let Some(&fi) = self.by_path.get(path) else {
                impact.modes.push("unknown-file".into());
                impact.full_suite = true;
                continue;
            };
            let f = &self.files[fi];
            let inner = f.defs.iter().filter(|d| d.start <= *line && *line <= d.end).max_by_key(|d| (d.start, u32::MAX - d.end));
            match inner {
                None => {
                    impact.modes.push("module".into());
                    seeds.push(sym(&f.module, ""));
                    seeds.extend(f.defs.iter().map(|d| sym(&f.module, &d.qual)));
                    import_time.insert(f.module.clone());
                }
                Some(d) if d.kind == DefKind::Class => {
                    impact.modes.push("class-body".into());
                    let prefix = format!("{}.", d.qual);
                    seeds.push(sym(&f.module, &d.qual));
                    seeds.extend(f.defs.iter().filter(|m| m.qual.starts_with(&prefix)).map(|m| sym(&f.module, &m.qual)));
                }
                Some(d) => {
                    impact.modes.push("symbol".into());
                    let id = sym(&f.module, &d.qual);
                    if d.is_method {
                        // Callers typed as a base class may dispatch here.
                        let class = sym(&f.module, parent(&d.qual));
                        let name = d.qual.rsplit('.').next().unwrap_or(&d.qual);
                        let mut ancestors = self.bases(&class, 0);
                        let mut seen = HashSet::new();
                        while let Some(a) = ancestors.pop() {
                            if !seen.insert(a.clone()) {
                                continue;
                            }
                            if let Some(m) = self.members.get(&a).and_then(|ms| ms.get(name)) {
                                seeds.push(m.clone());
                            }
                            ancestors.extend(self.bases(&a, 0));
                        }
                    }
                    seeds.push(id);
                }
            }
        }
        let mut reached: HashSet<Sym> = seeds.iter().cloned().collect();
        let mut frontier = seeds;
        while let Some(node) = frontier.pop() {
            if let Some(users) = self.users.get(&node) {
                for user in users {
                    if reached.insert(user.clone()) {
                        frontier.push(user.clone());
                    }
                }
            }
        }
        impact.reached_symbols = reached.len();
        // Module-level code that uses a reached symbol runs at import time.
        for node in &reached {
            if let Some(module) = node.strip_suffix(':') {
                import_time.insert(module.to_string());
            }
        }
        let mut reached_modules: HashSet<String> =
            reached.iter().filter_map(|s| s.split_once(':').map(|(m, _)| m.to_string())).collect();
        impact.import_time = import_time.iter().cloned().collect();
        let mut import_affected: HashSet<String> = HashSet::new();
        for module in &import_time {
            import_affected.extend(self.importers(module));
        }
        reached_modules.extend(import_affected.iter().cloned());
        for (fi, f) in self.files.iter().enumerate() {
            if !Self::is_test_file(&f.path, test_roots) {
                continue;
            }
            if f.parse_errors || import_affected.contains(&f.module) {
                impact.add_file(&f.path, if f.parse_errors { "parse-error" } else { "import-time" });
                continue;
            }
            // Subprocess and path-loaded use: `python -m pkg.mod`, `-c "import pkg"`, `tools/x.py`.
            if let Some(s) = f.strings.iter().find(|s| mentions_module(s, &reached_modules)) {
                impact.add_file(&f.path, &format!("string: {}", s.chars().take(60).collect::<String>()));
            }
            let _ = fi;
        }
        for node in &reached {
            let Some((module, qual)) = node.split_once(':') else { continue };
            let Some(&fi) = self.by_module.get(module) else { continue };
            let f = &self.files[fi];
            if !Self::is_test_file(&f.path, test_roots) {
                continue;
            }
            let parts: Vec<&str> = qual.split('.').collect();
            let last = parts.last().copied().unwrap_or("");
            let in_test_class = parts.len() == 1 || parts[parts.len() - 2].starts_with("Test");
            if !qual.is_empty() && last.starts_with("test") && in_test_class {
                impact.tests.insert((f.path.clone(), parts.join("::")));
                continue;
            }
            // Helpers, fixtures (injected by name) and module-level use: the whole file,
            // files importing the helper, and a conftest's whole directory.
            impact.add_file(&f.path, &format!("helper: {qual}"));
            let top = parts.first().copied().unwrap_or("");
            for target in [format!("{module}.{top}"), module.to_string()] {
                for importer in self.imported_by.get(&target).into_iter().flatten() {
                    impact.add_file(&self.files[*importer].path, &format!("imports helper: {module}.{top}"));
                }
            }
            if f.path.ends_with("/conftest.py") || f.path == "conftest.py" {
                let dir = f.path.rsplit_once('/').map(|(d, _)| format!("{d}/")).unwrap_or_default();
                for other in &self.files {
                    if other.path.starts_with(&dir) && Self::is_test_file(&other.path, test_roots) {
                        impact.add_file(&other.path, &format!("conftest: {}", f.path));
                    }
                }
            }
        }
        impact
    }
}

/// Does a string literal name one of the modules (dotted name, `-m` target or script path)?
fn mentions_module(text: &str, modules: &HashSet<String>) -> bool {
    text.split(|c: char| !(c.is_alphanumeric() || c == '_' || c == '.' || c == '/'))
        .filter(|t| !t.is_empty())
        .any(|token| {
            // Every dotted suffix: `src/pkg/cli.py` -> pkg.cli, cli; `-m pkg.cli` -> pkg.cli.
            let dotted = token.trim_end_matches(".py").trim_start_matches("./").replace('/', ".");
            let mut rest = dotted.as_str();
            loop {
                if !rest.is_empty() && modules.contains(rest) {
                    return true;
                }
                match rest.split_once('.') {
                    Some((_, tail)) => rest = tail,
                    None => return false,
                }
            }
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::facts::extract;

    fn project(files: &[(&str, &str)]) -> Project {
        let roots = vec!["src".to_string(), "tests".to_string()];
        Project::build(files.iter().map(|(p, s)| extract(p, s.as_bytes(), &roots)).collect())
    }

    fn line_of(src: &str, needle: &str) -> u32 {
        src.lines().position(|l| l.contains(needle)).unwrap() as u32 + 1
    }

    const ENGINE: &str = "class Base:\n    def sync(self):\n        return 1\n\nclass Engine(Base):\n    \
        def _scan(cls):\n        return 2\n    @classmethod\n    def check(cls):\n        return cls._scan()\n    \
        def build(self):\n        return self.check()\n";
    const SERVICE: &str = "from pkg.engine import Engine\n\nclass Service:\n    def __init__(self):\n        \
        self.engine = Engine()\n    def run(self) -> Engine:\n        return self.engine.build()\n\n\
        def make() -> Service:\n    return Service()\n";
    const TESTS: &str = "import pytest\nfrom pkg.service import make, Service\n\n@pytest.fixture\ndef service():\n    \
        return Service()\n\ndef test_run():\n    make().run()\n\ndef test_other():\n    assert 1\n\n\
        def test_fixture(service):\n    assert service\n\nclass TestCli:\n    def test_cli(self):\n        \
        run(['python', '-m', 'pkg.cli'])\n";

    #[test]
    fn method_change_reaches_tests_through_self_cls_attributes_and_returns() {
        let p = project(&[
            ("src/pkg/__init__.py", ""),
            ("src/pkg/engine.py", ENGINE),
            ("src/pkg/service.py", SERVICE),
            ("src/pkg/cli.py", "from pkg.service import make\n\ndef main():\n    make().run()\n"),
            ("tests/test_service.py", TESTS),
        ]);
        let line = line_of(ENGINE, "return 2");
        let impact = p.impact(&[("src/pkg/engine.py".into(), line)], &["tests".into()]);
        assert_eq!(impact.modes, vec!["symbol"]);
        assert!(impact.tests.contains(&("tests/test_service.py".into(), "test_run".into())), "{:?}", impact.tests);
        assert!(!impact.tests.contains(&("tests/test_service.py".into(), "test_other".into())));
        // pkg.cli is reached and named in a subprocess argument: the whole file runs.
        assert!(impact.files.contains("tests/test_service.py"));
    }

    #[test]
    fn unrelated_change_selects_nothing_and_module_level_is_conservative() {
        let other = "def unused():\n    return 3\n\nVALUE = 4\n";
        let p = project(&[
            ("src/pkg/__init__.py", ""),
            ("src/pkg/other.py", other),
            ("src/pkg/engine.py", ENGINE),
            ("tests/test_engine.py", "from pkg.engine import Engine\n\ndef test_build():\n    Engine().build()\n"),
            ("tests/test_other.py", "import pkg.other\n\ndef test_value():\n    assert pkg.other.VALUE\n"),
        ]);
        let unused = p.impact(&[("src/pkg/other.py".into(), 2)], &["tests".into()]);
        assert!(unused.tests.is_empty() && unused.files.is_empty(), "{:?} {:?}", unused.tests, unused.files);
        let module_level = p.impact(&[("src/pkg/other.py".into(), 4)], &["tests".into()]);
        assert_eq!(module_level.modes, vec!["module"]);
        assert!(module_level.files.contains("tests/test_other.py"));
        assert!(!module_level.files.contains("tests/test_engine.py"));
    }

    #[test]
    fn base_method_change_reaches_subclass_callers_and_shared_fixtures() {
        let conftest = "import pytest\nfrom pkg.engine import Engine\n\n@pytest.fixture\ndef engine():\n    \
            return Engine().sync()\n";
        let p = project(&[
            ("src/pkg/__init__.py", ""),
            ("src/pkg/engine.py", ENGINE),
            ("tests/conftest.py", conftest),
            ("tests/test_uses_fixture.py", "def test_engine(engine):\n    assert engine\n"),
        ]);
        let line = line_of(ENGINE, "return 1");
        let impact = p.impact(&[("src/pkg/engine.py".into(), line)], &["tests".into()]);
        assert!(impact.files.contains("tests/test_uses_fixture.py"), "{:?}", impact.files);
    }

    #[test]
    fn strings_name_modules_and_scripts() {
        let modules: HashSet<String> = ["pkg.cli", "qualify"].iter().map(|s| s.to_string()).collect();
        assert!(mentions_module("-m pkg.cli", &modules));
        assert!(mentions_module("tools/qualify.py", &modules));
        assert!(!mentions_module("pkg.client", &modules));
    }
}
