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
    # FIXTHIS error check
    return self.contents[user]

  def display(self, user):
    # Return format more nicely?
    return self.lookup(user)

  def dump(self):
    return self.contents

class Worker():
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

class LoadBalancer():
  """Load balancer for distributing requests over multiple workers.

  Default hashing function is to pick a machine uniformly at random,
  but user can call set_hash(<fn>) to provide their own.
  Stylistially, load balancers are designed to be silent and
  transparent with respect to logging, etc., to simplify and
  intensify the diagnostic process for users.
  """
  def default_hash():
    return 1  # FIXTHIS implement uniform random selection

  def __init__(self, pool):
    self.pool = pool
    self.hash_function = default_hash

  def handle_request(self, req):
    choice = self.hash_function(req.user)
    # FIXTHIS sanity check choice against pool size.
    machine = self.pool[choice]
    machine.handle_request(req)

class Simulation():
  """Engine for running a simulation.

  Constructed with a single edge machine (i.e. point of entry)
  consisting of either an individual worker or a load balancer over
  multiple workers.
  """

  def __init__(self, machine, random_seed=42):
    self.edge = machine
    self.failuresP = False
    random.seed(random_seed)

  def set_failures(self, setting):
    # FIXTHIS check for boolean.
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
