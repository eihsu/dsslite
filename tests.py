from dsslite import *

def test_db():
  assert 1==1

def test_ex1():
  import examples.ex1_single_server as x
  assert x.db.lookup("apple") == {
    'dob': '2015-06-01', 'name': 'Apple Person'}
