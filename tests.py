import dsslite
from dsslite import *
from mock import Mock
import pytest

# Lol random module not guaranteed to be consistent across platforms
# and versions, even with the same seed; so, mock it using fixed list.
class MyRand:
  rands = [ ((i * 987) % 1000) / 1000.0 for i in range(0, 10000, 123) ]
  num_rands = len(rands)

  def init(self, seed=42):
    self.seed(seed)

  def seed(self, seed):
    self.i = seed

  def random(self):
    self.i = (self.i + 1) % MyRand.num_rands
    return MyRand.rands[self.i]

  def randint(self, min, max):
    n = max - min + 1
    return int(min + int(self.random() * n))

mr1 = MyRand()
dsslite.random.random = mr1.random
mr2 = MyRand()
dsslite.random.randint = mr2.randint

#############
# TEST DATA #
#############

req1 = Request("nannak", { "dob": "1954-12-08" })
req2 = Request("celinat", { "dob": "1946-10-18" })
req3 = Request("stanislavy", { "dob": "1979-01-12" })
req4 = Request("archers", { "dob": "1975-08-30" })
req5 = Request("isabellem", { "dob": "1961-06-26" })
req6 = Request("maey", { "dob": "1988-03-01" })
req7 = Request("jakobk", { "dob": "1999-07-07" })
req8 = Request("halimah", { "dob": "1954-12-08" })
req9 = Request("halimah", { "dob": "1900-01-01" })

reqs = [req1, req2, req3, req4, req5, req6, req7, req8, req9]

##############
# UNIT TESTS #
##############

def test_db():
  db1 = Database()
  db2 = Database()

  db1.dump == ""
  assert db1.lookup("nannak") == None

  db1.upsert("nannak", { "full_name": "Nanna N. Karlsen" })
  assert db1.lookup("nannak") == { "full_name": "Nanna N. Karlsen" }

  db1.upsert("nannak", { "dob": "1954-08-12" })
  assert db1.lookup("nannak") == { "full_name": "Nanna N. Karlsen",
                                   "dob": "1954-08-12" }

  db1.upsert("nannak", { "dob": "1954-12-08" })
  assert db1.lookup("nannak") == { "full_name": "Nanna N. Karlsen",
                                   "dob": "1954-12-08" }

  db2.upsert("halimah", { "dob": "1982-07-14",
                          "full_name": "Halima Harb",
                          "mmn": "Khoury" })
  assert db2.lookup("nannak") == None
  assert db2.lookup("halimah") == { "dob": "1982-07-14",
                                    "full_name": "Halima Harb",
                                    "mmn": "Khoury" }
  db1.upsert("halimah", { "dob": "1900-01-01" })
  assert db1.lookup("halimah") == { "dob": "1900-01-01" }
  assert db2.lookup("halimah") == { "dob": "1982-07-14",
                                    "full_name": "Halima Harb",
                                    "mmn": "Khoury" }


def test_worker():
  db1 = Database()
  db2 = Database()

  w1 = Worker(db1)
  w2 = Worker(db1)
  w3 = Worker(db2)

  assert Worker.count == 3
  assert [w1.id, w2.id, w3.id] == [1, 2, 3]

  w1.handle_request(req1)
  w2.handle_request(req2)
  w3.handle_request(req3)
  w1.handle_request(req4)
  w2.handle_request(req5)
  w3.handle_request(req6)
  w1.handle_request(req7)
  w2.handle_request(req8)
  w3.handle_request(req9)

  # (coupled testing to db instead of mocks)
  assert db1.lookup("nannak") == { "dob": "1954-12-08" }
  assert db2.lookup("nannak") == None
  assert db1.lookup("celinat") == { "dob": "1946-10-18" }
  assert db2.lookup("celinat") == None
  assert db1.lookup("stanislavy") == None
  assert db2.lookup("stanislavy") == { "dob": "1979-01-12" }
  assert db1.lookup("archers") == { "dob": "1975-08-30" }
  assert db2.lookup("archers") == None
  assert db1.lookup("isabellem") == { "dob": "1961-06-26" }
  assert db2.lookup("isabellem") == None
  assert db1.lookup("maey") == None
  assert db2.lookup("maey") == { "dob": "1988-03-01" }
  assert db1.lookup("jakobk") == { "dob": "1999-07-07" }
  assert db2.lookup("jakobk") == None
  assert db1.lookup("halimah") == { "dob": "1954-12-08" }
  assert db2.lookup("halimah") == { "dob": "1900-01-01" }
  assert db1.lookup("nobody") == None
  assert db2.lookup("nobody") == None

