from dsslite import *

import simulator

db1 = Database()
db2 = Database()
db3 = Database()
db4 = Database()
db5 = Database()

w1 = Worker(db1)
w2 = Worker(db2)
w3 = Worker(db3)
w4 = Worker(db4)
w5 = Worker(db5)

w6 = Worker(db1)
w7 = Worker(db2)
w8 = Worker(db3)
w9 = Worker(db4)
w10 = Worker(db5)

def alpha_hash(name):
  # (Participants can examine traffic and find that disproportionate
  #  number of requests involve a single account.  Can explain caching
  #  as alternative to dedicated machine.)
  if name == "kim":
    return 1

  c = name[0]
  if c < 'h':
    # names starting with [a-f]
    return 2
  if c < 'g':
    # names starting with [g-n]
    return 3
  if c < 'g':
    # names starting with [o-s]
    return 4

  # all others, i.e. names starting with [t-z]
  return 5

lb.set_hash(alpha_hash)

lb = LoadBalancer([[w1,w6],
                   [w2,w7],
                   [w3,w8],
                   [w4,w9],
                   [w5,w10]])

sim = Simulation(lb, sim_speed=0)
sim.set_failures(True)
sim.run()
