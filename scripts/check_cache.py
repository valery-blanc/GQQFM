"""Diagnostic du cache Polygon — comptage par type de requête."""
import sqlite3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else r"C:/WORK/GQQFM/data/.polygon_cache.db"
c = sqlite3.connect(path)
total = c.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
print(f"Total entries: {total}")
patterns = c.execute("""
  SELECT
    CASE
      WHEN cache_key LIKE '%/range/1/day/%' AND cache_key LIKE '%O:%' THEN 'leg-bars'
      WHEN cache_key LIKE '%/range/1/day/%' THEN 'underlying-bars'
      WHEN cache_key LIKE '%reference/options/contracts%' THEN 'chain-listing'
      WHEN cache_key LIKE '%/range/1/hour/%' OR cache_key LIKE '%/range/1/minute/%' OR cache_key LIKE '%/range/15/minute/%' OR cache_key LIKE '%/range/5/minute/%' THEN 'intraday-bars'
      ELSE 'other'
    END AS kind,
    COUNT(*)
  FROM responses GROUP BY 1 ORDER BY 2 DESC
""").fetchall()
for k, n in patterns:
    print(f"  {k}: {n:,}")
