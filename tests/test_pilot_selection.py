from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from pilot_workspace import context_packet, full_packet


def project(tmp_path, files):
    root=tmp_path/'repo';root.mkdir()
    (root/'specgraph.yaml').write_text('project_name: fixture\nindexed_paths: [src]\npython_source_roots: [src]\ngoverned_paths: []\n')
    for name,body in files.items():
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(body)
    return root


def test_outgoing_dependency_outside_manifest_does_not_pull_in_callers(tmp_path):
    root=project(tmp_path,{
        'src/main.py':'from helpers import leaf\n\ndef target(value):\n    return leaf(str(value))\n\ndef unrelated():\n    return 9\n',
        'src/helpers.py':'def leaf(value):\n    return value\n\ndef expensive_unrelated():\n    return 42\n',
        'src/callers.py':'from main import target\n\ndef consumer():\n    return target(1)\n'})
    packet,metrics,traces=context_packet(root,tmp_path/'index','target',['src/main.py'])
    assert b'def target' in packet and b'def leaf' in packet
    assert b'def consumer' not in packet and b'def expensive_unrelated' not in packet and b'def unrelated' not in packet
    assert metrics['fallback_deliveries']==0
    assert {r['file_path'] for r in metrics['delivered_sources']}=={'src/main.py','src/helpers.py'}
    assert any(n['kind']=='static_resolution_boundary' for n in metrics['coverage_notes'])
    assert metrics['dependency_completeness']=='NOT_CLAIMED'
    assert metrics['source_verification_reads']==metrics['packet_source_reads']==2
    assert b'SHA256:' in packet and b'SHA256:' in full_packet(root,['src/main.py'])[0]


def test_retrieval_ranking_limit_is_not_a_missing_dependency(tmp_path):
    root=project(tmp_path,{'src/main.py':'def target():\n    return 1\n\ndef other():\n    return 2\n'})
    import specgraph.query
    original=specgraph.query.project_query
    def limited(*args,**kwargs):
        result=original(*args,**kwargs)
        if kwargs['mode']=='search':result['omitted'].append({'reason':'limit','count':'AT_LEAST_ONE'})
        return result
    from unittest.mock import patch
    with patch.object(specgraph.query,'project_query',limited):
        packet,metrics,_=context_packet(root,tmp_path/'index','target',['src/main.py'])
    assert b'def other' not in packet and metrics['fallback_deliveries']==0
    assert metrics['coverage_notes'][0]['kind']=='retrieval_boundary'


def test_selected_method_keeps_class_header_decorators_and_data(tmp_path):
    root=project(tmp_path,{'src/main.py':'class Service:\n    SCALE = 2\n\n    @staticmethod\n    def target(x):\n        return x * Service.SCALE\n\n    def irrelevant(self):\n        return 17\n'})
    packet,metrics,_=context_packet(root,tmp_path/'index','Service.target',['src/main.py'])
    assert b'class Service:' in packet and b'@staticmethod' in packet and b'SCALE = 2' in packet
    # The graph may legitimately include the referenced Service class, in which
    # case all of its methods are part of the selected definition, not fallback.
    assert metrics['fallback_deliveries']==0


def test_missing_outgoing_neighbors_only_fall_back_the_affected_file(tmp_path):
    root=project(tmp_path,{'src/main.py':'def a():\n    return 1\ndef b():\n    return 2\ndef target():\n    return a()+b()\ndef spare():\n    return 3\n',
        'src/other.py':'def unrelated():\n    return 987\n'})
    packet,metrics,_=context_packet(root,tmp_path/'index','target',['src/main.py','src/other.py'],limit=1)
    assert metrics['fallback_deliveries']==1 and metrics['fallback_file_count']==1
    assert metrics['fallback_reason']=='outgoing_dependency_limit'
    assert b'def spare' in packet and b'def unrelated' not in packet


def test_source_change_after_query_aborts_instead_of_claiming_snapshot(tmp_path,monkeypatch):
    root=project(tmp_path,{'src/main.py':'def target():\n    return 1\n'})
    import specgraph.query
    original=specgraph.query.project_query
    def changed(*args,**kwargs):
        result=original(*args,**kwargs)
        if kwargs['mode']=='context':(root/'src/main.py').write_text('def target():\n    return 2\n')
        return result
    monkeypatch.setattr(specgraph.query,'project_query',changed)
    with pytest.raises(ValueError,match='differs from published'):
        context_packet(root,tmp_path/'index','target',['src/main.py'])


