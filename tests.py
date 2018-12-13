import dsslite
from dsslite import *
import logging
from mock import Mock
import pytest
import simpy

# Turn regular logging off during testing.
logging.disable(logging.CRITICAL)

# Lol random module not guaranteed to be consistent across platforms
# and versions, even with the same seed; so, mock it using fixed list.
class MyRand:
  rands = [ ((i * 987) % 1000) / 1000.0 for i in range(0, 10000, 123) ]
  num_rands = len(rands)

  def __init__(self, seed=9963):
    self.seed(seed)

  def seed(self, seed):
    self.i = seed

  def random(self):
    self.i = (self.i + 1) % MyRand.num_rands
    return MyRand.rands[self.i]

  def randint(self, min, max):
    n = max - min + 1
    return int(min + int(self.random() * n))

mr = MyRand()
dsslite.random.random = mr.random
dsslite.random.randint = mr.randint
dsslite.random.seed = mr.seed

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

test_script = [ (r.user, r.data) for r in reqs ]

###################
# COMPONENT TESTS #
###################

def test_worker():
  db1 = Database()
  db2 = Database()

  w1 = Worker(db1)
  w2 = Worker(db1)
  w3 = Worker(db2)

  assert Worker.count == 3
  assert [w1.id, w2.id, w3.id] == [1, 2, 3]

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

def test_load_balancer():
  db1 = Database()
  db2 = Database()

  w1 = Worker(db1)
  w1.receive_request = Mock()
  w2 = Worker(db1)
  w2.receive_request = Mock()
  w3 = Worker(db2)
  w3.receive_request = Mock()

  # Default (random) load balancer.
  lb1 = LoadBalancer([w1, w2, w3])

  lb1.receive_request(req1)
  w3.receive_request.assert_called_with(req1)
  lb1.receive_request(req2)
  w1.receive_request.assert_called_with(req2)
  lb1.receive_request(req3)
  w2.receive_request.assert_called_with(req3)
  lb1.receive_request(req4)
  w1.receive_request.assert_called_with(req4)
  lb1.receive_request(req5)
  w2.receive_request.assert_called_with(req5)
  lb1.receive_request(req6)
  w3.receive_request.assert_called_with(req6)
  lb1.receive_request(req7)
  w1.receive_request.assert_called_with(req7)
  lb1.receive_request(req8)
  w2.receive_request.assert_called_with(req8)
  lb1.receive_request(req9)
  w1.receive_request.assert_called_with(req9)

  # Default load balancer on different pool.
  lb2 = LoadBalancer([w1, w2])

  lb2.receive_request(req1)
  w1.receive_request.assert_called_with(req1)
  lb2.receive_request(req2)
  w2.receive_request.assert_called_with(req2)
  lb2.receive_request(req3)
  w1.receive_request.assert_called_with(req3)
  lb2.receive_request(req4)
  w2.receive_request.assert_called_with(req4)
  lb2.receive_request(req5)
  w1.receive_request.assert_called_with(req5)
  lb2.receive_request(req6)
  w1.receive_request.assert_called_with(req6)
  lb2.receive_request(req7)
  w2.receive_request.assert_called_with(req7)
  lb2.receive_request(req8)
  w1.receive_request.assert_called_with(req8)
  lb2.receive_request(req9)
  w2.receive_request.assert_called_with(req9)

  # Custom hash function.
  def alpha_hash(name):
    if name[0] < "i":
      return 1
    if name[0] < "p":
      return 2
    return 3

  lb3 = LoadBalancer([w1, w2, w3])
  lb3.set_hash(alpha_hash)

  lb3.receive_request(req1)
  w2.receive_request.assert_called_with(req1)
  lb3.receive_request(req2)
  w1.receive_request.assert_called_with(req2)
  lb3.receive_request(req3)
  w3.receive_request.assert_called_with(req3)
  lb3.receive_request(req4)
  w1.receive_request.assert_called_with(req4)
  lb3.receive_request(req5)
  w2.receive_request.assert_called_with(req5)
  lb3.receive_request(req6)
  w2.receive_request.assert_called_with(req6)
  lb3.receive_request(req7)
  w2.receive_request.assert_called_with(req7)
  lb3.receive_request(req8)
  w1.receive_request.assert_called_with(req8)
  lb3.receive_request(req9)
  w1.receive_request.assert_called_with(req9)

  # Hash returns invalid choice.
  lb4 = LoadBalancer([w1])
  lb4.set_hash( lambda x: 2 )
  with pytest.raises(ValueError) as e:
    lb4.receive_request(req1)
  lb4.set_hash( lambda x: -1 )
  with pytest.raises(ValueError) as e:
    lb4.receive_request(req1)
  lb4.set_hash( lambda x: "apple")
  with pytest.raises(ValueError) as e:
    lb4.receive_request(req1)

  # Load balancer with empty pool.
  with pytest.raises(ValueError) as e:
    LoadBalancer([])

