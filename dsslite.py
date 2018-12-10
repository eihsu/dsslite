import logging
import random
import simpy

# Checking whether by mistake the user is running dss directly
if __name__ == "__main__":
  print("eih-dss.py should not be called directly")
  print("Check README file for usage information.")
  exit(1)


# Max number of any component type (worker, db, lb) in any system.
MAX_INSTANCES = 100

# Max simultaneous connections a database can process w/out blocking.
# Can be overriden when constructing a new database.
DB_CONCURRENCY = 2

# Size of worker's queue's, entire system fails with overflow.
WORKER_QUEUE_SIZE = 10


# Time to dequeue a request and set up context for handling it.
# (Currently, not really distinct from general request handling time
# cost.)
TIMECOST_DEQUEUE = 2

# Time to actually handle a request, on the worker side.
TIMECOST_HANDLE = 10

# Time for DB to receive, execute, and respond to an upsert request.
# Simulated by worker object.  Does not include time spent waiting for
# a driver to fill up, which depends on the actions of other workers.
TIMECOST_DB = 30

# Monetary cost for pricing component types, in thousands of dollars.
COST_DB = 15
COST_WORKER = 2
COST_LOAD_BALANCER = 5

# After last scripted request is generated, simulator waits for
# workers to finish handling requests.  But in case of totally dropped
# requests, will never get an ack, so we also check for this timeout.
TERMINAL_WAIT_TIMEOUT = 1000

class Request():
  """ Basic unit of traffic for simulation, a key/value pair.

  Key and value represent upsert operation to be transacted on db.  In
  example scenario, they represent a username (key) and a dictionary
  containing one or more key/value pairs of user profile data to
  upsert for that user.
  """

  def __init__(self, key, value, time=0):
    self.user = key
    self.data = value
    self.time = time
    self.ack_time = None

class Database():
  """ Database (full or just a simplified shard). 

  Key/value store representing users and their profile information.
  The store itself is a dictionary, as is the profile information
  itself.

  Concurrency-wise, databases (or rather, their drivers) are
  registered as resources in the PySim simulation.  You have to
  request a driver, waiting for it to free up, then perform a write
  using `upsert` while using your own timeout processes to simulate
  the write time--blocking others from using the driver in the
  meantime--and then release the resource.  The number of drivers
  controls the degree of concurrency the database supports, and can be
  altered during construction (default is set by `DB_CONCURRENCY`
  setting.)
  """
  count = 0

  def __init__(self, num_drivers=DB_CONCURRENCY):
    self.logger = logging.getLogger(__name__)
    Database.count += 1
    self.id = Database.count
    self.name = "DB #" + str(self.id)
    self.contents = {}
    self.concurrency = num_drivers

  def upsert(self, user, data):
    if user in self.contents:
      self.contents[user].update(data)
    else:
      self.contents[user] = data
    self.logger.info(self.name +
                     " updates {} with {}".format(user, data))

  def register(self, env):
    self.driver = simpy.Resource(env, capacity=self.concurrency)

  def lookup(self, user):
    return self.contents.get(user)

  def display(self, user):
    data = self.lookup(user)
    if data is None:
      print("Sorry, user {} not found in db.".format(user))
      return

    header = "   " + self.name + " record for " + user + "   "
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    # lol right justify the attribute names
    pad_to = max([len(k) for k in data.keys()]) + 4
    for key in data:
      print(" " * (pad_to - len(key)) + key + " : " + data[key])
    print("=" * len(header))


  def write_as_html(self, user):
    data = self.lookup(user)
    if data is None:
      print("Sorry, user {} not found in db.".format(user))
      return

    # Data is dictionary of user attribute names and values
    data_html = ""
    if "name" in data:
      # Show user's name first, as large heading, if available.
      data_html += "<h1>" + data["name"] + "</h1>\n"
    # Show username as secondary heading.
    data_html += "<h2>Username: [{}]</h2>".format(user)
    # Show rest of attributes in pairs.
    for key in data:
      if key == "name":
        continue
      data_html += "<p><b>{}</b>:  {}</p>\n".format(key.upper(), data[key])

    output = """
      <!DOCTYPE html>
      <html>
        <head>
          <title>User: {}</title>
        </head>
        <body>
          {}
        </body>
      </html>
      """.format(user, data_html)

    filename = user + ".html"
    with open(filename, "w") as f:
      f.write(output)

  def dump(self):
    return self.contents

