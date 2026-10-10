import sqlite3
c = sqlite3.connect('greyhound_racing.db')
cur = c.cursor()
cur.execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name")
for row in cur.fetchall():
    print(row[0])
    print()
