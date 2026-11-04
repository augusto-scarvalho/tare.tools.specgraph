//! specgraph-core: native symbol extraction, binding and test impact for SpecGraph.
//!
//! ```text
//! specgraph-core impact --root R [--source-root src]... [--test-root tests]...
//!                       [--cache DIR] --changed path:line [--changed ...]
//! specgraph-core facts  --root R [--source-root src]... path
//! ```
//! Output is JSON on stdout. Facts are cached per file by (size, mtime); only changed
//! files are parsed again, in parallel.

mod facts;
mod graph;

use facts::Facts;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::time::{Instant, UNIX_EPOCH};

const SKIP_DIRS: &[&str] = &["__pycache__", ".git", ".venv", "venv", "node_modules", "build", "dist", ".specgraph"];
const CACHE_VERSION: u32 = 2;

struct Options {
    root: PathBuf,
    source_roots: Vec<String>,
    test_roots: Vec<String>,
    cache: Option<PathBuf>,
    changed: Vec<(String, u32)>,
    rest: Vec<String>,
}

fn parse_args(args: &[String]) -> Result<Options, String> {
    let mut o = Options {
        root: PathBuf::from("."),
        source_roots: Vec::new(),
        test_roots: Vec::new(),
        cache: None,
        changed: Vec::new(),
        rest: Vec::new(),
    };
    let mut i = 0;
    while i < args.len() {
        let value = || args.get(i + 1).cloned().ok_or(format!("{} needs a value", args[i]));
        match args[i].as_str() {
            "--root" => o.root = PathBuf::from(value()?),
            "--source-root" => o.source_roots.push(value()?),
            "--test-root" => o.test_roots.push(value()?),
            "--cache" => o.cache = Some(PathBuf::from(value()?)),
            "--changed" => {
                let v = value()?;
                let (path, line) = v.rsplit_once(':').ok_or(format!("--changed wants path:line, got {v}"))?;
                let line = line.parse().map_err(|_| format!("bad line in {v}"))?;
                o.changed.push((path.replace('\\', "/"), line));
            }
            other if other.starts_with("--") => return Err(format!("unknown option {other}")),
            other => {
                o.rest.push(other.to_string());
                i += 1;
                continue;
            }
        }
        i += 2;
    }
    if o.source_roots.is_empty() {
        o.source_roots = vec!["src".into()];
    }
    if o.test_roots.is_empty() {
        o.test_roots = vec!["tests".into()];
    }
    Ok(o)
}

/// Python files under the roots: Git's view of the project when available (tracked and
/// untracked, honoring .gitignore), otherwise a directory walk.
fn discover(root: &Path, folders: &[String]) -> Vec<String> {
    let mut args = vec!["ls-files".to_string(), "-co".into(), "--exclude-standard".into(), "--".into()];
    for folder in folders {
        let folder = folder.trim_end_matches('/');
        args.push(if folder == "." || folder.is_empty() { "*.py".into() } else { format!("{folder}/*.py") });
    }
    if let Ok(out) = std::process::Command::new("git").args(&args).current_dir(root).output() {
        if out.status.success() {
            let mut found: Vec<String> = String::from_utf8_lossy(&out.stdout)
                .lines()
                .map(|l| l.trim().replace('\\', "/"))
                .filter(|l| l.ends_with(".py") && !l.split('/').any(|p| SKIP_DIRS.contains(&p)) && root.join(l).is_file())
                .collect();
            found.sort();
            found.dedup();
            return found;
        }
    }
    walk(root, folders)
}

fn walk(root: &Path, folders: &[String]) -> Vec<String> {
    let mut found = Vec::new();
    let mut stack: Vec<PathBuf> = folders.iter().map(|f| root.join(f)).filter(|p| p.is_dir()).collect();
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().to_string();
            if path.is_dir() {
                if !SKIP_DIRS.contains(&name.as_str()) {
                    stack.push(path);
                }
            } else if name.ends_with(".py") {
                if let Ok(rel) = path.strip_prefix(root) {
                    found.push(rel.to_string_lossy().replace('\\', "/"));
                }
            }
        }
    }
    found.sort();
    found.dedup();
    found
}

#[derive(Serialize, Deserialize)]
struct CacheEntry {
    version: u32,
    roots: Vec<String>,
    size: u64,
    mtime_ns: u128,
    facts: Facts,
}

