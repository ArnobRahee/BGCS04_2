import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Tests never use real services or the keys in a local .env. Empty values (not removed ones),
# because load_dotenv() only fills variables that aren't set at all.
for name in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "AIVANA_API_KEY", "AIVANA_API_BASE",
             "GOOGLE_MAPS_API_KEY", "GOOGLE_MAPS_BROWSER_KEY"):
    os.environ[name] = ""
