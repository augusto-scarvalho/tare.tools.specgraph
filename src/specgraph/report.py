"""One-file offline visualization of a bounded, published source snapshot."""
from __future__ import annotations

import base64
import hashlib
import html
import json
from pathlib import Path
from typing import Any

from .storage import RepositoryIndex

_STYLE = """
:root{color-scheme:dark;font:15px system-ui;background:#111827;color:#e5e7eb}
body{margin:0}header,main{padding:1.1rem 1.5rem}header{border-bottom:1px solid #374151}
h1{margin:.1rem 0;font-size:1.6rem}p{line-height:1.5}small{color:#a7b0bf}
input,select,button{font:inherit;padding:.5rem;border:1px solid #526078;border-radius:.35rem;background:#1f2937;color:inherit}
button{cursor:pointer}button:hover{background:#334155}label{display:inline-block;margin:.3rem .6rem .3rem 0}
.grid{display:grid;grid-template-columns:minmax(220px,1fr) minmax(400px,3fr);gap:1rem}
#nodes{max-height:560px;overflow:auto;display:flex;flex-direction:column;gap:.25rem}
#nodes button{text-align:left;overflow-wrap:anywhere}svg{width:100%;height:450px;background:#182132;border-radius:.5rem}
svg text{font-size:10px;fill:#cbd5e1}svg circle{fill:#38bdf8;cursor:pointer}svg .selected{fill:#fb923c}
svg line{stroke:#596882;stroke-width:1.2}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0b1220;padding:1rem;border-radius:.5rem}
a{color:#7dd3fc}#notice{color:#fbbf24}#result{min-height:2rem}section{margin:1rem 0}
@media(max-width:800px){.grid{grid-template-columns:1fr}#nodes{max-height:200px}}
"""

_SCRIPT = r"""
'use strict';
const data=JSON.parse(document.getElementById('snapshot').textContent);
const byId=new Map(data.nodes.map(n=>[n.id,n]));
const el=id=>document.getElementById(id), ns='http://www.w3.org/2000/svg';
let selected=null;
function option(select,value){const o=document.createElement('option');o.value=value;o.textContent=value;select.appendChild(o);}
for(const k of [...new Set(data.nodes.map(n=>n.kind))].sort())option(el('kind'),k);
for(const k of [...new Set(data.nodes.map(n=>n.language))].sort())option(el('language'),k);
for(const n of data.nodes){option(el('from'),n.id);option(el('to'),n.id);}
el('generation').textContent='Revision '+data.revision_seq+' · '+data.content_hash+' · '+data.root;
el('notice').textContent='Published snapshot; current files are not checked. '+data.nodes.length+' nodes, '+data.edges.length+' relations. '+JSON.stringify(data.omitted);
el('coverage').textContent=JSON.stringify({coverage:data.coverage,freshness:data.freshness,limits:data.limits},null,2);
function choose(id){selected=id;const n=byId.get(id);if(!n)return;
 el('selection').textContent=n.id+' · '+n.file_path+':'+n.start_line+'-'+n.end_line;
 el('source').textContent=n.source_snippet===null?'Source range omitted or unavailable in this export.':n.source_snippet;
 el('facts').textContent=JSON.stringify({language:n.language,kind:n.kind,sha256:n.sha256,linked_specs:n.linked_specs,references:n.references},null,2);
 el('from').value=id;draw();}
function visible(){const q=el('filter').value.toLocaleLowerCase(),k=el('kind').value,l=el('language').value;
 return data.nodes.filter(n=>(!q||n.id.toLocaleLowerCase().includes(q))&&(!k||k===n.kind)&&(!l||l===n.language));}
function draw(){const all=visible();el('nodes').replaceChildren();for(const n of all){const b=document.createElement('button');b.textContent=n.name+' ['+n.language+']';b.onclick=()=>choose(n.id);el('nodes').appendChild(b);}
 const shown=all.slice(0,120), ids=new Set(shown.map(n=>n.id)),svg=el('map');svg.replaceChildren();svg.setAttribute('viewBox','0 0 900 450');
 const positions=new Map(shown.map((n,i)=>{const a=2*Math.PI*i/Math.max(shown.length,1);return[n.id,[450+350*Math.cos(a),225+180*Math.sin(a)]];}));
 for(const e of data.edges){if(!ids.has(e.from)||!ids.has(e.to))continue;const a=positions.get(e.from),b=positions.get(e.to),line=document.createElementNS(ns,'line');for(const[k,v]of Object.entries({x1:a[0],y1:a[1],x2:b[0],y2:b[1]}))line.setAttribute(k,v);const title=document.createElementNS(ns,'title');title.textContent=e.from+' → '+e.to+' ('+e.type+')';line.appendChild(title);svg.appendChild(line);}
 for(const n of shown){const p=positions.get(n.id),g=document.createElementNS(ns,'g'),c=document.createElementNS(ns,'circle');c.setAttribute('cx',p[0]);c.setAttribute('cy',p[1]);c.setAttribute('r',selected===n.id?8:5);if(selected===n.id)c.setAttribute('class','selected');c.onclick=()=>choose(n.id);const title=document.createElementNS(ns,'title');title.textContent=n.id;c.appendChild(title);g.appendChild(c);const t=document.createElementNS(ns,'text');t.setAttribute('x',p[0]+7);t.setAttribute('y',p[1]);t.textContent=n.name.slice(-30);g.appendChild(t);svg.appendChild(g);}
 el('map-count').textContent=shown.length+' of '+all.length+' filtered nodes drawn (drawing cap 120; queries use full exported subset).';}
function traverse(start,target,reverse){const queue=[[start,[start]]],seen=new Set([start]),paths=[];
 while(queue.length){const [id,path]=queue.shift();if(id===target)return path;if(path.length-1>=data.limits.max_hops)continue;
  for(const e of data.edges){const a=reverse?e.to:e.from,b=reverse?e.from:e.to;if(a!==id||seen.has(b))continue;seen.add(b);const next=path.concat([b]);queue.push([b,next]);paths.push(next);}}
 return target?null:paths;}
el('path').onclick=()=>{const p=traverse(el('from').value,el('to').value,false);el('result').textContent=p?p.join(' → '):'No directed path found in this exported subset and hop budget; absence is not universal.';};
el('impact').onclick=()=>{const paths=traverse(el('from').value,null,true);el('result').textContent=paths.length?paths.map(p=>p.join(' ← ')).join('\n'):'No upstream indexed relations in this subset. This is not a runtime impact proof.';};
for(const id of ['filter','kind','language'])el(id).addEventListener('input',draw);
draw();if(data.nodes.length)choose(data.nodes[0].id);
"""