fn stamp(path: &Path) -> Option<(u64, u128)> {
    let meta = std::fs::metadata(path).ok()?;
    let mtime = meta.modified().ok()?.duration_since(UNIX_EPOCH).ok()?.as_nanos();
    Some((meta.len(), mtime))
}

fn cache_file(cache: &Path, rel: &str) -> PathBuf {
    let mut h = DefaultHasher::new();
    rel.hash(&mut h);
    cache.join(format!("{:016x}.json", h.finish()))
}

/// Facts for every file, reusing cached ones whose (size, mtime) still match.
fn load_facts(o: &Options, roots: &[String], files: &[String]) -> (Vec<Facts>, usize) {
    if let Some(cache) = &o.cache {
        let _ = std::fs::create_dir_all(cache);
    }
    let results: Vec<(Facts, bool)> = files
        .par_iter()
        .map(|rel| {
            let abs = o.root.join(rel);
            let st = stamp(&abs);
            if let (Some(cache), Some((size, mtime))) = (&o.cache, st) {
                let entry = std::fs::read(cache_file(cache, rel))
                    .ok()
                    .and_then(|b| serde_json::from_slice::<CacheEntry>(&b).ok());
                if let Some(e) = entry {
                    if e.version == CACHE_VERSION && e.size == size && e.mtime_ns == mtime && e.roots == roots {
                        return (e.facts, true);
                    }
                }
            }
            let source = std::fs::read(&abs).unwrap_or_default();
            let facts = facts::extract(rel, &source, roots);
            if let (Some(cache), Some((size, mtime))) = (&o.cache, st) {
                let entry = CacheEntry { version: CACHE_VERSION, roots: roots.to_vec(), size, mtime_ns: mtime, facts };
                if let Ok(bytes) = serde_json::to_vec(&entry) {
                    let target = cache_file(cache, rel);
                    let tmp = target.with_extension("tmp");
                    if std::fs::write(&tmp, bytes).is_ok() {
                        let _ = std::fs::rename(&tmp, &target);
                    }
                }
                return (entry.facts, false);
            }
            (facts, false)
        })
        .collect();
    let reused = results.iter().filter(|(_, hit)| *hit).count();
    (results.into_iter().map(|(f, _)| f).collect(), reused)
}

fn run(args: &[String]) -> Result<serde_json::Value, String> {
    let command = args.first().ok_or("usage: specgraph-core impact|facts ...")?.clone();
    let o = parse_args(&args[1..])?;
    // Test modules import each other by bare name (pytest puts their directory on sys.path).
    let roots: Vec<String> = o.source_roots.iter().chain(o.test_roots.iter()).cloned().collect();
    match command.as_str() {
        "facts" => {
            let path = o.rest.first().ok_or("facts needs a path")?;
            let source = std::fs::read(o.root.join(path)).map_err(|e| e.to_string())?;
            serde_json::to_value(facts::extract(path, &source, &roots)).map_err(|e| e.to_string())
        }
        "impact" => {
            let t0 = Instant::now();
            let files = discover(&o.root, &roots);
            let t1 = Instant::now();
            let (facts, reused) = load_facts(&o, &roots, &files);
            let t2 = Instant::now();
            let project = graph::Project::build(facts);
            let t3 = Instant::now();
            let impact = project.impact(&o.changed, &o.test_roots);
            let t4 = Instant::now();
            let ms = |a: Instant, b: Instant| (b - a).as_secs_f64() * 1000.0;
            Ok(serde_json::json!({
                "engine": concat!("specgraph-core ", env!("CARGO_PKG_VERSION")),
                "files": files.len(),
                "reused_facts": reused,
                "edges": project.edges,
                "unresolved_attributes": project.unresolved_attributes,
                "modes": impact.modes,
                "full_suite": impact.full_suite,
                "reached_symbols": impact.reached_symbols,
                "tests": impact.tests.iter().map(|(p, t)| format!("{p}::{t}")).collect::<Vec<_>>(),
                "test_files": impact.files,
                "file_reasons": impact.reasons,
                "import_time": impact.import_time,
                "timings_ms": {
                    "discover": ms(t0, t1), "facts": ms(t1, t2), "bind": ms(t2, t3), "impact": ms(t3, t4),
                    "total": ms(t0, t4)
                }
            }))
        }
        other => Err(format!("unknown command {other}")),
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(value) => println!("{value}"),
        Err(message) => {
            eprintln!("specgraph-core: {message}");
            std::process::exit(2);
        }
    }
}
