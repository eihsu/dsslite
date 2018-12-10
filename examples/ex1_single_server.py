from dsslite import *

db = Database()
w = Worker(db)
sim = Simulation(w, sim_speed=0)
sim.run()