def test_load_balancer():
  db1 = Database()
  db2 = Database()

  w1 = Worker(db1)
  w1.handle_request = Mock()
  w2 = Worker(db1)
  w2.handle_request = Mock()
  w3 = Worker(db2)
  w3.handle_request = Mock()

  # Default (random) load balancer.
  lb1 = LoadBalancer([w1, w2, w3])

  lb1.handle_request(req1)
  w3.handle_request.assert_called_with(req1)
  lb1.handle_request(req2)
  w1.handle_request.assert_called_with(req2)
  lb1.handle_request(req3)
  w2.handle_request.assert_called_with(req3)
  lb1.handle_request(req4)
  w1.handle_request.assert_called_with(req4)
  lb1.handle_request(req5)
  w2.handle_request.assert_called_with(req5)
  lb1.handle_request(req6)
  w3.handle_request.assert_called_with(req6)
  lb1.handle_request(req7)
  w1.handle_request.assert_called_with(req7)
  lb1.handle_request(req8)
  w2.handle_request.assert_called_with(req8)
  lb1.handle_request(req9)
  w1.handle_request.assert_called_with(req9)

  # Default load balancer on different pool.
  lb2 = LoadBalancer([w1, w2])

  lb2.handle_request(req1)
  w1.handle_request.assert_called_with(req1)
  lb2.handle_request(req2)
  w2.handle_request.assert_called_with(req2)
  lb2.handle_request(req3)
  w1.handle_request.assert_called_with(req3)
  lb2.handle_request(req4)
  w2.handle_request.assert_called_with(req4)
  lb2.handle_request(req5)
  w1.handle_request.assert_called_with(req5)
  lb2.handle_request(req6)
  w1.handle_request.assert_called_with(req6)
  lb2.handle_request(req7)
  w2.handle_request.assert_called_with(req7)
  lb2.handle_request(req8)
  w1.handle_request.assert_called_with(req8)
  lb2.handle_request(req9)
  w2.handle_request.assert_called_with(req9)

  # Custom hash function.
  def alpha_hash(name):
    if name[0] < "i":
      return 1
    if name[0] < "p":
      return 2
    return 3

  lb3 = LoadBalancer([w1, w2, w3])
  lb3.set_hash(alpha_hash)

  lb3.handle_request(req1)
  w2.handle_request.assert_called_with(req1)
  lb3.handle_request(req2)
  w1.handle_request.assert_called_with(req2)
  lb3.handle_request(req3)
  w3.handle_request.assert_called_with(req3)
  lb3.handle_request(req4)
  w1.handle_request.assert_called_with(req4)
  lb3.handle_request(req5)
  w2.handle_request.assert_called_with(req5)
  lb3.handle_request(req6)
  w2.handle_request.assert_called_with(req6)
  lb3.handle_request(req7)
  w2.handle_request.assert_called_with(req7)
  lb3.handle_request(req8)
  w1.handle_request.assert_called_with(req8)
  lb3.handle_request(req9)
  w1.handle_request.assert_called_with(req9)

  # Hash returns invalid choice.
  lb4 = LoadBalancer([w1])
  lb4.set_hash( lambda x: 2 )
  with pytest.raises(ValueError) as e:
    lb4.handle_request(req1)
  lb4.set_hash( lambda x: -1 )
  with pytest.raises(ValueError) as e:
    lb4.handle_request(req1)
  lb4.set_hash( lambda x: "apple")
  with pytest.raises(ValueError) as e:
    lb4.handle_request(req1)

  # Load balancer with empty pool.
  with pytest.raises(ValueError) as e:
    LoadBalancer([])

def test_simulation():
  db1 = Database()
  w1 = Worker(db1)

  # Invalid construction
  with pytest.raises(ValueError) as e:
    Simulation([w1])

  # Invalid configuration.
  s = Simulation(w1)
  with pytest.raises(ValueError) as e:
    s.set_failures("on")



def test_worker_concurrency():
  pass



################
# SYSTEM TESTS #
################

# Randomness controlled by providing pseudorandom generator with a
# fixed seed (default argument to simulation constructor.

def test_ex1():
  import examples.ex1_single_server as x
  assert x.db.lookup("apple") == {
    'dob': '2015-06-01', 'name': 'Apple Person'}
