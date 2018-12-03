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

lb = LoadBalancer([[w1,w6],
                   [w2,w7],
                   [w3,w8],
                   [w4,w9],
                   [w5,w10]])

sim = Simulation(lb)
sim.set_failures(True)
sim.run()
