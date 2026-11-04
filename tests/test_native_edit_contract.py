import pytest
from tools.native_edit_contract import parse_edit_block, observe_edit_response, native_decision
from tools.qualify_context_selection import checked_replacement

SOURCE='def target():\n    value = "\\n"\n    return value\n'
TASK={'path':'a.py','target_range':[1,3]}
TEXT='a.py\n```python\n<<<<<<< SEARCH\n    value = "\\n"\n=======\n    value = "\\t"\n>>>>>>> REPLACE\n```'


def test_native_code_preserves_backslash_literals_without_json_decoding():
    proposal=parse_edit_block(TEXT)
    assert checked_replacement(SOURCE.encode(),TASK,proposal)==SOURCE.replace('"\\n"','"\\t"').encode()


@pytest.mark.parametrize('text',[TEXT+'\nprose',TEXT.replace('>>>>>>> REPLACE',''),TEXT+'\n'+TEXT,TEXT.replace('=======','=======\n======='),TEXT.replace('<<<<<<< SEARCH','<<<<<<< SEARCH\n=======')])
def test_ambiguous_partial_and_extra_output_is_refused(text):
    with pytest.raises(ValueError):parse_edit_block(text)


def test_source_path_and_unique_substring_still_gate_native_edit():
    for text in [TEXT.replace('a.py','../other.py'),TEXT.replace('    value = "\\n"','    value = "different"')]:
        with pytest.raises(ValueError):checked_replacement(SOURCE.encode(),TASK,parse_edit_block(text))


def test_a_complete_looking_block_with_length_finish_is_not_accepted():
    response={'choices':[{'finish_reason':'length','message':{'content':TEXT}}]}
    assert observe_edit_response(response)['answer'] is None
    response['choices'][0]['finish_reason']='stop'
    assert observe_edit_response(response)['response_status']=='EDIT_PROPOSAL'


def test_qualification_requires_four_correct_distinct_cells():
    rows=[{'round':n,'task':task,'response_profile':'native-edit','success':True,'calls':[{'usage':{'total_tokens':100}}]} for n in (1,2) for task in ('a','b')]
    assert native_decision(rows,['a','b'])['status']=='QUALIFIED_LOCAL_EDIT_FORMAT'
    rows[-1]['success']=False
    assert native_decision(rows,['a','b'])['status']=='REJECT_LOCAL_EDIT_FORMAT'
    rows[-1]=rows[0]
    assert native_decision(rows,['a','b'])['status']=='INCOMPLETE'
