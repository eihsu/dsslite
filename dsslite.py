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
      self.contents[user] = data
    self.logger.info(self.name +
                     " updates {} with {}".format(user, data))

  def register(self, env):
    # Clear DB contents during registration to reset between runs onf
    # the simiulation.
    self.contents = {}
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
    self.random_seed = random_seed
    
    self.initialize()

  # Called at start of each run to clear stats and pysim env from
  # previous runs, so user can try different settings without
  # creating multiple simulations.
  def initialize(self):
    random.seed(self.random_seed)
    self.reqs_sent = {}  # reqs sent out to system, keyed by timestamp
    self.reqs_acked = {} # timestamp when req sent out -> time of ack
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
    s += "[ {} / {} / {} / {} ] ms.\n".format(stats['min_wait'],
                                              stats['max_wait'],
                                              stats['median_wait'],
                                              stats['mean_wait'])

    print(s)

  def traffic_generator(self, env, rate, script):
    if not script:
      script = Simulation.script
    # Send out scripted requests at random intervals.
    for line in script:
      # Uniformly 1 to 100 ms between requests, scaled by rate.
      interval = int(random.randint(1,100) / float(rate))
      yield env.timeout(interval)
      timestamp = env.now
      req = Request(line[0], line[1], timestamp)
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

  # traffic_rate: scaling factor for time interval between requests.
  # script: unless provided, will use the one at the end of this file.
  def run(self, traffic_rate=1.0, script=False):
    # Reset everything between runs so users can experiment with
    # multiple successive traffic rates within same sim object.
    self.traffic_rate = traffic_rate
    self.initialize()

    p = self.env.process(self.traffic_generator(self.env,
                                                traffic_rate,
                                                script))
    self.env.run(until=p)
    self.finish_time = self.env.now
    self.display_stats()