def _html(data: dict[str, Any]) -> str:
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    encoded = encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    script_hash = base64.b64encode(hashlib.sha256(_SCRIPT.encode("utf-8")).digest()).decode("ascii")
    title = html.escape(Path(data["root"]).name)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>SpecGraph · {title}</title><style>{_STYLE}</style></head><body>
<header><h1>SpecGraph · {title}</h1><small id="generation"></small><p id="notice"></p></header>
<main><label>Find <input id="filter" type="search" placeholder="Symbol or path"></label>
<label>Kind <select id="kind"><option value="">All</option></select></label>
<label>Language <select id="language"><option value="">All</option></select></label>
<div class="grid"><nav id="nodes" aria-label="Indexed symbols"></nav><div><svg id="map" role="img" aria-label="Bounded project graph"></svg><small id="map-count"></small></div></div>
<section><h2>Directed paths and upstream relations</h2><label>From <select id="from"></select></label><label>To <select id="to"></select></label>
<button id="path">Find path →</button> <button id="impact">Upstream / impact ←</button><pre id="result"></pre>
<small>Edges retain their indexed type and direction. Static dependencies and containment do not prove runtime behavior.</small></section>
<section><h2 id="selection">Source snapshot</h2><a href="#source">Open selected source range</a><pre id="source"></pre><details><summary>Symbol facts</summary><pre id="facts"></pre></details></section>
<details><summary>Coverage, freshness and export limits</summary><pre id="coverage"></pre></details></main>
<script type="application/json" id="snapshot">{encoded}</script><script>{_SCRIPT}</script></body></html>
'''


def report_html(root: Path | str, *, state_dir: Path | str | None = None, max_nodes: int = 200,
                max_edges: int = 800, max_hops: int = 6, max_bytes: int = 2_000_000) -> str:
    """Export a bounded induced code subgraph with complete source units.

    Source units may be omitted whole to meet the byte budget, then nodes are
    removed whole if necessary. Metadata and the executable UI are mandatory.

@spec SPEC-SPECGRAPH-C6
    """
    if not 1 <= max_nodes <= 1000 or not 1 <= max_edges <= 5000 or not 0 <= max_hops <= 10:
        raise ValueError("max_nodes must be 1..1000, max_edges 1..5000, max_hops 0..10")
    if not 8192 <= max_bytes <= 10_000_000:
        raise ValueError("max_bytes must be 8192..10000000")
    with RepositoryIndex(root, state_dir=state_dir) as index:
        metadata = index.metadata()
        rows = index.connection.execute("SELECT payload FROM symbols ORDER BY symbol_id LIMIT ?", (max_nodes + 1,)).fetchall()
        nodes, omitted, sources = [], [], {}
        if len(rows) > max_nodes:
            omitted.append({"reason": "max_nodes", "count": "AT_LEAST_ONE"})
        for raw, in rows[:max_nodes]:
            symbol = json.loads(raw)
            path = symbol["file_path"]
            if path not in sources:
                row = index.connection.execute("SELECT body, sha256 FROM sources WHERE file_path=?", (path,)).fetchone() if metadata.get("capabilities", {}).get("source_ranges") else None
                sources[path] = row
            row = sources[path]
            snippet = None
            if row:
                lines = row[0].splitlines(keepends=True)
                if 1 <= symbol["start_line"] <= symbol["end_line"] <= len(lines):
                    snippet = "".join(lines[symbol["start_line"] - 1:symbol["end_line"]])
            nodes.append({"id": symbol["symbol_id"], "name": symbol["name"], "kind": symbol["kind"],
                          "language": symbol.get("language", "python"), "file_path": path,
                          "start_line": symbol["start_line"], "end_line": symbol["end_line"],
                          "linked_specs": symbol["linked_specs"], "references": symbol.get("references", []),
                          "sha256": row[1] if row else None, "source_snippet": snippet})
        ids = {node["id"] for node in nodes}
        edges = []
        # Bound traversal by selected source IDs and edge count; no full graph
        # JSON is deserialized, and external nodes remain outside this export.
        for node_id in sorted(ids):
            remaining = max_edges - len(edges)
            candidates = index.connection.execute("SELECT payload FROM edges WHERE source=? ORDER BY target, relation LIMIT ?",
                                                  (node_id, remaining + 1)).fetchall()
            if len(candidates) > remaining and not any(item["reason"] == "edge_candidate_budget" for item in omitted):
                omitted.append({"reason": "edge_candidate_budget", "count": "UNKNOWN"})
            for raw, in candidates:
                edge = json.loads(raw)
                if edge["to"] in ids:
                    edges.append(edge)
            if len(edges) > max_edges:
                omitted.append({"reason": "max_edges", "count": "AT_LEAST_ONE"})
                edges = edges[:max_edges]
                break
        data = {"schema": "tare.tools/specgraph-offline/1", "root": metadata["repository_root"],
                "revision_seq": metadata["revision_seq"], "content_hash": metadata["content_hash"],
                "coverage": metadata["coverage"], "freshness": metadata["freshness"],
                "nodes": nodes, "edges": edges, "omitted": omitted,
                "limits": {"max_nodes": max_nodes, "max_edges": max_edges, "max_hops": max_hops,
                           "max_bytes": max_bytes, "scope": "exported induced code subgraph"}}
        output = _html(data)
        dropped_sources = 0
        for node in reversed(nodes):
            if len(output.encode("utf-8")) <= max_bytes:
                return output
            if node["source_snippet"] is not None:
                node["source_snippet"] = None
                dropped_sources += 1
                data["omitted"] = omitted + [{"reason": "source_max_bytes", "count": dropped_sources, "unit": "complete_source_range"}]
                output = _html(data)
        dropped_nodes = 0
        while len(output.encode("utf-8")) > max_bytes and nodes:
            removed = nodes.pop()["id"]
            data["edges"] = [edge for edge in data["edges"] if edge["from"] != removed and edge["to"] != removed]
            dropped_nodes += 1
            data["omitted"] = omitted + [{"reason": "max_bytes", "count": dropped_nodes, "unit": "complete_node"},
                                          {"reason": "source_max_bytes", "count": dropped_sources, "unit": "complete_source_range"}]
            output = _html(data)
        if len(output.encode("utf-8")) > max_bytes:
            raise ValueError("Required offline UI and snapshot metadata exceed max_bytes; increase the budget")
        return output