def test_fallback_cannot_silently_exceed_the_packet_budget(tmp_path):
    root=project(tmp_path,{'src/main.py':'def target():\n    return 1\n'})
    with pytest.raises(ValueError,match='exceeds packet byte budget'):
        context_packet(root,tmp_path/'index','missing',['src/main.py'],max_bytes=20)


def test_class_selection_inspects_dependencies_of_its_included_methods(tmp_path):
    root=project(tmp_path,{
        'src/main.py':'from helpers import leaf\n\nclass Service:\n    def work(self):\n        return leaf()\n',
        'src/helpers.py':'def leaf():\n    return 1\n\ndef unrelated():\n    return 7\n'})
    packet,metrics,_=context_packet(root,tmp_path/'index','Service',['src/main.py'])
    assert b'def work' in packet and b'def leaf' in packet and b'def unrelated' not in packet
    assert metrics['query_api_calls']==3


def test_matching_method_avoids_unneeded_container_and_merges_only_blank_gaps(tmp_path):
    root=project(tmp_path,{'src/main.py':'class Service:\n    """Service target operations."""\n    SCALE = 2\n\n    def target(self):\n        return 1\n\n    def unrelated(self):\n        return 7\n'})
    packet,metrics,_=context_packet(root,tmp_path/'index','Service target',['src/main.py'])
    assert b'def target' in packet and b'def unrelated' not in packet
    assert b'SCALE = 2' in packet and packet.count(b'File:') == 1
    assert any(n['kind']=='matched_member_preferred_to_container' for n in metrics['coverage_notes'])


def test_canonical_preparation_index_is_reused_by_the_public_resolver(tmp_path, monkeypatch):
    monkeypatch.setenv('TARE_SPECGRAPH_STATE_DIR', str(tmp_path/'native-state'))
    root=project(tmp_path, {'src/main.py':'def target():\n    return 1\n'})
    packet, metrics, _=context_packet(root,None,'target',['src/main.py'])
    from specgraph.resolve import resolve_repository
    resolved=resolve_repository(root,'target').to_dict()
    assert resolved['source']=='specgraph' and resolved['status']=='HIT'
    assert resolved['snapshot']['content_hash']==metrics['snapshot']
    assert metrics['canonical_host_state'] and Path(metrics['published_index_path']).is_file()
    with pytest.raises(ValueError,match='must start cold'):
        context_packet(root,None,'target',['src/main.py'])


def test_success_criteria_participate_in_selection(tmp_path):
    root=project(tmp_path, {'src/main.py':'def target():\n    return 1\n',
        'src/helper.py':'def canonical_helper():\n    return 2\n'})
    task={'description':'target', 'success_criteria':'Use canonical_helper.'}
    packet,_,_=context_packet(root,tmp_path/'index',task['description'],['src/main.py','src/helper.py'],success_criteria=task['success_criteria'])
    assert b'def canonical_helper' in packet and b'def target' in packet


def test_a_criteria_hit_cannot_hide_failure_to_locate_the_implementation_target(tmp_path):
    root=project(tmp_path, {'src/main.py':'def target():\n    return 1\n',
        'src/helper.py':'def canonical_helper():\n    return 2\n'})
    packet,metrics,_=context_packet(root,tmp_path/'index','unmatched objective',['src/main.py'],success_criteria='Use canonical_helper.')
    assert b'def target' in packet and b'def canonical_helper' in packet
    assert metrics['fallback_reason']=='retrieval_miss'


def test_state_override_is_rejected_during_preflight_before_any_trial(tmp_path,monkeypatch):
    import json
    from qualify_context_pilot import prepare, SCHEMA
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'schema':SCHEMA}))
    monkeypatch.delenv('TARE_EXPERIMENT_RUN_DIR', raising=False)
    monkeypatch.setenv('TARE_SPECGRAPH_STATE_DIR',str(tmp_path/'override'))
    with pytest.raises(ValueError,match='pilot state override requires a managed experiment lot'):
        prepare(manifest,tmp_path/'output')
    assert not (tmp_path/'output').exists()
