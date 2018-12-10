from dsslite import *

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

lb = LoadBalancer([w1,w2,w3,w4,w5])

def trivial_hash(name):
  choice = 1
  return choice

def simple_hash(name):
  if len(name) == 1:
    choice = 1
  if len(name) == 2:
    choice = 2
  if len(name) == 3:
    choice = 3
  if len(name) == 4:
    choice = 4
  if len(name) > 4:
    choice = 5

  return choice

# Initially can show:
# lb.set_hash(trivial_hash)

lb.set_hash(simple_hash)

sim = Simulation(lb, sim_speed=0)
sim.run()
