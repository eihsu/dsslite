import logging
import random
import simpy

# Checking whether by mistake the user is running dss directly
if __name__ == "__main__":
  print("eih-dss.py should not be called directly")
  print("Check README file for usage information.")
  exit(1)


MAX_INSTANCES = 100
QUEUE_LIMIT_BEFORE_OVERFLOW_FAILURE = 50 # FIXTHIS

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
    self.logger = logging.getLogger(__name__)
    self.contents = {}

  def upsert(self, user, data):
    #yield env.timeout(TIMECOST_DB) # STARTHERE
    if user in self.contents:
      self.contents[user].update(data)
    else:
      self.contents[user] = data
    self.logger.info("Upserted {} with {}".format(user, data))

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
    self.logger = logging.getLogger(__name__)
    self.db = db
    self.queue = []
    self.freep = True
    Worker.count += 1
    self.id = Worker.count
    self.name = "WORKER #" + str(self.id)

  def handle_request(self, req):
    self.logger.info(self.name + " received request for user " + req.user)
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
  def default_hash_factory(pool_size):
    return lambda x: random.randint(1, pool_size)

  def __init__(self, pool):
    self.logger = logging.getLogger(__name__)
    if len(pool) < 1:
      raise ValueError(("Hey, LoadBalancer should be constructed with "
                        "a list of one or more machines (either "
                        "workers or more load balancers.)"))
    self.pool = pool
    self.hash_function = LoadBalancer.default_hash_factory(len(pool))

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

class SimFilter(logging.Filter):
  """
  Inject information from the simulation (the simulated timestamp)
  into log messages.  Philosophically, the logger is not really the
  log of the dsslite distributed system simulator, so much as the
  logger of the system created by the dsslite user for simulation.
  """
  def __init__(self, env):
    self.env = env  # pysim env for retrieving simulation timestamps

  def filter(self, record):
    record.stime = self.env.now
    return True

class Simulation():
  """Engine for running a simulation.

  Constructed with a single edge machine (i.e. point of entry)
  consisting of either an individual worker or a load balancer over
  multiple workers.
  """

  def __init__(self, machine, random_seed=42):
    self.logger = logging.getLogger(__name__)
    if not isinstance(machine, Machine):
      raise ValueError(("Hey, when creating a simulation, please "
                        "provide a single machine (either a worker "
                        "or a load balancer)."))
    self.edge = machine
    self.failuresP = False
    random.seed(random_seed)
    
    # Create pysim environment and register it with all the
    # workers, databases, and load balancers in the system; have to do
    # it the hard way (post-construction) for convenience of users.
    (self.env,
     self.load_balancers,
     self.workers,
     self.databases) = self.register_components(machine)

    self.configure_logger(self.env)

  def configure_logger(self, env):
    self.logger.setLevel(logging.DEBUG)

    f = SimFilter(env)  # custom filter for adding simulation time
    self.logger.addFilter(f)

    formatter = logging.Formatter('[%(stime)5s ms] %(message)s')

    # Log to stdout
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(formatter)
    self.logger.addHandler(ch)

    # Log to file
    fh = logging.FileHandler("test.log")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    self.logger.addHandler(fh)

  def register_components(self, machine):
    env = simpy.Environment()
    load_balancers = []
    workers = []
    databases = []
    ms = [machine]
    while ms:
      m = ms.pop(0)  # lol python pop is expensive, I miss car/cdr!
      if isinstance(m, Worker):
        # Register worker and its database.
        m.env = env
        m.db.env = env
        workers.append(m)
        databases.append(m.db)
      if isinstance(m, LoadBalancer):
        # Register load balancer; enqueue its pool for processing.
        m.env = env
        load_balancers.append(m)
        ms.extend(m.pool)
    # Eliminate duplicates in registration lists.
    workers = list(set(workers))
    databases = list(set(databases))

    return (env, load_balancers, workers, databases)

  def set_failures(self, setting):
    if not isinstance(setting, bool):
      raise ValueError(("Hey, when changing the simulator's setting "
                        "for failures, please supply either True "
                        "(on) or False (off)."))
    self.failuresP = setting

  def traffic_generator(self, speed):
    for req in Simulation.requests:
      # Uniformly 1 to 100 ms between requests, scaled by speed.
      interval = int(random.randint(1,100) * speed)
      yield self.env.timeout(interval)
      self.logger.info("New request: {}".format(req))
      self.edge.handle_request(Request(req[0],req[1]))

  def run(self, speed=1.0):
    p = self.env.process(self.traffic_generator(speed))
    self.env.run(until=p)

# x insert spacing between requests
# generate fake data, add spikes
# x add logging
# actual (real-time) delay so feels like sim is running
#
# database transactions take <d> time, everything else instantaneous
# worker requests instantaneous to receive, transactions cost <q>
#   to enqueue, and <q> to dequeue, <p> to process (plus db wait time).
# load balancer instantaneous
# x random variation: gaps between requests


  requests = [
    ("apple", {"name": "apple person", "dob": "2015-06-01"}),
    ("banana", {"name": "Banana Person", "dob": "2012-11-10"}),
    ("apple", {"name": "Apple Person", "dob": "2015-06-01"}),
    ("banana", {"country": "Canada"})
  ]
