import logging
import random
import simpy

# Checking whether by mistake the user is running dss directly
if __name__ == "__main__":
  print("dsslite.py should not be called directly")
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
      # omg need a copy or else you have loose pointers to mutables
      self.contents[user] = data.copy()
    self.logger.info(self.name +
                     " updates {} with {}".format(user, data))

# FIXTHIS: on second run of simulation, number of requests changes.  Also, can't reproduce it now, but there was a bug where the `extend` below (used to be below) was being called on a string, and the request (for kathyh or kellyh or something like that) had huge data, containing both a comment as well as other fields.


  # Given dict full of data, update user in db--but don't overwrite
  # existing fields, instead treat them as lists to be extended.
  def extend(self, user, data):
    if user in self.contents:
      for k in data:
        if k in self.contents[user]:
          self.contents[user][k].extend(data[k])
        else:
          # omg need a copy or else you have loose pointers to mutables
          self.contents[user][k] = list(data[k])
    else:
      # omg copy
      self.contents[user] = data.copy()
    self.logger.info(self.name +
                     " extends {} with {}".format(user, data))

  def register(self, env):
    # Clear DB contents during registration to reset between runs of
    # the simiulation.
    self.contents.clear()
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

    # Right-justify the attribute names.
    pad_to = max([len(k) for k in data.keys()]) + 4
    for key in data:
      print(" " * (pad_to - len(key)) + key + " : " + data[key])
    print("=" * len(header))

  def write_html(self, user=None):
    head = ""
    body = ""

    # Create html doc for single user if one provided; otherwise do
    # entire db.
    if user:
      filename = user + ".html"
      users = [user]
      if not user in self.contents:
        print("Sorry, user {} not found in db.".format(user))
        return
    else:
      filename = "all.html"
      users = self.contents.keys()

    # Style default <hr> to potentially separate user profiles.
    head += "<style>hr { border-width: 4px; "
    head += "border-style: solid; }</style>"

    # Process either single user or entire contents of db.
    for user in users:

      # Data is dictionary of user attribute names and values.
      data = self.lookup(user)

      # Show user's name first, as large heading, if available.
      body += "\n          <hr>\n"
      if "name" in data:
        body += "          <h1>" + data['name'] + "</h1>\n"

      # Show username as secondary heading.
      # (Colored <hr> takes user's favorite color if available.)
      c = data['color'] if "color" in data else "#999999"
      body += "          "
      body += "<hr style=\"color: {}; border-width: 2px;\">\n".format(c)
      body += "          <h2>Username: [{}]</h2>\n".format(user)
      body += "          "
      body += "<hr style=\"color: {}; border-width: 2px;\">\n".format(c)

      # Show rest of attributes in pairs.
      for key in data:
        if key == "name":
          continue
        body += "          "
        body += "<p><b>{}</b>:  {}</p>\n".format(key.upper(), data[key])
      
      body += "          <br>\n"

    output = """      <!DOCTYPE html>
      <html>
        <head>
          <title>User: {}</title>
          {}
        </head>
        <body>
          {}        </body>
      </html>
      """.format(user, head, body)

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
  worker's queue for receiving incoming requests, and the various
  delays

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
                         "slow down the simulated traffic rate.")
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
      # Handle comments differently from since they don't overwrite;
      # guaranteed to be the only data type in a request.
      if 'comment' in req.data:
        self.db.extend(req.user, req.data)
      else:
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
  def __init__(self, machine, sim_speed=0.0001, random_seed=42):
    self.logger = logging.getLogger(__name__)
    if not isinstance(machine, Machine):
      raise ValueError(("Hey, when creating a simulation, please "
                        "provide a single machine (either a worker "
                        "or a load balancer)."))
    self.sim_speed = sim_speed
    self.edge = machine
    self.outagesP = False
    self.random_seed = random_seed
    self.reqs_sent = {}  # reqs sent out to system, keyed by timestamp
    self.reqs_acked = {} # timestamp when req sent out -> time of ack
    
    self.initialize()

  # Called at start of each run to clear stats and pysim env from
  # previous runs, so user can try different settings without
  # creating multiple simulations.
  def initialize(self):
    random.seed(self.random_seed)
    self.reqs_sent.clear()
    self.reqs_acked.clear()
    self.finish_time = None

    # Create pysim environment and register it with all the
    # workers, databases, and load balancers in the system; have to do
    # it the hard way (post-construction) for convenience of users.
    (self.env,
     self.load_balancers,
     self.workers,
     self.databases) = self.register_components(self.edge)

    self.configure_logger()

  def configure_logger(self):
    self.logger.setLevel(logging.INFO)

    f = SimFilter(self.env)  # custom filter for adding simulation time
    self.logger.addFilter(f)

    formatter = logging.Formatter('[%(stime)5s ms] %(message)s')

    # Log to stdout
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(formatter)

    # Log to file (mode 'w' overwrites, default of 'a' appends.)
    fh = logging.FileHandler("simulation.log", mode='w')
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

  def generate_stats(self):
    res = {}
    reqs_sent = self.reqs_sent
    reqs_acked = self.reqs_acked
    for k in reqs_acked:  # Keys are timestamps of outgoing reqs.
      reqs_sent[k].ack_time = reqs_acked[k]  # Annotate with ack time.
    
    num_reqs = len(reqs_sent)
    num_failed = 0
    for r in reqs_sent.values():
      if r.ack_time is None:
        num_failed += 1
    response_times = [ r.ack_time - r.time
                       for r in reqs_sent.values() if r.ack_time ]
    res['num_reqs'] = len(reqs_sent)
    res['num_failed'] = num_failed

    last_req_time = sorted(reqs_sent.keys())[-1]
    res['tps'] = res['num_reqs'] * 1000.0 / last_req_time

    if response_times:
      response_times.sort()
      res['mean_wait'] = sum(response_times)/float(len(response_times))
      res['min_wait'] = min(response_times)
      res['max_wait'] = max(response_times)
      lrt = len(response_times)
      if lrt % 2 == 0:
        res['median_wait'] = (response_times[lrt // 2 - 1] +
                              response_times[lrt // 2]) / 2
      else:
        res['median_wait'] = response_times[lrt // 2]
    res['reqs'] = [ (r.user, r.time, r.ack_time) for r
                    in reqs_sent.values() ]
    res['sim_time'] = self.finish_time
    res['num_dbs'] = len(self.databases)
    res['num_workers'] = len(self.workers)
    res['num_lbs'] = len(self.load_balancers)
    res['cost'] = (res['num_dbs'] * COST_DB +
                   res['num_workers'] * COST_WORKER +
                   res['num_lbs'] * COST_LOAD_BALANCER)
    return res

  def display_stats(self):
    stats = self.generate_stats()
    s = ""
    s += "\n"
    s += "=======================\n" 
    s += "*  Simulation Results * \n"
    s += "=======================\n"
    s += "Random Seed: {} | Traffic Rate {} | Outages: {}\n".format(
      self.random_seed,
      self.traffic_rate,
      "Yes" if self.outagesP else "No")
    s += "\n"

    s += "  {} databases at ${}K each\n".format(
      stats['num_dbs'], COST_DB)
    s += "+ {} workers at ${}K each\n".format(
      stats['num_workers'], COST_WORKER)
    s += "+ {} load balancers at ${}K each\n".format(
      stats['num_lbs'], COST_LOAD_BALANCER)
    s += "= ${:,} system hardware cost.\n".format(stats['cost'] * 1000)
    s += "\n"
    s += ("System handled {:.2f}".format(stats['tps']) +
          " transactions per second ")
    if stats['num_failed']:
      s += "with {} failures.\n"
    else:
      s += "without failures.\n"
    s += "\n"
    s += "Successfully processed {} of {} requests in {} s.\n".format(
      stats['num_reqs'] - stats['num_failed'],
      stats['num_reqs'], stats['sim_time'] / 1000.0)
    s += "Min/Max/Median/Mean response time: "
    s += "[ {} / {} / {} / {:.2f} ] ms.\n".format(stats['min_wait'],
                                                  stats['max_wait'],
                                                  stats['median_wait'],
                                                  stats['mean_wait'])

    print(s)

  def traffic_generator(self, env, rate, script):
    if not script:
      script = Simulation.script

    # Send out scripted requests at random intervals.
    for event in script:
      # Uniformly 1 to 100 ms between requests, scaled by rate.
      interval = int(random.randint(1,100) / float(rate))
      interval = max(interval, 1)
      yield env.timeout(interval)
      timestamp = env.now
      req = Request(event[0], event[1], timestamp)
      self.logger.debug("New request: {}".format(req))
      self.edge.receive_request(req)
      self.reqs_sent[timestamp] = req

    # Wait until all requests have been acked
    # flushed their queues before ending the simulation.
    countdown = TERMINAL_WAIT_TIMEOUT
    while (len(self.reqs_acked) <
           len(self.reqs_sent)) and countdown > 0:
      countdown -= 1
      yield env.timeout(1)

  # rate: scaling factor for time interval between requests.
  # script: unless provided, will use the one at the end of this file.
  def run(self, rate=1.0, script=False):
    # Reset everything between runs so users can experiment with
    # multiple successive traffic rates within same sim object.
    self.traffic_rate = rate
    self.initialize()

    p = self.env.process(self.traffic_generator(self.env,
                                                self.traffic_rate,
                                                script))
    self.env.run(until=p)
    self.finish_time = self.env.now
    self.display_stats()

# TODO:
#
# x bug with ever growing comments (and really, repeatedly overwritten
#   non-comment attributes--UGH that was crazy.  LEARNING: lists and
#   dicts assigned by reference and not value, have to copy to prevent
#   side-effects.  Miss the immutatbility of LISP/Scheme/Scala etc.)
# x bug with fewer number of requests on runs with higher traffic
#   rates.  Lol was losing traffic generation events because of zero
#   interval between certain requests, every once in a while, based on
#   high rate and bad luck.
# x bug with log--no not really, it was an artifact.
# display commands for diagnosis
# create (easier to use) commands for diagnosis
# machines have their own individual logs for people to inspect?
# load balancer can take varargs instead of list?
# tests for worker concurrency (for instance straight to handling vs queue)
# test db concurrency
#
# load balancer can take varargs instead of list?
#
# add stats/config info for individual runs to log?
#
# Pythonize and refactor with fewer self.<functions> all over, fewer
#   self.<var> for passing vars instead of parameters, documentation,
#   single versus double quotes,
#
# x generate fake data, add spike
# x test for max instances
# x test for stats
# x flip semantics for traffic rate
# x reset between multiple runs of same simulation
# x show stats for simulation
# x html output for one or all users
# x enforce max instances
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
#
#  script = [
#    ("apple", {"name": "apple person", "dob": "2015-06-01"}),
#    ("banana", {"name": "Banana Person", "dob": "2012-11-10"}),
#    ("apple", {"name": "Apple Person", "dob": "2015-06-01"}),
#    ("banana", {"country": "Canada"}),
#    ("apple", {"color": "aqua"}),
#  ]
  script = [('bgk',{'comment':['[katherineg] Card itself.']}),('chelseas',{'dob':'04/29/1978', 'city':'Clarkberg, Ontario'}),('jozefa',{'project':'Future-Proofed User-Facing Project', 'name':'Jozef Albin'}),('ryanc',{'project':'Reactive 24Hour System Engine', 'color':'blue'}),('josem',{'color':'silver'}),('bgk',{'comment':['[soraiaa] Book!']}),('javierw',{'comment':['[meiz] Former strategy audience.']}),('shawne',{'project':'Optimized Fresh-Thinking Collaboration', 'city':'Port Richard, Nova Scotia'}),('bgk',{'comment':['[franckb] Effort!']}),('bgk',{'comment':['[madhavir] Which.']}),('francoisb',{'color':'blanc', 'city':'Karenhaven, Prince Edward Island', 'name':'Francois Baron'}),('ambikab',{'job':'Engineer, Automotive'}),('susanr',{'name':'Susan Reid'}),('bgk',{'comment':['[tamir] Design!']}),('alexandrao',{'dob':'04/24/1945', 'color':'gray', 'job':'Personnel Officer'}),('bgk',{'comment':['[jozefa] Operation?!']}),('timoro',{'project':'Phased Incremental Flexibility', 'name':'Timor Olegshov'}),('bgk',{'comment':['[debraw] Scene!']}),('suzanneg',{'project':'La Liberte De Changer a L etat Pur'}),('kathleenc',{'comment':['[domingog] Offer.']}),('bgk',{'comment':['[susanr] Throughout!']}),('bgk',{'comment':['[gordonl] Else!']}),('bgk',{'comment':['[deborahd] Increase.']}),('anilv',{'city':'Cohenburgh, British Columbia', 'job':'Graphic Designer'}),('katherinev',{'city':'Travisfort, British Columbia', 'job':'Podiatrist', 'name':'Katherine Vaughn'}),('anthonyd',{'project':'Implemented Optimizing Service-Desk'}),('bgk',{'comment':['[alexandrao] A whether weight?!']}),('emilye',{'city':'Lake Charles, New Brunswick'}),('franckb',{'comment':['[clerosb] Industry.']}),('bgk',{'comment':['[hunterl] Realize chance!']}),('barbaras',{'project':'Profound Uniform Circuit', 'color':'yellow', 'name':'Barbara Sampson'}),('rickyp',{'comment':['[christophea] Forward.']}),('jayan',{'dob':'03/28/1995'}),('johnl',{'comment':['[kellyh] Three.']}),('karnavatik',{'comment':['[kristeng] Administration sea.']}),('marekn',{'name':'Marek Narodotskya'}),('bgk',{'comment':['[wandas] Through!']}),('joannev',{'job':'Editor, Magazine Features'}),('michelew',{'comment':['[travism] Feel.']}),('lyndat',{'comment':['[michaelc] Finish.']}),('barbaras',{'dob':'09/30/1977', 'job':'Hospital Doctor'}),('michaelc',{'dob':'07/18/1981', 'name':'Michael Chan'}),('wandas',{'color':'purple', 'name':'Wanda Santos'}),('hunterl',{'dob':'03/03/1971'}),('bgk',{'comment':['[sandrat] Feel?!']}),('jessicaa',{'name':'Jessica Adams'}),('leonardob',{'color':'teal'}),('zuzanr',{'color':'gray', 'name':'Zuzan Rogers'}),('christophea',{'project':'L Art De Changer a la Pointe', 'city':'Cameronburgh, Nunavut', 'dob':'09/09/1944', 'color':'argent'}),('sueellenv',{'dob':'10/15/1959', 'city':'Port April, Quebec', 'job':'Artist', 'color':'gray'}),('ireneb',{'dob':'08/05/1947', 'city':'East Jacqueline, Newfoundland and Labrador', 'job':'Accountant, Chartered Certified'}),('anthonyg',{'dob':'07/08/1948', 'city':'Amyberg, Nunavut'}),('javierw',{'color':'maroon', 'job':'Community Development Worker'}),('mohammedj',{'project':'Triple-Buffered Disintermediate Groupware', 'city':'Hoffmanside, New Brunswick', 'dob':'06/14/1947'}),('montserrata',{'project':'Cross-Group Uniform Encoding', 'city':'West Richardport, Saskatchewan'}),('susanr',{'comment':['[bryanm] Identify staff director.']}),('bgk',{'comment':['[melissab] Smile send including!']}),('jeremyp',{'city':'Santanafurt, Manitoba'}),('fortunatac',{'comment':['[henriettek] Simple recently.']}),('bgk',{'comment':['[johnl] Work relate?!']}),('charlesw',{'dob':'06/22/1996', 'city':'Gardnerfort, Nunavut', 'job':'Cartographer', 'name':'Charles Ware'}),('bgk',{'comment':['[ambikab] Church <3']}),('bgk',{'comment':['[schang] Hair!!!']}),('sorayav',{'project':'Focused Secondary Framework', 'city':'Kevinborough, Quebec', 'job':'Student', 'name':'Soraya Vatanakh'}),('jenniferl',{'color':'silver', 'city':'West Carla, Quebec', 'project':'Compatible National Emulation'}),('aaronc',{'job':'Student'}),('montserrata',{'dob':'09/28/1979', 'job':'Engineer, Biomedical', 'name':'Montserrat Aparicio'}),('bgk',{'comment':['[davidz] Person <3']}),('michelles',{'comment':['[joshuam] See cold.']}),('daniellej',{'dob':'04/23/1972'}),('bgk',{'comment':['[maryn] Fund!!!']}),('bgk',{'comment':['[leonardob] Feeling catch!!!']}),('rebeccam',{'dob':'08/09/1938'}),('fortunatac',{'comment':['[charlesw] Cell wait industry.']}),('bgk',{'comment':['[danac] Write half!!!']}),('christophea',{'job':'Controleur De Performances'}),('renah',{'comment':['[agatheg] Site thus.']}),('bgk',{'comment':['[alexandrec] His.']}),('luismiguelc',{'color':'green', 'dob':'11/05/1943', 'name':'Luis Miguel Cerezo'}),('cemalk',{'color':'olive', 'dob':'09/18/1996', 'name':'Cemal Keudel'}),('michelles',{'name':'Michelle Smith'}),('bgk',{'comment':['[franciscojavierg] Low cultural director!']}),('martinb',{'comment':['[susanf] Likely form vote.']}),('tanl',{'comment':['[michaelc] Other.']}),('gordonl',{'project':'Synergized Systemic Definition', 'color':'aqua'}),('bgk',{'comment':['[aaronn] Bank various!']}),('bgk',{'comment':['[henriettek] Say.']}),('tanl',{'project':'Cross-Group Transitional Matrix', 'color':'yellow', 'job':'Teacher', 'name':'Tan Li'}),('bgk',{'comment':['[amishp] The <3']}),('philiph',{'project':'Universal Solution-Oriented Database', 'color':'white', 'job':'Learning Disability Nurse'}),('melissab',{'dob':'02/14/1945'}),('agatheg',{'project':'L Avantage De Rouler Sans Soucis'}),('kellyh',{'dob':'11/09/1966', 'name':'Kelly Hall'}),('soraiaa',{'project':'Object-Based Local Capability'}),('dianaf',{'city':'Ellisburgh, New Brunswick'}),('melaniew',{'dob':'11/05/1947', 'color':'aqua', 'name':'Melanie Williams'}),('bryanm',{'color':'blue', 'city':'Loriburgh, Newfoundland and Labrador'}),('tarekk',{'project':'Secured Encompassing Task-Force', 'dob':'04/14/1989'}),('kathleenc',{'color':'teal'}),('andresv',{'job':'Lecturer, Further Education'}),('julieb',{'comment':['[katherineg] Property amount star.']}),('virginieg',{'name':'Virginie Guyot'}),('michelew',{'comment':['[melissab] Often.']}),('zacharys',{'comment':['[nicolej] May.']}),('franciscojavierg',{'project':'Intuitive User-Facing Structure'}),('franciscojavierg',{'color':'blue', 'city':'Craigmouth, Alberta', 'name':'Francisco Javier Gilabert'}),('bgk',{'comment':['[jenniferl] Talk!']}),('davidd',{'job':'Health And Safety Adviser'}),('nicoles',{'color':'silver', 'name':'Nicole Suarez'}),('bgk',{'comment':['[lindseyl] Check outside!']}),('josettem',{'comment':['[lindseyl] Within culture.']}),('bgk',{'comment':['[heatherm] Unit at bed!']}),('kathleenk',{'comment':['[ronaldt] Very.']}),('shawne',{'dob':'07/19/1962', 'job':'Paediatric Nurse', 'name':'Shawn Edwards'}),('bgk',{'comment':['[barbarah] School!!!']}),('christophers',{'dob':'05/02/1939', 'name':'Christopher Smith'}),('bgk',{'comment':['[ireneb] Visit <3']}),('bgk',{'comment':['[michelles] Company six!']}),('bgk',{'comment':['[davidd] Specific small?!']}),('bgk',{'comment':['[barbarah] School <3']}),('bgk',{'comment':['[elenag] Growth!!!']}),('belenc',{'dob':'05/25/1982', 'city':'West Johnberg, New Brunswick', 'name':'Belen Cabanas', 'color':'yellow'}),('maryn',{'color':'navy'}),('henriettek',{'color':'bordeaux', 'project':'Le Pouvoir D Innover Naturellement', 'name':'Henriette Klein'}),('marcinj',{'dob':'04/29/1981', 'color':'silver', 'city':'South Sarahchester, British Columbia'}),('bgk',{'comment':['[belenc] Trial agency skin!']}),('hollyb',{'comment':['[meiz] Could.']}),('bgk',{'comment':['[gemmaa] Quite?!']}),('diedrichs',{'city':'New Alexandriabury, Newfoundland and Labrador', 'name':'Diedrich Staude'}),('bgk',{'comment':['[charlesp] White <3']}),('hollyb',{'color':'teal', 'dob':'10/16/1994', 'job':'Engineer, Control And Instrumentation', 'name':'Holly Bender'}),('aaronn',{'project':'User-Centric Asymmetric Infrastructure', 'job':'Sports Therapist', 'name':'Aaron Nguyen'}),('bgk',{'comment':['[davidz] Boy?!']}),('wandas',{'dob':'11/10/1980', 'city':'South David, New Brunswick'}),('bgk',{'comment':['[marcinj] Mention tell wrong!']}),('bhairavig',{'name':'Bhairavi Gupta'}),('zacharys',{'color':'navy', 'name':'Zachary Schultz'}),('verap',{'comment':['[julieb] Job.']}),('clerosb',{'city':'West Bradley, Manitoba'}),('mamiec',{'color':'olive', 'project':'Customizable Grid-Enabled Throughput'}),('verap',{'dob':'09/01/1980'}),('bgk',{'comment':['[danac] Who <3']}),('kimberlyd',{'name':'Kimberly Diaz'}),('katherineg',{'city':'Harrismouth, Saskatchewan', 'name':'Katherine Garrett'}),('hayleyj',{'comment':['[mohammedj] Rest.']}),('marekn',{'color':'lime', 'project':'Programmable Context-Sensitive Customer Loyalty', 'job':'Chef', 'city':'Lake Catherinebury, Nova Scotia'}),('bgk',{'comment':['[zuzanr] Here always nature!']}),('bgk',{'comment':['[omars] Approach!!!']}),('ireneb',{'comment':['[lyndat] Individual.']}),('bgk',{'comment':['[melissac] Process behavior free <3']}),('bgk',{'comment':['[bryanm] Store?!']}),('omars',{'dob':'11/12/1952'}),('bgk',{'comment':['[ryanj] Sometimes!']}),('ryanj',{'project':'Pre-Emptive 3Rdgeneration Functionalities', 'city':'New Richard, Manitoba'}),('agatheg',{'job':'Fleuriste', 'name':'Agathe Garnier'}),('mamiec',{'comment':['[melissac] Care century.']}),('michelew',{'city':'Stevensburgh, Quebec'}),('bgk',{'comment':['[ahmets] Order piece!']}),('stephaniev',{'dob':'10/04/1954', 'city':'Jenniferport, Quebec'}),('alexandrec',{'color':'ble', 'city':'West Granttown, Prince Edward Island'}),('jenniferh',{'project':'Switchable Non-Volatile Encryption'}),('nathanr',{'dob':'03/16/1957', 'city':'Josephfurt, New Brunswick'}),('wojciechs',{'comment':['[josem] North.']}),('marcinj',{'name':'Marcin Janc'}),('bgk',{'comment':['[clerosb] Thus!!!']}),('agatheg',{'comment':['[cemalk] Recognize care explain.']}),('ronaldt',{'city':'Holmestown, New Brunswick', 'name':'Ronald Thompson'}),('tamir',{'comment':['[jenniferh] Camera behavior for.']}),('aaronc',{'city':'Stevenmouth, Quebec'}),('meiz',{'job':'Technical Officer', 'name':'Mei Zhang'}),('omars',{'project':'Optimized Methodical Conglomeration', 'color':'teal', 'name':'Omar Smith'}),('travism',{'city':'Hayesside, New Brunswick'}),('bgk',{'comment':['[wandas] Shake!']}),('melissab',{'color':'purple'}),('hayleyj',{'job':'Emergency Planning/Management Officer'}),('wojciechs',{'comment':['[christineh] Sort bed ago.']}),('leonardob',{'project':'Multi-Channeled 6Thgeneration Implementation', 'city':'New Christopherport, Quebec', 'job':'IT Consultant', 'name':'Leonardo Baptista'}),('verap',{'color':'navy', 'project':'Progetto Fondamentale Non-Volatile', 'city':'South Cynthia, Yukon Territory'}),('ronaldt',{'comment':['[joannev] My.']}),('bgk',{'comment':['[erminiam] Man!!']}),('melissac',{'project':'Persistent Systemic Strategy', 'color':'black', 'job':'Intelligence Analyst'}),('bgk',{'comment':['[ireneb] Area!']}),('johnl',{'comment':['[joannev] Rate.']}),('martinb',{'color':'fuchsia', 'city':'Lake Audrey, Quebec', 'name':'Martin Brady'}),('belenc',{'job':'Therapist, Nutritional'}),('bgk',{'color':'silver', 'city':'Lake Richard, British Columbia', 'job':'Various'}),('kristeng',{'color':'gray', 'city':'Port Wendy, Nova Scotia'}),('anthonyg',{'project':'Self-Enabling Neutral Focus Group', 'color':'fuchsia', 'name':'Anthony Gonzalez'}),('nicolej',{'color':'fuchsia', 'job':'Risk Manager'}),('clerosb',{'color':'black', 'job':'Television Floor Manager', 'name':'Cleros Bernardi'}),('danac',{'dob':'09/02/1953', 'project':'Fully-Configurable Interactive Budgetary Management'}),('josem',{'comment':['[ronaldr] Him.']}),('alexandrao',{'project':'Persevering Solution-Oriented Task-Force', 'city':'Smithmouth, New Brunswick'}),('mohammedj',{'job':'Scientist, Audiological'}),('bgk',{'comment':['[amyh] Check anyone!!']}),('francoisb',{'dob':'03/12/1953'}),('bgk',{'comment':['[heatherm] Worry?!']}),('hsiangmeic',{'project':'Self-Enabling Clear-Thinking Help-Desk', 'city':'Andersonbury, Manitoba'}),('bgk',{'comment':['[meryemm] Possible idea <3']}),('reishiongx',{'job':'Retail'}),('bgk',{'comment':['[travism] Exactly me.']}),('bgk',{'comment':['[mamiec] Establish!!!']}),('bgk',{'comment':['[alexandrao] Get!!!']}),('bgk',{'comment':['[luismiguelc] Yet!!']}),('bgk',{'comment':['[julieb] Challenge.']}),('chelseas',{'comment':['[timoro] Part.']}),('bgk',{'comment':['[andresv] There attorney!']}),('lyndat',{'project':'Business-Focused Modular Synergy', 'job':'Advertising Copywriter'}),('amhedo',{'project':'Seamless Reciprocal Leverage', 'color':'lime'}),('tren',{'dob':'01/14/1998', 'name':'Tong Ren'}),('bgk',{'comment':['[ambikab] Cover!']}),('bgk',{'comment':['[tamir] Civil instead guess.']}),('yongmeim',{'job':'Driver'}),('bgk',{'comment':['[hunterl] Church!!!']}),('bgk',{'comment':['[hunterl] Catch!']}),('rickyp',{'comment':['[martinb] We.']}),('danac',{'job':'Doctor, General Practice', 'name':'Dana Cannon'}),('emilye',{'project':'Centralized Composite Data-Warehouse'}),('michaelw',{'dob':'06/19/1981', 'color':'green'}),('cynthiav',{'project':'Face-To-Face Value-Added Protocol', 'city':'West Mathewfurt, Saskatchewan'}),('jessicaa',{'comment':['[jenniferh] Peace.']}),('clerosb',{'project':'Set Di Istruzioni Visionaria Locale', 'dob':'03/04/1955'}),('karnavatik',{'dob':'08/23/1957', 'city':'North Darlenemouth, Ontario', 'job':'Gaffer', 'name':'Karnavati Kaseri'}),('meiz',{'comment':['[virginieg] Government education standard.']}),('martinb',{'job':'Translator'}),('whitneyh',{'color':'teal'}),('angelad',{'name':'Angela Davies'}),('annak',{'comment':['[lib] Help.']}),('cynthiav',{'dob':'05/09/1975', 'job':'Police Officer'}),('charlesw',{'project':'Front-Line Tertiary Project'}),('bgk',{'comment':['[kimberlyd] A!']}),('bgk',{'comment':['[nathanr] Position analysis!!!']}),('madhavir',{'dob':'12/11/1969', 'city':'Port Williamshire, New Brunswick', 'name':'Madhadvi Ravind'}),('katherinev',{'dob':'01/18/1974', 'project':'Front-Line Logistical Info-Mediaries'}),('schang',{'name':'Sigmund Chang'}),('bgk',{'comment':['[franciscojavierg] Whom!!!']}),('bgk',{'comment':['[peters] Word!!!']}),('angelac',{'dob':'05/06/1959', 'city':'Parsonsberg, Alberta', 'name':'Angela Cohen'}),('jenniferl',{'dob':'02/28/1987', 'name':'Jennifer Lee'}),('jayan',{'comment':['[agatheg] Direction develop smile prevent.']}),('johnl',{'color':'aqua', 'project':'Synergistic Intermediate Analyzer', 'job':'Health And Safety Inspector'}),('angelad',{'job':'Operations Geologist'}),('javierw',{'project':'Enhanced Client-Server Access', 'dob':'04/08/1974'}),('sandrat',{'comment':['[marcinj] Weight may figure.']}),('bgk',{'project':'B7', 'dob':'07/13/1981'}),('bgk',{'comment':['[gemmaa] World probably?!']}),('tarekk',{'comment':['[angelac] Everybody work moment.']}),('bgk',{'comment':['[diedrichs] Effect!']}),('fortunatac',{'name':'Fortunata Caputo'}),('josettem',{'color':'sarcelle'}),('katherinev',{'color':'navy'}),('cynthiav',{'color':'olive', 'name':'Cynthia Vasquez'}),('ryanj',{'color':'lime', 'name':'Ryan Johnson'}),('meiz',{'color':'silver'}),('montserrata',{'comment':['[daniellej] She those quality.']}),('bgk',{'comment':['[elenag] Sure!!']}),('amberv',{'dob':'09/10/1991', 'color':'yellow', 'job':'Investment Banker, Corporate', 'name':'Amber Vance'}),('henriettek',{'comment':['[charlesp] Economic on.']}),('renah',{'comment':['[tren] In cold.']}),('shawne',{'comment':['[jeremyw] Hotel clear.']}),('timoro',{'city':'Brandonland, Newfoundland and Labrador'}),('elizabethm',{'color':'silver'}),('deborahd',{'project':'Robust 5th-Generation Portal', 'job':'Dancer'}),('ronaldr',{'project':'Cross-Group Interactive Process Improvement', 'job':'Community Arts Worker'}),('kellyh',{'project':'Pre-Emptive Multi-State Task-Force', 'color':'aqua', 'city':'Jacquelineview, Ontario'}),('tarekk',{'city':'Port Erin, British Columbia'}),('nicoles',{'dob':'04/21/1958', 'city':'East Martha, Ontario'}),('gemmaa',{'project':'Universal Bifurcated Hub'}),('joshuam',{'project':'Organized Holistic Help-Desk'}),('ericp',{'name':'Eric Pierce'}),('ronaldr',{'color':'maroon', 'city':'New Joseph, New Brunswick'}),('bgk',{'comment':['[justinj] Drug!']}),('bgk',{'comment':['[aaronc] Send road!!!']}),('hsiangmeic',{'color':'fuchsia', 'dob':'11/19/1965', 'name':'Hsiang-Mei Ch'}),('katherineg',{'job':'Press Photographer'}),('bgk',{'comment':['[sueellenv] Call!']}),('amishp',{'dob':'12/12/1972', 'job':'Media Planner'}),('diedrichs',{'comment':['[angelad] Open process child.']}),('jordanm',{'color':'maroon'}),('suzanneg',{'city':'North Paige, British Columbia'}),('andresv',{'dob':'11/07/1949', 'city':'New Rebecca, British Columbia'}),('michaelc',{'project':'Self-Enabling Bi-Directional Database', 'color':'white', 'job':'Counselor'}),('travism',{'project':'Switchable Eco-Centric Forecast', 'job':'Physicist, Medical', 'name':'Travis Martinez'}),('fernandov',{'color':'white', 'dob':'12/29/1948'}),('joannev',{'color':'aqua', 'project':'Secured Mission-Critical Forecast', 'dob':'03/27/1981', 'city':'Brandonstad, New Brunswick'}),('gordonl',{'city':'Sanchezmouth, Northwest Territories', 'job':'Investment Analyst', 'name':'Gordon Lee'}),('shawne',{'color':'white'}),('bgk',{'comment':['[melaniew] Carry same line protect!']}),('elenag',{'name':'Elena Gemen'}),('fatimab',{'city':'Osbornbury, New Brunswick'}),('michaelc',{'city':'East Patrick, Nova Scotia'}),('bgk',{'comment':['[anilv] Argue point!!']}),('christophers',{'city':'Zoeborough, Ontario', 'job':'Optometrist'}),('davidz',{'project':'Diverse Non-Volatile Core', 'city':'New Christinebury, Nunavut', 'job':'Television Production Assistant', 'name':'David Zamora'}),('ahmets',{'city':'West Janeport, Quebec'}),('bgk',{'comment':['[joshuam] Half room by?!']}),('debraw',{'name':'Debra Wright'}),('bgk',{'comment':['[bhairavig] Also <3']}),('jayan',{'color':'white', 'job':'Chief Operating Officer'}),('zacharys',{'job':'Company Secretary'}),('rachelc',{'dob':'01/29/1993'}),('susanr',{'comment':['[meiz] Politics once.']}),('ryanc',{'comment':['[melissab] Camera traditional over.']}),('bgk',{'comment':['[madhavir] Ever commercial even very!!!']}),('nicolej',{'dob':'04/03/1997'}),('bgk',{'comment':['[amhedo] Peace!']}),('julieb',{'color':'white', 'city':'Wesleyhaven, Manitoba'}),('karenm',{'dob':'11/13/1995', 'project':'Fundamental Needs-Based Moderator'}),('bgk',{'comment':['[gemmaa] Age!!']}),('susanf',{'project':'L Assurance De Louer Autrement', 'color':'blanc', 'name':'Susan Fontaine'}),('cemalk',{'comment':['[nicolej] Blue.']}),('daniellej',{'project':'Managed 4Thgeneration Conglomeration', 'city':'East Jill, Ontario', 'job':'Audiological Scientist', 'name':'Danielle Johnson'}),('bgk',{'comment':['[amyh] Gas!']}),('bgk',{'comment':['[barbarah] End class.']}),('annak',{'project':'Quality-Focused Mission-Critical Collaboration'}),('rebeccam',{'color':'silver'}),('barbarah',{'dob':'07/10/1941', 'color':'purple', 'job':'Production Designer, Theatre/Television/Film', 'name':'Barbara Holland'}),('michelles',{'project':'Mandatory Heuristic Time-Frame', 'job':'Building Control Surveyor'}),('alexandrec',{'project':'La Possibilite dInnover Plus Rapidement', 'job':'Opticien', 'name':'Alexandre Chauvet'}),('bryanm',{'comment':['[katherineg] Whom century brother.']}),('peters',{'color':'purple', 'name':'Peter Singh'}),('bgk',{'comment':['[tarekk] Side!']}),('dianaf',{'dob':'11/10/1952', 'color':'blue', 'job':'Development Worker, Community', 'name':'Diana Foster'}),('virginieg',{'dob':'07/05/1954', 'city':'East David, Quebec', 'job':'Maraicher'}),('sandrat',{'city':'Port Matthewchester, Nova Scotia'}),('bgk',{'comment':['[tamir] Again <3']}),('lindseyl',{'dob':'07/16/1996', 'city':'South Rodneyview, Prince Edward Island', 'name':'Lindsey Lee', 'project':'Compatible Foreground Focus Group'}),('kristeng',{'comment':['[glennf] Bed.']}),('anthonyd',{'color':'olive', 'city':'South Brentbury, Manitoba', 'job':'Public Relations Officer', 'name':'Anthony Day'}),('luismiguelc',{'project':'Total Solution-Oriented Instruction Set'}),('bgk',{'comment':['[tanl] Position <3']}),('erminiam',{'color':'maroon', 'project':'Successo Ricontestualizzata Stabile', 'job':'Event Organiser'}),('tamir',{'color':'gray', 'dob':'06/23/1990'}),('tanl',{'dob':'01/24/1939'}),('aaronn',{'color':'black'}),('bgk',{'comment':['[kathleenk] Chance!!']}),('amyh',{'dob':'10/10/1984', 'color':'olive'}),('ahmets',{'color':'fuchsia', 'project':'Synergistic 5Thgeneration Access', 'job':'Armed Forces Technical Officer', 'name':'Ahmet Schafer'}),('bgk',{'comment':['[henriettek] No know care!']}),('bhairavig',{'dob':'12/27/1954', 'color':'gray', 'job':'Engineer, Technical Sales'}),('meryemm',{'comment':['[susanr] Individual including.']}),('timoro',{'dob':'08/20/1979', 'color':'lime', 'job':'Manager'}),('bgk',{'comment':['[madhavir] Check!']}),('joshuam',{'color':'lime', 'city':'Heidiland, Saskatchewan', 'job':'Sport And Exercise Psychologist', 'name':'Joshua Mccann'}),('bgk',{'comment':['[susanr] Be <3']}),('tren',{'color':'maroon', 'city':'Port Tomberg, British Columbia', 'job':'Community Organizer'}),('henriettek',{'comment':['[soraiaa] Occur president industry.']}),('bgk',{'comment':['[michelles] Drug represent <3']}),('heatherm',{'color':'teal', 'job':'Systems Analyst'}),('vinodk',{'color':'blue', 'dob':'07/12/1944'}),('bgk',{'comment':['[karenm] Race!!!']}),('mohammedj',{'comment':['[fatimab] Property late ball.']}),('bgk',{'comment':['[josettem] Here!']}),('jozefa',{'comment':['[domingog] Spring building explain because.']}),('franciscojavierg',{'dob':'05/31/1970', 'job':'Tefl Teacher'}),('nicoles',{'project':'Profound 24Hour Protocol', 'job':'Warden/Ranger'}),('bgk',{'comment':['[renah] Board!']}),('johnl',{'dob':'11/24/2005', 'city':'West Joshua, New Brunswick'}),('jeremyw',{'comment':['[andresv] Different.']}),('jenniferh',{'comment':['[karenm] Quality.']}),('wojciechs',{'project':'Face-To-Face Non-Volatile Forecast', 'city':'Port Loretta, Yukon Territory'}),('christineh',{'dob':'03/04/1981', 'job':'Artist'}),('bgk',{'comment':['[emilye] Him!']}),('bgk',{'comment':['[timoro] Our!!']}),('anthonyd',{'comment':['[soraiaa] Yet main me.']}),('bgk',{'comment':['[mamiec] Kid!!!']}),('anilv',{'dob':'03/15/1959'}),('bgk',{'comment':['[agatheg] Source.']}),('bgk',{'comment':['[anilv] Other!!']}),('bgk',{'comment':['[gemmaa] Throughout force!']}),('ahmets',{'comment':['[tarekk] Pressure.']}),('rachelc',{'project':'Up-Sized Systemic Utilization', 'name':'Rachel Clark'}),('bgk',{'comment':['[omars] Know!']}),('jessicaa',{'color':'navy', 'job':'Horticultural Consultant'}),('amyh',{'project':'Organic Discrete Attitude', 'city':'Serranoton, Manitoba'}),('montserrata',{'color':'gray'}),('chelseas',{'job':'Clinical Embryologist', 'name':'Chelsea Singh'}),('kellyh',{'job':'Regulatory Affairs Officer'}),('martinb',{'dob':'09/25/1939', 'project':'Sharable Mission-Critical Model'}),('mamiec',{'city':'Lake Melissaburgh, Prince Edward Island', 'job':'President', 'name':'Mamie Chen'}),('gracer',{'dob':'08/11/1956'}),('michelew',{'dob':'09/11/2003', 'job':'Administrator, Local Government', 'name':'Michele Walker'}),('bgk',{'comment':['[amhedo] Indeed hand.']}),('wojciechs',{'color':'fuchsia', 'dob':'01/11/1968', 'job':'Hycel'}),('wandas',{'comment':['[ahmets] Box.']}),('franckb',{'project':'Le Confort D Avancer Sans Soucis'}),('charlesw',{'color':'green'}),('emilye',{'color':'white', 'dob':'05/29/2001', 'job':'Wellsite Geologist', 'name':'Emily Evans'}),('lilyw',{'comment':['[annak] Figure.']}),('wandas',{'project':'Enterprise-Wide Context-Sensitive Approach', 'job':'Medical Technical Officer'}),('marcinj',{'project':'Ergonomic Multi-Tasking Internet Solution', 'job':'Oceanonauta'}),('erminiam',{'dob':'03/28/1964'}),('bgk',{'comment':['[franckb] Daughter activity mother!']}),('bgk',{'comment':['[jamesd] Natural!']}),('karenm',{'city':'Robertfurt, Manitoba'}),('gracer',{'color':'blue', 'city':'South John, Nunavut', 'job':'Water Quality Scientist'}),('cemalk',{'comment':['[clerosb] Scene.']}),('bgk',{'comment':['[javierw] Eye <3']}),('franckb',{'color':'violet', 'job':'Boulanger', 'name':'Franck Besnard'}),('bgk',{'comment':['[johnl] Pick development lawyer?!']}),('melissab',{'project':'Future-Proofed Static Secured Line', 'city':'Port Kimberlyborough, Northwest Territories', 'job':'Interpreter', 'name':'Melissa Burke'}),('aaronc',{'comment':['[meryemm] Thing.']}),('domingog',{'dob':'08/13/1956', 'city':'New Ronald, Ontario'}),('jozefa',{'dob':'07/16/1956', 'job':'Intendent'}),('francoisb',{'project':'Le Pouvoir D Avancer Sans Soucis', 'job':'Ingenieur Recherche Et Developpement En Agroalimentaire'}),('cemalk',{'project':'Balanced Stable Approach', 'job':'Herbalist'}),('gemmaa',{'color':'silver'}),('angelad',{'color':'aqua', 'project':'Front-Line Grid-Enabled Leverage', 'dob':'09/30/1987', 'city':'Donnafort, Nunavut'}),('bgk',{'comment':['[aaronn] Age never change appear!']}),('vinodk',{'comment':['[bryanm] These raise agent.']}),('bgk',{'comment':['[karenm] Wonder apply?!']}),('melaniew',{'city':'New Moniqueview, Ontario', 'job':'Air Cabin Crew'}),('aaronn',{'dob':'08/27/1942', 'city':'Port Natashafort, Newfoundland and Labrador'}),('bgk',{'comment':['[jenniferh] Position.']}),('bgk',{'comment':['[hsiangmeic] Line arm <3']}),('bgk',{'comment':['[karnavatik] Nice!!']}),('bgk',{'name':'B. G. K.'}),('susanf',{'city':'Camachoport, New Brunswick'}),('sandrat',{'dob':'09/27/1948'}),('zacharys',{'comment':['[annak] Painting.']}),('whitneyh',{'dob':'12/24/2003', 'project':'Innovative Zero Tolerance Internet Solution', 'job':'Archaeologist', 'name':'Whitney Hicks'}),('bgk',{'comment':['[zacharys] Political deal!!']}),('bgk',{'comment':['[alexandrec] Situation they!!!']}),('elenag',{'project':'User-Centric Background Access', 'dob':'04/12/1959'}),('renah',{'project':'Phased Radical Monitoring', 'city':'Lorifort, New Brunswick', 'name':'Rena Hofmann'}),('ericp',{'dob':'07/19/1972', 'color':'teal', 'city':'Christophershire, Saskatchewan'}),('ryanc',{'dob':'07/24/1974', 'job':'Designer, Interior/Spatial', 'name':'Ryan Chandler'}),('angelac',{'color':'olive', 'job':'Diplomatic Services Operational Officer'}),('hollyb',{'city':'Troyton, Alberta'}),('virginieg',{'comment':['[angelad] The image.']}),('hayleyj',{'name':'Hayley Jones'}),('fatimab',{'comment':['[amhedo] Picture.']}),('alexandrao',{'comment':['[annak] Direction.']}),('heatherm',{'comment':['[andresv] Thought.']}),('renah',{'dob':'12/28/1949', 'job':'Community Pharmacist'}),('peters',{'dob':'12/12/1942', 'city':'Port Jessica, Prince Edward Island'}),('lyndat',{'dob':'10/09/2005', 'city':'West Jennifer, Prince Edward Island', 'name':'Lynda Taylor'}),('glennf',{'name':'Glenn Forster'}),('nicolej',{'project':'Expanded Regional Array', 'city':'Martinside, Quebec', 'name':'Nicole Johnson'}),('karenm',{'comment':['[josettem] Student agree investment.']}),('ryanc',{'city':'New William, Northwest Territories'}),('tinag',{'dob':'07/08/1942'}),('alexandrec',{'dob':'03/16/1972'}),('nicoles',{'comment':['[johnl] Pattern large.']}),('josettem',{'project':'L Assurance De Changer Plus Rapidement', 'name':'Josette Martinea'}),('bgk',{'comment':['[charlesp] Model!']}),('barbaras',{'city':'Chambersburgh, Alberta'}),('zuzanr',{'job':'Lecturer, Higher Education'}),('ryanj',{'dob':'04/28/1976', 'job':'Cartographer'}),('tinag',{'color':'white', 'project':'Phased Reciprocal Algorithm', 'job':'Chartered Public Finance Accountant', 'name':'Tina Gould'}),('ireneb',{'project':'Synergistic Local Graphic Interface', 'color':'purple'}),('bgk',{'comment':['[jeremyw] World use cold give!']}),('whitneyh',{'comment':['[zacharys] Maybe also.']}),('annak',{'comment':['[gordonl] Hotel civil form.']}),('hunterl',{'project':'User-Centric Asymmetric Paradigm'}),('bgk',{'comment':['[gemmaa] Finish stay!!!']}),('michaelw',{'project':'De-Engineered Exuding Website'}),('jordanm',{'comment':['[julieb] Travel.']}),('barbarah',{'project':'Robust Local Architecture'}),('bgk',{'comment':['[melissab] Recently!']}),('angelac',{'project':'Down-Sized Value-Added Structure'}),('stephaniev',{'project':'Reduced Holistic Database', 'job':'Surveyor, Minerals'}),('deborahd',{'name':'Deborah Dunn'}),('josettem',{'dob':'12/19/1966', 'city':'Kaylachester, New Brunswick', 'job':'Chef De Rayon'}),('amberv',{'city':'Josephside, Nunavut'}),('alexandrao',{'comment':['[vinodk] Across discussion.']}),('madhavir',{'color':'purple', 'project':'Networked Tangible Monitoring'}),('tarekk',{'comment':['[amberv] Animal across.']}),('annak',{'dob':'09/16/1954', 'color':'gray', 'job':'Operational Investment Banker'}),('debraw',{'comment':['[rickyp] Bring.']}),('lindseyl',{'comment':['[alexandrao] Huge.']}),('bgk',{'comment':['[soraiaa] Really opportunity!!']}),('amhedo',{'city':'Swansonchester, Alberta'}),('jamesd',{'project':'Customer-Focused Cohesive Firmware', 'dob':'12/23/1982', 'job':'Arboriculturist', 'name':'James de Gruil'}),('davidd',{'dob':'03/05/1980', 'city':'Krystalberg, Nova Scotia'}),('bgk',{'comment':['[christophea] May them!!']}),('jamesd',{'comment':['[jenniferl] Her forward.']}),('rachelc',{'color':'silver', 'city':'North Kimberlyhaven, Northwest Territories', 'job':'Chief Operating Officer'}),('amberv',{'project':'Cross-Platform Cohesive Budgetary Management'}),('bgk',{'comment':['[martinb] Officer heavy.']}),('fernandov',{'name':'Fernando Vinas'}),('kathleenc',{'city':'South Andreaburgh, Saskatchewan', 'job':'Consulting Civil Engineer'}),('henriettek',{'city':'Port Carlosfort, British Columbia'}),('bgk',{'comment':['[kellyh] Alone military election!!']}),('philiph',{'city':'Gregoryland, Nova Scotia', 'name':'Philip Harris'}),('bgk',{'comment':['[debraw] Stop!!']}),('lindseyl',{'comment':['[alexandrec] Full.']}),('katherineg',{'project':'Cross-Platform Zero Administration Methodology', 'color':'teal', 'dob':'03/21/1954'}),('bryanm',{'project':'Multi-Tiered Bottom-Line Intranet', 'dob':'05/31/1961', 'name':'Bryan McDonald'}),('bgk',{'comment':['[johnl] Spend church require!']}),('elenag',{'color':'gray', 'city':'Christinefort, Manitoba', 'job':'Advertising Account Planner'}),('heatherm',{'name':'Heather Mccarthy'}),('bgk',{'comment':['[aaronn] Again alone!!']}),('bgk',{'comment':['[travism] Trip worker cause?!']}),('bgk',{'comment':['[domingog] Machine.']}),('gordonl',{'dob':'09/01/1991'}),('jamesd',{'color':'yellow'}),('yongmeim',{'dob':'12/17/1962'}),('renah',{'color':'green'}),('angelac',{'comment':['[ericp] Similar standard view.']}),('bgk',{'comment':['[sitas] Myself!!']}),('kristeng',{'dob':'10/25/1974', 'name':'Kristen Gibson'}),('jeremyw',{'comment':['[kathleenk] Together wear business.']}),('bgk',{'comment':['[hunterl] Front!!']}),('schang',{'comment':['[suzanneg] Own.']}),('dianaf',{'project':'Persevering Tertiary Graphic Interface'}),('julieb',{'dob':'12/09/1953', 'job':'Arts Administrator', 'name':'Julie Brown'}),('bgk',{'comment':['[javierw] Who important natural!']}),('erminiam',{'city':'Lake Michelle, Newfoundland and Labrador', 'name':'Erminia Moretti'}),('peters',{'comment':['[justinj] Measure yet magazine.']}),('clerosb',{'comment':['[shawne] Heart technology.']}),('soraiaa',{'color':'black'}),('bgk',{'comment':['[ericp] Worker.']}),('tamir',{'project':'Quality-Focused Modular Artificial Intelligence', 'name':'Tami Rodriguez'}),('bgk',{'comment':['[reishiongx] Main race!']}),('jayan',{'comment':['[hollyb] Mother.']}),('mamiec',{'dob':'01/16/1953'}),('charlesp',{'project':'Adaptive Optimizing Product'}),('kristeng',{'project':'Focused Well-Modulated Leverage', 'job':'Chief Operating Officer'}),('meryemm',{'comment':['[rickyp] Baby.']}),('luismiguelc',{'city':'Lake Krystalmouth, Prince Edward Island', 'job':'Social Researcher'}),('vinodk',{'project':'Multi-Lateral Didactic Customer Loyalty'}),('omars',{'city':'Lake Melissa, Yukon Territory', 'job':'Psychologist, Prison And Probation Services'}),('bgk',{'comment':['[karenm] Answer!!!']}),('melissac',{'dob':'11/20/1967'}),('tren',{'project':'Configurable Real-Time Service-Desk'}),('amishp',{'project':'Fully-Configurable Real-Time Initiative', 'city':'Johnhaven, Manitoba', 'name':'Amish Panja'}),('tren',{'comment':['[franckb] Blood.']}),('bgk',{'comment':['[omars] Two!']}),('timoro',{'comment':['[josem] Eye sound.']}),('bgk',{'comment':['[aaronc] Paper assume!']}),('bgk',{'comment':['[erminiam] Hand himself college!']}),('tamir',{'city':'West Ericashire, Northwest Territories', 'job':'Art Gallery Manager'}),('jozefa',{'comment':['[kellyh] Weight question.']}),('kathleenc',{'comment':['[jayan] Beautiful white mother.']}),('marekn',{'comment':['[melaniew] Half.']}),('tinag',{'city':'New Michaelfort, Ontario'}),('lib',{'color':'purple', 'city':'North Wayneberg, Yukon Territory', 'dob':'10/05/1991'}),('diedrichs',{'comment':['[lindseyl] Huge realize.']}),('diedrichs',{'dob':'08/15/1972', 'project':'De-Engineered Impactful Array', 'job':'Engineer, Maintenance (It)'}),('karnavatik',{'comment':['[sorayav] Activity even program only.']}),('lyndat',{'color':'aqua'}),('nathanr',{'comment':['[joshuam] Exist enter.']}),('jordanm',{'city':'Port Amanda, Yukon Territory', 'name':'Jordan May'}),('bgk',{'comment':['[susanr] Evening collection!!!']}),('bryanm',{'comment':['[fortunatac] Heavy.']}),('bgk',{'comment':['[ahmets] See certainly!']}),('meiz',{'project':'Virtual Actuating Interface', 'city':'South Christinaberg, Northwest Territories', 'dob':'06/11/1972'}),('ireneb',{'name':'Irene Burke'}),('meiz',{'comment':['[jeremyw] Loss thing.']}),('kathleenc',{'project':'Operative Object-Oriented Complexity', 'dob':'02/22/1959', 'name':'Kathleen Cannon'}),('bgk',{'comment':['[amhedo] We <3']}),('justinj',{'color':'navy'}),('bgk',{'comment':['[lib] Necessary commercial sell!!!']}),('ericp',{'project':'Streamlined Hybrid Paradigm', 'job':'Buyer, Retail'}),('fortunatac',{'project':'Implementazione Progressiva Modulare', 'color':'green', 'city':'North Amy, Quebec'}),('amishp',{'color':'olive'}),('leonardob',{'dob':'10/25/1992'}),('bgk',{'comment':['[sitas] Really large!!']}),('lib',{'project':'Upgradable Stable Moderator', 'job':'writer'}),('anthonyd',{'dob':'02/06/1955'}),('bgk',{'comment':['[ryanc] Challenge majority!']}),('meryemm',{'project':'Self-Enabling Mobile Service-Desk', 'city':'Jennabury, British Columbia', 'dob':'06/28/2000', 'name':'Meryem Meyer'}),('martinb',{'comment':['[zuzanr] Offer.']}),('vinodk',{'comment':['[jordanm] Lose sell.']}),('reishiongx',{'project':'Centralized Dedicated Service-Desk', 'city':'Hortonland, Ontario', 'dob':'04/07/1975', 'name':'Rei Shiong X'}),('marcinj',{'comment':['[ronaldt] Difficult.']}),('bgk',{'comment':['[amberv] Deep!!']}),('christophea',{'name':'Christophe Andre'}),('ambikab',{'city':'Lake Andrea, Nunavut'}),('meryemm',{'color':'black'}),('jeremyw',{'job':'Charity Fundraiser'}),('bgk',{'comment':['[ambikab] Opportunity call!']}),('joshuam',{'dob':'06/26/1950'}),('zuzanr',{'dob':'05/06/1994', 'project':'Virtual Exuding Knowledge User', 'city':'North Melaniestad, Quebec'}),('christophers',{'color':'yellow', 'project':'Reverse-Engineered Value-Added Complexity'}),('fernandov',{'project':'Multi-Lateral Coherent Array', 'city':'Port Amy, New Brunswick', 'job':'Set Designer'}),('sitas',{'city':'Dayview, Ontario'}),('bgk',{'comment':['[emilye] Father language since!!!']}),('bgk',{'comment':['[tren] Feeling gun <3']}),('bgk',{'comment':['[melissab] Treat <3']}),('bgk',{'comment':['[zacharys] Put!!!']}),('bgk',{'comment':['[hollyb] Successful!!']}),('bgk',{'comment':['[gemmaa] Health live!!']}),('lilyw',{'city':'Lifurt, Quebec'}),('bgk',{'comment':['[javierw] Age record significant <3']}),('katherineg',{'comment':['[kathleenk] Project.']}),('lilyw',{'color':'yellow'}),('tanl',{'city':'West Kevintown, Northwest Territories'}),('bgk',{'comment':['[hsiangmeic] Purpose although read!']}),('alexandrao',{'name':'Alexandra Owen'}),('bgk',{'comment':['[renah] Wonder pass side!']}),('amberv',{'comment':['[joshuam] Cost offer.']}),('nathanr',{'name':'Nathan Richards'}),('bgk',{'comment':['[glennf] Glass?!']}),('annak',{'city':'Port Matthewborough, Prince Edward Island', 'name':'Anna Kuijpers'}),('franckb',{'comment':['[tamir] Method.']}),('anilv',{'color':'black', 'project':'Multi-Tiered Cohesive Methodology', 'name':'Anil Valimbe'}),('sorayav',{'comment':['[agatheg] Little woman.']}),('jessicaa',{'dob':'10/24/1995', 'city':'Rivaston, Saskatchewan', 'project':'Polarized Exuding Adapter'}),('amhedo',{'comment':['[nicoles] Another standard.']}),('yongmeim',{'color':'blue', 'city':'East Kaitlyn, New Brunswick', 'name':'Yongmei Ma', 'project':'Self-Enabling Holistic Service-Desk'}),('heatherm',{'comment':['[nathanr] Feel.']}),('glennf',{'project':'Synergistic Object-Oriented Utilization', 'color':'green'}),('marcinj',{'comment':['[tanl] Wait protect.']}),('fatimab',{'comment':['[fatimab] Half lead.']}),('justinj',{'dob':'09/18/1943', 'job':'Hotel Manager', 'name':'Justin Jordan'}),('jeremyp',{'color':'teal', 'job':'Engineer, Structural'}),('michaelw',{'city':'East Sharonfurt, Nunavut', 'job':'Programme Researcher, Broadcasting/Film/Video', 'name':'Michael Wood'}),('jeremyw',{'project':'Upgradable Local Info-Mediaries', 'city':'Jonesberg, Ontario', 'dob':'04/20/1991', 'name':'Jeremy Whitehead'}),('ambikab',{'color':'maroon', 'project':'Vision-Oriented Non-Volatile Interface', 'dob':'04/01/1990', 'name':'Ambika Barvadekar'}),('fortunatac',{'dob':'02/16/1993', 'job':'Radiographer, Diagnostic'}),('bgk',{'comment':['[kathleenk] Organization!!!']}),('diedrichs',{'color':'purple'}),('schang',{'city':'Cookstad, British Columbia'}),('bgk',{'comment':['[philiph] Travel draw treatment!!!']}),('jenniferl',{'comment':['[johnl] Since.']}),('bgk',{'comment':['[cemalk] Likely!']}),('elijahm',{'city':'Anneberg, Ontario', 'job':'Civil Service Fast Streamer'}),('bgk',{'comment':['[belenc] Likely.']}),('agatheg',{'dob':'12/20/1974', 'city':'West Stephanie, Alberta', 'color':'violet'}),('bgk',{'comment':['[alexandrao] Hundred.']}),('bgk',{'comment':['[michelles] Visit!']}),('elijahm',{'color':'yellow', 'name':'Elijah Martinez'}),('lindseyl',{'color':'gray'}),('kimberlyd',{'color':'white', 'city':'South Cassie, Ontario'}),('bryanm',{'job':'Youth Worker'}),('zuzanr',{'comment':['[hollyb] Body relationship.']}),('elizabethm',{'dob':'03/02/1971'}),('christineh',{'color':'olive', 'city':'Janettown, New Brunswick'}),('bgk',{'comment':['[lyndat] Million visit?!']}),('michelew',{'color':'navy', 'project':'Distributed Empowering Standardization'}),('christineh',{'project':'Enterprise-Wide Attitude-Oriented Definition', 'name':'Christine Hensley'}),('rickyp',{'project':'Polarized Disintermediate Intranet', 'color':'gray', 'job':'Surveyor, Quantity'}),('virginieg',{'comment':['[lindseyl] Recognize.']}),('amhedo',{'dob':'01/01/1987', 'job':'Engineer', 'name':'Ahmed Omari'}),('elenag',{'comment':['[jenniferl] Mention.']}),('ahmets',{'comment':['[annak] Do car.']}),('bgk',{'comment':['[karnavatik] Democrat!']}),('reishiongx',{'color':'teal'}),('charlesp',{'color':'navy', 'dob':'03/12/1945', 'city':'Jameschester, New Brunswick'}),('deborahd',{'color':'blue', 'city':'West Jacob, Ontario', 'dob':'11/25/1985'}),('belenc',{'project':'Front-Line Object-Oriented Access'}),('bgk',{'comment':['[vinodk] Do?!']}),('bgk',{'comment':['[annak] Happy.']}),('sueellenv',{'project':'Middleware Condivisibile Locale'}),('chelseas',{'color':'green', 'project':'Centralized 24Hour Hierarchy'}),('suzanneg',{'dob':'06/14/1948', 'color':'sarcelle', 'job':'Maroquinier', 'name':'Suzanne Gomez'}),('rickyp',{'city':'North Robert, Yukon Territory', 'name':'Ricky Powell'}),('schang',{'comment':['[erminiam] What worry.']}),('bgk',{'comment':['[melissac] Top!']}),('josem',{'project':'Right-Sized Interactive Product', 'job':'Chief Of Staff'}),('jenniferh',{'dob':'12/23/1958', 'city':'Michaelton, Ontario', 'color':'olive'}),('vinodk',{'city':'Hesterland, Northwest Territories', 'job':'Engineer, Civil (Contracting)', 'name':'Vinod Kayal'}),('soraiaa',{'dob':'04/04/1960', 'city':'New Christian, Nova Scotia', 'job':'Licensed Conveyancer', 'name':'Soraia Antunes'}),('julieb',{'project':'Digitized Methodical Neural-Net'}),('mohammedj',{'color':'black', 'name':'Mohammed Jennings'}),('nathanr',{'comment':['[rachelc] Teach growth.']}),('bgk',{'comment':['[bhairavig] System development cover dark!!']}),('jeremyp',{'comment':['[heatherm] Treatment.']}),('jenniferl',{'job':'Instructor'}),('maryn',{'dob':'12/24/1955'}),('sandrat',{'color':'green', 'project':'Mandatory Grid-Enabled Interface', 'job':'Herbalist', 'name':'Sandra Tan'}),('bgk',{'comment':['[agatheg] Movie career!!']}),('melissac',{'city':'Lorishire, New Brunswick', 'name':'Melissa Cooper'}),('michelles',{'dob':'07/03/1997', 'color':'silver', 'city':'Erinmouth, New Brunswick'}),('bgk',{'comment':['[zacharys] Foot!!!']}),('danac',{'comment':['[cemalk] Animal throughout nor fact.']}),('glennf',{'dob':'09/21/2003', 'city':'Matthewland, Yukon Territory', 'job':'Toxicologist'}),('franckb',{'dob':'09/13/1939', 'city':'Lake Jamieberg, Saskatchewan'}),('bgk',{'comment':['[wojciechs] Maybe piece government!!!']}),('davidz',{'comment':['[jordanm] Trial.']}),('bgk',{'comment':['[marekn] During break <3']}),('barbarah',{'city':'Jasonbury, Newfoundland and Labrador'}),('travism',{'color':'maroon', 'dob':'05/12/1942'}),('luismiguelc',{'comment':['[amberv] Why.']}),('hayleyj',{'dob':'04/04/1960', 'color':'black', 'project':'Down-Sized Bi-Directional Database', 'city':'Brandonbury, Nunavut'}),('bgk',{'comment':['[jordanm] Speak give?!']}),('davidz',{'dob':'11/10/1964'}),('heatherm',{'project':'Persevering Neutral Standardization', 'city':'West Amybury, Manitoba', 'dob':'01/03/1990'}),('melissac',{'comment':['[jozefa] Significant analysis.']}),('susanr',{'dob':'07/26/1967', 'project':'Self-Enabling Interactive Infrastructure', 'job':'Dispensing Optician', 'color':'white'}),('philiph',{'dob':'01/09/1994'}),('jessicaa',{'comment':['[meiz] Seem before case.']}),('jeremyp',{'project':'Persevering Motivating Hardware', 'dob':'12/14/1954', 'name':'Jeremy Potts'}),('fatimab',{'project':'Up-Sized System-Worthy Ability', 'dob':'07/26/1976', 'job':'Tourism Officer', 'color':'white'}),('jenniferh',{'job':'Engineer, Building Services', 'name':'Jennifer Harmon'}),('debraw',{'dob':'04/07/2000', 'project':'Monitored Zero Tolerance Emulation', 'job':'Designer, Textile'}),('bgk',{'comment':['[lyndat] Beautiful article among <3']}),('ronaldt',{'dob':'06/09/1978', 'color':'fuchsia'}),('peters',{'project':'Organized Coherent Knowledge User', 'job':'Fisheries Officer'}),('bgk',{'comment':['[nicoles] Attack.']}),('jeremyw',{'color':'navy'}),('jozefa',{'color':'gray', 'city':'Evansburgh, Manitoba'}),('daniellej',{'comment':['[fernandov] Drug.']}),('justinj',{'project':'Triple-Buffered Responsive Challenge', 'city':'Dianaview, New Brunswick'}),('bgk',{'comment':['[jeremyp] Car able!!!']}),('jordanm',{'comment':['[rickyp] Five.']}),('henriettek',{'dob':'05/12/1960', 'job':'Operateur De Raffinerie'}),('kathleenk',{'dob':'03/18/1948', 'job':'Occupational Hygienist'}),('bgk',{'comment':['[clerosb] Lose?!']}),('karnavatik',{'project':'Proactive Hybrid Migration'}),('bgk',{'comment':['[jenniferl] Line reason there!']}),('aaronc',{'dob':'04/16/1970', 'project':'Self-Enabling Multimedia Firmware', 'name':'Aaron Chan', 'color':'aqua'}),('susanr',{'city':'Zimmermanview, Prince Edward Island'}),('verap',{'job':'Administrator', 'name':'Vera Palumbo'}),('davidd',{'color':'teal', 'project':'Synergistic Zero-Defect Encryption', 'name':'David Duran'}),('marekn',{'dob':'04/08/1989'}),('kathleenk',{'color':'blue', 'name':'Kathleen Krein'}),('leonardob',{'comment':['[rebeccam] Song contain.']}),('gracer',{'project':'Focused Zero Administration Monitoring', 'name':'Grace Ryan'}),('javierw',{'city':'Tiffanyport, New Brunswick', 'name':'Javier Washington'}),('bgk',{'comment':['[debraw] Area!']}),('bgk',{'comment':['[whitneyh] Out summer <3']}),('chelseas',{'comment':['[nicoles] Study.']}),('jordanm',{'project':'Devolved Well-Modulated Data-Warehouse', 'dob':'04/26/1945', 'job':'Trading Standards Officer'}),('sueellenv',{'name':'Sue Ellen Valentini'}),('josem',{'dob':'01/04/1974', 'city':'Port Monicaburgh, Nunavut', 'name':'Jose Mason'}),('bgk',{'comment':['[josem] Top!!']}),('lilyw',{'dob':'07/20/1962', 'project':'Open-Source Multimedia Hardware', 'job':'Nurse', 'name':'Lily Wang'}),('kathleenk',{'project':'Pre-Emptive Systemic Migration', 'city':'Wolfview, Saskatchewan'}),('jamesd',{'comment':['[yongmeim] Throughout.']}),('fatimab',{'name':'Fatima Buendia'}),('sorayav',{'color':'lime'}),('hsiangmeic',{'job':'Professor'}),('joannev',{'name':'Joanne Vincent'}),('susanf',{'dob':'10/22/1999', 'job':'Animalier De Laboratoire'}),('bgk',{'comment':['[alexandrec] Energy free style!']}),('bgk',{'comment':['[ryanc] Nothing?!']}),('bgk',{'comment':['[henriettek] Owner teach lose!']}),('virginieg',{'project':'Le Pouvoir De Rouler a Sa Source', 'color':'sarcelle'}),('bgk',{'comment':['[melissab] Successful nice?!']}),('andresv',{'comment':['[barbaras] Believe.']}),('ahmets',{'dob':'08/01/2000'}),('hunterl',{'comment':['[elenag] Security maybe church.']}),('jayan',{'project':'Profound Non-Volatile Matrix', 'city':'West Amandaport, Manitoba', 'name':'Jaya Nambisan'}),('lilyw',{'comment':['[josettem] Probably.']}),('bgk',{'comment':['[angelac] Discover machine nearly!!!']}),('domingog',{'job':'Aid Worker'}),('madhavir',{'job':'Ambulance Person'}),('lindseyl',{'job':'Visual Merchandiser'}),('bgk',{'comment':['[justinj] Development right product!']}),('danac',{'color':'olive', 'city':'Courtneyburgh, British Columbia'}),('gemmaa',{'dob':'03/21/1955', 'city':'Jacksonton, Saskatchewan', 'job':'Dealer', 'name':'Gemma Armstrong'}),('kimberlyd',{'dob':'01/04/1965', 'project':'Right-Sized Local Internet Solution', 'job':'Exhibition Designer'}),('sitas',{'color':'purple', 'project':'Seamless Motivating Circuit', 'dob':'08/27/2003'}),('whitneyh',{'city':'North Danielstad, Quebec'}),('ronaldt',{'project':'Self-Enabling Multi-Tasking Website', 'job':'Higher Education Careers Adviser'}),('bgk',{'comment':['[christineh] Side!!!']}),('bgk',{'comment':['[alexandrao] Produce?!']}),('domingog',{'project':'Strategia Orizzontale Valore Aggiunto', 'color':'purple', 'name':'Domingo Grassi'}),('nathanr',{'project':'Reverse-Engineered Background Hardware', 'color':'teal', 'job':'Doctor, Hospital'}),('stephaniev',{'color':'blue', 'name':'Stephanie Vance'}),('sorayav',{'dob':'10/07/1983'}),('glennf',{'comment':['[kristeng] Region.']}),('bgk',{'comment':['[rebeccam] Address director!']}),('karnavatik',{'color':'yellow'}),('cemalk',{'city':'East Brian, Nova Scotia'}),('bgk',{'comment':['[andresv] Its!!']}),('wojciechs',{'name':'Wojciech Sarniak'}),('maryn',{'project':'Balanced Exuding Website', 'city':'Allisonville, New Brunswick', 'job':'Fitness Centre Manager', 'name':'Mary Novak'}),('debraw',{'color':'navy', 'city':'Lake Victorbury, Nova Scotia'}),('josem',{'comment':['[meiz] Sometimes.']}),('rickyp',{'dob':'12/22/1937'}),('melaniew',{'project':'Secured Discrete Framework'}),('charlesw',{'comment':['[amyh] Hope.']}),('andresv',{'color':'blue', 'project':'Re-Contextualized User-Facing Support', 'name':'Andres Vicens'}),('hunterl',{'color':'olive', 'city':'Petersonhaven, Ontario', 'job':'Accounting Technician', 'name':'Hunter Lewis'}),('bgk',{'comment':['[ronaldt] Interest half!']}),('michaelc',{'comment':['[ronaldt] Land.']}),('johnl',{'name':'John Lee'}),('bgk',{'comment':['[kathleenc] Decide success old?!']}),('elizabethm',{'project':'Diverse Motivating Data-Warehouse', 'city':'North Danielmouth, New Brunswick', 'job':'Product/Process Development Scientist', 'name':'Elizabeth Mcmillan'}),('bgk',{'comment':['[gordonl] Consider computer!']}),('schang',{'dob':'09/24/2001', 'project':'Quality-Focused Fault-Tolerant Matrices', 'job':'Tradesman', 'color':'purple'}),('bgk',{'comment':['[justinj] Interesting!!!']}),('bgk',{'comment':['[kimberlyd] Surface pretty worry dream.']}),('rebeccam',{'project':'Reduced Intermediate Hardware', 'city':'West Joshuatown, Prince Edward Island', 'job':'Development Worker, Community', 'name':'Rebecca Mathis'}),('amyh',{'job':'Psychologist, Sport And Exercise', 'name':'Amy Holland'}),('ronaldr',{'dob':'07/15/1950', 'name':'Ronald Reese'}),('bgk',{'comment':['[fatimab] Natural head above!']}),('lib',{'name':'Li Bai'}),('bgk',{'comment':['[johnl] Food <3']}),('bgk',{'comment':['[amberv] Floor father <3']}),('katherinev',{'comment':['[dianaf] Scientist.']}),('luismiguelc',{'comment':['[jayan] System.']}),('anthonyg',{'job':'Accountant, Chartered Management'}),('bgk',{'comment':['[ahmets] Rise or?!']}),('glennf',{'comment':['[ryanj] Mr worry old.']}),('barbaras',{'comment':['[melissac] Thank.']}),('tarekk',{'color':'white', 'job':'Financial Trader', 'name':'Tarek Kurdi'}),('karenm',{'comment':['[gordonl] Participant camera.']}),('hollyb',{'project':'Exclusive Asynchronous Toolset'}),('mohammedj',{'comment':['[ambikab] Create and.']}),('karenm',{'color':'lime', 'job':'Trade Union Research Officer', 'name':'Karen Murphy'}),('jamesd',{'city':'Lake Samantha, Saskatchewan'}),('bhairavig',{'project':'User-Centric Tertiary Software', 'city':'North Ericville, Newfoundland and Labrador'}),('zacharys',{'dob':'09/23/1995', 'city':'Lake Karen, Ontario', 'project':'Ameliorated Content-Based Info-Mediaries'}),('bgk',{'comment':['[ahmets] Modern environment mean!']}),('bgk',{'comment':['[julieb] Garden?!']}),('davidz',{'color':'lime'}),('barbaras',{'comment':['[jenniferh] Develop.']}),('meryemm',{'job':'Engineer, Electrical'}),('bgk',{'comment':['[joshuam] Sense scene fish <3']}),('elijahm',{'project':'Phased Optimizing Array', 'dob':'08/28/1993'}),('charlesp',{'job':'Outdoor Activities/Education Manager', 'name':'Charles Perry'}),('gracer',{'comment':['[angelad] Matter.']}),('sitas',{'job':'Sports Therapist', 'name':'Sita Sinha'}),('daniellej',{'color':'aqua'}),('gracer',{'comment':['[melissab] Provide.']})]
