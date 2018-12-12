#!/Users/eric.hsu/anaconda2/bin/python

from faker import Faker

# "Census in Brief: Ethnic and cultural origins of Canadians: Portrait of a rich heritage"
#https://www12.statcan.gc.ca/census-recensement/2016/as-sa/98-200-x/2016016/98-200-x2016016-eng.cfm

locale_tallies = [
  ('en_CA', 64), # Canadian (First Nations? Racists? Anti-Racist?)
  ('en_GB', 12), # English
  ('en_GB', 4),  # Scottish
  ('fr_FR', 10), # French
  ('en_GB', 5),  # Irish :(
  ('de_DE', 6),  # German
  ('zh_CN', 14), # Chinese
  ('it_IT', 6),  # Italian
  ('en_CA', 5),  # First Nations :(
  ('hi_IN', 11), # East Indian
  ('uk_UA', 3),  # Ukrainian
  ('nl_NL', 3),  # Dutch
  ('pl_PL', 3),  # Polish
  ('es_ES', 7),  # Filipino :(
  ('ru_RU', 1),  # Russia
  ('pt_PT', 2)   # Portuguese
]

# Names), etc. generated from other locales, but cities/provinces are
# all from this one.
BASE_LOCALE = 'en_CA'
base_faker = Faker(locale=BASE_LOCALE)
base_faker.seed(42)

def gen_locale(locale=BASE_LOCALE, num=1):
  faker = Faker(locale=locale)
  return [ gen_person(faker) for _ in range(num) ]

def gen_person(faker):
  p = {}
  fn = faker.first_name()
  ln = faker.last_name()
  p['name'] = fn + " " + ln
  p['user'] = (fn + ln[0]).lower()
  p['city'] = base_faker.city() + ", " + base_faker.province()
  p['color'] = faker.safe_color_name()
  p['job'] = faker.job().title()
  p['project'] = faker.catch_phrase().title()
  p['dob'] = faker.date_of_birth(minimum_age=13,
                                 maximum_age=80).strftime('%m/%d/%Y')
  return p

def format_person(person):
  #atts = ['  "{}" : "{}"'.format(f, person[f]) for f in person]
  atts = []
  for k in person:
    atts.append("  '" + k + "' : '" + person[k] + "'")
  return "{\n" + ",\n".join(atts) + "\n}\n"

people = []
for (l,n) in locale_tallies:
  people += gen_locale(locale=l, num=n)

for person in people:
  print(format_person(person))