# TODO:
#
# tests for worker concurrency (for instance straight to handling vs queue)
# test db concurrency
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
  script = [('leonardob',{'comment':'[ronaldt] Major air!!'}),('stephaniev',{'dob':'10/04/1954','city':'Jenniferport,Quebec'}),('ryanj',{'comment':'[joshuam] Foreign eat.'}),('glennf',{'color':'green','dob':'09/21/2003'}),('kathleenk',{'project':'Pre-Emptive Systemic Migration','city':'Wolfview,Saskatchewan','job':'Occupational Hygienist','dob':'03/18/1948'}),('zacharys',{'job':'Company Secretary'}),('leonardob',{'comment':'[michaelc] Rock member <3'}),('leonardob',{'comment':'[glennf] Property <3'}),('bgk',{'color':'silver','project':'B7','job':'Various','city':'Lake Richard,British Columbia'}),('leonardob',{'comment':'[charlesw] You!'}),('karnavatik',{'color':'yellow','job':'Gaffer'}),('martinb',{'comment':'[amyh] Realize behavior.'}),('daniellej',{'project':'Managed 4Thgeneration Conglomeration','city':'East Jill,Ontario','job':'Audiological Scientist','dob':'04/23/1972'}),('leonardob',{'comment':'[clerosb] Second?!'}),('leonardob',{'comment':'[karenm] Industry!'}),('jeremyw',{'comment':'[lilyw] Theory.'}),('wojciechs',{'project':'Face-To-Face Non-Volatile Forecast'}),('melaniew',{'project':'Secured Discrete Framework','name':'Melanie Williams'}),('wojciechs',{'color':'fuchsia','city':'Port Loretta,Yukon Territory','name':'Wojciech Sarniak'}),('melaniew',{'dob':'11/05/1947','city':'New Moniqueview,Ontario','job':'Air Cabin Crew'}),('leonardob',{'comment':'[mohammedj] Matter?!'}),('tarekk',{'comment':'[elenag] Arrive few.'}),('peters',{'job':'Fisheries Officer'}),('lyndat',{'comment':'[cemalk] Call.'}),('leonardob',{'comment':'[fortunatac] Later education!'}),('susanr',{'comment':'[michelew] Option fall treat.'}),('ireneb',{'project':'Synergistic Local Graphic Interface','dob':'08/05/1947','city':'East Jacqueline,Newfoundland and Labrador'}),('deborahd',{'dob':'11/25/1985','project':'Robust 5th-Generation Portal','job':'Dancer'}),('yongmeim',{'dob':'12/17/1962','city':'East Kaitlyn,New Brunswick','job':'Driver'}),('leonardob',{'comment':'[josettem] Hear?!'}),('rebeccam',{'project':'Reduced Intermediate Hardware','city':'West Joshuatown,Prince Edward Island','name':'Rebecca Mathis'}),('bryanm',{'project':'Multi-Tiered Bottom-Line Intranet','city':'Loriburgh,Newfoundland and Labrador'}),('leonardob',{'comment':'[stephaniev] Apply participant!!'}),('davidd',{'color':'teal','project':'Synergistic Zero-Defect Encryption'}),('leonardob',{'comment':'[danac] Test.'}),('suzanneg',{'project':'La Liberte De Changer a L etat Pur','city':'North Paige,British Columbia','job':'Maroquinier'}),('cynthiav',{'name':'Cynthia Vasquez'}),('nicoles',{'color':'silver'}),('josem',{'color':'silver'}),('fortunatac',{'comment':'[alexandrao] Behind thousand easy.'}),('leonardob',{'comment':'[heatherm] Notice?!'}),('hollyb',{'dob':'10/16/1994','project':'Exclusive Asynchronous Toolset'}),('leonardob',{'comment':'[kellyh] Gun throw!!!'}),('jamesd',{'project':'Customer-Focused Cohesive Firmware','job':'Arboriculturist'}),('mamiec',{'color':'olive','city':'Lake Melissaburgh,Prince Edward Island','dob':'01/16/1953'}),('joannev',{'comment':'[michelles] Partner same.'}),('charlesw',{'name':'Charles Ware'}),('leonardob',{'comment':'[meiz] Physical.'}),('leonardob',{'comment':'[jozefa] Ten question key order?!'}),('susanf',{'color':'blanc'}),('bhairavig',{'comment':'[ryanj] Impact reality.'}),('leonardob',{'comment':'[sitas] Happen?!'}),('leonardob',{'comment':'[wojciechs] Heavy.'}),('michaelw',{'color':'green','job':'Programme Researcher,Broadcasting/Film/Video'}),('hsiangmeic',{'name':'Hsiang-Mei Ch'}),('karenm',{'dob':'11/13/1995','color':'lime','city':'Robertfurt,Manitoba'}),('leonardob',{'comment':'[nicoles] Drug!'}),('justinj',{'comment':'[rebeccam] International point must.'}),('gemmaa',{'color':'silver'}),('leonardob',{'comment':'[tanl] Detail adult human <3'}),('vinodk',{'project':'Multi-Lateral Didactic Customer Loyalty','job':'Engineer,Civil (Contracting)','name':'Vinod Kayal'}),('luismiguelc',{'comment':'[peters] Old worker.'}),('kellyh',{'project':'Pre-Emptive Multi-State Task-Force','dob':'11/09/1966','job':'Regulatory Affairs Officer'}),('tanl',{'color':'yellow','project':'Cross-Group Transitional Matrix','job':'Teacher','city':'West Kevintown,Northwest Territories'}),('ronaldt',{'color':'fuchsia','name':'Ronald Thompson'}),('leonardob',{'comment':'[hunterl] Foot <3'}),('deborahd',{'city':'West Jacob,Ontario','name':'Deborah Dunn'}),('whitneyh',{'color':'teal','name':'Whitney Hicks'}),('katherinev',{'color':'navy','project':'Front-Line Logistical Info-Mediaries','name':'Katherine Vaughn'}),('leonardob',{'comment':'[elijahm] Cause likely!!'}),('alexandrao',{'job':'Personnel Officer','name':'Alexandra Owen'}),('wandas',{'project':'Enterprise-Wide Context-Sensitive Approach','dob':'11/10/1980','job':'Medical Technical Officer'}),('schang',{'color':'purple','dob':'09/24/2001','job':'Tradesman','project':'Quality-Focused Fault-Tolerant Matrices'}),('kellyh',{'name':'Kelly Hall'}),('christineh',{'comment':'[fortunatac] Newspaper pick car able.'}),('kellyh',{'comment':'[tinag] Whether trial.'}),('leonardob',{'comment':'[jenniferh] Wear!!!'}),('christineh',{'comment':'[leonardob] See human.'}),('jeremyw',{'project':'Upgradable Local Info-Mediaries','name':'Jeremy Whitehead'}),('sueellenv',{'project':'Middleware Condivisibile Locale','job':'Artist'}),('leonardob',{'comment':'[ryanc] Herself.'}),('ryanj',{'dob':'04/28/1976'}),('leonardob',{'comment':'[kathleenc] That one?!'}),('leonardob',{'comment':'[amishp] Eye!'}),('leonardob',{'comment':'[nicoles] Management!'}),('leonardob',{'comment':'[anilv] My network.'}),('franciscojavierg',{'comment':'[whitneyh] Late production.'}),('hollyb',{'city':'Troyton,Alberta','job':'Engineer,Control And Instrumentation'}),('hayleyj',{'color':'black','project':'Down-Sized Bi-Directional Database'}),('leonardob',{'comment':'[melaniew] Computer!'}),('leonardob',{'comment':'[javierw] Dinner.'}),('leonardob',{'comment':'[belenc] Speech!'}),('domingog',{'color':'purple'}),('jenniferl',{'job':'Instructor'}),('nathanr',{'job':'Doctor,Hospital'}),('charlesp',{'color':'navy','city':'Jameschester,New Brunswick'}),('luismiguelc',{'color':'green','city':'Lake Krystalmouth,Prince Edward Island','name':'Luis Miguel Cerezo'}),('agatheg',{'project':'L Avantage De Rouler Sans Soucis','job':'Fleuriste'}),('lib',{'comment':'[hunterl] Meeting.'}),('leonardob',{'comment':'[michelles] Only upon.'}),('lyndat',{'city':'West Jennifer,Prince Edward Island'}),('anthonyd',{'dob':'02/06/1955','color':'olive','job':'Public Relations Officer'}),('christophers',{'dob':'05/02/1939','color':'yellow','job':'Optometrist','city':'Zoeborough,Ontario'}),('leonardob',{'comment':'[jayan] Easy!!'}),('lib',{'comment':'[katherineg] Officer.'}),('diedrichs',{'color':'purple'}),('zuzanr',{'dob':'05/06/1994','city':'North Melaniestad,Quebec','job':'Lecturer,Higher Education','project':'Virtual Exuding Knowledge User'}),('marcinj',{'project':'Ergonomic Multi-Tasking Internet Solution'}),('leonardob',{'comment':'[nathanr] Decision war special!!'}),('leonardob',{'comment':'[aaronc] Meet?!'}),('annak',{'color':'gray','project':'Quality-Focused Mission-Critical Collaboration','name':'Anna Kuijpers','city':'Port Matthewborough,Prince Edward Island'}),('timoro',{'color':'lime'}),('anthonyd',{'comment':'[emilye] Yet.'}),('susanr',{'comment':'[vinodk] Present.'}),('leonardob',{'comment':'[zuzanr] Board too <3'}),('leonardob',{'comment':'[angelac] Including!!!'}),('rickyp',{'comment':'[jamesd] Huge society hotel.'}),('luismiguelc',{'comment':'[zuzanr] Early.'}),('jeremyp',{'color':'teal'}),('leonardob',{'comment':'[nathanr] Everything <3'}),('elijahm',{'color':'yellow','project':'Phased Optimizing Array','job':'Civil Service Fast Streamer'}),('leonardob',{'comment':'[erminiam] Too <3'}),('michelew',{'city':'Stevensburgh,Quebec'}),('fernandov',{'dob':'12/29/1948'}),('philiph',{'comment':'[heatherm] Range.'}),('ireneb',{'comment':'[ryanj] Present could walk.'}),('heatherm',{'project':'Persevering Neutral Standardization','job':'Systems Analyst','name':'Heather Mccarthy'}),('annak',{'comment':'[davidd] Along campaign.'}),('kristeng',{'comment':'[luismiguelc] Environmental increase.'}),('leonardob',{'comment':'[luismiguelc] Community.'}),('amyh',{'dob':'10/10/1984','city':'Serranoton,Manitoba','job':'Psychologist,Sport And Exercise'}),('ericp',{'dob':'07/19/1972','job':'Buyer,Retail','name':'Eric Pierce'}),('chelseas',{'project':'Centralized 24Hour Hierarchy','city':'Clarkberg,Ontario'}),('johnl',{'project':'Synergistic Intermediate Analyzer','name':'John Lee'}),('lib',{'project':'Upgradable Stable Moderator','city':'North Wayneberg,Yukon Territory'}),('jordanm',{'project':'Devolved Well-Modulated Data-Warehouse'}),('tren',{'name':'Tong Ren'}),('leonardob',{'comment':'[melaniew] Region!'}),('emilye',{'color':'white','city':'Lake Charles,New Brunswick'}),('sitas',{'color':'purple','dob':'08/27/2003','job':'Sports Therapist'}),('leonardob',{'comment':'[elizabethm] Represent!!!'}),('leonardob',{'comment':'[martinb] Specific?!'}),('aaronc',{'project':'Self-Enabling Multimedia Firmware'}),('whitneyh',{'comment':'[rickyp] You.'}),('meryemm',{'project':'Self-Enabling Mobile Service-Desk','city':'Jennabury,British Columbia'}),('soraiaa',{'job':'Licensed Conveyancer','name':'Soraia Antunes'}),('michelles',{'name':'Michelle Smith'}),('leonardob',{'comment':'[wandas] Professional!'}),('amhedo',{'comment':'[marekn] Movement pull.'}),('cemalk',{'project':'Balanced Stable Approach','city':'East Brian,Nova Scotia','dob':'09/18/1996'}),('renah',{'comment':'[anilv] Interesting.'}),('melissab',{'color':'purple','job':'Interpreter'}),('gemmaa',{'project':'Universal Bifurcated Hub'}),('julieb',{'city':'Wesleyhaven,Manitoba'}),('erminiam',{'comment':'[ronaldr] Certain.'}),('leonardob',{'comment':'[franciscojavierg] Something free <3'}),('chelseas',{'comment':'[jayan] Practice easy.'}),('jenniferh',{'color':'olive','city':'Michaelton,Ontario'}),('leonardob',{'comment':'[franckb] Total paper!!!'}),('leonardob',{'comment':'[ryanc] Reveal green single!'}),('leonardob',{'comment':'[shawne] Up!'}),('danac',{'project':'Fully-Configurable Interactive Budgetary Management','job':'Doctor,General Practice'}),('leonardob',{'comment':'[wandas] Material!!'}),('jeremyp',{'job':'Engineer,Structural'}),('nathanr',{'dob':'03/16/1957'}),('leonardob',{'comment':'[andresv] Explain!!'}),('leonardob',{'comment':'[maryn] Car side!!!'}),('ronaldt',{'dob':'06/09/1978','job':'Higher Education Careers Adviser'}),('leonardob',{'comment':'[nicoles] Might kind!'}),('leonardob',{'comment':'[ahmets] Believe property <3'}),('barbarah',{'color':'purple','dob':'07/10/1941'}),('leonardob',{'comment':'[travism] Mr the?!'}),('omars',{'dob':'11/12/1952','project':'Optimized Methodical Conglomeration','name':'Omar Smith','color':'teal'}),('anthonyg',{'name':'Anthony Gonzalez'}),('ambikab',{'dob':'04/01/1990','project':'Vision-Oriented Non-Volatile Interface'}),('leonardob',{'comment':'[jessicaa] Her friend probably!'}),('agatheg',{'dob':'12/20/1974','name':'Agathe Garnier'}),('melissac',{'dob':'11/20/1967'}),('sueellenv',{'dob':'10/15/1959','color':'gray'}),('wandas',{'name':'Wanda Santos'}),('heatherm',{'city':'West Amybury,Manitoba'}),('timoro',{'project':'Phased Incremental Flexibility','job':'Manager'}),('debraw',{'comment':'[hollyb] Baby.'}),('leonardob',{'comment':'[barbaras] Full option similar!'}),('josem',{'job':'Chief Of Staff'}),('amhedo',{'project':'Seamless Reciprocal Leverage','dob':'01/01/1987','job':'Engineer','name':'Ahmed Omari'}),('nathanr',{'project':'Reverse-Engineered Background Hardware','city':'Josephfurt,New Brunswick','name':'Nathan Richards','color':'teal'}),('meryemm',{'job':'Engineer,Electrical','name':'Meryem Meyer'}),('leonardob',{'comment':'[sandrat] Vote bar <3'}),('leonardob',{'comment':'[angelac] Ball!!'}),('glennf',{'project':'Synergistic Object-Oriented Utilization','job':'Toxicologist'}),('leonardob',{'comment':'[suzanneg] For speech among!!!'}),('leonardob',{'comment':'[kimberlyd] Home!!'}),('davidz',{'dob':'11/10/1964','name':'David Zamora'}),('amishp',{'color':'olive','dob':'12/12/1972','job':'Media Planner'}),('aaronn',{'color':'black','project':'User-Centric Asymmetric Infrastructure','city':'Port Natashafort,Newfoundland and Labrador'}),('leonardob',{'comment':'[annak] Among.'}),('leonardob',{'comment':'[sorayav] Test Republican!!!'}),('aaronc',{'city':'Stevenmouth,Quebec'}),('leonardob',{'comment':'[domingog] History manager!!'}),('zacharys',{'dob':'09/23/1995','city':'Lake Karen,Ontario','name':'Zachary Schultz'}),('franckb',{'project':'Le Confort D Avancer Sans Soucis'}),('rickyp',{'name':'Ricky Powell'}),('leonardob',{'color':'teal','job':'IT Consultant'}),('leonardob',{'comment':'[kathleenc] Dream!'}),('lindseyl',{'comment':'[gordonl] Political.'}),('dianaf',{'job':'Development Worker,Community','name':'Diana Foster'}),('nicolej',{'dob':'04/03/1997','city':'Martinside,Quebec','job':'Risk Manager','project':'Expanded Regional Array'}),('leonardob',{'comment':'[barbaras] Western.'}),('suzanneg',{'comment':'[jamesd] Him partner.'}),('bgk',{'dob':'07/13/1981'}),('angelad',{'color':'aqua','project':'Front-Line Grid-Enabled Leverage','name':'Angela Davies'}),('rachelc',{'project':'Up-Sized Systemic Utilization','color':'silver','job':'Chief Operating Officer'}),('leonardob',{'comment':'[tarekk] Give can economic!'}),('madhavir',{'comment':'[rebeccam] Us church.'}),('christophea',{'project':'L Art De Changer a la Pointe','dob':'09/09/1944','job':'Controleur De Performances'}),('sitas',{'city':'Dayview,Ontario'}),('angelad',{'city':'Donnafort,Nunavut'}),('leonardob',{'comment':'[aaronn] Different!'}),('lilyw',{'color':'yellow','dob':'07/20/1962','name':'Lily Wang'}),('zuzanr',{'name':'Zuzan Rogers'}),('joshuam',{'color':'lime','dob':'06/26/1950','job':'Sport And Exercise Psychologist'}),('emilye',{'dob':'05/29/2001','project':'Centralized Composite Data-Warehouse','name':'Emily Evans'}),('leonardob',{'comment':'[fortunatac] Push ask far.'}),('leonardob',{'comment':'[montserrata] Civil!!'}),('ireneb',{'color':'purple','name':'Irene Burke'}),('katherineg',{'city':'Harrismouth,Saskatchewan','name':'Katherine Garrett'}),('leonardob',{'comment':'[angelac] Heart <3'}),('ireneb',{'comment':'[travism] Drop.'}),('elenag',{'project':'User-Centric Background Access','job':'Advertising Account Planner','name':'Elena Gemen'}),('hunterl',{'dob':'03/03/1971','city':'Petersonhaven,Ontario'}),('marekn',{'name':'Marek Narodotskya'}),('ryanc',{'project':'Reactive 24Hour System Engine','name':'Ryan Chandler'}),('barbaras',{'project':'Profound Uniform Circuit','city':'Chambersburgh,Alberta'}),('leonardob',{'comment':'[wandas] High rock?!'}),('verap',{'comment':'[javierw] Break.'}),('dianaf',{'comment':'[jamesd] Social happy.'}),('kathleenk',{'name':'Kathleen Krein'}),('shawne',{'dob':'07/19/1962','color':'white'}),('susanr',{'dob':'07/26/1967'}),('leonardob',{'comment':'[franckb] Enjoy thus <3'}),('nicolej',{'name':'Nicole Johnson'}),('ericp',{'city':'Christophershire,Saskatchewan'}),('wandas',{'comment':'[jordanm] Improve.'}),('leonardob',{'comment':'[maryn] Quite again gun!'}),('bhairavig',{'color':'gray'}),('joshuam',{'project':'Organized Holistic Help-Desk','city':'Heidiland,Saskatchewan'}),('clerosb',{'color':'black','city':'West Bradley,Manitoba','job':'Television Floor Manager','dob':'03/04/1955'}),('reishiongx',{'comment':'[timoro] Whatever.'}),('leonardob',{'comment':'[barbaras] Cause?!'}),('daniellej',{'name':'Danielle Johnson'}),('tanl',{'comment':'[sueellenv] While such.'}),('melaniew',{'comment':'[peters] Pull.'}),('elizabethm',{'city':'North Danielmouth,New Brunswick'}),('soraiaa',{'project':'Object-Based Local Capability','city':'New Christian,Nova Scotia','dob':'04/04/1960'}),('leonardob',{'comment':'[hayleyj] Watch wife <3'}),('jenniferl',{'color':'silver','project':'Compatible National Emulation'}),('justinj',{'dob':'09/18/1943','city':'Dianaview,New Brunswick'}),('leonardob',{'comment':'[ambikab] Not!!!'}),('mohammedj',{'color':'black','city':'Hoffmanside,New Brunswick'}),('anthonyd',{'comment':'[justinj] Purpose knowledge rather.'}),('clerosb',{'comment':'[belenc] Our.'}),('lib',{'name':'Li Bai'}),('madhavir',{'project':'Networked Tangible Monitoring','job':'Ambulance Person'}),('tamir',{'dob':'06/23/1990','job':'Art Gallery Manager'}),('leonardob',{'comment':'[davidz] Main <3'}),('ryanj',{'comment':'[lilyw] Late.'}),('dianaf',{'dob':'11/10/1952','city':'Ellisburgh,New Brunswick','color':'blue'}),('belenc',{'comment':'[franckb] Concern.'}),('rebeccam',{'comment':'[aaronn] Range career.'}),('belenc',{'city':'West Johnberg,New Brunswick'}),('lindseyl',{'comment':'[alexandrec] Hit.'}),('jessicaa',{'color':'navy'}),('bryanm',{'dob':'05/31/1961','color':'blue','job':'Youth Worker'}),('mamiec',{'name':'Mamie Chen'}),('josettem',{'color':'sarcelle'}),('gracer',{'city':'South John,Nunavut','job':'Water Quality Scientist'}),('jozefa',{'dob':'07/16/1956','city':'Evansburgh,Manitoba'}),('josem',{'comment':'[hsiangmeic] Environment herself.'}),('francoisb',{'dob':'03/12/1953','project':'Le Pouvoir D Avancer Sans Soucis','color':'blanc','city':'Karenhaven,Prince Edward Island'}),('tinag',{'color':'white','job':'Chartered Public Finance Accountant'}),('angelac',{'comment':'[suzanneg] Six.'}),('renah',{'comment':'[schang] You.'}),('joannev',{'job':'Editor,Magazine Features'}),('mohammedj',{'dob':'06/14/1947','project':'Triple-Buffered Disintermediate Groupware','job':'Scientist,Audiological'}),('tren',{'project':'Configurable Real-Time Service-Desk','color':'maroon','job':'Community Organizer','city':'Port Tomberg,British Columbia'}),('stephaniev',{'comment':'[mamiec] Sing who.'}),('leonardob',{'comment':'[christophea] Talk <3'}),('katherinev',{'city':'Travisfort,British Columbia'}),('leonardob',{'dob':'10/25/1992','city':'New Christopherport,Quebec','name':'Leonardo Baptista'}),('amyh',{'comment':'[cemalk] Artist.'}),('leonardob',{'comment':'[jeremyw] The!'}),('leonardob',{'comment':'[stephaniev] Improve play!'}),('fortunatac',{'dob':'02/16/1993','city':'North Amy,Quebec'}),('erminiam',{'name':'Erminia Moretti'}),('annak',{'dob':'09/16/1954'}),('karnavatik',{'project':'Proactive Hybrid Migration','city':'North Darlenemouth,Ontario','name':'Karnavati Kaseri'}),('marekn',{'comment':'[christineh] Near guess direction.'}),('amhedo',{'color':'lime'}),('aaronc',{'dob':'04/16/1970','color':'aqua','job':'Student','name':'Aaron Chan'}),('leonardob',{'comment':'[elijahm] Young president <3'}),('andresv',{'comment':'[kristeng] Simply.'}),('leonardob',{'comment':'[rickyp] Other!!'}),('leonardob',{'comment':'[elenag] Clear system!'}),('davidz',{'project':'Diverse Non-Volatile Core','job':'Television Production Assistant'}),('verap',{'comment':'[jeremyw] Dinner.'}),('ryanj',{'project':'Pre-Emptive 3Rdgeneration Functionalities','name':'Ryan Johnson'}),('leonardob',{'comment':'[michelew] Admit drug!!!'}),('leonardob',{'comment':'[cynthiav] My <3'}),('peters',{'comment':'[julieb] Base certainly.'}),('leonardob',{'comment':'[annak] Task edge condition player!'}),('debraw',{'job':'Designer,Textile'}),('leonardob',{'comment':'[renah] General east thing!'}),('leonardob',{'comment':'[meryemm] Challenge <3'}),('johnl',{'color':'aqua','job':'Health And Safety Inspector'}),('ireneb',{'job':'Accountant,Chartered Certified'}),('danac',{'city':'Courtneyburgh,British Columbia'}),('gordonl',{'dob':'09/01/1991','project':'Synergized Systemic Definition'}),('sandrat',{'dob':'09/27/1948'}),('leonardob',{'comment':'[rebeccam] Hit collection?!'}),('susanr',{'project':'Self-Enabling Interactive Infrastructure'}),('melissab',{'comment':'[philiph] Artist.'}),('leonardob',{'comment':'[ireneb] These!'}),('davidd',{'dob':'03/05/1980','city':'Krystalberg,Nova Scotia','job':'Health And Safety Adviser'}),('leonardob',{'comment':'[zuzanr] Purpose top!'}),('virginieg',{'color':'sarcelle','job':'Mara\xeecher','name':'Virginie Guyot'}),('meiz',{'project':'Virtual Actuating Interface','city':'South Christinaberg,Northwest Territories'}),('leonardob',{'comment':'[tarekk] Us letter <3'}),('leonardob',{'comment':'[hollyb] Let company!!!'}),('barbarah',{'project':'Robust Local Architecture','job':'Production Designer,Theatre/Television/Film','name':'Barbara Holland'}),('anthonyd',{'project':'Implemented Optimizing Service-Desk'}),('danac',{'dob':'09/02/1953','color':'olive','name':'Dana Cannon'}),('leonardob',{'comment':'[whitneyh] Stock!!'}),('leonardob',{'comment':'[domingog] It!!'}),('leonardob',{'comment':'[susanf] Challenge?!'}),('yongmeim',{'comment':'[michaelw] House.'}),('fatimab',{'job':'Tourism Officer'}),('leonardob',{'comment':'[marcinj] Attack price hard.'}),('shawne',{'job':'Paediatric Nurse'}),('meryemm',{'dob':'06/28/2000','color':'black'}),('charlesw',{'comment':'[susanr] Draw drug entire.'}),('leonardob',{'comment':'[yongmeim] Knowledge!'}),('leonardob',{'comment':'[bgk] Who space!'}),('fernandov',{'color':'white','project':'Multi-Lateral Coherent Array','job':'Set Designer'}),('marcinj',{'dob':'04/29/1981','city':'South Sarahchester,British Columbia','job':'Oceanonauta','name':'Marcin Janc'}),('vinodk',{'dob':'07/12/1944'}),('charlesw',{'dob':'06/22/1996','color':'green','job':'Cartographer'}),('karenm',{'comment':'[ambikab] Management.'}),('michaelc',{'dob':'07/18/1981','city':'East Patrick,Nova Scotia'}),('barbarah',{'comment':'[ryanc] Important.'}),('shawne',{'project':'Optimized Fresh-Thinking Collaboration','city':'Port Richard,Nova Scotia','name':'Shawn Edwards'}),('nicoles',{'dob':'04/21/1958','city':'East Martha,Ontario','job':'Warden/Ranger','name':'Nicole Suarez'}),('debraw',{'dob':'04/07/2000'}),('fatimab',{'comment':'[madhavir] Garden.'}),('leonardob',{'comment':'[angelad] Later <3'}),('leonardob',{'comment':'[sueellenv] Enjoy somebody?!'}),('leonardob',{'comment':'[omars] Couple pressure?!'}),('jeremyp',{'dob':'12/14/1954','city':'Santanafurt,Manitoba','name':'Jeremy Potts','project':'Persevering Motivating Hardware'}),('leonardob',{'comment':'[karnavatik] Its important!!!'}),('alexandrec',{'project':'La Possibilite dInnover Plus Rapidement','city':'West Granttown,Prince Edward Island','job':'Opticien'}),('javierw',{'dob':'04/08/1974','project':'Enhanced Client-Server Access','job':'Community Development Worker'}),('katherineg',{'project':'Cross-Platform Zero Administration Methodology'}),('mamiec',{'project':'Customizable Grid-Enabled Throughput','job':'President'}),('leonardob',{'comment':'[josem] Often democratic!'}),('leonardob',{'comment':'[katherinev] Day!'}),('jamesd',{'dob':'12/23/1982','city':'Lake Samantha,Saskatchewan'}),('mohammedj',{'comment':'[elijahm] Upon because.'}),('whitneyh',{'dob':'12/24/2003','city':'North Danielstad,Quebec'}),('justinj',{'name':'Justin Jordan'}),('montserrata',{'comment':'[jenniferh] Song tell already.'}),('suzanneg',{'comment':'[lyndat] Between.'}),('karenm',{'comment':'[michaelw] Question key sort.'}),('jessicaa',{'project':'Polarized Exuding Adapter','dob':'10/24/1995','job':'Horticultural Consultant'}),('franckb',{'dob':'09/13/1939','city':'Lake Jamieberg,Saskatchewan','job':'Boulanger'}),('leonardob',{'comment':'[karnavatik] Whom?!'}),('wandas',{'color':'purple','city':'South David,New Brunswick'}),('anilv',{'city':'Cohenburgh,British Columbia'}),('leonardob',{'comment':'[wandas] Born!'}),('charlesp',{'dob':'03/12/1945','job':'Outdoor Activities/Education Manager'}),('leonardob',{'comment':'[domingog] Long important.'}),('whitneyh',{'project':'Innovative Zero Tolerance Internet Solution','job':'Archaeologist'}),('tanl',{'name':'Tan Li'}),('marekn',{'color':'lime','project':'Programmable Context-Sensitive Customer Loyalty'}),('franciscojavierg',{'job':'Tefl Teacher'}),('diedrichs',{'project':'De-Engineered Impactful Array','city':'New Alexandriabury,Newfoundland and Labrador','job':'Engineer,Maintenance (It)'}),('bgk',{'comment':'[julieb] Include ask worker.'}),('suzanneg',{'dob':'06/14/1948'}),('angelac',{'dob':'05/06/1959','city':'Parsonsberg,Alberta'}),('renah',{'name':'Rena Hofmann'}),('leonardob',{'comment':'[omars] Religious similar water <3'}),('josettem',{'job':'Chef De Rayon'}),('zacharys',{'comment':'[justinj] Grow.'}),('sandrat',{'project':'Mandatory Grid-Enabled Interface','city':'Port Matthewchester,Nova Scotia','job':'Herbalist','name':'Sandra Tan'}),('montserrata',{'comment':'[jenniferh] Follow group hotel.'}),('aaronn',{'dob':'08/27/1942','name':'Aaron Nguyen'}),('amyh',{'project':'Organic Discrete Attitude','name':'Amy Holland'}),('leonardob',{'comment':'[ronaldr] Evening until!!!'}),('soraiaa',{'color':'black'}),('angelac',{'color':'olive','job':'Diplomatic Services Operational Officer','name':'Angela Cohen'}),('davidz',{'color':'lime','city':'New Christinebury,Nunavut'}),('leonardob',{'comment':'[susanf] Company night.'}),('diedrichs',{'comment':'[nicolej] Join.'}),('barbarah',{'comment':'[michaelw] Dog.'}),('zacharys',{'comment':'[angelad] Require quickly join.'}),('virginieg',{'dob':'07/05/1954','city':'East David,Quebec'}),('chelseas',{'dob':'04/29/1978','job':'Clinical Embryologist','name':'Chelsea Singh'}),('yongmeim',{'project':'Self-Enabling Holistic Service-Desk','color':'blue'}),('christophers',{'name':'Christopher Smith'}),('leonardob',{'comment':'[kathleenk] Thousand!!!'}),('jeremyw',{'city':'Jonesberg,Ontario'}),('heatherm',{'color':'teal','dob':'01/03/1990'}),('schang',{'comment':'[barbaras] Sort party data.'}),('philiph',{'dob':'01/09/1994','city':'Gregoryland,Nova Scotia'}),('leonardob',{'comment':'[rickyp] Arrive!'}),('leonardob',{'comment':'[anthonyd] Fast <3'}),('leonardob',{'comment':'[omars] Fight!'}),('leonardob',{'comment':'[susanf] Recently?!'}),('jamesd',{'comment':'[anthonyg] Look.'}),('davidd',{'name':'David Duran'}),('michaelw',{'project':'De-Engineered Exuding Website','name':'Michael Wood'}),('omars',{'city':'Lake Melissa,Yukon Territory'}),('deborahd',{'comment':'[zuzanr] Record write.'}),('leonardob',{'comment':'[elizabethm] Tend war?!'}),('amberv',{'dob':'09/10/1991','city':'Josephside,Nunavut','job':'Investment Banker,Corporate','name':'Amber Vance'}),('travism',{'name':'Travis Martinez'}),('kimberlyd',{'project':'Right-Sized Local Internet Solution','job':'Exhibition Designer'}),('melissac',{'city':'Lorishire,New Brunswick'}),('leonardob',{'comment':'[tinag] Happen recognize!!'}),('barbarah',{'city':'Jasonbury,Newfoundland and Labrador'}),('franciscojavierg',{'project':'Intuitive User-Facing Structure','city':'Craigmouth,Alberta'}),('leonardob',{'comment':'[daniellej] Street!!'}),('peters',{'dob':'12/12/1942','color':'purple','name':'Peter Singh'}),('cynthiav',{'comment':'[renah] Hand position.'}),('elizabethm',{'project':'Diverse Motivating Data-Warehouse','job':'Product/Process Development Scientist','name':'Elizabeth Mcmillan'}),('melissac',{'color':'black','project':'Persistent Systemic Strategy','job':'Intelligence Analyst','name':'Melissa Cooper'}),('melissac',{'comment':'[nicolej] Game.'}),('leonardob',{'comment':'[dianaf] Direction continue vote!'}),('leonardob',{'comment':'[fernandov] Gun fire!'}),('joannev',{'dob':'03/27/1981','color':'aqua','name':'Joanne Vincent'}),('andresv',{'color':'blue','city':'New Rebecca,British Columbia','name':'Andres Vicens'}),('joannev',{'project':'Secured Mission-Critical Forecast','city':'Brandonstad,New Brunswick'}),('tinag',{'dob':'07/08/1942'}),('melissab',{'project':'Future-Proofed Static Secured Line'}),('amyh',{'color':'olive'}),('jenniferh',{'comment':'[aaronc] Camera.'}),('schang',{'city':'Cookstad,British Columbia'}),('ronaldt',{'project':'Self-Enabling Multi-Tasking Website','city':'Holmestown,New Brunswick'}),('leonardob',{'comment':'[wojciechs] Center role!'}),('elenag',{'dob':'04/12/1959','city':'Christinefort,Manitoba'}),('franciscojavierg',{'comment':'[michaelw] Store operation fund.'}),('angelac',{'comment':'[annak] Rich.'}),('leonardob',{'comment':'[montserrata] Card!!!'}),('tamir',{'project':'Quality-Focused Modular Artificial Intelligence'}),('javierw',{'city':'Tiffanyport,New Brunswick'}),('leonardob',{'comment':'[anilv] Tv <3'}),('diedrichs',{'comment':'[cynthiav] Big service.'}),('montserrata',{'project':'Cross-Group Uniform Encoding','job':'Engineer,Biomedical'}),('clerosb',{'name':'Cleros Bernardi'}),('justinj',{'comment':'[annak] Movie consumer side.'}),('jeremyw',{'comment':'[ronaldt] I simply.'}),('tanl',{'comment':'[amyh] Fight.'}),('chelseas',{'color':'green'}),('bryanm',{'name':'Bryan McDonald'}),('suzanneg',{'color':'sarcelle','name':'Suzanne Gomez'}),('kathleenc',{'project':'Operative Object-Oriented Complexity','color':'teal','name':'Kathleen Cannon','city':'South Andreaburgh,Saskatchewan'}),('lindseyl',{'color':'gray'}),('martinb',{'project':'Sharable Mission-Critical Model','job':'Translator','name':'Martin Brady'}),('katherinev',{'dob':'01/18/1974','job':'Podiatrist'}),('ahmets',{'project':'Synergistic 5Thgeneration Access'}),('francoisb',{'comment':'[joannev] Prepare.'}),('elijahm',{'city':'Anneberg,Ontario','name':'Elijah Martinez'}),('virginieg',{'comment':'[angelad] Together.'}),('leonardob',{'comment':'[glennf] Pretty!!'}),('marekn',{'comment':'[melissac] Cold benefit happy.'}),('leonardob',{'comment':'[amishp] Campaign!!!'}),('leonardob',{'comment':'[michaelc] Hold!'}),('michaelc',{'color':'white'}),('hunterl',{'color':'olive','project':'User-Centric Asymmetric Paradigm','name':'Hunter Lewis'}),('michaelw',{'dob':'06/19/1981','city':'East Sharonfurt,Nunavut'}),('leonardob',{'comment':'[anthonyg] Friend conference!'}),('leonardob',{'comment':'[belenc] Son.'}),('lilyw',{'comment':'[meiz] Character.'}),('yongmeim',{'name':'Yongmei Ma'}),('aaronn',{'job':'Sports Therapist'}),('montserrata',{'dob':'09/28/1979','name':'Montserrat Aparicio'}),('michelles',{'city':'Erinmouth,New Brunswick'}),('andresv',{'dob':'11/07/1949','job':'Lecturer,Further Education'}),('schang',{'name':'Sigmund Chang'}),('leonardob',{'comment':'[lyndat] Sure world!'}),('gemmaa',{'dob':'03/21/1955','city':'Jacksonton,Saskatchewan','job':'Dealer','name':'Gemma Armstrong'}),('jenniferh',{'project':'Switchable Non-Volatile Encryption'}),('meiz',{'color':'silver','job':'Technical Officer'}),('maryn',{'dob':'12/24/1955','color':'navy'}),('daniellej',{'color':'aqua'}),('leonardob',{'comment':'[julieb] War!!'}),('leonardob',{'comment':'[lib] Chair budget!!'}),('amberv',{'comment':'[virginieg] Suggest physical.'}),('domingog',{'dob':'08/13/1956','city':'New Ronald,Ontario','job':'Aid Worker'}),('leonardob',{'comment':'[sorayav] Hear become poor?!'}),('leonardob',{'comment':'[mohammedj] Near lose exactly!!!'}),('cemalk',{'color':'olive','job':'Herbalist'}),('rickyp',{'color':'gray','dob':'12/22/1937','job':'Surveyor,Quantity'}),('barbaras',{'color':'yellow','job':'Hospital Doctor','name':'Barbara Sampson'}),('leonardob',{'comment':'[karnavatik] Shoulder seek fire.'}),('leonardob',{'comment':'[daniellej] Many!'}),('travism',{'color':'maroon','city':'Hayesside,New Brunswick'}),('fatimab',{'comment':'[wandas] Interest.'}),('wandas',{'comment':'[kathleenc] Interest.'}),('cynthiav',{'dob':'05/09/1975','color':'olive'}),('elizabethm',{'dob':'03/02/1971','color':'silver'}),('tarekk',{'dob':'04/14/1989','city':'Port Erin,British Columbia','name':'Tarek Kurdi'}),('alexandrao',{'color':'gray','project':'Persevering Solution-Oriented Task-Force','city':'Smithmouth,New Brunswick'}),('leonardob',{'comment':'[tinag] Name investment drop!'}),('christophers',{'comment':'[timoro] Would.'}),('fernandov',{'comment':'[ryanj] Friend near attention.'}),('ambikab',{'city':'Lake Andrea,Nunavut','job':'Engineer,Automotive','name':'Ambika Barvadekar'}),('amhedo',{'city':'Swansonchester,Alberta'}),('leonardob',{'comment':'[ireneb] Size.'}),('johnl',{'comment':'[rachelc] Become citizen.'}),('leonardob',{'comment':'[alexandrao] Break.'}),('franciscojavierg',{'color':'blue','dob':'05/31/1970','name':'Francisco Javier Gilabert'}),('nathanr',{'comment':'[joshuam] Morning difficult coach.'}),('meiz',{'dob':'06/11/1972','name':'Mei Zhang'}),('maryn',{'comment':'[melaniew] Best.'}),('leonardob',{'comment':'[jenniferh] Six!!'}),('christophea',{'comment':'[shawne] Election.'}),('philiph',{'color':'white'}),('timoro',{'comment':'[hsiangmeic] Total story senior.'}),('belenc',{'dob':'05/25/1982','project':'Front-Line Object-Oriented Access','name':'Belen Cabanas','color':'yellow'}),('elizabethm',{'comment':'[aaronn] Wait federal.'}),('danac',{'comment':'[jamesd] Artist go college.'}),('christophea',{'color':'argent','city':'Cameronburgh,Nunavut'}),('henriettek',{'dob':'05/12/1960'}),('leonardob',{'comment':'[henriettek] Western cover.'}),('melissac',{'comment':'[cynthiav] Least.'}),('leonardob',{'comment':'[ryanj] Expert image!'}),('barbaras',{'dob':'09/30/1977'}),('leonardob',{'comment':'[leonardob] Mean <3'}),('josettem',{'dob':'12/19/1966','city':'Kaylachester,New Brunswick','name':'Josette Martinea','project':'L Assurance De Changer Plus Rapidement'}),('stephaniev',{'job':'Surveyor,Minerals'}),('jeremyp',{'comment':'[aaronn] Consumer.'}),('leonardob',{'comment':'[fatimab] Church east!'}),('kristeng',{'color':'gray','city':'Port Wendy,Nova Scotia','job':'Chief Operating Officer'}),('jeremyp',{'comment':'[elizabethm] Despite will.'}),('michelles',{'dob':'07/03/1997','color':'silver','job':'Building Control Surveyor','project':'Mandatory Heuristic Time-Frame'}),('martinb',{'dob':'09/25/1939'}),('sorayav',{'color':'lime','city':'Kevinborough,Quebec','job':'Student','name':'Soraya Vatanakh'}),('leonardob',{'comment':'[ryanc] Entire report!'}),('henriettek',{'name':'Henriette Klein'}),('katherineg',{'dob':'03/21/1954','color':'teal','job':'Press Photographer'}),('hollyb',{'color':'teal','name':'Holly Bender'}),('leonardob',{'comment':'[hsiangmeic] Boy!'}),('verap',{'project':'Progetto Fondamentale Non-Volatile','city':'South Cynthia,Yukon Territory','dob':'09/01/1980'}),('reishiongx',{'project':'Centralized Dedicated Service-Desk','dob':'04/07/1975','color':'teal','city':'Hortonland,Ontario'}),('annak',{'job':'Operational Investment Banker'}),('charlesw',{'comment':'[luismiguelc] Themselves remember.'}),('renah',{'project':'Phased Radical Monitoring','job':'Community Pharmacist'}),('josettem',{'comment':'[suzanneg] During.'}),('kathleenc',{'job':'Consulting Civil Engineer'}),('hsiangmeic',{'color':'fuchsia','city':'Andersonbury,Manitoba','job':'Professor'}),('gordonl',{'comment':'[ryanc] Seek consider.'}),('jozefa',{'comment':'[dianaf] Green care often.'}),('leonardob',{'comment':'[susanr] Card item especially <3'}),('leonardob',{'comment':'[wandas] Behind!'}),('ryanc',{'color':'blue','dob':'07/24/1974','city':'New William,Northwest Territories'}),('maryn',{'project':'Balanced Exuding Website','city':'Allisonville,New Brunswick','name':'Mary Novak'}),('leonardob',{'comment':'[renah] Parent <3'}),('julieb',{'comment':'[emilye] Whole decide.'}),('sandrat',{'color':'green'}),('elijahm',{'dob':'08/28/1993'}),('julieb',{'color':'white','job':'Arts Administrator','name':'Julie Brown'}),('kathleenc',{'dob':'02/22/1959'}),('tanl',{'dob':'01/24/1939'}),('sorayav',{'project':'Focused Secondary Framework'}),('vinodk',{'color':'blue','city':'Hesterland,Northwest Territories'}),('cemalk',{'comment':'[jayan] Economic work.'}),('christineh',{'dob':'03/04/1981'}),('josem',{'project':'Right-Sized Interactive Product','city':'Port Monicaburgh,Nunavut','dob':'01/04/1974','name':'Jose Mason'}),('anilv',{'color':'black','dob':'03/15/1959','job':'Graphic Designer','name':'Anil Valimbe'}),('martinb',{'color':'fuchsia','city':'Lake Audrey,Quebec'}),('christineh',{'project':'Enterprise-Wide Attitude-Oriented Definition','city':'Janettown,New Brunswick','job':'Artist','color':'olive'}),('deborahd',{'color':'blue'}),('danac',{'comment':'[nathanr] Education.'}),('leonardob',{'comment':'[kathleenc] Candidate themselves bad.'}),('ericp',{'color':'teal','project':'Streamlined Hybrid Paradigm'}),('bgk',{'name':'B. G. K.'}),('alexandrao',{'comment':'[timoro] Once.'}),('tinag',{'project':'Phased Reciprocal Algorithm','city':'New Michaelfort,Ontario','name':'Tina Gould'}),('marcinj',{'color':'silver'}),('alexandrec',{'color':'ble','name':'Alexandre Chauvet'}),('domingog',{'project':'Strategia Orizzontale Valore Aggiunto','name':'Domingo Grassi'}),('leonardob',{'comment':'[tren] Yet!!'}),('leonardob',{'comment':'[kathleenc] Different!'}),('amberv',{'color':'yellow'}),('jozefa',{'comment':'[suzanneg] Bank.'}),('ryanc',{'job':'Designer,Interior/Spatial'}),('montserrata',{'color':'gray','city':'West Richardport,Saskatchewan'}),('jenniferl',{'dob':'02/28/1987','city':'West Carla,Quebec','name':'Jennifer Lee'}),('rachelc',{'dob':'01/29/1993'}),('franckb',{'color':'violet','name':'Franck Besnard'}),('leonardob',{'comment':'[sorayav] Imagine serve?!'}),('leonardob',{'comment':'[nicolej] Easy!!'}),('christophea',{'name':'Christophe Andre'}),('amberv',{'comment':'[marekn] Stuff type.'}),('tarekk',{'project':'Secured Encompassing Task-Force'}),('rachelc',{'city':'North Kimberlyhaven,Northwest Territories','name':'Rachel Clark'}),('verap',{'job':'Administrator'}),('leonardob',{'comment':'[josettem] Right event exist!'}),('jordanm',{'color':'maroon','city':'Port Amanda,Yukon Territory','job':'Trading Standards Officer'}),('hsiangmeic',{'comment':'[katherinev] Game.'}),('debraw',{'color':'navy','city':'Lake Victorbury,Nova Scotia','name':'Debra Wright','project':'Monitored Zero Tolerance Emulation'}),('jessicaa',{'city':'Rivaston,Saskatchewan','name':'Jessica Adams'}),('karenm',{'project':'Fundamental Needs-Based Moderator','job':'Trade Union Research Officer'}),('leonardob',{'comment':'[alexandrec] Surface.'}),('kathleenk',{'color':'blue'}),('bryanm',{'comment':'[melissac] Write.'}),('omars',{'job':'Psychologist,Prison And Probation Services'}),('travism',{'project':'Switchable Eco-Centric Forecast','dob':'05/12/1942','job':'Physicist,Medical'}),('leonardob',{'comment':'[sorayav] Fast!'}),('glennf',{'city':'Matthewland,Yukon Territory','name':'Glenn Forster'}),('leonardob',{'comment':'[charlesw] Near policy today body!'}),('leonardob',{'comment':'[verap] Poor!'}),('elijahm',{'comment':'[rachelc] Wonder.'}),('leonardob',{'comment':'[philiph] Speak.'}),('reishiongx',{'name':'Rei Shiong X'}),('leonardob',{'comment':'[ireneb] Step size available like!'}),('leonardob',{'comment':'[hunterl] Impact reality!'}),('ronaldr',{'comment':'[soraiaa] South.'}),('luismiguelc',{'project':'Total Solution-Oriented Instruction Set','job':'Social Researcher'}),('leonardob',{'comment':'[anthonyg] Protect national!!'}),('leonardob',{'comment':'[domingog] Than!!!'}),('gordonl',{'city':'Sanchezmouth,Northwest Territories','name':'Gordon Lee'}),('michelew',{'project':'Distributed Empowering Standardization'}),('cynthiav',{'project':'Face-To-Face Value-Added Protocol','city':'West Mathewfurt,Saskatchewan','job':'Police Officer'}),('leonardob',{'comment':'[debraw] Republican school?!'}),('rebeccam',{'color':'silver'}),('jayan',{'color':'white','name':'Jaya Nambisan'}),('lib',{'color':'purple','dob':'10/05/1991','job':'writer'}),('susanf',{'dob':'10/22/1999','job':'Animalier De Laboratoire'}),('wojciechs',{'dob':'01/11/1968','job':'Hycel'}),('leonardob',{'comment':'[davidd] Create!'}),('meryemm',{'comment':'[katherinev] Answer.'}),('cemalk',{'name':'Cemal Keudel'}),('jeremyw',{'color':'navy','dob':'04/20/1991','job':'Charity Fundraiser'}),('jenniferh',{'dob':'12/23/1958','job':'Engineer,Building Services','name':'Jennifer Harmon'}),('leonardob',{'comment':'[marekn] Cause!'}),('amberv',{'project':'Cross-Platform Cohesive Budgetary Management'}),('julieb',{'project':'Digitized Methodical Neural-Net','dob':'12/09/1953'}),('agatheg',{'color':'violet','city':'West Stephanie,Alberta'}),('julieb',{'comment':'[sitas] Answer value mouth.'}),('nicoles',{'project':'Profound 24Hour Protocol'}),('karenm',{'name':'Karen Murphy'}),('elenag',{'color':'gray'}),('charlesw',{'project':'Front-Line Tertiary Project','city':'Gardnerfort,Nunavut'}),('karnavatik',{'dob':'08/23/1957'}),('gordonl',{'color':'aqua','job':'Investment Analyst'}),('rebeccam',{'dob':'08/09/1938','job':'Development Worker,Community'}),('mamiec',{'comment':'[hsiangmeic] Agent.'}),('ronaldr',{'project':'Cross-Group Interactive Process Improvement'}),('anilv',{'project':'Multi-Tiered Cohesive Methodology'}),('jozefa',{'color':'gray','job':'Intendent','name':'Jozef Albin'}),('francoisb',{'name':'Francois Baron'}),('melaniew',{'color':'aqua'}),('verap',{'color':'navy','name':'Vera Palumbo'}),('amishp',{'city':'Johnhaven,Manitoba'}),('rickyp',{'project':'Polarized Disintermediate Intranet','city':'North Robert,Yukon Territory'}),('sueellenv',{'city':'Port April,Quebec','name':'Sue Ellen Valentini'}),('melissab',{'dob':'02/14/1945','city':'Port Kimberlyborough,Northwest Territories','name':'Melissa Burke'}),('joshuam',{'name':'Joshua Mccann'}),('glennf',{'comment':'[virginieg] Enjoy.'}),('ambikab',{'color':'maroon'}),('kristeng',{'name':'Kristen Gibson'}),('madhavir',{'dob':'12/11/1969','city':'Port Williamshire,New Brunswick','name':'Madhadvi Ravind'}),('leonardob',{'comment':'[franckb] Win!!!'}),('charlesp',{'project':'Adaptive Optimizing Product','name':'Charles Perry'}),('angelac',{'project':'Down-Sized Value-Added Structure'}),('christineh',{'name':'Christine Hensley'}),('erminiam',{'city':'Lake Michelle,Newfoundland and Labrador','job':'Event Organiser'}),('davidz',{'comment':'[jessicaa] Necessary production.'}),('leonardob',{'comment':'[jozefa] Speak!'}),('amishp',{'project':'Fully-Configurable Real-Time Initiative','name':'Amish Panja'}),('fatimab',{'project':'Up-Sized System-Worthy Ability','city':'Osbornbury,New Brunswick'}),('leonardob',{'comment':'[erminiam] Reveal!!!'}),('ryanj',{'color':'lime','city':'New Richard,Manitoba','job':'Cartographer'}),('gracer',{'dob':'08/11/1956'}),('philiph',{'project':'Universal Solution-Oriented Database','job':'Learning Disability Nurse','name':'Philip Harris'}),('leonardob',{'comment':'[debraw] Us program <3'}),('hayleyj',{'dob':'04/04/1960'}),('zacharys',{'project':'Ameliorated Content-Based Info-Mediaries','color':'navy'}),('ahmets',{'color':'fuchsia','city':'West Janeport,Quebec','job':'Armed Forces Technical Officer','dob':'08/01/2000'}),('emilye',{'comment':'[julieb] Over show.'}),('erminiam',{'project':'Successo Ricontestualizzata Stabile','color':'maroon','dob':'03/28/1964'}),('leonardob',{'comment':'[karnavatik] Soldier quality.'}),('lyndat',{'project':'Business-Focused Modular Synergy','job':'Advertising Copywriter'}),('fortunatac',{'color':'green'}),('ahmets',{'name':'Ahmet Schafer'}),('leonardob',{'comment':'[zacharys] Enough as environmental <3'}),('tamir',{'comment':'[nicolej] Top.'}),('kellyh',{'color':'aqua','city':'Jacquelineview,Ontario'}),('vinodk',{'comment':'[madhavir] Civil.'}),('leonardob',{'comment':'[fernandov] Score himself relationship?!'}),('kristeng',{'comment':'[hunterl] Yourself.'}),('fernandov',{'comment':'[ronaldt] Make.'}),('alexandrao',{'dob':'04/24/1945'}),('peters',{'comment':'[christophea] Purpose purpose.'}),('kathleenc',{'comment':'[sueellenv] Station.'}),('andresv',{'project':'Re-Contextualized User-Facing Support'}),('belenc',{'comment':'[zuzanr] Family.'}),('leonardob',{'comment':'[aaronn] Role choose?!'}),('amishp',{'comment':'[stephaniev] That argue.'}),('leonardob',{'comment':'[sandrat] Around!'}),('belenc',{'job':'Therapist,Nutritional'}),('michaelc',{'project':'Self-Enabling Bi-Directional Database','job':'Counselor','name':'Michael Chan'}),('stephaniev',{'color':'blue','project':'Reduced Holistic Database','name':'Stephanie Vance'}),('leonardob',{'comment':'[barbaras] Blue!!!'}),('clerosb',{'project':'Set Di Istruzioni Visionaria Locale'}),('lindseyl',{'dob':'07/16/1996'}),('josem',{'comment':'[belenc] Character.'}),('reishiongx',{'job':'Retail'}),('justinj',{'color':'navy','project':'Triple-Buffered Responsive Challenge','job':'Hotel Manager'}),('alexandrec',{'comment':'[elenag] Eight.'}),('christophers',{'project':'Reverse-Engineered Value-Added Complexity'}),('leonardob',{'comment':'[hayleyj] Too laugh!'}),('debraw',{'comment':'[bhairavig] Nature.'}),('kristeng',{'project':'Focused Well-Modulated Leverage','dob':'10/25/1974'}),('leonardob',{'comment':'[belenc] Community!!'}),('anthonyd',{'city':'South Brentbury,Manitoba','name':'Anthony Day'}),('timoro',{'dob':'08/20/1979','city':'Brandonland,Newfoundland and Labrador','name':'Timor Olegshov'}),('michelew',{'color':'navy','dob':'09/11/2003','job':'Administrator,Local Government','name':'Michele Walker'}),('jordanm',{'dob':'04/26/1945','name':'Jordan May'}),('jozefa',{'project':'Future-Proofed User-Facing Project'}),('sitas',{'project':'Seamless Motivating Circuit','name':'Sita Sinha'}),('davidd',{'comment':'[jordanm] Federal.'}),('wojciechs',{'comment':'[rebeccam] Catch of happen.'}),('jayan',{'dob':'03/28/1995','job':'Chief Operating Officer'}),('lyndat',{'color':'aqua','dob':'10/09/2005','name':'Lynda Taylor'}),('leonardob',{'comment':'[ronaldt] Alone full?!'}),('leonardob',{'comment':'[ronaldt] Herself house!'}),('kimberlyd',{'color':'white'}),('leonardob',{'comment':'[montserrata] Appear?!'}),('lilyw',{'project':'Open-Source Multimedia Hardware'}),('tarekk',{'color':'white','job':'Financial Trader'}),('maryn',{'job':'Fitness Centre Manager'}),('agatheg',{'comment':'[fortunatac] Born science media.'}),('marekn',{'dob':'04/08/1989','city':'Lake Catherinebury,Nova Scotia','job':'Chef'}),('leonardob',{'comment':'[zacharys] Join.'}),('christophea',{'comment':'[wandas] Age low policy.'}),('leonardob',{'comment':'[schang] Quite product!!!'}),('joshuam',{'comment':'[domingog] Truth.'}),('leonardob',{'comment':'[lilyw] We!'}),('angelad',{'dob':'09/30/1987','job':'Operations Geologist'}),('leonardob',{'comment':'[amberv] Realize report.'}),('bhairavig',{'project':'User-Centric Tertiary Software','city':'North Ericville,Newfoundland and Labrador','name':'Bhairavi Gupta'}),('emilye',{'job':'Wellsite Geologist'}),('leonardob',{'comment':'[ronaldt] Area general buy drug!!!'}),('nicolej',{'color':'fuchsia'}),('melaniew',{'comment':'[susanf] Fill building.'}),('alexandrec',{'dob':'03/16/1972'}),('sorayav',{'dob':'10/07/1983'}),('anthonyg',{'color':'fuchsia','city':'Amyberg,Nunavut','dob':'07/08/1948','project':'Self-Enabling Neutral Focus Group'}),('ambikab',{'comment':'[francoisb] Past.'}),('martinb',{'comment':'[belenc] Same.'}),('leonardob',{'comment':'[angelad] Size!!'}),('leonardob',{'comment':'[tanl] Sea!!'}),('leonardob',{'comment':'[charlesp] Former!'}),('dianaf',{'project':'Persevering Tertiary Graphic Interface'}),('fortunatac',{'project':'Implementazione Progressiva Modulare','job':'Radiographer,Diagnostic','name':'Fortunata Caputo'}),('tamir',{'color':'gray','city':'West Ericashire,Northwest Territories','name':'Tami Rodriguez'}),('leonardob',{'comment':'[bgk] Recognize!!'}),('susanf',{'project':'L Assurance De Louer Autrement','city':'Camachoport,New Brunswick','name':'Susan Fontaine'}),('jayan',{'project':'Profound Non-Volatile Matrix','city':'West Amandaport,Manitoba'}),('leonardob',{'project':'Multi-Channeled 6Thgeneration Implementation'}),('fatimab',{'dob':'07/26/1976','color':'white','name':'Fatima Buendia'}),('diedrichs',{'dob':'08/15/1972','name':'Diedrich Staude'}),('virginieg',{'project':'Le Pouvoir De Rouler a Sa Source'}),('javierw',{'color':'maroon','name':'Javier Washington'}),('leonardob',{'comment':'[tarekk] Senior floor!!'}),('josettem',{'comment':'[melissab] What very.'}),('kimberlyd',{'dob':'01/04/1965','city':'South Cassie,Ontario','name':'Kimberly Diaz'}),('leonardob',{'comment':'[stephaniev] Value!'}),('elijahm',{'comment':'[joannev] Old his chance market.'}),('meryemm',{'comment':'[tanl] Ask.'}),('jamesd',{'color':'yellow','name':'James de Gruil'}),('leonardob',{'comment':'[marcinj] Around sign prepare?!'}),('madhavir',{'color':'purple'}),('francoisb',{'job':'Ingenieur Recherche Et Developpement En Agroalimentaire'}),('lilyw',{'city':'Lifurt,Quebec','job':'Nurse'}),('erminiam',{'comment':'[erminiam] Poor.'}),('lindseyl',{'project':'Compatible Foreground Focus Group','city':'South Rodneyview,Prince Edward Island','job':'Visual Merchandiser','name':'Lindsey Lee'}),('timoro',{'comment':'[diedrichs] Work.'}),('luismiguelc',{'dob':'11/05/1943'}),('leonardob',{'comment':'[karnavatik] Nice!!'}),('renah',{'dob':'12/28/1949','city':'Lorifort,New Brunswick','color':'green'}),('henriettek',{'project':'Le Pouvoir D Innover Naturellement','city':'Port Carlosfort,British Columbia','job':'Operateur De Raffinerie','color':'bordeaux'}),('leonardob',{'comment':'[lyndat] Eight majority plant fill?!'}),('hsiangmeic',{'dob':'11/19/1965','project':'Self-Enabling Clear-Thinking Help-Desk'}),('anthonyg',{'job':'Accountant,Chartered Management'}),('francoisb',{'comment':'[christineh] Move.'}),('fernandov',{'city':'Port Amy,New Brunswick','name':'Fernando Vinas'}),('leonardob',{'comment':'[agatheg] Establish thousand?!'}),('peters',{'project':'Organized Coherent Knowledge User','city':'Port Jessica,Prince Edward Island'}),('johnl',{'dob':'11/24/2005','city':'West Joshua,New Brunswick'}),('tren',{'dob':'01/14/1998'}),('gracer',{'color':'blue','project':'Focused Zero Administration Monitoring','name':'Grace Ryan'}),('ronaldr',{'color':'maroon','city':'New Joseph,New Brunswick','dob':'07/15/1950','name':'Ronald Reese'}),('leonardob',{'comment':'[angelac] Forward!'}),('elizabethm',{'comment':'[jayan] Production.'}),('susanr',{'color':'white','city':'Zimmermanview,Prince Edward Island','job':'Dispensing Optician','name':'Susan Reid'}),('mohammedj',{'name':'Mohammed Jennings'}),('leonardob',{'comment':'[kathleenk] Benefit score!'}),('zuzanr',{'color':'gray'}),('leonardob',{'comment':'[mohammedj] Character on!'}),('leonardob',{'comment':'[madhavir] Fact.'}),('leonardob',{'comment':'[aaronc] Quickly off!'}),('marcinj',{'comment':'[leonardob] Unit.'}),('cynthiav',{'comment':'[philiph] Edge.'}),('ronaldr',{'job':'Community Arts Worker'}),('deborahd',{'comment':'[davidd] But interview.'}),('hunterl',{'job':'Accounting Technician'}),('leonardob',{'comment':'[jayan] Role!!!'}),('christophers',{'comment':'[angelac] Help.'}),('aaronn',{'comment':'[katherineg] Method.'}),('bhairavig',{'dob':'12/27/1954','job':'Engineer,Technical Sales'}),('tinag',{'comment':'[ryanc] Back.'}),('bryanm',{'comment':'[reishiongx] Edge.'}),('hayleyj',{'city':'Brandonbury,Nunavut','job':'Emergency Planning/Management Officer','name':'Hayley Jones'}),('alexandrao',{'comment':'[lilyw] Choice.'}),('susanf',{'comment':'[dianaf] Song.'})]

