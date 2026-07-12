---
name: family_helper
description: Разработка и поддержка системы автоскрининга очков семьи в GTA SA-MP (Moonloader + Python Telegram Bot)
---

# Разработка системы Family Helper (Advance RP)

Этот документ описывает технические детали, протоколы взаимодействия, архитектуру базы данных и обработку крайних случаев для системы учета очков семьи. Любой агент, работающий в этом репозитории, должен следовать этим правилам.

## 🛠 Архитектурные детали и крайние случаи (Edge Cases)

В ходе проектирования были учтены следующие критически важные детали:

### 1. Безопасность API (Авторизация запросов)
Поскольку Flask-сервер принимает POST-запросы из интернета, любой злоумышленник, узнавший IP-адрес сервера, может отправить фейковые данные о начислении очков.
* **Решение**: Внедрение `API_KEY` (секретного токена) в `config.json`.
* Lua-скрипт отправляет заголовок `Authorization: Bearer <секретный_токен>`.
* Flask-сервер сверяет токен перед обработкой запроса. При несовпадении возвращается `401 Unauthorized`.

### 2. Автоматическое списание долгов из игры (Smart Pay)
Создателю семьи неудобно каждый раз после перевода денег в игре заходить в Telegram и писать команду выплаты.
* **Решение**: Lua-скрипт перехватывает исходящие команды `/pay [ID] [Сумма]` и `/transfer [ID/Номер_Счета] [Сумма]` (банковский перевод).
* Скрипт по ID игрока мгновенно определяет его ник через Moonloader API: `sampGetPlayerNickname(id)`.
* Отправляет на Flask-сервер запрос списания: `{ "player_name": "Nick_Name", "amount": 50000, "source": "game_chat" }`.
* Бот списывает долг игрока по текущему курсу и шлет отчет в Telegram: `💵 [В игре] Создатель перевел Nick_Name 50,000$ (списано 5 очков). Остаток долга: 0 очков.`

### 3. Смена никнеймов в SAMP
Игроки в SAMP часто меняют ники. Если игрок сменит ник, база данных создаст новую запись, а старый долг зависнет на старом нике.
* **Решение**: Добавление команды в Telegram-бот: `/rename [Старый_Ник] [Новый_Ник]`.
* Команда обновляет имя игрока во всех таблицах (`members`, `payments`) без потери истории очков и выплат.

### 4. Сохранение долга уволенных игроков
Если игрока выгнали или он сам ушел из семьи, он пропадет из диалога `/family`.
* **Решение**: База данных хранит историю навсегда. Игрок пропадет из отчетов новых скринингов, но в общем списке должников `/stats` он останется, пока создатель не закроет его долг.

### 5. Обнуление очков сервером (Сбросы)
В Advance RP очки за день сбрасываются в полночь, а также могут обнуляться при пересоздании семьи.
* **Математика долга**: `unpaid_points = points_total - points_paid`.
* При первом добавлении игрока: `points_paid = points_total - points_day` (долг равен сегодняшним очкам, старые очки считаются выплаченными).
* Если при очередном сканировании обнаружено, что `new_points_total < old_points_total` (произошел сброс), бот автоматически корректирует `points_paid`, приравнивая его к `new_points_total - new_points_day`, чтобы долг игрока не ушел в минус и рассчитывался корректно.

---

## 🔌 Протокол API (Lua -> Python)

Flask-сервер работает по умолчанию на порту `5000` и имеет два эндпоинта:

### 1. Отправка результатов скрининга
* **URL**: `POST /api/scan`
* **Headers**: 
  - `Content-Type: application/json`
  - `Authorization: Bearer <API_KEY>`
* **Payload**:
  ```json
  {
    "scanner_name": "Maximo_Himars",
    "members": [
      {
        "name": "Oscar_Sequence",
        "rank": "6 (Фея)",
        "points_day": 0,
        "points_total": 50
      },
      {
        "name": "Jack_Vodogrey",
        "rank": "10 (Супер-Голова)",
        "points_day": 10,
        "points_total": 6598
      }
    ]
  }
  ```

### 2. Сигнал внутриигровой выплаты (Smart Pay)
* **URL**: `POST /api/game-pay`
* **Headers**:
  - `Content-Type: application/json`
  - `Authorization: Bearer <API_KEY>`
* **Payload**:
  ```json
  {
    "sender_name": "Maximo_Himars",
    "player_name": "Jack_Vodogrey",
    "amount": 100000
  }
  ```

---

## 🗄 Структура Базы Данных (SQLite)

Файл базы данных: `family.db`.

```sql
CREATE TABLE IF NOT EXISTS members (
    name TEXT PRIMARY KEY,
    rank TEXT,
    points_day INTEGER,
    points_total INTEGER,
    points_paid INTEGER DEFAULT 0,
    money_paid INTEGER DEFAULT 0,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name TEXT,
    points INTEGER,
    money INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    comment TEXT,
    FOREIGN KEY(player_name) REFERENCES members(name)
);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    scanner_name TEXT,
    total_members INTEGER,
    raw_data TEXT
);
```
