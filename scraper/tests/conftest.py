import os
import sys

# Tests import modules with flat names (e.g. `import domain_rules`) matching
# how the scraper's own modules import each other in production (no
# package/`src` layout). Make sure `scraper/` is on sys.path regardless of
# the directory pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
