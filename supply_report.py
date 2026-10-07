import requests
import pandas as pd
from datetime import datetime, timedelta

# --- НАСТРОЙКИ ---
SALES_API_URL = "https://statistics-api.wildberries.ru/api/v1/supplier/sales"
SUPPLIES_API_URL = "https://supplies-api.wildberries.ru/api/v1/supplies"
TOKEN_STATISTICS = "ТОКЕН_СТАТИСТИКИ"
TOKEN_SUPPLIES = "ТОКЕН_ПОСТАВОК"

# Список конкретных идентификаторов поставок
TARGET_INCOME_IDS = [56735459, 56735460, 56735461]


def fetch_supply_info(token, income_id):
    """
    Запрашивает информацию о поставке через supplies-api: /api/v1/supplies/{ID}
    Возвращает словарь с полями поставки, включая supplyDate.
    """
    url = f"{SUPPLIES_API_URL}/{income_id}"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
    }

    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


def fetch_supply_goods(token, income_id):
    """
    Запрашивает список товаров по поставке через supplies-api.
    Возвращает список словарей — по одному на каждый товар (nmID).
    """
    url = f"{SUPPLIES_API_URL}/{income_id}/goods"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
    }

    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()

    if isinstance(data, list):
        return data
    return []


def fetch_wb_sales_data(token, date_from, date_to=None, limit=10000):
    """
    Запрашивает данные о продажах и возвратах из statistics-api.
    """
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
    }
    params = {
        "dateFrom": date_from,
        "limit": limit,
    }
    if date_to:
        params["dateTo"] = date_to

    response = requests.get(SALES_API_URL, headers=headers, params=params)
    response.raise_for_status()
    return response.json()


def parse_date(date_str):
    """Безопасно парсит дату из строки. Возвращает None, если не получилось."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def build_supply_report(income_ids, supplies_token, stats_token):
    """
    Строит отчёт в разрезе поставка + товар (nmID):
      - supplyDate — из supplies-api (информация о поставке)
      - поступило — из supplies-api (/supplies/{ID}/goods, поле quantity)
      - продано / возвращено — из statistics-api (/supplier/sales)
      - максимальная дата продажи и возврата — из statistics-api
    """

    # --- Шаг 1: для каждой поставки получаем supplyDate и список товаров ---
    # supply_dates: {income_id: "YYYY-MM-DD"}
    # supplied: {(income_id, nm_id): qty}
    supply_dates = {}
    supplied = {}

    for income_id in income_ids:
        # Информация о поставке
        try:
            info = fetch_supply_info(supplies_token, income_id)
            supply_date = info.get("supplyDate", "")
            supply_dates[income_id] = supply_date
            print(f"  Поставка {income_id}: supplyDate = {supply_date}")
        except requests.HTTPError as e:
            print(f"  Поставка {income_id}: ошибка при получении информации — {e}")
            supply_dates[income_id] = ""

        # Товары в поставке
        try:
            goods = fetch_supply_goods(supplies_token, income_id)
            print(f"  Поставка {income_id}: товаров в ответе — {len(goods)}")
        except requests.HTTPError as e:
            print(f"  Поставка {income_id}: ошибка при получении товаров — {e}")
            continue

        for item in goods:
            nm_id = item.get("nmID")
            qty = item.get("quantity", 0)
            if nm_id is None:
                continue
            key = (income_id, nm_id)
            supplied[key] = supplied.get(key, 0) + qty

    # --- Шаг 2: запрашиваем продажи/возвраты ---
    # DATE_FROM = минимальная из всех supplyDate
    # DATE_TO   = сегодня
    valid_dates = [
        parse_date(d) for d in supply_dates.values() if parse_date(d) is not None
    ]
    if valid_dates:
        date_from = min(valid_dates).strftime("%Y-%m-%d")
    else:
        # Фолбэк: последние 90 дней, если supplyDate не получены
        date_from = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    date_to = datetime.now().strftime("%Y-%m-%d")

    print(f"\nЗапрос продаж с {date_from} по {date_to}...")
    try:
        sales_data = fetch_wb_sales_data(stats_token, date_from, date_to)
    except requests.HTTPError as e:
        print(f"Ошибка при запросе продаж: {e}")
        return []

    # --- Шаг 3: считаем продажи, возвраты и максимальные даты ---
    target_set = set(income_ids)
    sold = {}
    returned = {}
    last_sale_date = {}      # {(income_id, nm_id): "YYYY-MM-DDTHH:MM:SS"}
    last_return_date = {}    # {(income_id, nm_id): "YYYY-MM-DDTHH:MM:SS"}

    for row in sales_data:
        income_id = row.get("incomeID")
        nm_id = row.get("nmId")
        if income_id is None or income_id not in target_set:
            continue
        if nm_id is None:
            continue

        key = (income_id, nm_id)
        row_date = row.get("date") or row.get("lastChangeDate")

        if row.get("isRealization") is True:
            sold[key] = sold.get(key, 0) + 1
            if row_date:
                if key not in last_sale_date or row_date > last_sale_date[key]:
                    last_sale_date[key] = row_date

        if row.get("isSupply") is True:
            returned[key] = returned.get(key, 0) + 1
            if row_date:
                if key not in last_return_date or row_date > last_return_date[key]:
                    last_return_date[key] = row_date

    # --- Шаг 4: формируем итоговую таблицу ---
    all_keys = set(supplied.keys()) | set(sold.keys()) | set(returned.keys())

    rows = []
    for income_id, nm_id in sorted(all_keys):
        sup = supplied.get((income_id, nm_id), 0)
        s = sold.get((income_id, nm_id), 0)
        ret = returned.get((income_id, nm_id), 0)
        current_stock = sup - s
        sdate = supply_dates.get(income_id, "")

        rows.append({
            "Идентификатор поставки": income_id,
            "Дата поставки": sdate,
            "Идентифкатор товара": nm_id,
            "Кол-во товара в поставке": sup,
            "Кол-во проданного товара": s,
            "Кол-во возвращенного товара": ret,
            "Фактическое кол-во товара на складе": current_stock,
            "Максимальная дата продажи": last_sale_date.get((income_id, nm_id), ""),
            "Максимальная дата возврата": last_return_date.get((income_id, nm_id), ""),
        })

    return rows


def main():
    if not TARGET_INCOME_IDS:
        print("Не указаны идентификаторы поставок. Заполните TARGET_INCOME_IDS.")
        return

    print("Запрос информации о поставках и товарах...")
    report = build_supply_report(
        TARGET_INCOME_IDS, TOKEN_SUPPLIES, TOKEN_STATISTICS
    )

    if not report:
        print("Нет данных для отчёта.")
        return

    df = pd.DataFrame(report)

    output_file = "wb_supply_report.xlsx"
    df.to_excel(output_file, index=False)
    print(f"\nОтчёт сохранён в {output_file}, строк: {len(df)}")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
