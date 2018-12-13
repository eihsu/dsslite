#!/Users/eric.hsu/anaconda2/bin/python

import json
import random
import faker


#[user]
#city
#color
#dob
#job
#name
#project

fake = faker.Faker()

# Load json into a list of dicts, each dict representing a person.
with open('people.json', 'r') as f:
  people = json.load(f)["people"]

reqs = []

# Split up atts into requests
for p in people:
  user = p['user']
  atts = [ (k, p[k]) for k in p.keys() if k != 'user' ]
  # randomly partition atts into three non-empty sets.
  hat = range(1, len(atts))
  c1 = random.choice(hat)
  hat.remove(c1)
  c2 = random.choice(hat)
  cuts = sorted([c1, c2])
  random.shuffle(atts)
  atts1 = atts[:cuts[0]]
  atts2 = atts[cuts[0]:cuts[1]]
  atts3 = atts[cuts[1]:]
  for atts in [atts1, atts2, atts3]:
      reqs.append((user, dict(atts)))

# Add zero to two comments per person
for p in people:
  user = p['user']
  for _ in xrange(random.randint(0,2)):
      n = random.randint(1,3)
      author = random.choice(people)['user']
      comm = "[{}] {}".format(author, fake.sentence(nb_words=n)) 
      reqs.append((user, { 'comment' : [ comm ] }))

# Extra commments for bgk, replace period with exclamation points.
for _ in xrange(200):
  n = random.randint(1,3)
  author = random.choice(people)['user']
  comm = "[{}] {}".format(author, fake.sentence(nb_words=n))[:-1]
  comm += random.choice(["!", "!", "!", "!!", "!!!", " <3", "?!", "."])
  reqs.append(('bgk', { 'comment' : [ comm ] }))

# For us, data will live with code lol.
random.shuffle(reqs)
s = "{}".format(reqs)
with open('reqs.txt', 'w') as f:
  f.write(s)
  f.write('\n')