class Machine:
  pass

class Worker(Machine):
  """Worker instance for processing requests.

  Workers interface with a single database instance and receive
  (UPSERT) tasks directly from environment, or from  load balancers.

  Concurrency for the system is implemented mostly here, in terms of a
  worker's queue for receiving incoming requests, and the various delays
  associated with waiting for database driver access and for
  processing requests.  Even the wait for the database driver is
  implemented within a worker, though the driver itself (modeled as a
  SimPy resource), and the method for actually performing an upsert,
  are attributes of the database object.

  The life cycle for a request, provided instantaneously in base
  python real time via the `receive_request` method, starts there with
  insertion into the worker's queue.  In SimPy simulated time, it is
  pulled in turn by a process running the `consume` method, which
  polls the queue and launches further SimPy processes using
  `handle_request` for individual requests.  This is where the worker
  waits for a free database driver and pays the update, paying time
  costs via timeouts along the way.
  """
  count = 0

  def __init__(self, db):
    self.logger = logging.getLogger(__name__)
    self.db = db
    Worker.count += 1
    self.id = Worker.count
    self.name = "WORKER #" + str(self.id)
    self.sim = None

  def receive_request(self, req):
    num_queued = len(self.queue.items)
    self.logger.info(self.name + " receives request for " + 
                     req.user + " at time " + str(req.time) +
                     "; reqs queued: " + str(num_queued))

    if num_queued >= WORKER_QUEUE_SIZE:
      self.logger.error("***")
      self.logger.error("*** Uh-oh, " + self.name + "'s queue " +
                        "overflowed.")
      self.logger.error("***")
      raise RuntimeError("Hey, " + self.name + " built up a " +
                         "backlog of requests to process, " +
                         "exceeding the limit of " +
                         str(WORKER_QUEUE_SIZE) + ".  Please " +
                         "add more workers, or improve the " +
                         "balance between existing workers, or " +
                         "slow down the simulated request rate.")
    self.queue.put(req)

  def ack_request(self, req, acktime):
    self.sim.receive_ack(req, acktime)

  def handle_request(self, env, req):
    self.logger.info(self.name + " processing request for " + 
                     req.user + " from time " + str(req.time))
    yield env.timeout(TIMECOST_HANDLE)
    with self.db.driver.request() as r:
      yield r
      yield env.timeout(TIMECOST_DB)
      self.db.upsert(req.user, req.data)
    self.ack_request(req, env.now)

  def consume(self, env):
    # Keep consuming from the queue of requests.
    while True:
      req = yield self.queue.get()
      # We have to pay a time cost for dequeueing unless we were idle
      # (empty queue) when the request arrived (right now).  This
      # would be reflected by a queue with no other requests on it
      # (the request in question would still go through the queue
      # since this is how all requests progress from `receive_request`
      # in pure python to `handle_request` as a SimPy process.
      if len(self.queue.items) > 0:
        yield env.timeout(TIMECOST_DEQUEUE)
      # Kick off a SimPy process for handling request, and yield it.
      yield env.process(self.handle_request(env, req))

  def activate(self, env):
    self.queue = simpy.Store(env, capacity=WORKER_QUEUE_SIZE)
    self.consumer = env.process(self.consume(env))

