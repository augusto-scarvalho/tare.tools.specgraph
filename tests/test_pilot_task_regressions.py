from pathlib import Path

import pytest

from specgraph.config import SpecGraphConfig
from specgraph.test_parser import TestParser


@pytest.mark.parametrize('document',['false','0','0.0','[]','""',"''",'!!set {}'])
def test_falsy_non_mapping_configuration_roots_are_invalid(tmp_path,document):
    path=tmp_path/'specgraph.yaml';path.write_text(document)
    config=SpecGraphConfig.load(tmp_path)
    assert len(config.config_errors)==1 and 'Invalid configuration root' in config.config_errors[0]
    assert path.read_text()==document


@pytest.mark.parametrize('document',['','# empty\n','null','~','{}'])
def test_empty_null_and_mapping_configuration_defaults_remain_valid(tmp_path,document):
    (tmp_path/'.specgraph.yaml').write_text(document)
    assert SpecGraphConfig.load(tmp_path)==SpecGraphConfig()


@pytest.mark.parametrize('rules,expected',[
    (['tests/slow/test_case.py'],['tests/slowly/test_case.py']),
    (['tests/slow'],['tests/slowly/test_case.py']),
    (['tests\\slow'],['tests/slowly/test_case.py']),
    (['/tests/slow/'],['tests/slowly/test_case.py']),
    (['slow'],['tests/slowly/test_case.py']),
    (['test_case.py'],[]),
    (['','/','tests/*.py'],['tests/slow/test_case.py','tests/slowly/test_case.py']),
])
def test_discovery_honors_canonical_exclusion_boundaries(tmp_path,rules,expected):
    for directory in ['slow','slowly']:
        path=tmp_path/'tests'/directory/'test_case.py';path.parent.mkdir(parents=True)
        path.write_text('async def test_case():\n    pass\n')
    tests,errors=TestParser.discover_tests(['tests'],rules,tmp_path)
    assert not errors and [test.file_path for test in tests]==expected


def test_excluded_invalid_utf8_file_is_not_parsed_even_as_an_explicit_test_root(tmp_path):
    path=tmp_path/'tests/invalid/test_bad.py';path.parent.mkdir(parents=True);path.write_bytes(b'\xff')
    assert TestParser.discover_tests(['tests'],['tests/invalid'],tmp_path)==([],[])
    assert TestParser.discover_tests(['tests/invalid/test_bad.py'],['tests/invalid'],tmp_path)==([],[])
    tests,errors=TestParser.discover_tests(['tests'],[],tmp_path)
    assert not tests and len(errors)==1 and 'UTF-8 Decoding Error' in errors[0]
