from dsslite import *
import json
from pprint import pprint

db = Database()
w1 = Worker(db)
w2 = Worker(db)
w3 = Worker(db)
w4 = Worker(db)
lb = LoadBalancer([w1, w2, w3, w4])
sim = Simulation(lb, sim_speed=0)
sim.run()


rs1 = sim.reqs_sent.copy()

sim.run(rate=2.00)

rs2 = sim.reqs_sent.copy()

sim.run(rate=3.00)

rs3 = sim.reqs_sent.copy()

s1 = set([ (r.user,json.dumps(r.data)) for r in rs1.values() ])
s2 = set([ (r.user,json.dumps(r.data)) for r in rs2.values() ])
s3 = set([ (r.user,json.dumps(r.data)) for r in rs3.values() ])

print len(rs1)
print len(rs2)
print len(rs3)
print len(s1)
print len(s2)
print len(s3)


pprint(s1 - s2)

print("\n\n\n")

pprint(s1 - s3)
