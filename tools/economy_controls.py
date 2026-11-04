"""Hidden positive and intentionally incomplete repairs, frozen before inference."""
from economy_oracles import TASKS


def variants(source, task):
    if task == TASKS[0]:
        old = 'data = yaml.safe_load(raw_text) or {}'
        good = source.replace(old, 'data = yaml.safe_load(raw_text)\n                if data is None:\n                    data = {}')
        mutant = source.replace(old, 'data = yaml.safe_load(raw_text)\n                if not data:\n                    data = {}')
    elif task == TASKS[1]:
        old = 'if any(ex in rel.split("/") for ex in excluded_patterns):'
        good = source.replace(old, 'from .config import SpecGraphConfig\n                if SpecGraphConfig.is_path_excluded(rel, excluded_patterns):')
        mutant = source.replace(old, 'if any(ex in rel for ex in excluded_patterns):')
    else:
        old = '    if not 1 <= max_nodes <= 1000 or not 1 <= max_edges <= 5000 or not 0 <= max_hops <= 10:'
        prefix = '    if any(type(value) is not int for value in (max_nodes, max_edges, max_hops, max_bytes)):\n        raise ValueError("report budgets must be integers")\n'
        good = source.replace(old, prefix + old)
        mutant = source.replace(old, prefix.replace('type(value) is not int', 'not isinstance(value, int)') + old)
    if good == source or mutant == source:
        raise ValueError('control anchor absent')
    return {'reference': good, 'incomplete': mutant}
