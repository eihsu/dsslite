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

sim = Simulation(lb)
sim.run()
