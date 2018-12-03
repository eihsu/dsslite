from dsslite import *

db = Database()

w1 = Worker(db)
w2 = Worker(db)
w3 = Worker(db)
w4 = Worker(db)
w5 = Worker(db)

lb = LoadBalancer([w1,w2,w3,w4,w5])

sim = Simulation(lb)
sim.run()
