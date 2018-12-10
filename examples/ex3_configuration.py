from dsslite import *

db = Database()

w1 = Worker(db)
w2 = Worker(db)
w3 = Worker(db)
w4 = Worker(db)
w5 = Worker(db)
w6 = Worker(db)
w7 = Worker(db)
w8 = Worker(db)
w9 = Worker(db)
w10 = Worker(db)

lb = LoadBalancer([w1,w2,w3,w4,w5,w6,w7,w8,w9,w10])

# Can show shortcuts like loops or comprehensions, i.e.:
# lb = LoadBalancer([ Worker(db) for i in range(10) ])

sim = Simulation(lb, sim_speed=0)
sim.run()