def test_simulation():
  db1 = Database()
  db2 = Database()
  w1 = Worker(db1)
  w2 = Worker(db2)
  w3 = Worker(db1)
  w4 = Worker(db2)

  # Invalid construction
  with pytest.raises(ValueError) as e:
    Simulation([w1])

  # Invalid configuration for outages.
  s = Simulation(w1)
  with pytest.raises(ValueError) as e:
    s.set_outages("on")

  # Check registration of components during sim initialization.
  lb2 = LoadBalancer([w3, w4, w1])
  lb1 = LoadBalancer([w1, w2, lb2])
  s = Simulation(lb1)
  s.initialize()
  assert len(s.load_balancers) == 2
  assert len(s.workers) == 4
  assert len(s.databases) == 2
  assert set(s.load_balancers) == set([lb1, lb2])
  assert set(s.workers) == set([w1, w2, w3, w4])
  assert set(s.databases) == set([db1, db2])

  # Check limit on components
  temp = dsslite.MAX_INSTANCES
  dsslite.MAX_INSTANCES = 4
  s = Simulation(lb1)
  with pytest.raises(RuntimeError) as e:
    dsslite.MAX_INSTANCES = 3
    s = Simulation(lb1)
  dsslite.MAX_INSTANCES = temp

def test_system():
  db1 = Database()
  db2 = Database()

  w1 = Worker(db1)
  w2 = Worker(db1)
  w3 = Worker(db2)

  lb = LoadBalancer([w1,w2,w3])
  sim = Simulation(lb, sim_speed=0)
  sim.run(script=test_script, rate=1.0)

  # (coupled testing to db instead of mocks)
  assert db1.lookup("nannak") == { "dob": "1954-12-08" }
  assert db2.lookup("nannak") == None
  assert db1.lookup("celinat") == { "dob": "1946-10-18" }
  assert db2.lookup("celinat") == None
  assert db1.lookup("stanislavy") == { "dob": "1979-01-12" }
  assert db2.lookup("stanislavy") == None
  assert db1.lookup("archers") == { "dob": "1975-08-30" }
  assert db2.lookup("archers") == None
  assert db1.lookup("isabellem") == None
  assert db2.lookup("isabellem") == { "dob": "1961-06-26" }
  assert db1.lookup("maey") == { "dob": "1988-03-01" }
  assert db2.lookup("maey") == None
  assert db1.lookup("jakobk") == { "dob": "1999-07-07" }
  assert db2.lookup("jakobk") == None
  assert db1.lookup("halimah") == { "dob": "1900-01-01" }
  assert db2.lookup("halimah") == None
  assert db1.lookup("nobody") == None
  assert db2.lookup("nobody") == None

  # Test stats.
  stats = sim.generate_stats()
  assert stats['sim_time'] == 450
  assert stats['tps'] == pytest.approx(21.95, 0.01)
  assert stats['num_reqs'] == 9
  assert stats['num_failed'] == 0
  assert stats['min_wait'] == 40
  assert stats['max_wait'] == 75
  assert stats['median_wait'] == 40
  assert stats['mean_wait'] == pytest.approx(47.67, 0.01)

  # Test sim reset and traffic rate, plus stats again.
  sim.run(script=test_script, rate = 2.0)
  stats = sim.generate_stats()
  assert stats['sim_time'] == 250
  assert stats['tps'] == pytest.approx(44.33, 0.01)
  assert stats['num_reqs'] == 9
  assert stats['num_failed'] == 0
  assert stats['min_wait'] == 40
  assert stats['max_wait'] == 78
  assert stats['median_wait'] == 40
  assert stats['mean_wait'] == pytest.approx(50.00, 0.01)

  # Test sim reproducability
  sim.run(script=test_script, rate = 1.0)
  stats = sim.generate_stats()
  assert stats['sim_time'] == 450
  assert stats['tps'] == pytest.approx(21.95, 0.01)
  assert stats['num_reqs'] == 9
  assert stats['num_failed'] == 0
  assert stats['min_wait'] == 40
  assert stats['max_wait'] == 75
  assert stats['median_wait'] == 40
  assert stats['mean_wait'] == pytest.approx(47.67, 0.01)


def test_worker_concurrency():
  # Queue overflow by dumping 9 times allowable number of requests,
  # all at once.
  max = dsslite.WORKER_QUEUE_SIZE
  db = Database()
  w = Worker(db)
  sim = Simulation(w, sim_speed=0)
  with pytest.raises(RuntimeError) as e:
    sim.run(script=test_script * max,
            rate=1000000)  # basically no time between requests

  # STARTHERE make request(s) go straight onto idle worker,
  # versus a bunch that make it busy and use its queue, check
  # that queue actually contains something.

def test_db_concurrency():
  
  # STARTHERE request a bunch of writes at once, make sure
  # it takes as long as expected.
  cost = dsslite.TIMECOST_DB
  db = Database()


##################
# SCENARIO TESTS #
##################

def test_ex1():
  import examples.ex1_single_server as x
