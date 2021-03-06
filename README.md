# `dsslite` Distributed Systems Simulator, Lite

Simple deterministic distributed systems simulator for
instructional purposes.

For use in hands-on demonstrations with participants who have
potentially no experience with Python, distributed systems, or
computers in general.  Designed for ease of deployment (for instance,
resides in a single file, compatible with Python 2 and Python 3), and
usage (configuration through constructors and accessors, simple
logging.)

### Usage

Import as a module, for instance `from dsslite import *`.

Here is a minimal example:

```python
from dsslite import *

db = Database()
w = Worker(db)
sim = Simulation(w)
sim.run()
```

This example and others are found in the `examples/` subdirectory.


### Next steps:

+ Fancier text-based output.  (Prefer not to go with gui, so
participants can get an arguably more authentic back-end experience,
and to maintain ease of deployment.)

+ Still not a UI, but maybe plot out a static graph at the end, as a
file.

+ HTML page for viewing db contents at the end, so people can make the
connection to the final product they see on for instance a social
media feed.

+ More options for configuring time delays and simulation speed.  Tune
options to accentuate desired observations as users progress through
the examples.

+ Allow multi-processing on workers.

+ Live high score system where runtime results get posted to server.
