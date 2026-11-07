import matplotlib.pyplot as plt
import pandas as pd
import io
from aiogram.types import BufferedInputFile
from datetime import datetime, timedelta


def generate_pie_chart(transactions, title: str = "Расходы по категориям"):
    if not transactions:
        return None

    df = pd.DataFrame(transactions)
    grouped = df.groupby('category')['amount'].sum()
    max_categories = 10
    if len(grouped) > max_categories:
        top = grouped.nlargest(max_categories - 1)
        other = grouped.drop(top.index).sum()
        grouped = pd.concat([top, pd.Series({'Другое': other})])

    labels = [c.capitalize() for c in grouped.index]
    sizes = grouped.values.tolist()

    plt.figure(figsize=(8, 8))
    plt.pie(
        sizes,
        labels=labels,
        autopct='%1.1f%%',
        startangle=90,
        counterclock=False
    )
    plt.title(title, pad=20)
    plt.axis('equal')

    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=150)
    buf.seek(0)
    plt.close()

    return BufferedInputFile(buf.read(), filename="expenses_pie.png")
