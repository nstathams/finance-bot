import sqlite3

con = sqlite3.connect("finance.db")

print("Все операции за сегодня, все пользователи:")
for r in con.execute(
    "SELECT user_id, type, amount_kop/100.0, category, date "
    "FROM transactions WHERE substr(date,1,10) >= '2026-11-19'"
):
    print(r)

print("\nЧистый итог за сегодня по каждому пользователю:")
for r in con.execute(
    "SELECT user_id, "
    "SUM(CASE WHEN type='income' THEN amount_kop ELSE 0 END)/100.0 - "
    "SUM(CASE WHEN type='expense' THEN amount_kop ELSE 0 END) "
    "FROM transactions WHERE substr(date,1,10) >= '2026-11-19' GROUP BY user_id"
):
    print(r)
