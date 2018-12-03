from dsslite import *

db = Database()
w = Worker(db)
sim = Simulation(w)
sim.run()
