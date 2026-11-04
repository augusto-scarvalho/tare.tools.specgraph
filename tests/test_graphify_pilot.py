import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
from pilot_graphify import hydrate
from qualify_context_pilot import schedule


def test_three_arm_pilot_has_every_cell_and_alternates_hosts():
    tasks = [{'id': 'cfg'}, {'id': 'exc'}]; hosts = [{'id': 'q'}, {'id': 'o'}]
    rows = schedule(tasks, hosts, 1, 29, 'interleaved-hosts', ['full', 'focused', 'graphify'])
    assert len(rows) == 12
    assert [r['host'] for r in rows] == ['q', 'o'] * 6
    for t in tasks:
        for h in hosts:
            assert {r['arm'] for r in rows if r['task'] == t['id'] and r['host'] == h['id']} == {'full', 'focused', 'graphify'}
    with pytest.raises(ValueError):
        schedule(tasks, hosts, 1, 29)


def test_graphify_witness_hydrates_member_without_unselected_class_body(tmp_path):
    (tmp_path / 'a.py').write_text('import math\n\nclass Box:\n    SCALE = 2\n    @staticmethod\n    def target(x):\n        return x * Box.SCALE\n\n    def unrelated(self):\n        return 999\n')
    rows = [{'file': 'a.py', 'name': 'Box', 'start': 3}, {'file': 'a.py', 'name': 'target', 'start': 6}]
    packet, metrics = hydrate(tmp_path, rows, ['a.py'], max_bytes=4096, primary_found=True)
    assert b'import math' in packet and b'class Box:' in packet and b'SCALE = 2' in packet
    assert b'@staticmethod' in packet and b'def target' in packet and b'unrelated' not in packet
    assert metrics['fallback_deliveries'] == 0


def test_graphify_miss_is_full_fallback_and_overflow_is_explicit(tmp_path):
    (tmp_path / 'a.py').write_text('def target():\n    return 19\n')
    packet, metrics = hydrate(tmp_path, [], ['a.py'], max_bytes=4096, primary_found=False)
    assert b'return 19' in packet and metrics['fallback_deliveries'] == 1
    with pytest.raises(ValueError, match='byte budget'):
        hydrate(tmp_path, [], ['a.py'], max_bytes=8, primary_found=False)


def test_graphify_cannot_hydrate_outside_the_trial(tmp_path):
    with pytest.raises(ValueError):
        hydrate(tmp_path, [{'file': '../hidden.py', 'name': 'answer', 'start': 1}], [], max_bytes=4096, primary_found=True)


def test_large_graphify_neighborhood_falls_back_without_losing_attempt_cost(tmp_path):
    (tmp_path / 'a.py').write_text('def target():\n    return 19\n')
    (tmp_path / 'b.py').write_text('def broad():\n' + '    x = 1\n' * 100)
    rows = [{'file': 'a.py', 'name': 'target', 'start': 1}, {'file': 'b.py', 'name': 'broad', 'start': 1}]
    packet, metrics = hydrate(tmp_path, rows, ['a.py'], max_bytes=500, primary_found=True)
    assert b'def target' in packet and b'def broad' not in packet
    assert metrics['fallback_reason'] == 'packet_byte_limit'
    assert metrics['packet_source_reads'] == 2 and metrics['attempted_packet_bytes'] > 500
