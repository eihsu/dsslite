#!/Users/eric.hsu/anaconda2/bin/python

from faker import Faker
fake = Faker()

fake.seed(42)

def gen_person():
  r = {}
  fn = fake.first_name()
  ln = fake.last_name()
  r['name'] = fn + " " + ln
  r['user'] = (fn + ln[0]).lower()
  r['city'] = fake.city() + ", " + fake.country()
  r['color'] = fake.safe_color_name()
  r['job'] = fake.job()
  r['company'] = fake.company()
  r['dob'] = fake.date_of_birth(minimum_age=13, maximum_age=80)
  r['url'] = fake.url()
  r['comments'] = []
  return r

print(gen_person())
