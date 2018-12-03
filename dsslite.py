import random
import time

# Checking whether by mistake the user is running dss directly
if __name__ == "__main__":
  print("eih-dss.py should not be called directly")
  print("Read the README file for further details")
  exit(1)


MAX_INSTANCES = 100
QUEUE_LIMIT_BEFORE_OVERFLOW_FAILURE = 50 # FIXTHIS

# FIXTHIS capture Databases and Workers and Load Balancers in a global
# var as they are created, in case it's useful to have a handle on
# them later.

class Request():
  """ Basic unit of traffic for simulation, a key/value pair.

  Key and value represent upsert operation to be transacted on db.  In
  example scenario, they represent a username (key) and a dictionary
  containing one or more key/value pairs of user profile data to
  upsert for that user.
  """

  def __init__(self, key, value):
    self.user = key
    self.data = value

class Database():
  """ Database (full or just a simplified shard). 

  Key/value store representing users and their profile information.
  The store itself is a dictionary, as is the profile information
  itself.
  """

  def __init__(self):
    self.contents = {}

  def upsert(self, user, data):
    print "Upserting {} with {}".format(user, data)
    if user in self.contents:
      self.contents[user].update(data)
    else:
      self.contents[user] = data

  def lookup(self, user):
    return self.contents.get(user)

  def display(self, user):
    # Return format more nicely?
    return self.lookup(user)

  def dump(self):
    return self.contents

class Machine:
  pass

class Worker(Machine):
  """Worker instance for processing requests.

  Workers interface with a single database instance and
  receive (UPSERT) tasks directly from environment, or from
  load balancers.
  """

  count = 0

  def __init__(self, db):
    self.db = db
    self.queue = []
    self.freep = True
    Worker.count += 1
    self.id = Worker.count
    self.name = "WORKER #" + str(self.id)

  def handle_request(self, req):
    print self.name + " received request for user " + req.user
    self.db.upsert(req.user, req.data)

class LoadBalancer(Machine):
  """Load balancer for distributing requests over multiple workers.

  Default hashing function is to pick a machine uniformly at random,
  but user can call set_hash(<fn>) to provide their own.
  Stylistially, load balancers are designed to be silent and
  transparent with respect to logging, etc., to simplify and
  intensify the diagnostic process for users.
  """

  @staticmethod
  def default_hash_generator(pool_size):
    return lambda x: random.randint(1, pool_size)

  def __init__(self, pool):
    if len(pool) < 1:
      raise ValueError(("Hey, LoadBalancer should be constructed with "
                        "a list of one or more machines (either "
                        "workers or more load balancers.)"))
    self.pool = pool
    self.hash_function = LoadBalancer.default_hash_generator(len(pool))

  def set_hash(self, fn):
    self.hash_function = fn

  def handle_request(self, req):
    choice = self.hash_function(req.user)
    if not isinstance(choice, (int, long)) or choice < 1:
      raise ValueError("Hey, the hash function for " + req.user +
                       " returned the value <{}> for".format(choice) +
                       " user <" + req.user + ">, but it is" +
                       " supposed to return its choice of machine" +
                       " from its pool to handle the request, as an" +
                       " integer.")
    if choice > len(self.pool):
      raise ValueError("Hey, the hash function for " + req.user +
                       " chose machine # " + str(choice) +" for" +
                       " user <" + req.user + ">, but" +
                       " there are only " + str(len(self.pool)) + 
                       " machines in its pool.")
    machine = self.pool[choice - 1]
    machine.handle_request(req)

class Simulation():
  """Engine for running a simulation.

  Constructed with a single edge machine (i.e. point of entry)
  consisting of either an individual worker or a load balancer over
  multiple workers.
  """

  def __init__(self, machine, random_seed=42):
    if not isinstance(machine, Machine):
      raise ValueError(("Hey, when creating a simulation, please "
                        "provide a single machine (either a worker "
                        "or a load balancer)."))
    self.edge = machine
    self.failuresP = False
    random.seed(random_seed)

  def set_failures(self, setting):
    if not isinstance(setting, bool):
      raise ValueError(("Hey, when changing the simulator's setting "
                        "for failures, please supply either True "
                        "(on) or False (off)."))
    self.failuresP = setting

  def run(self, speed=1.0):
    for req in Simulation.requests:
      self.edge.handle_request(Request(req[0],req[1]))

  requests = [
    ("apple", {"name": "apple person", "dob": "2015-06-01"}),
    ("banana", {"name": "Banana Person", "dob": "2012-11-10"}),
    ("apple", {"name": "Apple Person", "dob": "2015-06-01"}),
    ("banana", {"country": "Canada"})
  ]