class LoadBalancer(Machine):
  """Load balancer for distributing requests over multiple workers.

  Default hashing function is to pick a machine uniformly at random,
  but user can call set_hash(<fn>) to provide their own.
  Stylistially, load balancers are designed to be silent and
  transparent with respect to runtime, logging,  etc., to simplify and
  intensify the diagnostic process for users.

  Concurrency-wise, load balancers don't actually register with the
  SimPy environment--for the sake of simplicity their activity is
  instantaneous (with respect to concurrency) and transparent (with
  respect to logging).  So, all of the timing and concurrency
  considerations fall on the workers and their databases, allowing
  users to isolate their focus on hashing alone when working with load
  balancers.  So, the load balancer code really just forwards requests
  along in real time via the base python runtime.
  """
  @staticmethod
  def default_hash_factory(pool_size):
    return lambda x: random.randint(1, pool_size)

  def __init__(self, pool):
    self.logger = logging.getLogger(__name__)
    if len(pool) < 1:
      raise ValueError(("Hey, LoadBalancer should be constructed "
                        "with a list of one or more machines (either "
                        "workers or more load balancers.)"))
    self.pool = pool
    self.hash_function = LoadBalancer.default_hash_factory(len(pool))

  def set_hash(self, fn):
    self.hash_function = fn

  def receive_request(self, req):
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
    machine.receive_request(req)

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

  Concurrency-wise, the simulation is driven by `generate_traffic`,
  which pulls scripted requests (from the end of this document) at
  random intervals and provides them to the edge for dispatch to
  workers.  The only other full-fledged process is the loop in the
  `consume` method of workers.  The database drier is just a resource,
  and the load balancer is just pure python code for routing a request
  to a worker.
  """
  def __init__(self, machine, sim_speed=0.01, random_seed=42):
    self.logger = logging.getLogger(__name__)
    if not isinstance(machine, Machine):
      raise ValueError(("Hey, when creating a simulation, please "
                        "provide a single machine (either a worker "
                        "or a load balancer)."))
    self.sim_speed = sim_speed
    self.edge = machine
    self.outagesP = False
    self.reqs_sent = {}  # reqs sent out to system, keyed by timestamp
    self.reqs_acked = {} # timestamp when req sent out -> time of ack
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
    self.logger.setLevel(logging.INFO)

    f = SimFilter(env)  # custom filter for adding simulation time
    self.logger.addFilter(f)

    formatter = logging.Formatter('[%(stime)5s ms] %(message)s')

    # Log to stdout
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(formatter)

    # Log to file (mode 'w' overwrites, default of 'a' appends.)
    fh = logging.FileHandler("test.log", mode='w')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)

    # Prevent duplicate logging if multiple simulations constructed
    # in a single environment (like, during testing).
    if len(self.logger.handlers) == 0:
      self.logger.addHandler(ch)
      self.logger.addHandler(fh)

  def register_components(self, machine):
    env = simpy.rt.RealtimeEnvironment(initial_time=0,
                                       factor=self.sim_speed,
                                       strict=False)
    load_balancers = []
    workers = []
    databases = []
    ms = [machine]

    # Traverse network, registering components.
    while ms:
      m = ms.pop(0)  # lol python pop is expensive, I miss car/cdr!
      if isinstance(m, Worker):
        workers.append(m)
        databases.append(m.db)
        m.sim = self # need this so it can ack requests
      if isinstance(m, LoadBalancer):
        load_balancers.append(m)
        ms.extend(m.pool)

    # Eliminate duplicates in registration lists.
    workers = list(set(workers))
    databases = list(set(databases))

    # Enforce limit on number of instances.
    if (len(workers) > MAX_INSTANCES or
        len(databases) > MAX_INSTANCES or
        len(load_balancers) > MAX_INSTANCES):
      raise RuntimeError("Hey, there's a limit on number of " +
                         "components in a system; you cannot " + 
                         "create more than " + str(MAX_INSTANCES) + 
                         "instances of any one kind (databases, " +
                         "workers, or load balancers.")

    # Kick off worker SimPy processes.
    for w in workers:
      w.activate(env)

    # Register databases as SimPy resources.
    for d in databases:
      d.register(env)

    return (env, load_balancers, workers, databases)

  def set_outages(self, setting):
    if not isinstance(setting, bool):
      raise ValueError(("Hey, when changing the simulator's setting "
                        "for outages, please supply either True "
                        "(on) or False (off)."))
    self.outagesP = setting

  def receive_ack(self, req, timestamp):
    self.reqs_acked[req.time] = timestamp

  def generate_stats(self, timestamp):
    res = {}
    reqs_sent = self.reqs_sent
    reqs_acked = self.reqs_acked
    for k in reqs_acked:  # Keys are timestamps of outgoing reqs.
      reqs_sent[k].ack_time = reqs_acked[k]  # Annotate req with ack time.
    
    num_reqs = len(reqs_sent)
    num_failed = 0
    for r in reqs_sent.values():
      if r.ack_time is None:
        num_failed += 1
    response_times = [ r.ack_time - r.time
                       for r in reqs_sent.values() if r.ack_time ]
    res["num_reqs"] = len(reqs_sent)
    res["num_failed"] = num_failed
    if response_times:
      response_times.sort()
      res["mean_wait"] = sum(response_times)/float(len(response_times))
      res["min_wait"] = min(response_times)
      res["max_wait"] = max(response_times)
      lrt = len(response_times)
      if lrt % 2 == 0:
        res["median_wait"] = (response_times[lrt // 2 - 1] +
                              response_times[lrt // 2]) / 2
      else:
        res["median_wait"] = response_times[lrt // 2]
    res["reqs"] = [ (r.user, r.time, r.ack_time) for r
                    in reqs_sent.values() ]
    res["sim_time"] = timestamp
    res["num_dbs"] = len(self.databases)
    res["num_workers"] = len(self.workers)
    res["num_lbs"] = len(self.load_balancers)
    res["cost"] = (res["num_dbs"] * COST_DB +
                   res["num_workers"] * COST_WORKER +
                   res["num_lbs"] * COST_LOAD_BALANCER)
    return res

  def display_stats(self, stats):
    s = ""
    s += "========================\n" 
    s += " Results for Simulation \n"
    s += "========================\n"
    s += "  {} databases at ${}K each\n".format(stats["num_dbs"],
                                                COST_DB)
    s += "+ {} workers at ${}K each\n".format(stats["num_workers"],
                                              COST_WORKER)
    s += "+ {} load balancers at ${}K each\n".format(stats["num_lbs"],
                                                     COST_LOAD_BALANCER)
    s += "= ${:,} system hardware cost.\n".format(stats["cost"] * 1000)
    s += "\n"
    s += "Processed {} of {} requests in {} seconds.\n".format(
      stats["num_reqs"] - stats["num_failed"],
      stats["num_reqs"], stats["sim_time"] / 1000.0)
    s += "Min/Max/Mean/Median response time: "
    s += "[ {} / {} / {} / {} ] ms.\n".format(stats["min_wait"],
                                              stats["max_wait"],
                                              stats["mean_wait"],
                                              stats["median_wait"])
    # STARTHERE Report traffic rate, and TPS (with caveat).
    print(s)

  def traffic_generator(self, env, rate, script):
    if not script:
      script = Simulation.script
    # Send out scripted requests at random intervals.
    for line in script:
      # Uniformly 1 to 100 ms between requests, scaled by rate.
      interval = int(random.randint(1,100) * rate)
      yield env.timeout(interval)
      timestamp = env.now
      req = Request(line[0], line[1], timestamp)
      self.logger.debug("New request: {}".format(req))
      self.edge.receive_request(req)
      self.reqs_sent[timestamp] = req

    # Wait until all requests have been acked
    # flushed their queues before ending the simulation.
    countdown = TERMINAL_WAIT_TIMEOUT
    while (len(self.reqs_acked) < len(self.reqs_sent)) and countdown > 0:
      countdown -= 1
      yield env.timeout(1)

  # traffic_rate: scaling factor for time interval between requests.
  # script: unless provided, will use the one at the end of this file.
  def run(self, traffic_rate=1.0, script=False):
    p = self.env.process(self.traffic_generator(self.env,
                                                traffic_rate,
                                                script))
    self.env.run(until=p)
    stats = self.generate_stats(self.env.now)
    self.display_stats(stats)

# show stats for simulation
# generate fake data, add spikes
# x enforce max instances
# test for max instances
# tests for worker concurrency (for instance straight to handling vs queue)
# test for stats
# x visualize load on queue
# x insert spacing between requests
# x add logging
# x actual (real-time) delay so feels like sim is running
# x improved db output to see complete profiles
# x enforce max queue capacity
# x db concurrency
#
# x database transactions take <d> time
# x worker requests instantaneous to receive, transactions cost <q>
# x  <q> to dequeue, <p> to process (plus db wait and processing time).
# x load balancer instantaneous
# x random variation: gaps between requests
#

  script = [
    ("apple", {"name": "apple person", "dob": "2015-06-01"}),
    ("banana", {"name": "Banana Person", "dob": "2012-11-10"}),
    ("apple", {"name": "Apple Person", "dob": "2015-06-01"}),
    ("banana", {"country": "Canada"})
  ]







