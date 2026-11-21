# Read automatically by gunicorn from this folder (Render runs `gunicorn app:app` here).
# Threads let quick requests (trips, announcements) run while a slow one waits on the
# NCAA API or Aivana, instead of every page sitting on "Loading..." behind it.
workers = 1          # demo mode keeps data in memory, so it must stay in one process
threads = 8
timeout = 90
